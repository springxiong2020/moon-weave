import ipaddress
import json
import logging
import os
import random
import time
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import redis
import requests
from sqlalchemy import select

from .db import Location

log = logging.getLogger(__name__)


def location_text(value):
    """Provider fields may be null, placeholders, or unexpected JSON types."""
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return (
        ""
        if value.lower() in {"", "none", "null", "unknown", "n/a", "-", "0"}
        else value
    )


def normalize_location(data):
    content = data.get("content")
    detail = content.get("address_detail") if isinstance(content, dict) else None
    if not isinstance(detail, dict):
        raise TypeError("百度 status=0 但缺少有效 content.address_detail 对象")
    province = location_text(detail.get("province"))
    city = location_text(detail.get("city"))
    nation = location_text(detail.get("nation"))
    code = location_text(detail.get("nation_code")).upper()
    address = location_text(data.get("address"))
    address_code = address.split("|", 1)[0].strip().upper() if "|" in address else ""
    if not code and address_code.isalpha() and len(address_code) in (2, 3):
        code = address_code
    regions = {
        "HK": "香港特别行政区",
        "HKG": "香港特别行政区",
        "MO": "澳门特别行政区",
        "MAC": "澳门特别行政区",
        "TW": "台湾省",
        "TWN": "台湾省",
    }
    domestic = code in {"CN", "CHN"} or nation in {
        "中国",
        "中华人民共和国",
        "China",
        "CHINA",
    }
    if code in regions:
        province = regions[code]
    elif (code and code not in {"CN", "CHN"}) or (nation and not domestic):
        province = "境外 / " + (nation or code)
    elif not province:
        province = "未知"
        log.warning(
            "geo.location_unknown reason=no_province_or_country; retry_policy=normal_refresh"
        )
    # Match existing MySQL String(100) columns without a schema migration.
    return {"province": province[:100], "city": city[:100]}


class RateLimited(RuntimeError):
    def __init__(self, message, retry_after):
        super().__init__(message)
        self.retry_after = retry_after


class Geocoder:
    def __init__(self, sessions, cache=None, http=None):
        self.sessions = sessions
        self.cache = cache or redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            socket_timeout=2,
            socket_connect_timeout=2,
        )
        self.http = http or requests.Session()
        self.qps = float(os.getenv("GEO_QPS", "1"))
        self.cooldown_seconds = float(os.getenv("GEO_COOLDOWN_SECONDS", "60"))
        self.max_cooldown_seconds = float(os.getenv("GEO_MAX_COOLDOWN_SECONDS", "900"))
        if not (0 < self.qps <= 100) or not (
            0 < self.cooldown_seconds <= self.max_cooldown_seconds < float("inf")
        ):
            raise ValueError(
                "GEO_QPS 必须在 (0,100]，冷却时间必须为有限正数且最大值不小于初始值"
            )
        self.next_request_at = 0.0
        self.cooldown_until = 0.0
        self.throttle_count = 0

    def throttle(self, message, retry_after=0):
        self.throttle_count += 1
        base = min(
            self.max_cooldown_seconds,
            self.cooldown_seconds * 2 ** min(self.throttle_count - 1, 10),
        )
        delay = max(
            retry_after,
            min(self.max_cooldown_seconds, base + random.uniform(0, base * 0.2)),
        )
        self.cooldown_until = time.monotonic() + delay
        log.warning(
            "geo.rate_limited cooldown_seconds=%.1f streak=%s qps=%s reason=%s",
            delay,
            self.throttle_count,
            self.qps,
            message,
        )
        return RateLimited(message, delay)

    def wait_for_request(self):
        now = time.monotonic()
        if now < self.cooldown_until:
            raise RateLimited("定位服务正在冷却", self.cooldown_until - now)
        delay = self.next_request_at - now
        if delay > 0:
            log.debug("geo.rate_wait seconds=%.3f qps=%s", delay, self.qps)
            time.sleep(delay)
        self.next_request_at = time.monotonic() + 1 / self.qps

    def locate(self, ip):
        address = ipaddress.ip_address(ip)
        if not address.is_global:
            return {"province": "内网/保留地址", "city": ""}
        key = "insight:geo:" + ip
        try:
            cached = self.cache.get(key)
            if cached:
                result = json.loads(cached)
                if (
                    not isinstance(result, dict)
                    or not isinstance(result.get("province"), str)
                    or not result["province"]
                    or not isinstance(result.get("city"), str)
                ):
                    raise ValueError("缓存结构无效")
                log.debug("geo.cache_hit ip=%s", ip)
                return result
        except (redis.RedisError, ValueError, TypeError) as exc:
            log.warning("geo.cache_read_failed ip=%s error=%s; fallback=baidu", ip, exc)
        ak = os.getenv("BAIDU_MAP_AK")
        if not ak:
            raise RuntimeError("请设置 BAIDU_MAP_AK")
        self.wait_for_request()
        started = time.monotonic()
        try:
            response = self.http.get(
                "https://api.map.baidu.com/location/ip",
                params={"ip": ip, "ak": ak, "coor": "bd09ll"},
                timeout=5,
            )
            # Log before HTTP/JSON/business validation so failures retain evidence.
            # Encode as one line and redact the AK even outside SafeFormatter.
            body = json.dumps(
                str(response.text).replace(ak, "[REDACTED]"), ensure_ascii=False
            )
            log.info(
                "geo.baidu_response ip=%s http_status=%s elapsed_ms=%.1f body=%s",
                ip,
                response.status_code,
                (time.monotonic() - started) * 1000,
                body,
            )
            if response.status_code == 429:
                try:
                    retry_after = max(
                        0, float(response.headers.get("Retry-After", "0"))
                    )
                    if not retry_after < float("inf"):
                        retry_after = 0
                except (TypeError, ValueError):
                    try:
                        retry_date = parsedate_to_datetime(
                            response.headers.get("Retry-After", "")
                        )
                        retry_after = max(
                            0, (retry_date - datetime.now(UTC)).total_seconds()
                        )
                    except (TypeError, ValueError, OverflowError):
                        retry_after = 0
                raise self.throttle("百度 HTTP 429 Too Many Requests", retry_after)
            response.raise_for_status()
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            # requests exception text can contain the full URL including the AK.
            detail = str(exc).replace(ak, "[REDACTED]")[:1000]
            raise RuntimeError(
                f"百度网络请求失败 type={type(exc).__name__} http_status={status} detail={detail}"
            ) from None
        try:
            data = response.json()
        except ValueError:
            raise RuntimeError("百度响应不是 JSON，检查代理或网关响应") from None
        if not isinstance(data, dict):
            raise TypeError("百度响应结构错误：应为 JSON object")
        if str(data.get("status")) != "0":
            message = str(data.get("message") or data.get("msg") or "无错误说明")[:500]
            reason = f"百度定位失败 status={data.get('status')} message={message}"
            if str(data.get("status")) in {"401", "402"}:
                raise self.throttle(reason)
            raise RuntimeError(reason)
        result = normalize_location(data)
        self.throttle_count = 0
        log.debug(
            "geo.baidu_parsed ip=%s elapsed_ms=%.1f",
            ip,
            (time.monotonic() - started) * 1000,
        )
        try:
            self.cache.setex(key, 30 * 86400, json.dumps(result, ensure_ascii=False))
        except redis.RedisError as exc:
            log.warning(
                "geo.cache_write_failed ip=%s error=%s; lookup_result_retained=true",
                ip,
                exc,
            )
        return result

    def run_batch(self, limit=50):
        started = time.monotonic()
        succeeded = failed = processed = 0
        if time.monotonic() < self.cooldown_until:
            return 0
        now = datetime.now(UTC).replace(tzinfo=None)
        with self.sessions() as session:
            ips = list(
                session.scalars(
                    select(Location.ip)
                    .where(Location.retry_at <= now)
                    .order_by(Location.retry_at)
                    .limit(limit)
                )
            )
        for ip in ips:
            throttled = False
            with self.sessions.begin() as session:
                row = session.get(Location, ip)
                if row is None:
                    continue
                try:
                    result = self.locate(ip)
                    row.province, row.city = result["province"], result["city"]
                    row.state = "resolved"
                    row.retry_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(
                        days=30
                    )
                    row.attempts = 0
                    succeeded += 1
                    log.info(
                        "geo.resolved ip=%s province=%s city=%s",
                        ip,
                        row.province,
                        row.city,
                    )
                except RateLimited as exc:
                    # This is a service-wide quota problem, not a bad IP.
                    # Commit this retry then stop; untouched IPs keep their attempts.
                    row.state = "retry"
                    row.retry_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(
                        seconds=exc.retry_after
                    )
                    throttled = True
                    log.warning(
                        "geo.deferred ip=%s attempt=%s retry_at=%sZ reason=rate_limit",
                        ip,
                        row.attempts,
                        row.retry_at.isoformat(),
                    )
                except (
                    requests.RequestException,
                    ValueError,
                    TypeError,
                    KeyError,
                    RuntimeError,
                ) as exc:
                    row.attempts += 1
                    row.state = "retry"
                    row.retry_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(
                        seconds=min(3600, 30 * 2 ** min(row.attempts, 7))
                    )
                    failed += 1
                    log.warning(
                        "geo.retry ip=%s attempt=%s retry_at=%sZ error=%s",
                        ip,
                        row.attempts,
                        row.retry_at.isoformat(),
                        exc,
                    )
                row.updated_at = datetime.now(UTC).replace(tzinfo=None)
            processed += 1
            if throttled:
                break
        if ips:
            log.info(
                "geo.batch committed=true selected=%d processed=%d resolved=%d failed=%d deferred=%d elapsed_ms=%.1f",
                len(ips),
                processed,
                succeeded,
                failed,
                processed - succeeded - failed,
                (time.monotonic() - started) * 1000,
            )
        return processed

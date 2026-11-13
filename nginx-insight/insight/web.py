import ipaddress
import logging
import os
import re
import time
from datetime import UTC, datetime, timedelta

from flask import Flask, g, jsonify, render_template, request
from sqlalchemy import case, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException, NotFound
from werkzeug.middleware.dispatcher import DispatcherMiddleware

from .auth import install_auth, require_admin
from .db import Cursor, Event, Location, database
from .logging_config import configure_logging

log = logging.getLogger(__name__)


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


def local_now():
    return datetime.now()


def timestamp(value):
    return value.isoformat() if value else None


def utc_timestamp(value):
    return value.isoformat() + "Z" if value else None


def selection():
    hours = int(request.args.get("hours", "24"))
    page = int(request.args.get("page", "1"))
    if not 1 <= hours <= 720 or not 1 <= page <= 20000:
        raise ValueError("hours 必须是 1–720，page 必须是 1–20000")
    since = local_now() - timedelta(hours=hours)
    filters = [Event.timestamp >= since, Event.timestamp <= local_now()]
    site = request.args.get("site", "")
    if len(site) > 255:
        raise ValueError("站点名称过长")
    if site:
        filters.append(Event.site == site)
    return hours, since, filters, page


def create_app(sessions=None):
    configure_logging()
    app = Flask(__name__)
    prefix = os.getenv("URL_PREFIX", "").strip().rstrip("/")
    if not re.fullmatch(r"(?:/[A-Za-z0-9_-]+)*", prefix):
        raise ValueError(
            "URL_PREFIX 必须为空或以 / 开头的路径，路径段仅支持字母、数字、_ 和 -"
        )
    app.config.update(
        APPLICATION_ROOT=prefix or "/",
        SESSION_COOKIE_PATH=prefix or "/",
    )
    if sessions is None:
        _, sessions = database()

    @app.before_request
    def start_request():
        g.started = time.monotonic()

    install_auth(app)
    log.info("web.start mode=public_with_admin")

    @app.after_request
    def access_log(response):
        level = logging.WARNING if response.status_code >= 400 else logging.DEBUG
        log.log(
            level,
            "web.request method=%s path=%s status=%s elapsed_ms=%.1f",
            request.method,
            request.path,
            response.status_code,
            (time.monotonic() - g.started) * 1000,
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            response.vary.add("Cookie")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.errorhandler(SQLAlchemyError)
    def database_error(exc):
        log.exception("web.database_failed path=%s", request.path)
        return jsonify(error="数据库操作失败，请查看服务日志"), 503

    @app.errorhandler(Exception)
    def unexpected_error(exc):
        if isinstance(exc, HTTPException):
            return exc
        log.exception("web.unexpected_failed path=%s", request.path)
        return jsonify(error="服务内部错误，请查看服务日志"), 500

    @app.get("/")
    def index():
        return render_template("index.html", currentYear=local_now().year)

    @app.get("/api/stats")
    def stats():
        try:
            hours, since, filters, _ = selection()
        except ValueError as exc:
            return jsonify(error=str(exc) or "筛选参数无效"), 400
        access = filters + [Event.kind == "access", Event.parse_error.is_(None)]
        with sessions() as db:
            total, uv, failures, avg, traffic, server_errors = db.execute(
                select(
                    func.count(Event.id),
                    func.count(func.distinct(Event.ip)),
                    func.sum(case((Event.status >= 400, 1), else_=0)),
                    func.avg(Event.request_time),
                    func.sum(Event.body_bytes),
                    func.sum(case((Event.status >= 500, 1), else_=0)),
                ).where(*access)
            ).one()
            provinces = db.execute(
                select(func.coalesce(Location.province, "未知"), func.count(Event.id))
                .outerjoin(Location, Event.ip == Location.ip)
                .where(*access)
                .group_by(func.coalesce(Location.province, "未知"))
            ).all()
            dialect = db.get_bind().dialect.name
            bucket = (
                func.strftime("%Y-%m-%dT%H:00:00", Event.timestamp)
                if dialect == "sqlite"
                else func.date_format(Event.timestamp, "%Y-%m-%dT%H:00:00")
            )
            counts = dict(
                db.execute(
                    select(bucket, func.count(Event.id))
                    .where(*access)
                    .group_by(bucket)
                    .order_by(bucket)
                ).all()
            )
            start = since.replace(minute=0, second=0, microsecond=0)
            trend = []
            for index in range(hours + 1):
                key = (start + timedelta(hours=index)).strftime("%Y-%m-%dT%H:00:00")
                trend.append({"time": key, "value": counts.get(key, 0)})
            statuses = db.execute(
                select(Event.status, func.count()).where(*access).group_by(Event.status)
            ).all()
            site_stats = db.execute(
                select(
                    Event.site,
                    func.count(Event.id),
                    func.avg(Event.request_time),
                    func.sum(case((Event.status >= 500, 1), else_=0)),
                )
                .where(*access)
                .group_by(Event.site)
                .order_by(func.count(Event.id).desc())
                .limit(30)
            ).all()
            previous_filters = [
                Event.timestamp >= since - timedelta(hours=hours),
                Event.timestamp < since,
                Event.kind == "access",
                Event.parse_error.is_(None),
            ]
            if request.args.get("site"):
                previous_filters.append(Event.site == request.args["site"])
            previous = db.scalar(
                select(func.count()).select_from(Event).where(*previous_filters)
            )
            sites = list(db.scalars(select(Event.site).distinct().order_by(Event.site)))
            domestic = sum(
                v
                for name, v in provinces
                if name not in {"未知", "内网/保留地址"} and not name.startswith("境外")
            )
            overseas = sum(v for name, v in provinces if name.startswith("境外"))
            result = {
                "total": total,
                "unique_ips": uv,
                "failures": failures or 0,
                "average_ms": round((avg or 0) * 1000, 1),
                "traffic_bytes": traffic or 0,
                "success_rate": round((total - (server_errors or 0)) / total * 100, 2)
                if total
                else None,
                "previous_total": previous,
                "growth": round((total - previous) / previous * 100, 1)
                if previous
                else None,
                "provinces": [{"name": n, "value": v} for n, v in provinces],
                "trend": trend,
                "statuses": [{"status": s, "value": v} for s, v in statuses],
                "site_stats": [
                    {
                        "site": s,
                        "requests": n,
                        "average_ms": round((a or 0) * 1000, 1),
                        "server_errors": e or 0,
                    }
                    for s, n, a, e in site_stats
                ],
                "regions": {
                    "domestic": domestic,
                    "overseas": overseas,
                    "unknown": total - domestic - overseas,
                },
                "sites": sites,
                "updated_at": timestamp(local_now()),
            }
            # Public API intentionally contains no IPs, request URLs or raw logs.
            return jsonify(result)

    @app.get("/api/admin/overview")
    @require_admin
    def admin_overview():
        now = utc_now()
        with sessions() as db:
            states = dict(
                db.execute(
                    select(Location.state, func.count()).group_by(Location.state)
                ).all()
            )
            cursors = db.scalars(select(Cursor).order_by(Cursor.path).limit(100)).all()
            return jsonify(
                states=states,
                due=db.scalar(
                    select(func.count())
                    .select_from(Location)
                    .where(Location.retry_at <= now)
                ),
                last_location_update=utc_timestamp(
                    db.scalar(select(func.max(Location.updated_at)))
                ),
                next_location_due=utc_timestamp(
                    db.scalar(select(func.min(Location.retry_at)))
                ),
                latest_event=timestamp(db.scalar(select(func.max(Event.timestamp)))),
                malformed=db.scalar(
                    select(func.count())
                    .select_from(Event)
                    .where(Event.parse_error.is_not(None))
                ),
                cursors=[
                    {"path": c.path, "offset": c.offset, "generation": c.generation}
                    for c in cursors
                ],
                settings={
                    "geo_qps": os.getenv("GEO_QPS", "1"),
                    "whitelist": os.getenv("NGINX_IP_WHITELIST", ""),
                    "log_time": "日志原始时间",
                },
            )

    @app.get("/api/admin/locations")
    @require_admin
    def locations():
        try:
            _, _, _, page = selection()
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        state, query = request.args.get("state", ""), request.args.get("q", "").strip()
        if state not in {"", "pending", "retry", "resolved"} or len(query) > 100:
            return jsonify(error="定位筛选参数无效"), 400
        filters = []
        if state:
            filters.append(Location.state == state)
        if query:
            filters.append(Location.ip.contains(query, autoescape=True))
        with sessions() as db:
            total = db.scalar(
                select(func.count()).select_from(Location).where(*filters)
            )
            rows = db.scalars(
                select(Location)
                .where(*filters)
                .order_by(Location.updated_at.desc(), Location.ip)
                .offset((page - 1) * 20)
                .limit(20)
            ).all()
            return jsonify(
                total=total,
                page=page,
                page_size=20,
                items=[
                    {
                        "ip": r.ip,
                        "province": r.province,
                        "city": r.city,
                        "state": r.state,
                        "attempts": r.attempts,
                        "retry_at": utc_timestamp(r.retry_at),
                        "updated_at": utc_timestamp(r.updated_at),
                    }
                    for r in rows
                ],
            )

    @app.post("/api/admin/locations/retry")
    @require_admin
    def retry_locations():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error="重试参数无效"), 400
        ip = data.get("ip")
        filters = [Location.state == "retry"]
        if ip is not None:
            try:
                ipaddress.ip_address(ip)
            except (TypeError, ValueError):
                return jsonify(error="IP 地址无效"), 400
            filters.append(Location.ip == ip)
        elif data.get("scope") != "failed":
            return jsonify(error="请指定重试的 IP 或失败队列"), 400
        with sessions.begin() as db:
            result = db.execute(
                update(Location)
                .where(*filters)
                .values(state="pending", attempts=0, retry_at=utc_now())
            )
            affected = result.rowcount
        log.info("admin.geo_retry scope=%s affected=%s", ip or "failed", affected)
        return jsonify(affected=affected)

    @app.get("/api/admin/logs")
    @require_admin
    def logs():
        try:
            _, _, filters, page = selection()
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        kind, query = (
            request.args.get("kind", "error"),
            request.args.get("q", "").strip(),
        )
        if (
            kind not in {"error", "access", "non200", "slow", "malformed"}
            or len(query) > 200
        ):
            return jsonify(error="日志筛选参数无效"), 400
        if kind in {"access", "error"}:
            filters.append(Event.kind == kind)
        elif kind == "non200":
            filters.extend([Event.kind == "access", Event.status != 200])
        elif kind == "slow":
            filters.extend([Event.kind == "access", Event.request_time >= 1])
        else:
            filters.append(Event.parse_error.is_not(None))
        if query:
            filters.append(Event.raw.contains(query, autoescape=True))
        with sessions() as db:
            total = db.scalar(select(func.count()).select_from(Event).where(*filters))
            rows = db.scalars(
                select(Event)
                .where(*filters)
                .order_by(Event.timestamp.desc(), Event.id.desc())
                .offset((page - 1) * 20)
                .limit(20)
            ).all()
            return jsonify(
                total=total,
                page=page,
                page_size=20,
                items=[
                    {
                        "id": r.id,
                        "time": timestamp(r.timestamp),
                        "site": r.site,
                        "kind": r.kind,
                        "ip": r.ip,
                        "status": r.status,
                        "level": r.level if r.level is not None else "access",
                        "request_time": r.request_time,
                        "raw": r.raw,
                        "parse_error": r.parse_error,
                    }
                    for r in rows
                ],
            )

    @app.get("/api/admin/traffic")
    @require_admin
    def traffic():
        try:
            _, _, filters, _ = selection()
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        access = filters + [Event.kind == "access", Event.parse_error.is_(None)]
        with sessions() as db:
            paths = db.execute(
                select(Event.request, func.count(), func.avg(Event.request_time))
                .where(*access)
                .group_by(Event.request)
                .order_by(func.count().desc())
                .limit(10)
            ).all()
            ips = db.execute(
                select(
                    Event.ip,
                    func.count(),
                    func.sum(case((Event.status >= 400, 1), else_=0)),
                )
                .where(*access)
                .group_by(Event.ip)
                .order_by(func.count().desc())
                .limit(10)
            ).all()
            slow = db.scalar(
                select(func.count())
                .select_from(Event)
                .where(*access, Event.request_time >= 1)
            )
            return jsonify(
                paths=[
                    {"request": p, "count": n, "average_ms": round((a or 0) * 1000, 1)}
                    for p, n, a in paths
                ],
                ips=[{"ip": ip, "count": n, "errors": e or 0} for ip, n, e in ips],
                slow_count=slow,
            )

    if prefix:
        # Mount exclusively at the configured path; the proxy must preserve it.
        app.wsgi_app = DispatcherMiddleware(NotFound(), {prefix: app.wsgi_app})
    return app

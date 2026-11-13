"""Signed administrator sessions and CSRF checks for browser mutations."""

import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
from datetime import timedelta
from functools import wraps

from flask import jsonify, request, session

log = logging.getLogger(__name__)


def credentials():
    return os.getenv("DASHBOARD_USER", ""), os.getenv("DASHBOARD_PASSWORD", "")


def version():
    user, password = credentials()
    return hashlib.sha256((user + "\0" + password).encode()).hexdigest()


def is_admin():
    user, password = credentials()
    value = session.get("admin_version", "")
    return bool(
        user
        and password
        and os.getenv("SESSION_SECRET")
        and isinstance(value, str)
        and hmac.compare_digest(value, version())
    )


def require_admin(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not is_admin():
            return jsonify(error="请先登录管理员账号"), 401
        return function(*args, **kwargs)

    return wrapped


def install_auth(app):
    user, password = credentials()
    # Administrators require a stable, independently generated secret across workers.
    app.secret_key = os.getenv("SESSION_SECRET") or secrets.token_hex(32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "false").lower()
        == "true",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        SESSION_REFRESH_EACH_REQUEST=False,
        MAX_CONTENT_LENGTH=16384,
    )
    if user and password and not os.getenv("SESSION_SECRET"):
        log.warning(
            "auth.login_disabled reason=SESSION_SECRET_missing; public_statistics_available=true"
        )
    failures = {}
    lock = threading.Lock()

    @app.before_request
    def csrf_check():
        if request.path.startswith("/api/") and request.method in {
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
        }:
            if not request.is_json:
                return jsonify(error="请求必须使用 JSON"), 415
            expected = session.get("csrf_token", "")
            actual = request.headers.get("X-CSRF-Token", "")
            if not expected or not hmac.compare_digest(
                expected.encode(), actual.encode()
            ):
                return jsonify(error="会话校验失败，请刷新页面后重试"), 403

    @app.get("/api/auth")
    def auth_state():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        return jsonify(
            authenticated=is_admin(),
            configured=bool(all(credentials()) and os.getenv("SESSION_SECRET")),
            username=credentials()[0] if is_admin() else None,
            csrf_token=session["csrf_token"],
        )

    @app.post("/api/auth/login")
    def login():
        user, password = credentials()
        if not user or not password:
            return jsonify(
                error="管理员账号尚未配置，请设置 DASHBOARD_USER 和 DASHBOARD_PASSWORD"
            ), 503
        if not os.getenv("SESSION_SECRET"):
            return jsonify(
                error="管理员登录尚未启用，请先配置独立随机 SESSION_SECRET 并重启 Web 服务"
            ), 503
        key, now = request.remote_addr or "local", time.monotonic()
        with lock:
            for address, (_, until) in list(failures.items()):
                if until <= now:
                    del failures[address]
            count, until = failures.get(key, (0, now + 900))
            if count >= 5:
                return jsonify(error="登录失败次数过多，请 15 分钟后再试"), 429
            # Reserve an attempt before checking so concurrent requests also count.
            if len(failures) >= 4096 and key not in failures:
                return jsonify(error="登录请求繁忙，请稍后重试"), 429
            failures[key] = (count + 1, until)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error="登录参数无效"), 400
        supplied_user, supplied_password = data.get("username"), data.get("password")
        valid_user = isinstance(supplied_user, str) and hmac.compare_digest(
            supplied_user.encode(), user.encode()
        )
        valid_password = isinstance(supplied_password, str) and hmac.compare_digest(
            supplied_password.encode(), password.encode()
        )
        if not (valid_user and valid_password):
            log.warning("auth.login_failed remote=%s", key)
            return jsonify(error="账号或密码不正确"), 401
        with lock:
            failures.pop(key, None)
        session.clear()
        session.permanent = True
        session["admin_version"] = version()
        session["csrf_token"] = secrets.token_urlsafe(32)
        log.info("auth.login_success remote=%s", key)
        return jsonify(
            authenticated=True, username=user, csrf_token=session["csrf_token"]
        )

    @app.post("/api/auth/logout")
    def logout():
        session.clear()
        log.info("auth.logout")
        return jsonify(authenticated=False)

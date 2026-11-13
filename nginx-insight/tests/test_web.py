import os
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from insight.db import Base, Cursor, Event, Location, database
from insight.web import create_app


class WebTests(unittest.TestCase):
    def test_log_timestamps_have_no_timezone_conversion(self):
        client = create_app(self.sessions).test_client()
        data = client.get("/api/stats").get_json()
        self.assertEqual(data["updated_at"], self.now.isoformat())
        self.assertTrue(all(not row["time"].endswith("Z") for row in data["trend"]))
        self.assertEqual(data["total"], 3)

    def setUp(self):
        environment = patch.dict(
            os.environ,
            {
                "DASHBOARD_USER": "admin-test",
                "DASHBOARD_PASSWORD": "strong-test-password",
                "SESSION_SECRET": "test-session-secret-with-enough-entropy",
                "SESSION_COOKIE_SECURE": "false",
                "LOG_FILE": "",
                "LOG_LEVEL": "ERROR",
                "URL_PREFIX": "",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.engine, self.sessions = database(
            "sqlite:///" + str(Path(temp.name) / "db")
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.now = datetime.now(UTC).replace(tzinfo=None)
        clock = patch("insight.web.local_now", return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)
        with self.sessions.begin() as db:
            db.add_all(
                [
                    Location(
                        ip="1.2.3.4", province="广东省", city="深圳市", state="resolved"
                    ),
                    Location(
                        ip="5.6.7.8",
                        province="境外 / England",
                        state="retry",
                        attempts=4,
                    ),
                    Cursor(
                        identity="file",
                        path="/var/log/nginx/access.log",
                        offset=1234,
                        generation=0,
                        anchor="",
                    ),
                ]
            )
            for index, (site, kind, age, ip, status, duration, raw) in enumerate(
                [
                    (
                        "one.test",
                        "access",
                        1,
                        "1.2.3.4",
                        200,
                        0.1,
                        "PRIVATE_GET /private?token=secret",
                    ),
                    ("one.test", "access", 2, "5.6.7.8", 503, 2.0, "PRIVATE_GET /slow"),
                    ("two.test", "access", 3, "1.2.3.4", 302, 0.2, "PRIVATE_GET /two"),
                    (
                        "one.test",
                        "error",
                        1,
                        "5.6.7.8",
                        None,
                        None,
                        "PRIVATE_ERROR raw content",
                    ),
                    ("one.test", "access", 25, "1.2.3.4", 200, 0.3, "PRIVATE_PREVIOUS"),
                ]
            ):
                db.add(
                    Event(
                        source="file",
                        generation=0,
                        offset=index,
                        site=site,
                        kind=kind,
                        timestamp=self.now - timedelta(hours=age),
                        ip=ip,
                        status=status,
                        request_time=duration,
                        request=raw if kind == "access" else None,
                        raw=raw,
                        body_bytes=100,
                        level="error" if kind == "error" else None,
                    )
                )
        self.app = create_app(self.sessions)
        self.client = self.app.test_client()

    def token(self):
        return self.client.get("/api/auth").get_json()["csrf_token"]

    def test_subpath_assets_and_authenticated_api(self):
        prefix = "/nginx-insight"
        with patch.dict(os.environ, {"URL_PREFIX": prefix}):
            self.client = create_app(self.sessions).test_client()
        for path in ("/", "/api/auth", "/static/style.css", "/other/api/stats"):
            self.assertEqual(self.client.get(path).status_code, 404)
        redirect = self.client.get(prefix)
        self.assertEqual(redirect.status_code, 308)
        self.assertTrue(redirect.location.endswith(prefix + "/"))
        page = self.client.get(prefix + "/")
        self.assertEqual(page.status_code, 200)
        self.assertIn('src="static/app.js"', page.get_data(as_text=True))
        for asset in (
            "app.js",
            "style.css",
            "china.json",
            "echarts.min.js",
            "logo.svg",
        ):
            response = self.client.get(prefix + "/static/" + asset)
            self.assertEqual(response.status_code, 200, asset)
            response.close()
        self.assertEqual(self.client.get(prefix + "/api/stats").status_code, 200)
        self.assertEqual(
            self.client.get(prefix + "/api/admin/overview").status_code, 401
        )
        self.assertEqual(
            self.client.post(prefix + "/api/auth/login", json={}).status_code, 403
        )
        token = self.client.get(prefix + "/api/auth").get_json()["csrf_token"]
        self.assertEqual(self.client.get_cookie("session", path=prefix).path, prefix)
        login = self.client.post(
            prefix + "/api/auth/login",
            json={
                "username": "admin-test",
                "password": "strong-test-password",
            },
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(
            self.client.get(prefix + "/api/admin/overview").status_code, 200
        )
        self.assertEqual(
            self.client.post(
                prefix + "/api/auth/logout",
                json={},
                headers={
                    "X-CSRF-Token": login.get_json()["csrf_token"],
                },
            ).status_code,
            200,
        )

    def test_prefix_is_configurable(self):
        for prefix in ("/monitor", "/tools/monitor/"):
            with (
                self.subTest(prefix=prefix),
                patch.dict(os.environ, {"URL_PREFIX": prefix}),
            ):
                app = create_app(self.sessions)
                client = app.test_client()
                self.assertEqual(
                    client.get(prefix.rstrip("/") + "/api/stats").status_code, 200
                )
                self.assertEqual(client.get("/api/stats").status_code, 404)
                self.assertEqual(
                    client.get("/nginx-insight/api/stats").status_code, 404
                )

    def test_empty_prefix_only_serves_root(self):
        self.assertEqual(self.client.get("/api/stats").status_code, 200)
        self.assertEqual(self.client.get("/nginx-insight/api/stats").status_code, 404)

    def test_invalid_prefix_fails_at_startup(self):
        for prefix in ("monitor", "/a//b", "/a/../b", "//example.com", "/a?b"):
            with (
                self.subTest(prefix=prefix),
                patch.dict(os.environ, {"URL_PREFIX": prefix}),
            ):
                with self.assertRaisesRegex(ValueError, "URL_PREFIX"):
                    create_app(self.sessions)

    def login(self):
        token = self.token()
        response = self.client.post(
            "/api/auth/login",
            json={"username": "admin-test", "password": "strong-test-password"},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()["csrf_token"]

    def test_public_stats_never_exposes_private_data(self):
        response = self.client.get("/api/stats")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["previous_total"], 1)
        self.assertEqual(data["growth"], 200)
        self.assertEqual(data["regions"], {"domestic": 2, "overseas": 1, "unknown": 0})
        self.assertEqual(data["unique_ips"], 2)
        for private in ["PRIVATE_", "1.2.3.4", "5.6.7.8", "raw", "pending_ips"]:
            self.assertNotIn(private, response.get_data(as_text=True))
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.login()
        self.assertNotIn(
            "PRIVATE_", self.client.get("/api/stats").get_data(as_text=True)
        )

    def test_public_page_has_no_admin_bootstrap_data(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("管理员登录", response.get_data(as_text=True))
        self.assertNotIn("PRIVATE_", response.get_data(as_text=True))

    def test_every_admin_read_requires_login(self):
        for endpoint in ["overview", "locations", "logs", "traffic"]:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(
                    self.client.get("/api/admin/" + endpoint).status_code, 401
                )

    def test_login_and_logout_revoke_admin_access(self):
        token = self.login()
        self.assertTrue(self.client.get("/api/auth").get_json()["authenticated"])
        self.assertEqual(self.client.get("/api/admin/overview").status_code, 200)
        self.assertEqual(
            self.client.post(
                "/api/auth/logout", json={}, headers={"X-CSRF-Token": token}
            ).status_code,
            200,
        )
        self.assertEqual(self.client.get("/api/admin/overview").status_code, 401)

    def test_csrf_required_for_login_and_mutations(self):
        self.assertEqual(self.client.post("/api/auth/login", json={}).status_code, 403)
        self.login()
        self.assertEqual(
            self.client.post(
                "/api/admin/locations/retry", json={"scope": "failed"}
            ).status_code,
            403,
        )
        self.assertEqual(self.client.post("/api/auth/logout", json={}).status_code, 403)

    def test_bad_credentials_and_attempt_throttle(self):
        token = self.token()
        for _ in range(5):
            response = self.client.post(
                "/api/auth/login",
                json={"username": "admin-test", "password": "wrong"},
                headers={"X-CSRF-Token": token},
            )
            self.assertEqual(response.status_code, 401)
        self.assertEqual(
            self.client.post(
                "/api/auth/login",
                json={"username": "admin-test", "password": "strong-test-password"},
                headers={"X-CSRF-Token": token},
            ).status_code,
            429,
        )

    def test_password_change_invalidates_session(self):
        self.login()
        with patch.dict(os.environ, {"DASHBOARD_PASSWORD": "changed"}):
            self.assertEqual(self.client.get("/api/admin/logs").status_code, 401)

    def test_no_credentials_disables_admin_without_hiding_public_page(self):
        with patch.dict(os.environ, {"DASHBOARD_USER": "", "DASHBOARD_PASSWORD": ""}):
            self.assertEqual(self.client.get("/api/stats").status_code, 200)
            self.assertFalse(self.client.get("/api/auth").get_json()["configured"])
            self.assertEqual(
                self.client.post(
                    "/api/auth/login", json={}, headers={"X-CSRF-Token": self.token()}
                ).status_code,
                503,
            )

    def test_missing_secret_disables_admin_login(self):
        with patch.dict(os.environ, {"SESSION_SECRET": ""}):
            self.assertFalse(self.client.get("/api/auth").get_json()["configured"])
            self.assertEqual(
                self.client.post(
                    "/api/auth/login",
                    json={"username": "admin-test", "password": "strong-test-password"},
                    headers={"X-CSRF-Token": self.token()},
                ).status_code,
                503,
            )
            self.assertEqual(self.client.get("/api/stats").status_code, 200)

    def test_admin_logs_search_and_slow_filter(self):
        self.login()
        data = self.client.get("/api/admin/logs?kind=slow&site=one.test").get_json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["status"], 503)
        self.assertIn("PRIVATE_", data["items"][0]["raw"])
        self.assertEqual(
            self.client.get("/api/admin/logs?kind=access&q=%25").get_json()["total"], 0
        )
        self.assertEqual(
            self.client.get("/api/admin/logs?kind=error").get_json()["total"], 1
        )
        self.assertEqual(
            self.client.get("/api/admin/logs?site=two.test&kind=error").get_json()[
                "total"
            ],
            0,
        )

    def test_admin_logs_non200_filter(self):
        self.assertEqual(
            self.client.get("/api/admin/logs?kind=non200").status_code, 401
        )
        self.login()
        data = self.client.get("/api/admin/logs?kind=non200").get_json()
        self.assertEqual(data["total"], 2)
        self.assertEqual({item["status"] for item in data["items"]}, {302, 503})
        self.assertEqual(
            self.client.get("/api/admin/logs?kind=non200&site=one.test").get_json()[
                "total"
            ],
            1,
        )
        self.assertEqual(
            self.client.get("/api/admin/logs?kind=non200&hours=1").get_json()["total"],
            0,
        )
        self.assertEqual(
            self.client.get("/api/admin/logs?kind=non200&q=slow").get_json()["total"], 1
        )

    def test_admin_queue_and_retry_failed_only(self):
        token = self.login()
        self.assertEqual(
            self.client.get("/api/admin/locations?state=retry").get_json()["total"], 1
        )
        response = self.client.post(
            "/api/admin/locations/retry",
            json={"scope": "failed"},
            headers={"X-CSRF-Token": token},
        )
        self.assertEqual(response.get_json()["affected"], 1)
        with self.sessions() as db:
            row = db.get(Location, "5.6.7.8")
            self.assertEqual(row.state, "pending")
            self.assertEqual(row.attempts, 0)
            self.assertEqual(db.get(Location, "1.2.3.4").state, "resolved")
        self.assertEqual(
            self.client.post(
                "/api/admin/locations/retry",
                json={"scope": "failed"},
                headers={"X-CSRF-Token": token},
            ).get_json()["affected"],
            0,
        )

    def test_retry_requires_explicit_scope_or_valid_ip(self):
        token = self.login()
        for data in [{}, {"ip": "invalid"}, {"ip": []}, {"scope": "all"}]:
            with self.subTest(data=data):
                self.assertEqual(
                    self.client.post(
                        "/api/admin/locations/retry",
                        json=data,
                        headers={"X-CSRF-Token": token},
                    ).status_code,
                    400,
                )

    def test_overview_and_traffic(self):
        self.login()
        data = self.client.get("/api/admin/overview").get_json()
        self.assertEqual(data["states"]["retry"], 1)
        self.assertEqual(data["cursors"][0]["offset"], 1234)
        traffic = self.client.get("/api/admin/traffic?site=one.test").get_json()
        self.assertEqual(traffic["slow_count"], 1)
        self.assertEqual(sum(p["count"] for p in traffic["paths"]), 2)

    def test_invalid_filters_return_400(self):
        self.login()
        for path in [
            "/api/stats?hours=0",
            "/api/stats?hours=x",
            "/api/admin/logs?page=0",
            "/api/admin/logs?kind=bad",
            "/api/admin/locations?state=bad",
        ]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 400)

    def test_basic_authorization_does_not_grant_admin_access(self):
        import base64

        value = base64.b64encode(b"admin-test:strong-test-password").decode()
        self.assertEqual(
            self.client.get(
                "/api/admin/overview", headers={"Authorization": "Basic " + value}
            ).status_code,
            401,
        )


if __name__ == "__main__":
    unittest.main()

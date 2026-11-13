import gzip
import os
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

try:
    from insight.collector import Collector
    from insight.db import Base, Event, Location, database
    from insight.geo import Geocoder
    from insight.web import create_app
    from sqlalchemy import func, select

    DEPS = True
except ModuleNotFoundError:
    DEPS = False

LINE = b'1.2.3.4 - - [11/Nov/2026:14:35:01 +0800] "GET / HTTP/1.1" 200 123 "-" "test" rt=0.125\n'


@unittest.skipUnless(DEPS, "需要先安装项目依赖")
class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "DASHBOARD_USER": "",
                "DASHBOARD_PASSWORD": "",
                "LOG_FILE": "",
                "NGINX_IP_WHITELIST": "",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine, self.sessions = database("sqlite:///" + str(self.root / "test.db"))
        Base.metadata.create_all(self.engine)
        self.collector = Collector(self.sessions)
        self.path = self.root / "api.example.cn.access.log"

    def tearDown(self):
        self.collector.close()
        self.engine.dispose()
        self.temp.cleanup()

    def count(self):
        with self.sessions() as session:
            return session.scalar(select(func.count()).select_from(Event))

    def configure_whitelist(self, value):
        self.collector.close()
        os.environ["NGINX_IP_WHITELIST"] = value
        self.collector = Collector(self.sessions)

    def test_whitelist_skips_events_and_locations(self):
        self.configure_whitelist("1.2.3.4, 2001:db8::/32")
        self.path.write_bytes(
            LINE
            + LINE.replace(b"1.2.3.4", b"1.2.3.5")
            + LINE.replace(b"1.2.3.4", b"2001:db8::1")
        )
        self.assertEqual(self.collector.scan(self.root), 3)
        self.assertEqual(self.count(), 1)
        with self.sessions() as session:
            self.assertIsNone(session.get(Location, "1.2.3.4"))
            self.assertIsNone(session.get(Location, "2001:db8::1"))
            self.assertIsNotNone(session.get(Location, "1.2.3.5"))
        self.collector.close()
        self.assertEqual(self.collector.scan(self.root), 0)

    def test_whitelist_cidr_error_and_malformed_access(self):
        self.configure_whitelist("1.2.3.0/24")
        self.path.write_bytes(b"1.2.3.8 invalid access line\n")
        (self.root / "error.log").write_bytes(
            b"2026/11/11 14:35:01 [error] 123#123: failed, client: 1.2.3.9, server: test\n"
        )
        self.assertEqual(self.collector.scan(self.root), 2)
        self.assertEqual(self.count(), 0)
        with self.sessions() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(Location)), 0
            )

    def test_whitelist_client_endpoints_and_mapped_ipv4(self):
        self.configure_whitelist("1.2.3.4,2001:db8::/32")
        self.path.write_bytes(LINE.replace(b"1.2.3.4", b"::ffff:1.2.3.4"))
        (self.root / "error.log").write_text(
            "2026/11/11 14:35:01 [error] 123#123: *1 failed, client: 1.2.3.4:4321, server: test\n"
            "2026/11/11 14:35:01 [error] 123#123: *2 failed, client: [2001:db8::1]:4321, server: test\n"
            "bad error line, client:    1.2.3.4, server: test\n"
        )
        self.assertEqual(self.collector.scan(self.root), 4)
        self.assertEqual(self.count(), 0)
        with self.sessions() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(Location)), 0
            )
        self.collector.close()
        self.assertEqual(self.collector.scan(self.root), 0)

    def test_whitelist_file_changes_apply_without_collector_restart(self):
        env_file = self.root / ".env"
        env_file.write_text("NGINX_IP_WHITELIST=\n")
        self.collector.close()
        with patch("insight.collector.find_dotenv", return_value=str(env_file)):
            self.collector = Collector(self.sessions)
        env_file.write_text("NGINX_IP_WHITELIST=1.2.3.4\n")
        self.path.write_bytes(LINE)
        self.assertEqual(self.collector.scan(self.root), 1)
        self.assertEqual(self.count(), 0)
        env_file.write_text("NGINX_IP_WHITELIST=\n")
        with self.path.open("ab") as handle:
            handle.write(LINE)
        self.assertEqual(self.collector.scan(self.root), 1)
        self.assertEqual(self.count(), 1)

    def test_whitelist_environment_override_is_preserved(self):
        env_file = self.root / ".env"
        env_file.write_text("NGINX_IP_WHITELIST=1.2.3.5\n")
        self.collector.close()
        with patch("insight.collector.find_dotenv", return_value=str(env_file)):
            os.environ["NGINX_IP_WHITELIST"] = "1.2.3.4"
            self.collector = Collector(self.sessions)
        self.path.write_bytes(LINE)
        self.assertEqual(self.collector.scan(self.root), 1)
        self.assertEqual(self.count(), 0)

    def test_all_exempt_batch_does_not_end_import_early(self):
        self.configure_whitelist("1.2.3.4")
        with gzip.open(self.root / "access.log.1.gz", "wb") as handle:
            handle.write(LINE * 1001 + LINE.replace(b"1.2.3.4", b"1.2.3.5"))
        batches = []
        while True:
            count = self.collector.scan(self.root, archives=True)
            batches.append(count)
            if not count:
                break
        self.assertEqual(batches, [1000, 2, 0])
        self.assertEqual(self.count(), 1)

    def test_whitelist_does_not_remove_existing_data(self):
        self.path.write_bytes(LINE)
        self.collector.scan(self.root)
        self.configure_whitelist("1.2.3.4")
        with self.path.open("ab") as handle:
            handle.write(LINE)
        self.assertEqual(self.collector.scan(self.root), 1)
        self.assertEqual(self.count(), 1)

    def test_invalid_whitelist_fails_startup(self):
        with (
            patch.dict(os.environ, {"NGINX_IP_WHITELIST": "1.2.3.4,invalid"}),
            self.assertRaisesRegex(ValueError, "NGINX_IP_WHITELIST"),
        ):
            Collector(self.sessions)

    def test_restart_and_partial_line(self):
        self.path.write_bytes(LINE + LINE[:20])
        self.assertEqual(self.collector.scan(self.root), 1)
        self.collector.close()
        self.collector = Collector(self.sessions)
        self.assertEqual(self.collector.scan(self.root), 0)
        with self.path.open("ab") as f:
            f.write(LINE[20:])
        self.assertEqual(self.collector.scan(self.root), 1)
        self.assertEqual(self.count(), 2)

    def test_rename_rotation_late_write(self):
        self.path.write_bytes(LINE)
        self.collector.scan(self.root)
        old = self.path.rename(str(self.path) + ".1")
        self.path.write_bytes(LINE)
        with old.open("ab") as f:
            f.write(LINE)
        self.assertEqual(self.collector.scan(self.root), 2)
        self.assertEqual(self.count(), 3)

    def test_restart_after_rename(self):
        self.path.write_bytes(LINE)
        self.collector.scan(self.root)
        self.collector.close()
        old = self.path.rename(str(self.path) + ".1")
        old.write_bytes(LINE + LINE)
        self.path.write_bytes(LINE)
        self.assertEqual(self.collector.scan(self.root), 2)

    def test_copytruncate_regrown(self):
        self.path.write_bytes(LINE)
        self.collector.scan(self.root)
        self.path.write_bytes(LINE.replace(b"GET /", b"GET /new") * 2)
        self.assertEqual(self.collector.scan(self.root), 2)
        self.assertEqual(self.count(), 3)

    def test_gzip_and_repeat_import(self):
        with gzip.open(self.root / "access.log.2.gz", "wb") as f:
            f.write(LINE * 2)
        self.assertEqual(self.collector.scan(self.root, archives=True), 2)
        self.assertEqual(self.collector.scan(self.root, archives=True), 0)

    def test_compression_reuses_checkpoint(self):
        self.path.write_bytes(LINE)
        self.collector.scan(self.root)
        self.collector.close()
        with gzip.open(str(self.path) + ".1.gz", "wb") as f:
            f.write(LINE + LINE)
        self.path.unlink()
        self.assertEqual(self.collector.scan(self.root, archives=True), 1)
        self.assertEqual(self.count(), 2)

    def test_malformed_is_preserved(self):
        self.path.write_bytes(b"bad line\n")
        self.collector.scan(self.root)
        with self.sessions() as session:
            event = session.scalar(select(Event))
            self.assertEqual(event.raw, "bad line")
            self.assertTrue(event.parse_error)

    def test_binary_request_does_not_block_import_or_resume(self):
        binary = LINE.replace(b"GET / HTTP/1.1", rb"\x16\x03\x01" * 20).replace(
            b"200 123", b"400 123"
        )
        self.path.write_bytes(binary + LINE)
        self.assertEqual(self.collector.scan(self.root), 2)
        self.collector.close()
        self.assertEqual(self.collector.scan(self.root), 0)
        with self.sessions() as session:
            events = session.scalars(select(Event).order_by(Event.id)).all()
            self.assertEqual(len(events), 2)
            self.assertIsNone(events[0].method)
            self.assertEqual(events[0].status, 400)
            self.assertEqual(events[0].raw, binary.decode().rstrip("\n"))
            self.assertIsNone(events[0].parse_error)
            self.assertEqual(events[1].method, "GET")

    def test_dashboard_aggregation_and_filters(self):
        self.path.write_bytes(LINE)
        self.collector.scan(self.root)
        with self.sessions.begin() as session:
            session.scalar(select(Event)).timestamp = datetime.now()
            location = session.get(Location, "1.2.3.4")
            location.province = "广东省"
            location.state = "resolved"
        client = create_app(self.sessions).test_client()
        data = client.get("/api/stats").get_json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["average_ms"], 125)
        self.assertEqual(data["provinces"], [{"name": "广东省", "value": 1}])
        self.assertEqual(client.get("/api/stats?site=other").get_json()["total"], 0)
        self.assertEqual(client.get("/api/stats?hours=0").status_code, 400)
        self.assertEqual(client.get("/").status_code, 200)

    def test_baidu_cache_and_nonpublic_ip(self):
        cache, http = Mock(), Mock()
        cache.get.return_value = None
        http.get.return_value.json.return_value = {
            "status": 0,
            "content": {"address_detail": {"province": "广东省", "city": "深圳市"}},
        }
        worker = Geocoder(self.sessions, cache=cache, http=http)
        with patch.dict(os.environ, {"BAIDU_MAP_AK": "test"}):
            self.assertEqual(worker.locate("1.2.3.4")["province"], "广东省")
        http.get.assert_called_once()
        cache.setex.assert_called_once()
        self.assertEqual(worker.locate("127.0.0.1")["province"], "内网/保留地址")

    def test_geo_failure_retry(self):
        with self.sessions.begin() as session:
            session.add(Location(ip="1.2.3.4"))
        worker = Geocoder(self.sessions, cache=Mock(), http=Mock())
        worker.locate = Mock(side_effect=RuntimeError("quota"))
        self.assertEqual(worker.run_batch(), 1)
        with self.sessions() as session:
            row = session.get(Location, "1.2.3.4")
            self.assertEqual(row.state, "retry")
            self.assertGreater(row.retry_at, datetime.now(UTC).replace(tzinfo=None))


if __name__ == "__main__":
    unittest.main()

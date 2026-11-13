import os
import unittest
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from insight.db import Base, Location, database
from insight.geo import Geocoder, RateLimited
from sqlalchemy import select


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "BAIDU_MAP_AK": "test",
                "GEO_QPS": "1",
                "GEO_COOLDOWN_SECONDS": "60",
                "GEO_MAX_COOLDOWN_SECONDS": "120",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.now = 1000.0
        clock = patch("insight.geo.time.monotonic", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        sleep = patch("insight.geo.time.sleep", side_effect=self.sleep)
        self.sleep_mock = sleep.start()
        self.addCleanup(sleep.stop)
        jitter = patch("insight.geo.random.uniform", return_value=0)
        jitter.start()
        self.addCleanup(jitter.stop)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine, self.sessions = database(
            "sqlite:///" + str(Path(self.temp.name) / "db")
        )
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.cache, self.http = Mock(), Mock()
        self.cache.get.return_value = None
        self.http.get.return_value.status_code = 200
        self.success()
        self.worker = Geocoder(self.sessions, cache=self.cache, http=self.http)

    def sleep(self, delay):
        self.now += delay

    def success(self):
        self.http.get.return_value.json.return_value = {
            "status": 0,
            "content": {"address_detail": {"province": "广东省", "city": "深圳市"}},
        }

    def test_requests_are_spaced(self):
        self.worker.locate("1.2.3.4")
        self.worker.locate("1.2.3.5")
        self.sleep_mock.assert_called_once_with(1.0)
        self.assertEqual(self.http.get.call_count, 2)

    def test_cache_and_internal_ip_do_not_consume_rate(self):
        self.cache.get.return_value = b'{"province":"test", "city":""}'
        self.worker.locate("1.2.3.4")
        self.worker.locate("127.0.0.1")
        self.http.get.assert_not_called()
        self.sleep_mock.assert_not_called()
        self.assertEqual(self.worker.next_request_at, 0)

    def test_business_limit_stops_batch_and_preserves_attempts(self):
        with self.sessions.begin() as session:
            session.add_all([Location(ip="1.2.3.4"), Location(ip="1.2.3.5")])
        self.http.get.return_value.json.return_value = {
            "status": 401,
            "message": "quota",
        }
        self.assertEqual(self.worker.run_batch(), 1)
        self.assertEqual(self.worker.run_batch(), 0)
        self.http.get.assert_called_once()
        with self.sessions() as session:
            rows = session.scalars(select(Location).order_by(Location.ip)).all()
            self.assertEqual(rows[0].state, "retry")
            self.assertEqual(rows[0].attempts, 0)
            self.assertGreater(rows[0].retry_at, datetime.now(UTC).replace(tzinfo=None))
            self.assertEqual(rows[1].state, "pending")
            self.assertEqual(rows[1].attempts, 0)

    def test_cooldown_grows_and_success_resets_streak(self):
        self.http.get.return_value.json.return_value = {
            "status": "402",
            "message": "quota",
        }
        for delay in [60, 120, 120]:
            with self.assertRaises(RateLimited) as error:
                self.worker.locate("1.2.3.4")
            self.assertEqual(error.exception.retry_after, delay)
            self.now += delay
        self.success()
        self.worker.locate("1.2.3.4")
        self.assertEqual(self.worker.throttle_count, 0)

    def test_http429_respects_retry_after(self):
        self.http.get.return_value.status_code = 429
        self.http.get.return_value.headers = {"Retry-After": "180"}
        with self.assertRaises(RateLimited) as error:
            self.worker.locate("1.2.3.4")
        self.assertEqual(error.exception.retry_after, 180)
        with self.assertRaises(RateLimited):
            self.worker.locate("1.2.3.5")
        self.http.get.assert_called_once()

    def test_http429_retry_after_date(self):
        self.http.get.return_value.status_code = 429
        retry_date = datetime.now(UTC) + timedelta(seconds=180)
        self.http.get.return_value.headers = {
            "Retry-After": format_datetime(retry_date, usegmt=True)
        }
        with self.assertRaises(RateLimited) as error:
            self.worker.locate("1.2.3.4")
        self.assertGreater(error.exception.retry_after, 175)
        self.assertLessEqual(error.exception.retry_after, 180)

    def test_invalid_configuration(self):
        for qps in ["0", "-1", "nan", "inf"]:
            with (
                patch.dict(os.environ, {"GEO_QPS": qps}),
                self.assertRaises(ValueError),
            ):
                Geocoder(self.sessions, cache=self.cache, http=self.http)


if __name__ == "__main__":
    unittest.main()

import logging
import os
import unittest
from unittest.mock import Mock, patch

import requests
from insight.geo import Geocoder
from insight.logging_config import SafeFormatter


class LoggingTests(unittest.TestCase):
    def worker(self):
        cache = Mock()
        cache.get.return_value = None
        return Geocoder(Mock(), cache=cache, http=Mock())

    def test_api_error_includes_status_and_message(self):
        worker = self.worker()
        worker.http.get.return_value.json.return_value = {
            "status": 210,
            "message": "IP whitelist failed",
        }
        with (
            patch.dict(os.environ, {"BAIDU_MAP_AK": "secret-test"}),
            self.assertRaisesRegex(
                RuntimeError, "status=210 message=IP whitelist failed"
            ),
        ):
            worker.locate("1.2.3.4")

    def test_network_error_redacts_ak(self):
        worker = self.worker()
        worker.http.get.side_effect = requests.ConnectionError(
            "failed https://api.map.baidu.com/location/ip?ak=secret-test"
        )
        with (
            patch.dict(os.environ, {"BAIDU_MAP_AK": "secret-test"}),
            self.assertRaises(RuntimeError) as error,
        ):
            worker.locate("1.2.3.4")
        self.assertIn("ConnectionError", str(error.exception))
        self.assertNotIn("secret-test", str(error.exception))

    def test_malformed_response_retries_instead_of_crashing(self):
        worker = self.worker()
        with patch.dict(os.environ, {"BAIDU_MAP_AK": "secret-test"}):
            for response in [
                [],
                {"status": 0, "content": None},
            ]:
                worker.http.get.return_value.json.return_value = response
                with self.subTest(response=response), self.assertRaises(TypeError):
                    worker.locate("1.2.3.4")

    def test_string_success_status_and_corrupt_cache(self):
        worker = self.worker()
        worker.cache.get.return_value = b"bad json"
        worker.http.get.return_value.json.return_value = {
            "status": "0",
            "content": {"address_detail": {"province": "广东省", "city": "深圳市"}},
        }
        with (
            patch.dict(os.environ, {"BAIDU_MAP_AK": "secret-test"}),
            self.assertLogs("insight.geo", level="WARNING") as logs,
        ):
            result = worker.locate("1.2.3.4")
        self.assertEqual(result["province"], "广东省")
        self.assertIn("cache_read_failed", logs.output[0])

    def test_raw_response_logged_before_validation(self):
        for status, body in [
            (200, "not json\nsecret-test"),
            (500, "<html>upstream failure</html>"),
            (429, '{"message":"too many requests"}'),
        ]:
            with self.subTest(status=status, body=body):
                worker = self.worker()
                response = requests.Response()
                response.status_code = status
                response.encoding = "utf-8"
                response._content = body.encode()
                worker.http.get.return_value = response
                with (
                    patch.dict(os.environ, {"BAIDU_MAP_AK": "secret-test"}),
                    self.assertLogs("insight.geo", level="INFO") as logs,
                    self.assertRaises(RuntimeError),
                ):
                    worker.locate("1.2.3.4")
                output = logs.output[0]
                self.assertIn("geo.baidu_response ip=1.2.3.4", output)
                self.assertIn(f"http_status={status}", output)
                self.assertIn("body=", output)
                self.assertNotIn("secret-test", output)
                self.assertNotIn("\n", output)
                if status == 200 and "address_detail" in body:
                    self.assertIn("address_detail", output)

    def test_success_response_is_logged(self):
        worker = self.worker()
        response = requests.Response()
        response.status_code = 200
        response.encoding = "utf-8"
        response._content = '{"status":0,"content":{"address_detail":{"province":"广东省","city":"深圳市"}}}'.encode()
        worker.http.get.return_value = response
        with (
            patch.dict(os.environ, {"BAIDU_MAP_AK": "secret-test"}),
            self.assertLogs("insight.geo", level="INFO") as logs,
        ):
            self.assertEqual(worker.locate("1.2.3.4")["province"], "广东省")
        self.assertIn("广东省", logs.output[0])

    def test_formatter_redacts_secrets(self):
        record = logging.LogRecord(
            "insight.geo",
            logging.ERROR,
            "",
            1,
            "AK=%s database=mysql+pymysql://user:db-secret@localhost/db url=https://example.com/?ak=other-key",
            ("secret-test",),
            None,
        )
        with patch.dict(os.environ, {"BAIDU_MAP_AK": "secret-test"}):
            output = SafeFormatter("%(message)s").format(record)
        for secret in ("secret-test", "db-secret", "other-key"):
            self.assertNotIn(secret, output)
        self.assertIn("[REDACTED]", output)


if __name__ == "__main__":
    unittest.main()

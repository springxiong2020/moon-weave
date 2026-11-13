import unittest
from datetime import datetime

from insight.parser import parse

LINE = '1.2.3.4 - - [11/Nov/2026:14:35:01 +0800] "GET /hello?q=1 HTTP/1.1" 200 123 "-" "Mozilla/5.0" rt=0.125'


class ParserTests(unittest.TestCase):
    def test_access(self):
        event = parse(LINE, "access")
        self.assertEqual(event["timestamp"], datetime(2026, 11, 11, 14, 35, 1))
        self.assertEqual(event["status"], 200)
        self.assertEqual(event["request_time"], 0.125)
        self.assertEqual(event["body_bytes"], 123)
        self.assertEqual(event["method"], "GET")

    def test_invalid_and_oversized_request_methods(self):
        requests = [
            r"\x16\x03\x01" * 20,
            "A" * 33 + " / HTTP/1.1",
            "-",
            "GET",
            r"BAD\x00 / HTTP/1.1",
        ]
        for request in requests:
            with self.subTest(request=request):
                raw = LINE.replace("GET /hello?q=1 HTTP/1.1", request).replace(
                    "200 123", "400 123"
                )
                event = parse(raw, "access")
                self.assertIsNone(event["method"])
                self.assertEqual(event["request"], request)
                self.assertEqual(event["status"], 400)
                self.assertEqual(event["request_time"], 0.125)

    def test_extension_method_at_column_limit(self):
        request = "A" * 32 + " / HTTP/1.1"
        event = parse(LINE.replace("GET /hello?q=1 HTTP/1.1", request), "access")
        self.assertEqual(event["method"], "A" * 32)

    def test_ipv6_and_escaped_quote(self):
        event = parse(
            LINE.replace("1.2.3.4", "2001:4860:4860::8888").replace(
                "Mozilla/5.0", r"agent\"test"
            ),
            "access",
        )
        self.assertEqual(event["ip"], "2001:4860:4860::8888")
        self.assertEqual(event["user_agent"], r"agent\"test")

    def test_missing_request(self):
        event = parse(
            LINE.replace("GET /hello?q=1 HTTP/1.1", "-")
            .replace("200 123", "400 -")
            .replace("rt=0.125", "rt=-"),
            "access",
        )
        self.assertEqual(event["body_bytes"], 0)
        self.assertIsNone(event["request_time"])

    def test_error(self):
        event = parse(
            "2026/11/11 14:35:01 [error] 123#123: connect() failed, client: 1.2.3.4, server: test",
            "error",
        )
        self.assertEqual(event["timestamp"], datetime(2026, 11, 11, 14, 35, 1))
        self.assertEqual(event["level"], "error")
        self.assertEqual(event["ip"], "1.2.3.4")

    def test_access_offsets_do_not_change_printed_time(self):
        for offset in ("+0800", "+0000", "-0500"):
            with self.subTest(offset=offset):
                event = parse(LINE.replace("+0800", offset), "access")
                self.assertEqual(event["timestamp"], datetime(2026, 11, 11, 14, 35, 1))

    def test_production_error_with_connection_and_ipv6(self):
        event = parse(
            '2026/11/12 07:40:09 [error] 166554#166554: *15538 open() '
            '"/home/springxiong/app/web/index.html" failed (2: No such file or directory), '
            'client: 2001:db8::1, server: lab.springxiong.cn, '
            'request: "GET /spark/b/standalone/tenant_info HTTP/2.0", host: "lab.springxiong.cn"',
            "error",
        )
        self.assertEqual(event["timestamp"], datetime(2026, 11, 12, 7, 40, 9))
        self.assertEqual(event["ip"], "2001:db8::1")
        self.assertEqual(event["level"], "error")

    def test_error_without_client_context(self):
        event = parse("2026/11/12 10:43:49 [notice] 176324#176324: signal process started", "error")
        self.assertIsNone(event["ip"])
        self.assertEqual(event["timestamp"], datetime(2026, 11, 12, 10, 43, 49))

    def test_malformed(self):
        for kind in ("access", "error"):
            with self.assertRaises(ValueError):
                parse("invalid log line", kind)


if __name__ == "__main__":
    unittest.main()

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import Store, demo_sources, handler_for, parse_session


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sources = demo_sources(self.root / "examples")
        self.dbpath = self.root / "index.db"
        self.store = Store(self.dbpath, self.sources)
        self.store.scan()

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def test_both_adapters_and_incremental_index(self):
        rows = self.store.sessions()
        self.assertEqual(len(rows), 5)
        self.assertEqual({r["provider"] for r in rows}, {"codex", "claude"})
        self.assertTrue(
            all(
                any(m["role"] == "tool" for m in json.loads(r["messages"]))
                for r in rows
            )
        )
        claude = next(r for r in rows if r["provider"] == "claude")
        self.assertEqual(
            [m["role"] for m in json.loads(claude["messages"])],
            ["user", "tool", "assistant"],
        )
        self.assertEqual(self.store.scan()["indexed"], 0)
        path = self.sources[0][1] / "demo-0.jsonl"
        with path.open("a") as f:
            f.write("{incomplete")
        self.assertEqual(self.store.scan()["indexed"], 1)
        self.assertEqual(parse_session(path, "codex")["errors"], 1)
        self.assertIn("全文搜索", parse_session(path, "codex")["title"])

    def test_flags_persist_without_source_changes(self):
        row = self.store.sessions()[0]
        path = Path(row["path"])
        before = hashlib.sha256(path.read_bytes()).digest()
        self.store.flags(row["id"], {"favorite": True, "archived": True})
        second = Store(self.dbpath, self.sources)
        try:
            found = next(r for r in second.sessions() if r["id"] == row["id"])
            self.assertEqual((found["favorite"], found["archived"]), (1, 1))
            self.assertEqual(before, hashlib.sha256(path.read_bytes()).digest())
        finally:
            second.db.close()
        path.unlink()
        self.store.scan()
        self.assertEqual(len(self.store.sessions()), 4)

    def request(self, method, path, body=None, headers=None):
        from io import BytesIO
        from email.message import Message

        class Handler(handler_for(self.store, 8000)):
            def log_message(self, *args):
                pass

        def request(method, path, body=None, headers=None):
            handler = Handler.__new__(Handler)
            handler.headers = Message()
            handler.headers["Host"] = "127.0.0.1:8000"
            for key, value in (headers or {}).items():
                if key in handler.headers:
                    del handler.headers[key]
                handler.headers[key] = value
            payload = (body or "").encode()
            handler.headers["Content-Length"] = str(len(payload))
            handler.rfile = BytesIO(payload)
            handler.wfile = BytesIO()
            handler.path = path
            handler.request_version = "HTTP/1.1"
            handler.requestline = f"{method} {path} HTTP/1.1"
            handler.command = method
            getattr(handler, "do_" + method)()
            head, raw = handler.wfile.getvalue().split(b"\r\n\r\n", 1)
            status = int(head.split(b" ")[1])
            return status, raw

        return request(method, path, body, headers)

    def test_http_search_export_and_origin_protection(self):
        request = self.request

        from urllib.parse import quote

        status, raw = request("GET", "/api/sessions?q=" + quote("库存"))
        rows = json.loads(raw)
        self.assertEqual(status, 200)
        self.assertEqual(len(rows), 1)
        ident = rows[0]["id"]
        status, raw = request("GET", f"/api/sessions/{ident}/export?format=md")
        self.assertEqual(status, 200)
        self.assertIn("库存", raw.decode())
        self.assertEqual(request("POST", "/api/scan", "{}")[0], 403)
        self.assertEqual(
            request(
                "POST",
                "/api/scan",
                "{}",
                {"X-ASH": "1", "Origin": "https://evil.example"},
            )[0],
            403,
        )
        self.assertEqual(
            request("GET", "/api/sessions", headers={"Host": "evil.example"})[0], 403
        )
        self.assertEqual(
            request("GET", "/api/sessions", headers={"Sec-Fetch-Site": "cross-site"})[
                0
            ],
            403,
        )
        self.assertEqual(
            request(
                "POST",
                f"/api/sessions/{ident}",
                '{"favorite":true}',
                {"X-ASH": "1"},
            )[0],
            200,
        )
        self.assertEqual(request("GET", "/../../server.py")[0], 404)
        self.assertEqual(request("GET", "/")[0], 200)

    def test_delete_both_providers_cleans_file_index_and_flags(self):
        for provider in ("codex", "claude"):
            row = next(r for r in self.store.sessions() if r["provider"] == provider)
            self.store.flags(row["id"], {"favorite": True, "archived": True})
            path = Path(row["path"])
            other_files = {
                Path(r["path"]): Path(r["path"]).read_bytes()
                for r in self.store.sessions()
                if r["id"] != row["id"]
            }
            status, raw = self.request("GET", f"/api/sessions/{row['id']}")
            self.assertEqual(status, 200)
            detail = json.loads(raw)
            status, _ = self.request(
                "DELETE",
                f"/api/sessions/{row['id']}",
                json.dumps({"confirm": row["id"], "revision": detail["revision"]}),
                {"X-ASH": "1"},
            )
            self.assertEqual(status, 200)
            self.assertFalse(path.exists())
            self.assertIsNone(
                self.store.db.execute(
                    "SELECT * FROM flags WHERE id=?", (row["id"],)
                ).fetchone()
            )
            self.store.scan()
            self.assertFalse(any(r["id"] == row["id"] for r in self.store.sessions()))
            self.assertEqual(self.request("GET", f"/api/sessions/{row['id']}")[0], 404)
            for other, contents in other_files.items():
                self.assertEqual(other.read_bytes(), contents)

    def test_delete_requires_confirmation_revision_and_same_origin(self):
        row = self.store.sessions()[0]
        url = f"/api/sessions/{row['id']}"
        data = json.dumps({"confirm": row["id"], "revision": row["stamp"]})
        self.assertEqual(self.request("DELETE", url, data)[0], 403)
        self.assertEqual(
            self.request(
                "DELETE", url, data, {"X-ASH": "1", "Origin": "https://evil.example"}
            )[0],
            403,
        )
        self.assertEqual(self.request("DELETE", url, "{}", {"X-ASH": "1"})[0], 400)
        self.assertEqual(
            self.request("DELETE", url + "/export", data, {"X-ASH": "1"})[0], 404
        )
        self.assertEqual(
            self.request(
                "DELETE", url, json.dumps({"confirm": row["id"]}), {"X-ASH": "1"}
            )[0],
            409,
        )
        Path(row["path"]).write_text(Path(row["path"]).read_text() + "\n")
        self.assertEqual(self.request("DELETE", url, data, {"X-ASH": "1"})[0], 409)
        self.assertTrue(Path(row["path"]).exists())
        self.store.scan()
        self.assertEqual(self.request("DELETE", url, data, {"X-ASH": "1"})[0], 409)

    def test_delete_rejects_symlink_replacement_and_outside_path(self):
        row = self.store.sessions()[0]
        path = Path(row["path"])
        outside = self.root / "keep.jsonl"
        outside.write_text("must not delete")
        path.unlink()
        path.symlink_to(outside)
        with self.assertRaises(PermissionError):
            self.store.delete(row["id"], row["stamp"])
        self.assertTrue(outside.exists())
        self.store.db.execute(
            "UPDATE sessions SET path=? WHERE id=?", (str(outside), row["id"])
        )
        with self.assertRaises(PermissionError):
            self.store.delete(row["id"], row["stamp"])
        self.assertTrue(outside.exists())

    def test_delete_failure_keeps_state_and_missing_file_clears_state(self):
        from unittest.mock import patch

        row = self.store.sessions()[0]
        self.store.flags(row["id"], {"favorite": True})
        with patch("server.os.unlink", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                self.store.delete(row["id"], row["stamp"])
        self.assertTrue(Path(row["path"]).exists())
        self.assertTrue(
            next(r for r in self.store.sessions() if r["id"] == row["id"])["favorite"]
        )
        Path(row["path"]).unlink()
        self.assertTrue(self.store.delete(row["id"], row["stamp"])["already_missing"])

    def test_brand_assets(self):
        import xml.etree.ElementTree as ET

        status, body = self.request("GET", "/logo.svg")
        self.assertEqual(status, 200)
        self.assertEqual(ET.fromstring(body).tag, "{http://www.w3.org/2000/svg}svg")
        status, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("ASH · 本地智能体会话台", body.decode())
        self.assertIn('rel="icon" type="image/svg+xml" href="/logo.svg"', body.decode())

    def test_local_icons_are_served_and_safe(self):
        import xml.etree.ElementTree as ET
        from server import BASE

        manifest = json.loads((BASE / "web/icons/manifest.json").read_text())
        self.assertEqual(len(manifest["icons"]), 21)
        for name in manifest["icons"]:
            with self.subTest(icon=name):
                status, body = self.request("GET", f"/icons/{name}.svg")
                self.assertEqual(status, 200)
                svg = ET.fromstring(body)
                self.assertEqual(svg.attrib["viewBox"], "0 0 24 24")
                self.assertGreater(len(svg), 0)
                self.assertFalse(any(e.tag.endswith("script") for e in svg.iter()))
                self.assertEqual(
                    hashlib.sha256(body).hexdigest(), manifest["sha256"][name + ".svg"]
                )
        for url in (
            "/icons/../server.py",
            "/icons/missing.svg",
            "/icons/manifest.json",
        ):
            self.assertEqual(self.request("GET", url)[0], 404)

    def test_symlinks_outside_root_ignored(self):
        outside = self.root / "outside.jsonl"
        outside.write_text('{"type":"user","message":{"content":"secret"}}')
        (self.sources[1][1] / "external.jsonl").symlink_to(outside)
        self.store.scan()
        self.assertEqual(len(self.store.sessions()), 5)


if __name__ == "__main__":
    unittest.main()

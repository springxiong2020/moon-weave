#!/usr/bin/env python3
"""ASH: local agent session manager. Python 3.10+, no dependencies."""

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE = Path(__file__).resolve().parent


def text_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict)
            and b.get("type") in ("text", "input_text", "output_text")
        )
    return ""


def parse_session(path, provider):
    messages, errors = [], 0
    meta = {"sid": path.stem, "cwd": "", "model": "", "tokens": None}
    seen = set()
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    continue
                kind, stamp = row.get("type"), row.get("timestamp", "")
                payload = row.get("payload") or {}
                if not isinstance(payload, dict):
                    payload = {}
                if provider == "codex":
                    if kind == "session_meta":
                        meta.update(
                            sid=payload.get("id") or meta["sid"],
                            cwd=payload.get("cwd") or "",
                        )
                    if kind == "turn_context":
                        meta["model"] = payload.get("model") or meta["model"]
                    if kind == "event_msg" and payload.get("type") == "token_count":
                        info = payload.get("info") or {}
                        meta["tokens"] = (info.get("total_token_usage") or {}).get(
                            "total_tokens", meta["tokens"]
                        )
                    if kind != "response_item":
                        continue
                    subtype = payload.get("type")
                    if subtype == "message":
                        body = text_content(payload.get("content"))
                        if body:
                            messages.append(
                                {
                                    "role": payload.get("role", "system"),
                                    "text": body,
                                    "time": stamp,
                                }
                            )
                    elif subtype in (
                        "function_call",
                        "custom_tool_call",
                        "function_call_output",
                        "custom_tool_call_output",
                    ):
                        body = payload.get(
                            "arguments", payload.get("input", payload.get("output", ""))
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "name": payload.get("name", "工具结果"),
                                "text": body
                                if isinstance(body, str)
                                else json.dumps(body, ensure_ascii=False),
                                "time": stamp,
                            }
                        )
                else:
                    meta["sid"] = row.get("sessionId") or meta["sid"]
                    meta["cwd"] = row.get("cwd") or meta["cwd"]
                    msg = row.get("message") or {}
                    if not isinstance(msg, dict):
                        continue
                    meta["model"] = msg.get("model") or meta["model"]
                    if kind not in ("user", "assistant"):
                        continue
                    uid = row.get("uuid")
                    if uid and uid in seen:
                        continue
                    if uid:
                        seen.add(uid)
                    content = msg.get("content", [])
                    if isinstance(content, str):
                        messages.append({"role": kind, "text": content, "time": stamp})
                    for block in content if isinstance(content, list) else []:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") == "text" and block.get("text"):
                            messages.append(
                                {
                                    "role": kind,
                                    "text": str(block["text"]),
                                    "time": stamp,
                                }
                            )
                        elif block.get("type") in ("tool_use", "tool_result"):
                            body = block.get("input", block.get("content", ""))
                            messages.append(
                                {
                                    "role": "tool",
                                    "name": block.get("name", "工具结果"),
                                    "text": body
                                    if isinstance(body, str)
                                    else json.dumps(body, ensure_ascii=False),
                                    "time": stamp,
                                }
                            )
            except (ValueError, TypeError, AttributeError):
                errors += 1
    title = next(
        (
            m["text"].strip().split("\n")[0][:100]
            for m in messages
            if m["role"] == "user"
        ),
        path.stem,
    )
    return {**meta, "title": title, "messages": messages, "errors": errors}


class Store:
    def __init__(self, db, sources):
        self.sources = sources
        self.lock = threading.RLock()
        self.db = sqlite3.connect(db, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY, provider TEXT, path TEXT UNIQUE, stamp TEXT,
            title TEXT, cwd TEXT, model TEXT, updated REAL, tokens INTEGER,
            sid TEXT, messages TEXT, search TEXT, errors INTEGER);
            CREATE TABLE IF NOT EXISTS flags (id TEXT PRIMARY KEY, favorite INTEGER DEFAULT 0, archived INTEGER DEFAULT 0);
            CREATE INDEX IF NOT EXISTS updated_idx ON sessions(updated DESC);""")
        self.last_scan = None
        self.warnings = []

    def scan(self):
        with self.lock:
            warnings, count = [], 0
            for provider, root in self.sources:
                if not root.is_dir():
                    warnings.append(f"{provider}: 目录不存在 {root}")
                    continue
                try:
                    paths = list(root.rglob("*.jsonl"))
                except OSError as e:
                    warnings.append(f"{provider}: {e}")
                    continue
                for path in paths:
                    try:
                        # Only configured roots; do not follow symlinks out of them.
                        resolved = path.resolve()
                        if not resolved.is_relative_to(root.resolve()):
                            continue
                        st = path.stat()
                        stamp = f"{st.st_mtime_ns}:{st.st_size}"
                        old = self.db.execute(
                            "SELECT stamp FROM sessions WHERE path=?", (str(path),)
                        ).fetchone()
                        if old and old["stamp"] == stamp:
                            continue
                        data = parse_session(path, provider)
                        ident = hashlib.sha256(
                            f"{provider}:{path}".encode()
                        ).hexdigest()[:24]
                        self.db.execute(
                            "INSERT OR REPLACE INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                ident,
                                provider,
                                str(path),
                                stamp,
                                data["title"],
                                data["cwd"],
                                data["model"],
                                st.st_mtime,
                                data["tokens"],
                                data["sid"],
                                json.dumps(data["messages"], ensure_ascii=False),
                                "\n".join(m["text"] for m in data["messages"]),
                                data["errors"],
                            ),
                        )
                        count += 1
                    except OSError as e:
                        warnings.append(f"{path.name}: {e}")
            # Remove stale index entries, while keeping user flags for a possible restore.
            for row in self.db.execute("SELECT id,path FROM sessions").fetchall():
                if not Path(row["path"]).exists():
                    self.db.execute("DELETE FROM sessions WHERE id=?", (row["id"],))
            self.db.commit()
            self.last_scan = datetime.now(timezone.utc).isoformat()
            self.warnings = warnings
            return {"indexed": count, "warnings": warnings, "last_scan": self.last_scan}

    def sessions(self):
        with self.lock:
            rows = self.db.execute("""SELECT s.*, COALESCE(f.favorite,0) favorite, COALESCE(f.archived,0) archived
                FROM sessions s LEFT JOIN flags f ON s.id=f.id ORDER BY updated DESC""").fetchall()
            return [dict(r) for r in rows]

    def flags(self, ident, flags):
        with self.lock:
            if not self.db.execute(
                "SELECT id FROM sessions WHERE id=?", (ident,)
            ).fetchone():
                raise KeyError(ident)
            self.db.execute("INSERT OR IGNORE INTO flags(id) VALUES (?)", (ident,))
            for name in ("favorite", "archived"):
                if name in flags:
                    self.db.execute(
                        f"UPDATE flags SET {name}=? WHERE id=?",
                        (int(bool(flags[name])), ident),
                    )
            self.db.commit()

    def delete(self, ident, revision):
        """Unlink only an indexed, unchanged JSONL inside its configured source."""
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM sessions WHERE id=?", (ident,)
            ).fetchone()
            if not row:
                raise KeyError(ident)
            if not revision or revision != row["stamp"]:
                raise ValueError("会话索引已更新，请刷新详情后重试")
            path = Path(row["path"]).absolute()
            root = next(
                (
                    r.absolute()
                    for provider, r in self.sources
                    if provider == row["provider"] and path.is_relative_to(r.absolute())
                ),
                None,
            )
            if root is None or path.suffix != ".jsonl":
                raise PermissionError("文件不在已配置的会话目录内")
            relative = path.relative_to(root)
            if not relative.parts or ".." in relative.parts:
                raise PermissionError("无效会话路径")
            # Traverse with directory descriptors so swapped parent symlinks cannot
            # redirect deletion. O_NOFOLLOW also rejects a replaced source root.
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                for part in relative.parts[:-1]:
                    child = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=directory,
                    )
                    os.close(directory)
                    directory = child
                try:
                    current = os.stat(
                        relative.name, dir_fd=directory, follow_symlinks=False
                    )
                except FileNotFoundError:
                    current = None
                if current is not None:
                    if not stat.S_ISREG(current.st_mode):
                        raise PermissionError("拒绝删除符号链接或非普通文件")
                    if f"{current.st_mtime_ns}:{current.st_size}" != revision:
                        raise ValueError(
                            "会话文件已变化，可能仍在写入。请扫描并重新确认"
                        )
                    os.unlink(relative.name, dir_fd=directory)
            finally:
                os.close(directory)
            # Only clear local management state after unlink succeeds (or the
            # file is already absent). Filesystem failures leave the index intact.
            self.db.execute("DELETE FROM flags WHERE id=?", (ident,))
            self.db.execute("DELETE FROM sessions WHERE id=?", (ident,))
            self.db.commit()
            return {"ok": True, "already_missing": current is None}


def handler_for(store, port):
    class Handler(BaseHTTPRequestHandler):
        def reply(
            self, code, value, mime="application/json; charset=utf-8", download=None
        ):
            body = (
                value
                if isinstance(value, bytes)
                else json.dumps(value, ensure_ascii=False).encode()
            )
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
            )
            if download:
                self.send_header(
                    "Content-Disposition", f'attachment; filename="{download}"'
                )
            self.end_headers()
            self.wfile.write(body)

        def valid(self):
            allowed = {f"localhost:{port}", f"127.0.0.1:{port}"}
            if (
                self.headers.get("Host") not in allowed
                or self.headers.get("Sec-Fetch-Site") == "cross-site"
            ):
                self.reply(403, {"error": "仅允许本机同源访问"})
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://{host}" for host in allowed}:
                self.reply(403, {"error": "来源不匹配"})
                return False
            return True

        def do_GET(self):
            if not self.valid():
                return
            url = urlparse(self.path)
            query = parse_qs(url.query)
            if url.path == "/api/status":
                return self.reply(
                    200,
                    {
                        "last_scan": store.last_scan,
                        "warnings": store.warnings,
                        "sources": [
                            {"provider": p, "path": str(r), "exists": r.is_dir()}
                            for p, r in store.sources
                        ],
                    },
                )
            if url.path == "/api/sessions":
                result = []
                needle = query.get("q", [""])[0].casefold()
                for row in store.sessions():
                    if (
                        needle
                        and needle
                        not in (row["search"] + row["title"] + row["cwd"]).casefold()
                    ):
                        continue
                    row["count"] = len(json.loads(row.pop("messages")))
                    row.pop("search")
                    row.pop("stamp")
                    result.append(row)
                return self.reply(200, result)
            if url.path.startswith("/api/sessions/"):
                parts = url.path.split("/")
                row = next((r for r in store.sessions() if r["id"] == parts[3]), None)
                if not row:
                    return self.reply(404, {"error": "会话不存在"})
                row["messages"] = json.loads(row["messages"])
                row.pop("search")
                row["revision"] = row.pop("stamp")
                if len(parts) > 4 and parts[4] == "export":
                    if query.get("format", ["json"])[0] == "md":
                        body = (
                            "# "
                            + row["title"]
                            + "\n\n"
                            + "\n\n".join(
                                "## " + m["role"] + "\n\n" + m["text"]
                                for m in row["messages"]
                            )
                        )
                        return self.reply(
                            200,
                            body.encode(),
                            "text/markdown; charset=utf-8",
                            row["id"] + ".md",
                        )
                    return self.reply(200, row, download=row["id"] + ".json")
                return self.reply(200, row)
            assets = {
                "/": ("index.html", "text/html"),
                "/app.js": ("app.js", "text/javascript"),
                "/style.css": ("style.css", "text/css"),
                "/logo.svg": ("logo.svg", "image/svg+xml"),
                "/favicon.ico": ("logo.svg", "image/svg+xml"),
            }
            assets.update(
                {
                    f"/icons/{path.name}": (f"icons/{path.name}", "image/svg+xml")
                    for path in (BASE / "web" / "icons").glob("*.svg")
                }
            )
            if url.path in assets:
                name, mime = assets[url.path]
                return self.reply(
                    200, (BASE / "web" / name).read_bytes(), mime + "; charset=utf-8"
                )
            self.reply(404, {"error": "未找到"})

        def do_DELETE(self):
            if not self.valid():
                return
            if self.headers.get("X-ASH") != "1":
                return self.reply(403, {"error": "缺少请求标识"})
            parts = urlparse(self.path).path.split("/")
            if len(parts) != 4 or parts[1:3] != ["api", "sessions"] or not parts[3]:
                return self.reply(404, {"error": "未找到"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 0 or size > 4096:
                    return self.reply(413, {"error": "请求过大"})
                data = json.loads(self.rfile.read(size) or b"{}")
                if not isinstance(data, dict) or data.get("confirm") != parts[3]:
                    return self.reply(400, {"error": "请明确确认要删除的会话"})
                return self.reply(200, store.delete(parts[3], data.get("revision")))
            except KeyError:
                self.reply(404, {"error": "会话不存在或已被删除"})
            except (ValueError, TypeError):
                self.reply(409, {"error": "会话文件或索引已变化，请扫描并重新确认删除"})
            except PermissionError:
                self.reply(
                    403, {"error": "无法删除：没有权限，或文件路径已变化。请检查数据源"}
                )
            except OSError:
                self.reply(
                    500,
                    {"error": "文件删除失败，请检查目录和文件权限后重试；索引已保留"},
                )

        def do_POST(self):
            if not self.valid():
                return
            if self.headers.get("X-ASH") != "1":
                return self.reply(403, {"error": "缺少请求标识"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 0 or size > 4096:
                    return self.reply(413, {"error": "请求过大"})
                data = json.loads(self.rfile.read(size) or b"{}")
                if not isinstance(data, dict):
                    raise ValueError()
                if self.path == "/api/scan":
                    return self.reply(200, store.scan())
                if self.path.startswith("/api/sessions/"):
                    store.flags(self.path.split("/")[3], data)
                    return self.reply(200, {"ok": True})
                self.reply(404, {"error": "未找到"})
            except (ValueError, KeyError):
                self.reply(400, {"error": "无效请求或会话不存在"})

    return Handler


def demo_sources(root):
    root.mkdir(parents=True, exist_ok=True)
    examples = [
        (
            "codex",
            "为会话管理器增加全文搜索",
            "/workspace/agent-session-hub",
            "gpt-5-codex",
            "请为会话管理器增加全文搜索，支持按项目筛选，并保留中文搜索体验。",
            "我会先检查索引和查询接口，再添加搜索交互。\n\n实现方案：\n1. 统一会话与消息结构\n2. 增量更新本地索引\n3. 对标题、工作目录和消息内容进行检索",
            "rg --files src/",
        ),
        (
            "claude",
            "排查支付回调重复处理",
            "/workspace/commerce-api",
            "claude-sonnet",
            "支付 webhook 偶尔会重复扣库存，请检查幂等逻辑。",
            "问题出在检查事件与更新库存之间存在并发窗口。建议把事件登记和库存扣减放在同一个事务中，并为 event_id 添加唯一约束。",
            "pytest tests/test_webhooks.py",
        ),
        (
            "codex",
            "完善深色主题与键盘导航",
            "/workspace/agent-session-hub",
            "gpt-5-codex",
            "检查侧边栏的键盘导航和焦点样式。",
            "已梳理导航顺序。每个交互元素需要可见焦点，搜索框可以通过 / 快捷键聚焦。",
            "npm run build",
        ),
        (
            "claude",
            "梳理组件库的设计变量",
            "/workspace/design-system",
            "claude-sonnet",
            "将按钮和输入框的颜色、间距抽成设计变量。",
            "建议将变量分为基础色、语义色和组件级变量。这样可以独立调整品牌色与交互状态。",
            'find components -name "*.css"',
        ),
        (
            "codex",
            "优化批量导入的内存占用",
            "/workspace/commerce-api",
            "gpt-5-codex",
            "导入大文件时内存持续增长，帮我定位原因。",
            "读取方式需要改为逐行处理，同时设置有界批次，避免在内存中累积全部记录。",
            "python -m unittest",
        ),
    ]
    for i, (provider, title, cwd, model, user, answer, command) in enumerate(examples):
        folder = root / provider
        folder.mkdir(exist_ok=True)
        path = folder / f"demo-{i}.jsonl"
        stamp = datetime.now(timezone.utc).isoformat()
        if provider == "codex":
            rows = [
                {"type": "session_meta", "payload": {"id": f"demo-{i}", "cwd": cwd}},
                {"type": "turn_context", "payload": {"model": model}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": user}],
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "shell",
                        "arguments": json.dumps({"command": command}),
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": answer}],
                    },
                },
            ]
        else:
            rows = [
                {
                    "type": "user",
                    "sessionId": f"demo-{i}",
                    "cwd": cwd,
                    "message": {"content": user},
                },
                {
                    "type": "assistant",
                    "message": {
                        "model": model,
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Bash",
                                "input": {"command": command},
                            },
                            {"type": "text", "text": answer},
                        ],
                    },
                },
            ]
        if not path.exists():
            path.write_text(
                "\n".join(
                    json.dumps({**r, "timestamp": stamp}, ensure_ascii=False)
                    for r in rows
                )
                + "\n"
            )
    return [("codex", root / "codex"), ("claude", root / "claude")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--codex-dir",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        / "sessions",
    )
    parser.add_argument(
        "--claude-dir",
        type=Path,
        default=Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
        / "projects",
    )
    parser.add_argument("--data-dir", type=Path, default=BASE / ".data")
    parser.add_argument(
        "--demo", action="store_true", help="Only load generated example transcripts"
    )
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    sources = (
        demo_sources(args.data_dir / "examples")
        if args.demo
        else [
            ("codex", args.codex_dir.expanduser().resolve()),
            ("claude", args.claude_dir.expanduser().resolve()),
        ]
    )
    store = Store(
        args.data_dir / ("demo.sqlite" if args.demo else "index.sqlite"), sources
    )
    store.scan()
    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port), handler_for(store, args.port)
    )
    print(f"ASH: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.db.close()


if __name__ == "__main__":
    main()

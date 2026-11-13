"""Single collector; offsets and rows commit in the same transaction."""

import gzip
import hashlib
import ipaddress
import logging
import os
import re
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from .db import Cursor, Event, Location
from .parser import parse

log = logging.getLogger(__name__)
NAME = re.compile(r"^(?:(?P<site>.+)\.)?(?P<kind>access|error)\.log(?:[.-].*)?$")


class Collector:
    def __init__(self, sessions):
        self.sessions = sessions
        self.ip_whitelist = []
        for value in re.split(r"[,\s]+", os.getenv("NGINX_IP_WHITELIST", "").strip()):
            if value:
                try:
                    self.ip_whitelist.append(ipaddress.ip_network(value, strict=False))
                except ValueError:
                    raise ValueError(
                        f"NGINX_IP_WHITELIST 包含无效 IP/CIDR: {value}"
                    ) from None
        log.info("collector.whitelist_configured entries=%s", len(self.ip_whitelist))
        self.handles = {}
        self.idle = {}
        self.resources = {}

    def is_exempt(self, ip):
        if not self.ip_whitelist or not ip:
            return False
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(address in network for network in self.ip_whitelist)

    def source_ip(self, raw, kind):
        # Identify the client even when another field makes the line unparseable.
        if kind == "access":
            parts = raw.split(maxsplit=1)
            return parts[0] if parts else None
        match = re.search(r"(?:^|[, ])client: ([^, ]+)", raw)
        return match[1] if match else None

    def close(self):
        for resources in self.resources.values():
            resources.close()
        self.resources.clear()
        self.handles.clear()
        self.idle.clear()

    def scan(self, directory, archives=False):
        for path in sorted(Path(directory).iterdir()):
            match = NAME.match(path.name)
            if not match or not path.is_file():
                continue
            if not archives and not path.name.endswith(".log"):
                # Reattach renamed files after restart, even in live mode.
                stat = path.stat()
                identity = f"{stat.st_dev}:{stat.st_ino}"
                with self.sessions() as session:
                    if session.get(Cursor, identity) is None:
                        continue
            stat = path.stat()
            identity = f"{stat.st_dev}:{stat.st_ino}" + (
                ":gzip" if path.suffix == ".gz" else ""
            )
            if path.suffix == ".gz":
                # logrotate compression changes inode: reuse the original checkpoint
                # when its decompressed bytes match the preceding committed bytes.
                with gzip.open(path, "rb") as probe, self.sessions() as session:
                    candidates = session.scalars(
                        select(Cursor).where(Cursor.offset > 0)
                    ).all()
                    for candidate in candidates:
                        original = NAME.match(Path(candidate.path).name)
                        if (
                            not original
                            or original["kind"] != match["kind"]
                            or original["site"] != match["site"]
                        ):
                            continue
                        probe.seek(max(0, candidate.offset - 128))
                        anchor = hashlib.sha256(
                            probe.read(min(128, candidate.offset))
                        ).hexdigest()
                        if anchor == candidate.anchor:
                            identity = candidate.identity
                            break
            if identity not in self.handles:
                with ExitStack() as resources:
                    if path.suffix == ".gz":
                        handle = resources.enter_context(gzip.open(path, "rb"))
                    else:
                        handle = resources.enter_context(path.open("rb"))
                    self.handles[identity] = (
                        handle,
                        str(path),
                        match["site"] or "default",
                        match["kind"],
                    )
                    log.info(
                        "collector.file_open path=%s identity=%s kind=%s",
                        path,
                        identity,
                        match["kind"],
                    )
                    # Transfer ownership so renamed logs remain readable across scans.
                    self.resources[identity] = resources.pop_all()
        total = 0
        for identity, (handle, path, site, kind) in list(self.handles.items()):
            try:
                added = self.consume(identity, handle, path, site, kind)
                total += added
                self.idle[identity] = 0 if added else self.idle.get(identity, 0) + 1
                # Reopen on the next scan if needed; bound old descriptor retention.
                if self.idle[identity] >= 60:
                    self.resources.pop(identity).close()
                    del self.handles[identity]
                    del self.idle[identity]
            except (OSError, EOFError):
                log.exception("读取日志失败: %s", path)
            # Retain descriptors while nginx can still append to renamed files.
        return total

    def consume(self, identity, handle, path, site, kind):
        count = saved = skipped = 0
        with self.sessions.begin() as session:
            cursor = session.get(Cursor, identity)
            if cursor is None:
                cursor = Cursor(
                    identity=identity, path=path, offset=0, generation=0, anchor=""
                )
                session.add(cursor)
            # Check bytes preceding the checkpoint to detect copytruncate + regrowth.
            handle.seek(max(0, cursor.offset - 128))
            previous = handle.read(min(128, cursor.offset))
            if cursor.offset and hashlib.sha256(previous).hexdigest() != cursor.anchor:
                log.warning(
                    "collector.file_reset path=%s old_offset=%s generation=%s",
                    path,
                    cursor.offset,
                    cursor.generation + 1,
                )
                cursor.offset = 0
                cursor.generation += 1
            handle.seek(cursor.offset)
            for _ in range(1000):
                offset = handle.tell()
                line = handle.readline()
                if not line or not line.endswith(b"\n"):
                    handle.seek(offset)
                    break
                raw = line.decode("utf-8", errors="replace").rstrip("\r\n")
                # Progress includes exempt lines so an all-exempt batch does not
                # incorrectly terminate a historical import before the next batch.
                cursor.offset = handle.tell()
                count += 1
                ip = self.source_ip(raw, kind)
                if self.is_exempt(ip):
                    skipped += 1
                    log.debug(
                        "collector.whitelist_skip path=%s offset=%s ip=%s",
                        path,
                        offset,
                        ip,
                    )
                    continue
                try:
                    fields = parse(raw, kind)
                except (ValueError, OverflowError) as exc:
                    log.warning(
                        "collector.parse_failed path=%s offset=%s error=%s",
                        path,
                        offset,
                        exc,
                    )
                    fields = {
                        # Unparseable lines use the server clock as a fallback.
                        "timestamp": datetime.now(),
                        "parse_error": str(exc),
                    }
                event = Event(
                    source=identity,
                    generation=cursor.generation,
                    offset=offset,
                    site=site,
                    kind=kind,
                    raw=raw,
                    **fields,
                )
                session.add(event)
                ip = fields.get("ip")
                if ip and session.get(Location, ip) is None:
                    session.add(Location(ip=ip))
                    session.flush()
                saved += 1
            handle.seek(max(0, cursor.offset - 128))
            cursor.anchor = hashlib.sha256(
                handle.read(min(128, cursor.offset))
            ).hexdigest()
        if count:
            log.info(
                "collector.batch_committed path=%s processed=%s saved=%s skipped_whitelist=%s offset=%s generation=%s",
                path,
                count,
                saved,
                skipped,
                cursor.offset,
                cursor.generation,
            )
        return count

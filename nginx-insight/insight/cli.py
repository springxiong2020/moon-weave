import argparse
import logging
import os
import time

from sqlalchemy import func, select

from .collector import Collector
from .db import Base, Location, database
from .geo import Geocoder
from .logging_config import configure_logging

log = logging.getLogger(__name__)


def run():
    parser = argparse.ArgumentParser(description="Nginx 日志监控")
    parser.add_argument(
        "command", choices=["init-db", "collect", "import", "geo", "serve"]
    )
    parser.add_argument(
        "--directory", default=os.getenv("NGINX_LOG_DIR", "/var/log/nginx")
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default=None
    )
    parser.add_argument("--once", action="store_true", help="geo 只处理一个批次")
    parser.add_argument("--ip", help="geo 单 IP 诊断，绕过 Redis，不修改 MySQL")
    args = parser.parse_args()
    if (args.once or args.ip) and args.command != "geo":
        parser.error("--once / --ip 仅适用于 geo")
    configure_logging(args.log_level)
    log.info(
        "service.start command=%s log_time=original log_level=%s",
        args.command,
        args.log_level or os.getenv("LOG_LEVEL", "INFO"),
    )
    engine, sessions = database()
    if args.command == "init-db":
        Base.metadata.create_all(engine)
        log.info("database.schema_ready")
    elif args.command == "serve":
        from .web import create_app

        create_app(sessions).run(host=args.host, port=args.port)
    elif args.command == "geo":
        if not os.getenv("BAIDU_MAP_AK", "").strip():
            raise RuntimeError(
                "BAIDU_MAP_AK 未配置，请检查当前工作目录的 .env 或进程环境变量"
            )
        worker = Geocoder(sessions)
        log.info(
            "geo.start ak_configured=true cache=redis batch_size=50 qps=%s cooldown_seconds=%s",
            worker.qps,
            worker.cooldown_seconds,
        )
        if args.ip:
            # Diagnostic must exercise Baidu instead of returning a cached success.
            class NoCache:
                def get(self, key):
                    return None

                def setex(self, *args):
                    return None

            worker.cache = NoCache()
            result = worker.locate(args.ip)
            log.info(
                "geo.diagnostic ip=%s province=%s city=%s mysql_modified=false",
                args.ip,
                result["province"],
                result["city"],
            )
            return
        heartbeat = 0
        try:
            while True:
                worker.run_batch()
                if time.monotonic() - heartbeat >= 60:
                    with sessions() as session:
                        states = session.execute(
                            select(Location.state, func.count()).group_by(
                                Location.state
                            )
                        ).all()
                        next_retry = session.scalar(select(func.min(Location.retry_at)))
                    log.info(
                        "geo.heartbeat states=%s next_due_utc=%s", states, next_retry
                    )
                    heartbeat = time.monotonic()
                if args.once:
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            log.info("service.stop reason=interrupt")
    else:
        # OS lock prevents two collectors racing over the same offsets on this host.
        import fcntl
        import hashlib

        lock_name = hashlib.sha256(str(engine.url).encode()).hexdigest()[:16]
        with open(f"/tmp/nginx-insight-{lock_name}.lock", "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                parser.error("此数据库已有采集进程运行")
            log.info(
                "collector.start directory=%s archives=%s",
                args.directory,
                args.command == "import",
            )
            collector = Collector(sessions)
            try:
                while True:
                    count = collector.scan(
                        args.directory, archives=args.command == "import"
                    )
                    if count:
                        log.info("已处理 %d 条日志（包含白名单跳过）", count)
                    if args.command == "import" and count == 0:
                        log.info("collector.import_complete")
                        break
                    if args.command == "collect":
                        time.sleep(float(os.getenv("POLL_INTERVAL", "1")))
            except KeyboardInterrupt:
                log.info("service.stop reason=interrupt")
            finally:
                collector.close()


def main():
    try:
        run()
    except Exception:
        log.exception("service.failed")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

import logging
import os
from datetime import UTC, datetime

from dotenv import load_dotenv
from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

log = logging.getLogger(__name__)
load_dotenv()


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Cursor(Base):
    __tablename__ = "file_cursors"
    identity: Mapped[str] = mapped_column(String(180), primary_key=True)
    path: Mapped[str] = mapped_column(Text)
    offset: Mapped[int] = mapped_column(BigInteger, default=0)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    anchor: Mapped[str] = mapped_column(String(64), default="")


class Event(Base):
    __tablename__ = "log_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(180))
    generation: Mapped[int] = mapped_column(Integer)
    offset: Mapped[int] = mapped_column(BigInteger)
    site: Mapped[str] = mapped_column(String(255), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    ip: Mapped[str | None] = mapped_column(String(45))
    method: Mapped[str | None] = mapped_column(String(32))
    request: Mapped[str | None] = mapped_column(Text)
    status: Mapped[int | None] = mapped_column(Integer)
    body_bytes: Mapped[int | None] = mapped_column(BigInteger)
    request_time: Mapped[float | None] = mapped_column(Float)
    referer: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    level: Mapped[str | None] = mapped_column(String(32), default="access")
    raw: Mapped[str] = mapped_column(Text)
    parse_error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("source", "generation", "offset"),
        Index("ix_kind_time", "kind", "timestamp"),
    )


class Location(Base):
    __tablename__ = "ip_locations"
    ip: Mapped[str] = mapped_column(String(45), primary_key=True)
    province: Mapped[str] = mapped_column(String(100), default="未知")
    city: Mapped[str] = mapped_column(String(100), default="")
    state: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    retry_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


def database(url=None):
    engine = create_engine(
        url
        or os.getenv(
            "DATABASE_URL",
            "mysql+pymysql://insight:change-me@127.0.0.1/insight?charset=utf8mb4",
        ),
        pool_pre_ping=True,
        hide_parameters=True,
    )
    log.info(
        "database.configured dialect=%s host=%s port=%s database=%s",
        engine.dialect.name,
        engine.url.host or "local",
        engine.url.port,
        engine.url.database,
    )
    return engine, sessionmaker(engine, expire_on_commit=False)

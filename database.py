import os
import ssl
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from dotenv import load_dotenv

load_dotenv(override=True)

RAW_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./qinesystems.db").strip()


def _normalize_async_database_url(url: str) -> tuple[str, dict]:
    if url.startswith("postgres://"):
        url = "postgresql+asyncpg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://") and "+asyncpg" not in url:
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]

    if url.startswith("sqlite"):
        return url, {"check_same_thread": False}

    parts = urlsplit(url)
    query_pairs = dict(parse_qsl(parts.query, keep_blank_values=True))

    sslmode_val = query_pairs.pop("sslmode", "").strip().lower()
    ssl_val = query_pairs.pop("ssl", "").strip().lower()
    hostname_l = (parts.hostname or "").lower()

    disable_ssl = sslmode_val == "disable" or ssl_val in {"false", "0", "disable"}
    needs_ssl = (
        not disable_ssl
        and (
            sslmode_val in {"require", "verify-ca", "verify-full"}
            or ssl_val in {"true", "1", "require"}
            or "supabase.co" in hostname_l
        )
    )

    clean_query = urlencode(query_pairs)
    clean_url = urlunsplit((parts.scheme, parts.netloc, parts.path, clean_query, parts.fragment))

    connect_args: dict = {}
    if needs_ssl:
        connect_args["ssl"] = ssl.create_default_context()
    elif disable_ssl:
        connect_args["ssl"] = False

    return clean_url, connect_args


DATABASE_URL, _connect_args = _normalize_async_database_url(RAW_DATABASE_URL)

_engine_kwargs: dict = {
    "echo": False,
    "pool_pre_ping": True,
    "connect_args": _connect_args,
}
if not DATABASE_URL.startswith("sqlite"):
    _engine_kwargs.update({
        "pool_size": 5,
        "max_overflow": 5,
        "pool_recycle": 1800,
    })

engine = create_async_engine(DATABASE_URL, **_engine_kwargs)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
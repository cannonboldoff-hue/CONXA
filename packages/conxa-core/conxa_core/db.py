"""PostgreSQL-backed key-value store using SQLAlchemy Core.

All storage modules use these helpers as a DB-first layer, falling back to
the local filesystem when SKILL_DATABASE_URL is not set (local development).
"""
from __future__ import annotations

import json
import os
from pathlib import Path as _Path
from typing import Any

from sqlalchemy import create_engine, text

from conxa_core.config import settings

_engine = None
_WINDOWS_RESERVED_FILENAME_CHARS = frozenset('<>:"\\|?*')


def _get_engine():
    global _engine
    if _engine is None and settings.database_url:
        url = settings.database_url.replace("postgres://", "postgresql://", 1)
        _engine = create_engine(url, pool_pre_ping=True)
    return _engine


def get_engine():
    """Public accessor for the shared SQLAlchemy engine (Postgres mode) or
    None (filesystem mode). Lets callers that need atomic multi-statement
    kv_store updates (e.g. a wallet debit/credit) reuse this module's engine
    instead of opening a second one."""
    return _get_engine()


def _safe_namespace(namespace: str) -> str:
    """Sanitize a namespace for filesystem use (Windows forbids ':' etc. in path segments;
    namespaces like "manifest:dev" or "skill_packs:{company}:{slug}" use ':' as a joiner)."""
    parts = namespace.replace("/", os.sep).split(os.sep)
    return os.sep.join(
        "".join("_" if ch in _WINDOWS_RESERVED_FILENAME_CHARS else ch for ch in part)
        for part in parts
    )


def _fs_path(namespace: str, key: str) -> _Path:
    """Filesystem path for a (namespace, key) pair used when no DB is configured."""
    import hashlib
    safe_ns = _safe_namespace(namespace)
    # Hash the key to produce a filename that is safe on all platforms (Windows
    # forbids ':' in filenames; keys like selector_cache use ':' as a separator).
    safe_key = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return _Path(settings.data_dir) / "kv" / safe_ns / f"{safe_key}.json"


def _legacy_fs_path(namespace: str, key: str) -> _Path | None:
    """Pre-hash filesystem path used by older local kv stores."""
    if (
        not key
        or any(sep and sep in key for sep in (os.sep, os.altsep))
        or "\x00" in key
        or any(ch in _WINDOWS_RESERVED_FILENAME_CHARS for ch in key)
    ):
        return None
    safe_ns = _safe_namespace(namespace)
    return _Path(settings.data_dir) / "kv" / safe_ns / f"{key}.json"


def init_db() -> None:
    """Create kv_store table if it does not exist. Called once at startup."""
    engine = _get_engine()
    if engine is None:
        return
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS kv_store (
                namespace  TEXT        NOT NULL,
                key        TEXT        NOT NULL,
                data       JSONB       NOT NULL DEFAULT '{}',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (namespace, key)
            )
        """))
        conn.commit()


def healthcheck() -> bool:
    """Return True if the configured store is reachable.

    Filesystem mode (no SKILL_DATABASE_URL) is always reachable; Postgres mode
    issues a trivial ``SELECT 1``. Raises on failure so callers can surface the
    underlying error.
    """
    engine = _get_engine()
    if engine is None:
        return True
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return True


def using_database() -> bool:
    """True when a real database backend is configured (not the filesystem fallback)."""
    return _get_engine() is not None


def db_get(namespace: str, key: str) -> Any | None:
    engine = _get_engine()
    if engine is None:
        p = _fs_path(namespace, key)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        legacy = _legacy_fs_path(namespace, key)
        return json.loads(legacy.read_text(encoding="utf-8")) if legacy and legacy.exists() else None
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT data FROM kv_store WHERE namespace = :ns AND key = :key"),
            {"ns": namespace, "key": key},
        ).fetchone()
        return row[0] if row else None


def db_set(namespace: str, key: str, data: Any) -> None:
    engine = _get_engine()
    if engine is None:
        p = _fs_path(namespace, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data), encoding="utf-8")
        legacy = _legacy_fs_path(namespace, key)
        if legacy and legacy != p:
            legacy.unlink(missing_ok=True)
        return
    with engine.connect() as conn:
        conn.execute(
            text("""
                INSERT INTO kv_store (namespace, key, data)
                VALUES (:ns, :key, CAST(:data AS jsonb))
                ON CONFLICT (namespace, key) DO UPDATE
                SET data = EXCLUDED.data, updated_at = now()
            """),
            {"ns": namespace, "key": key, "data": json.dumps(data)},
        )
        conn.commit()


def db_delete(namespace: str, key: str) -> None:
    engine = _get_engine()
    if engine is None:
        p = _fs_path(namespace, key)
        p.unlink(missing_ok=True)
        legacy = _legacy_fs_path(namespace, key)
        if legacy and legacy != p:
            legacy.unlink(missing_ok=True)
        return
    with engine.connect() as conn:
        conn.execute(
            text("DELETE FROM kv_store WHERE namespace = :ns AND key = :key"),
            {"ns": namespace, "key": key},
        )
        conn.commit()


def db_list(namespace: str) -> list[Any]:
    """Return all values in a namespace ordered by created_at."""
    engine = _get_engine()
    if engine is None:
        d = _fs_path(namespace, "__sentinel__").parent
        if not d.exists():
            return []
        return [
            json.loads(f.read_text(encoding="utf-8"))
            for f in sorted(d.glob("*.json"), key=lambda f: f.stat().st_mtime)
        ]
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT data FROM kv_store WHERE namespace = :ns ORDER BY created_at"),
            {"ns": namespace},
        ).fetchall()
        return [r[0] for r in rows]


_MAPPED_CACHE: dict[tuple[str, str, str], tuple[tuple[int, int], Any]] = {}


def db_list_mapped(namespace: str, fn: Any) -> list[Any]:
    """``[fn(doc) for doc in db_list(namespace)]``, without re-reading documents that have not changed.

    On the filesystem store, listing a namespace parses every file on every call, and a dashboard asks several times:
    a store of a few dozen skills cost ~100 ms per request, serialised behind one lock. Here each file is parsed once per
    change (keyed by its modified time and size) and only ``fn``'s small result is kept, so a caller can never mutate a
    cached document. With a database nothing is cached (the query is the cost there, and rows are not files).

    The cache is keyed by ``fn``'s module and qualified name, not the object (a caller may build ``fn`` as a closure on
    every call, which would never hit): so ``fn`` must depend only on the document it is given."""
    if _get_engine() is not None:
        return [fn(doc) for doc in db_list(namespace)]
    d = _fs_path(namespace, "__sentinel__").parent
    if not d.exists():
        return []
    out: list[Any] = []
    for f in sorted(d.glob("*.json"), key=lambda f: f.stat().st_mtime):
        st = f.stat()
        stamp = (st.st_mtime_ns, st.st_size)
        key = (namespace, str(f), f"{getattr(fn, '__module__', '')}.{getattr(fn, '__qualname__', repr(fn))}")
        hit = _MAPPED_CACHE.get(key)
        if hit is not None and hit[0] == stamp:
            out.append(hit[1])
            continue
        value = fn(json.loads(f.read_text(encoding="utf-8")))
        _MAPPED_CACHE[key] = (stamp, value)
        out.append(value)
    return [dict(v) if isinstance(v, dict) else v for v in out]


def db_list_kv(namespace: str) -> list[tuple[str, Any]]:
    """Return (key, value) pairs in a namespace ordered by created_at."""
    engine = _get_engine()
    if engine is None:
        d = _fs_path(namespace, "__sentinel__").parent
        if not d.exists():
            return []
        return [
            (f.stem, json.loads(f.read_text(encoding="utf-8")))
            for f in sorted(d.glob("*.json"), key=lambda f: f.stat().st_mtime)
        ]
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT key, data FROM kv_store WHERE namespace = :ns ORDER BY created_at"),
            {"ns": namespace},
        ).fetchall()
        return [(r[0], r[1]) for r in rows]


def db_append(namespace: str, key: str, new_items: list) -> None:
    """Append items to a JSON array stored at (namespace, key).

    Creates the row with new_items as a JSON array on first call,
    then concatenates on subsequent calls.
    """
    engine = _get_engine()
    if engine is None:
        p = _fs_path(namespace, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        existing = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
        if not isinstance(existing, list):
            existing = [existing]
        existing.extend(new_items)
        p.write_text(json.dumps(existing), encoding="utf-8")
        return
    with engine.connect() as conn:
        conn.execute(
            text("""
                INSERT INTO kv_store (namespace, key, data)
                VALUES (:ns, :key, CAST(:items AS jsonb))
                ON CONFLICT (namespace, key) DO UPDATE
                SET data       = kv_store.data || CAST(:items AS jsonb),
                    updated_at = now()
            """),
            {"ns": namespace, "key": key, "items": json.dumps(new_items)},
        )
        conn.commit()

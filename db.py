# db.py
import sqlite3
from pathlib import Path
import hashlib
import secrets
from datetime import datetime, timezone
import re

DB_PATH = Path("gtj.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Better durability than default for a web app:
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db():
    conn = get_db()

    # 1) proposals: the thing you send / generate the PDF from
    conn.execute("""
    CREATE TABLE IF NOT EXISTS proposals (
        id TEXT PRIMARY KEY,                -- token/uuid
        created_at TEXT NOT NULL,           -- ISO UTC string
        business_name TEXT,
        client_name TEXT,
        client_email TEXT,
        total_price_cents INTEGER,          -- optional; keep if you have totals
        proposal_text TEXT NOT NULL,        -- what you sent (or the final merged text)
        proposal_hash TEXT NOT NULL,        -- SHA256 of key fields for immutability proof
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending','accepted','declined')),
        responded_at TEXT,                  -- ISO UTC
        responded_name TEXT,                -- typed name on accept
        responded_ip TEXT,                  -- requester IP
        decline_reason TEXT,                -- optional note on decline

        -- === Acceptance security ===
        accept_token TEXT,                  -- single-use secret
        accept_expires_at TEXT              -- ISO UTC expiry
    );
    """)

    # ---- proposal indexes ----
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_proposals_email ON proposals(client_email);"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_proposals_created ON proposals(created_at);"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_proposals_accept_token ON proposals(accept_token);"
    )

    # 2) free usage tracking (server-side enforcement)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS free_usage (
        key TEXT PRIMARY KEY,      -- device_id or ip
        used_count INTEGER NOT NULL,
        last_used_at TEXT NOT NULL
    );
    """)

    # 3) AI proposal cache (performance)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS ai_proposal_cache (
        input_hash TEXT PRIMARY KEY,     -- ai_input_hash(data)
        proposal_text TEXT NOT NULL,     -- final AI-generated proposal text
        trade TEXT NOT NULL,             -- for analytics/debugging
        created_at TEXT NOT NULL,        -- ISO UTC
        last_used_at TEXT NOT NULL       -- ISO UTC
    );
    """)

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_cache_last_used "
        "ON ai_proposal_cache(last_used_at);"
    )

    # ---- migrations ----
    ensure_decline_reason_column(conn)

    conn.commit()
    conn.close()


def ensure_decline_reason_column(conn):
    cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(proposals)")
    }
    if "decline_reason" not in cols:
        conn.execute(
            "ALTER TABLE proposals ADD COLUMN decline_reason TEXT"
        )
        conn.commit()


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_proposal_id():
    # short, URL-safe token
    return secrets.token_urlsafe(16)


def new_accept_token():
    # longer, single-use secret
    return secrets.token_urlsafe(32)


def compute_proposal_hash(*parts: str) -> str:
    joined = "\n".join([p.strip() for p in parts if p is not None])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


AI_CACHE_VERSION = "v1"


def _normalize(text: str) -> str:
    if not text:
        return ""
    text = text.strip().lower()
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\s+", " ", text)
    return text


def ai_input_hash(data: dict) -> str:
    """
    Deterministic hash of AI-relevant inputs only.
    Changing this function invalidates the cache contract.
    """
    parts = [
        AI_CACHE_VERSION,

        _normalize(data.get("trade")),
        _normalize(data.get("trade_profile")),

        _normalize(data.get("service_type")),
        _normalize(data.get("scope")),
        _normalize(data.get("tone")),
        _normalize(data.get("timeframe")),

        _normalize(data.get("your_business")),
        _normalize(data.get("abn")),
    ]

    joined = "\n".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def get_free_usage(conn, key: str) -> int:
    row = conn.execute(
        "SELECT used_count FROM free_usage WHERE key = ?",
        (key,)
    ).fetchone()
    return row["used_count"] if row else 0


def increment_free_usage(conn, key: str):
    now = utc_now_iso()
    conn.execute("""
        INSERT INTO free_usage (key, used_count, last_used_at)
        VALUES (?, 1, ?)
        ON CONFLICT(key) DO UPDATE SET
            used_count = used_count + 1,
            last_used_at = excluded.last_used_at
    """, (key, now))


def evict_old_ai_cache(conn, days: int = 30):
    """
    Remove AI cache entries not used within `days`.
    Safe to call frequently.
    """
    cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)
    cutoff_iso = datetime.fromtimestamp(
        cutoff,
        tz=timezone.utc
    ).isoformat(timespec="seconds")

    conn.execute(
        """
        DELETE FROM ai_proposal_cache
        WHERE last_used_at < ?
        """,
        (cutoff_iso,),
    )

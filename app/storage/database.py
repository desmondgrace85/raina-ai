"""
SQLite database initialisation for Raina AI.

Tables:
  signals        — every signal generated (history)
  telegram_users — authenticated Telegram users & subscription tier
  mt5_accounts   — per-user MT5 EA connection state
  mt5_settings   — per-user risk/scalping settings
  mt5_trades     — trade orders, open positions, closed history
"""
import logging
import os
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

_DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DB_PATH = _DATA_DIR / "raina.db"

_CREATE_SIGNALS = """
CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    asset           TEXT    NOT NULL,
    engine          TEXT    NOT NULL,
    timeframe       TEXT,
    direction       TEXT    NOT NULL,
    confidence      REAL    NOT NULL,
    risk_level      TEXT    NOT NULL,
    risk_reward     REAL,
    entry_low       REAL,
    entry_high      REAL,
    stop_loss       REAL,
    take_profit     TEXT,
    explanation     TEXT,
    generated_at    TEXT    NOT NULL,
    sent_telegram   INTEGER NOT NULL DEFAULT 0
);
"""

_CREATE_USERS = """
CREATE TABLE IF NOT EXISTS telegram_users (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id     INTEGER UNIQUE NOT NULL,
    telegram_name   TEXT,
    email           TEXT,
    subscription    TEXT    NOT NULL DEFAULT 'none',
    is_active       INTEGER NOT NULL DEFAULT 0,
    rainx_token     TEXT,
    created_at      TEXT    NOT NULL,
    last_seen       TEXT
);
"""

_CREATE_MT5_ACCOUNTS = """
CREATE TABLE IF NOT EXISTS mt5_accounts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id     INTEGER UNIQUE NOT NULL,
    api_key         TEXT    UNIQUE NOT NULL,
    metaapi_id      TEXT,
    account_mode    TEXT    NOT NULL DEFAULT 'demo',
    is_connected    INTEGER NOT NULL DEFAULT 0,
    broker_name     TEXT,
    account_number  TEXT,
    balance         REAL,
    equity          REAL,
    last_heartbeat  TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_MT5_SETTINGS = """
CREATE TABLE IF NOT EXISTS mt5_settings (
    telegram_id     INTEGER PRIMARY KEY,
    settings_json   TEXT NOT NULL DEFAULT '{}'
);
"""

_CREATE_MT5_TRADES = """
CREATE TABLE IF NOT EXISTS mt5_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id     INTEGER NOT NULL,
    api_key         TEXT    NOT NULL,
    asset           TEXT    NOT NULL,
    direction       TEXT    NOT NULL,
    lot_size        REAL    NOT NULL,
    entry_price     REAL,
    stop_loss       REAL,
    take_profit     REAL,
    confidence      REAL,
    timeframe       TEXT,
    status          TEXT    NOT NULL DEFAULT 'pending',
    mt5_ticket      INTEGER,
    open_price      REAL,
    close_price     REAL,
    profit          REAL,
    comment         TEXT    DEFAULT 'RainX',
    created_at      TEXT    NOT NULL,
    opened_at       TEXT,
    closed_at       TEXT
);
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_signals_asset  ON signals(asset);",
    "CREATE INDEX IF NOT EXISTS idx_signals_engine ON signals(engine);",
    "CREATE INDEX IF NOT EXISTS idx_signals_ts     ON signals(generated_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_users_tgid     ON telegram_users(telegram_id);",
    "CREATE INDEX IF NOT EXISTS idx_users_sub      ON telegram_users(subscription, is_active);",
    "CREATE INDEX IF NOT EXISTS idx_mt5_trades_tid ON mt5_trades(telegram_id, status);",
    "CREATE INDEX IF NOT EXISTS idx_mt5_trades_key ON mt5_trades(api_key, status);",
]

_db: aiosqlite.Connection | None = None


async def init_db() -> None:
    global _db
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL;")
    await _db.execute(_CREATE_SIGNALS)
    await _db.execute(_CREATE_USERS)
    await _db.execute(_CREATE_MT5_ACCOUNTS)
    await _db.execute(_CREATE_MT5_SETTINGS)
    await _db.execute(_CREATE_MT5_TRADES)
    for idx in _INDEXES:
        await _db.execute(idx)
    await _db.commit()
    logger.info(f"Database ready at {DB_PATH}")
    print(f"✅ Signal history DB ready: {DB_PATH}", flush=True)


async def close_db() -> None:
    global _db
    if _db:
        await _db.close()
        _db = None


def get_db() -> aiosqlite.Connection:
    if _db is None:
        raise RuntimeError("Database not initialised — call init_db() first")
    return _db

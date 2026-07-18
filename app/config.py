"""
Central configuration for Raina AI.

All tunable thresholds, intervals, and lists live here.
Override any value via environment variable (or .env file).
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Data provider: "yfinance" (real data) or "mock" (for testing)
    data_provider: str = "yfinance"

    # Optional keys for future premium data providers
    market_data_api_key: str = ""
    market_data_api_secret: str = ""

    host: str = "0.0.0.0"
    port: int = 8000

    # ── Signal quality gate ────────────────────────────────────────────────
    min_signal_confidence: float = 65.0
    scalp_min_confidence: float = 60.0
    scalp_timeframes: list[str] = ["1m", "5m", "15m"]

    # ── Background scanner intervals (minutes) ────────────────────────────
    # M15 watcher — analyses 15-minute candles
    m15_scan_interval_minutes: int = 15
    # H1-H4 watcher — analyses 1h/4h candles
    h1_scan_interval_minutes: int = 60
    h4_scan_interval_minutes: int = 240

    # ── Telegram ──────────────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""   # fallback admin chat only

    # ── RainX web app integration ─────────────────────────────────────────
    # Set this to your live site's API base URL, e.g. https://rainx.app/api
    rainx_api_url: str = ""

    # ── Default watchlist ─────────────────────────────────────────────────
    default_watchlist: list[str] = [
        "EURUSD", "GBPUSD", "USDJPY",   # forex
        "BTCUSD", "ETHUSD",              # crypto
        "XAUUSD",                        # gold
        "WTICOUSD",                      # oil
    ]

    class Config:
        env_file = ".env"
        extra = "ignore"   # silently drop unknown env vars (e.g. old field names)


settings = Settings()

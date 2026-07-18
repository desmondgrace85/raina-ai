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

    # ── OpenAI (optional — enables AI signal synthesis + smart chat) ────────
    # Set OPENAI_API_KEY in Railway env vars to enable GPT-4o-mini enhancement.
    openai_api_key: str = ""

    # ── Signal quality gate ────────────────────────────────────────────────
    min_signal_confidence: float = 65.0
    scalp_min_confidence: float = 60.0
    scalp_timeframes: list[str] = ["1m", "5m", "15m"]

    # ── Background scanner intervals (minutes) ────────────────────────────
    m15_scan_interval_minutes: int = 15
    h1_scan_interval_minutes: int = 60
    h4_scan_interval_minutes: int = 240

    # ── Telegram ──────────────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""   # fallback admin chat only

    # ── RainX web app integration ─────────────────────────────────────────
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
        extra = "ignore"


settings = Settings()

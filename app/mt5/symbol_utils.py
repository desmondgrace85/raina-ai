"""
Symbol normalisation utilities for Raina AI.

MT5 brokers append suffixes to standard symbol names, e.g.:
  BTCUSDZ  (Exness, FBS, HFM …)
  BTCUSDm  (some ECN brokers)
  BTCUSDT  (Binance-style)
  XAUUSD+  (some STP brokers)
  EURUSDr  (raw spread)

This module provides:
  • normalize_for_data(raw)  → canonical internal name for yfinance / price data
  • to_broker_symbol(canonical, suffix) → reconstruct broker-specific symbol for MetaAPI trades
  • extract_suffix(raw)       → detect the broker suffix from a raw symbol string

Rules (applied in order inside normalize_for_data):
  1. Strip whitespace, uppercase.
  2. Replace trailing USDT → USD for crypto pairs (BTCUSDT → BTCUSD).
  3. Look up the symbol in the canonical map; if found, done.
  4. Strip common single-char qualifiers (Z m r + # .) that follow a 6-char base.
  5. Try trimming 1–3 trailing chars until a known base is matched.
  6. Fall through: return the processed symbol unchanged (the map will handle it or fail gracefully).
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Canonical 6-char base symbols supported by Raina AI ──────────────────────

# Forex
FOREX_BASES = {
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD",
    "USDCHF", "NZDUSD", "GBPJPY", "EURJPY",
}

# Crypto (USD-quoted, normalised from USDT/USDTM/etc.)
CRYPTO_BASES = {
    "BTCUSD", "ETHUSD", "BNBUSD", "SOLUSD",
    "XRPUSD", "ADAUSD", "DOGEUSD",
}

# Metals & commodities (internal names)
COMMODITY_BASES = {
    "XAUUSD", "XAGUSD", "WTICOUSD", "BRENTUSD", "NATGAS", "COPPER",
    "SILVER", "USOIL",
}

KNOWN_BASES: frozenset[str] = frozenset(FOREX_BASES | CRYPTO_BASES | COMMODITY_BASES)

# ── Suffix patterns ───────────────────────────────────────────────────────────

# Matches trailing broker qualifiers:
#   single letter:  Z  m  r  n  +  #  .  -
#   multi-letter:   _SB  .a  _ECN  (up to 4 chars)
_SUFFIX_RE = re.compile(
    r"(?<=[A-Z]{6})"        # must follow exactly 6 uppercase letters
    r"([+#.\-]?[A-Za-z]{0,4}[+#.\-]?)$"
)


def extract_suffix(raw: str) -> str:
    """
    Return the broker suffix appended after the 6-char base, e.g.:
      'BTCUSDZ' → 'Z'
      'EURUSDm' → 'm'
      'XAUUSD+' → '+'
      'BTCUSD'  → ''
    """
    sym = raw.strip().upper()
    # First normalise USDT → USD so the base is always 6 chars
    if sym.endswith("USDT") and len(sym) > 9:
        sym = sym[:-4] + "USD"
    if len(sym) <= 6:
        return ""
    base = sym[:6]
    if base in KNOWN_BASES:
        return raw.strip()[6:]   # return in original case
    return ""


def normalize_for_data(raw: str) -> str:
    """
    Convert any broker-specific symbol variant to Raina AI's canonical internal
    form suitable for yfinance / data providers.

    Examples:
      BTCUSDZ   → BTCUSD
      BTCUSDm   → BTCUSD
      BTCUSDT   → BTCUSD
      XAUUSD+   → XAUUSD
      EURUSDr   → EURUSD
      BTCUSD    → BTCUSD  (unchanged)
      EURUSD    → EURUSD  (unchanged)
    """
    if not raw:
        return raw

    sym = raw.strip().upper()

    # Fast-path: already canonical
    if sym in KNOWN_BASES:
        return sym

    # --- Crypto: USDT suffix → USD -------------------------------------------
    # Handles: BTCUSDT, ETHUSDT, SOLUSDT, ADAUSDT, DOGEUSDT, XRPUSDT, BNBUSDT
    if sym.endswith("USDT"):
        candidate = sym[:-4] + "USD"
        if candidate in KNOWN_BASES:
            logger.debug(f"normalize_for_data: {raw} → {candidate} (USDT→USD)")
            return candidate
        sym = candidate   # continue processing as BTCUSD etc.

    if sym in KNOWN_BASES:
        return sym

    # --- Strip trailing broker qualifier(s) -----------------------------------
    # Try stripping 1, 2, 3 chars from the end in order
    for trim in range(1, 4):
        candidate = sym[:-trim] if len(sym) > trim else sym
        if candidate in KNOWN_BASES:
            logger.debug(f"normalize_for_data: {raw} → {candidate} (stripped {trim} char(s))")
            return candidate

    # --- Special suffixes with non-alpha chars --------------------------------
    cleaned = re.sub(r"[+#.\-]+$", "", sym)  # strip trailing punctuation
    if cleaned != sym and cleaned in KNOWN_BASES:
        logger.debug(f"normalize_for_data: {raw} → {cleaned} (stripped punct)")
        return cleaned

    # --- Fallthrough: return as-is (the yfinance map may still handle it) ----
    logger.debug(f"normalize_for_data: {raw} → {sym} (no base found, returning as-is)")
    return sym


def to_broker_symbol(canonical: str, broker_suffix: Optional[str]) -> str:
    """
    Reconstruct the broker-specific symbol from the canonical form.

    When Raina AI generates a signal for BTCUSD but the user's broker lists
    the asset as BTCUSDZ, MetaAPI must receive BTCUSDZ — otherwise the
    order will be rejected.

    Usage:
      to_broker_symbol("BTCUSD", "Z")   → "BTCUSDZ"
      to_broker_symbol("XAUUSD", "m")   → "XAUUSDm"
      to_broker_symbol("EURUSD", "")    → "EURUSD"
      to_broker_symbol("EURUSD", None)  → "EURUSD"
    """
    if not broker_suffix:
        return canonical
    return canonical + broker_suffix


def detect_broker_suffix(raw_symbol: str) -> str:
    """
    Auto-detect and return the broker suffix from a raw symbol the user typed
    or that was seen on their MT5 terminal.

    This is called once when the user connects their MT5 account so we can
    store it and apply it to all future trade orders automatically.

    Examples:
      'BTCUSDZ'  → 'Z'
      'XAUUSDm'  → 'm'
      'BTCUSDT'  → ''  (USDT is a quoting-currency variant, not a broker suffix)
      'EURUSD+'  → '+'
      'EURUSD'   → ''
    """
    raw = raw_symbol.strip()
    upper = raw.upper()

    # USDT is a currency variant, not a broker suffix
    if upper.endswith("USDT"):
        return ""

    # Normalise to find the 6-char canonical base
    canonical = normalize_for_data(raw)
    if len(raw) > len(canonical):
        # Suffix is whatever comes after the canonical base (preserve original case)
        return raw[len(canonical):]
    return ""

"""
Per-user risk/lot size calculator.
"""
from app.models.mt5 import RiskSettings


# Approximate pip values per standard lot for common assets
_PIP_VALUE_USD: dict[str, float] = {
    "EURUSD": 10.0, "GBPUSD": 10.0, "AUDUSD": 10.0,
    "NZDUSD": 10.0, "USDCAD": 7.7,  "USDCHF": 10.9,
    "USDJPY": 9.1,  "GBPJPY": 9.1,  "EURJPY": 9.1,
    "BTCUSD": 1.0,  "ETHUSD": 1.0,  "BNBUSD": 1.0,
    "SOLUSD": 1.0,  "XRPUSD": 1.0,  "ADAUSD": 1.0,
    "XAUUSD": 10.0, "XAGUSD": 50.0,
    "WTICOUSD": 10.0, "BRENTUSD": 10.0, "NATGAS": 10.0,
}

# SL distance in pips per asset class (used if no signal SL provided)
_DEFAULT_SL_PIPS: dict[str, float] = {
    "EURUSD": 20, "GBPUSD": 25, "AUDUSD": 20, "NZDUSD": 20,
    "USDCAD": 20, "USDCHF": 20, "USDJPY": 20, "GBPJPY": 35,
    "EURJPY": 30, "BTCUSD": 50, "ETHUSD": 30, "XAUUSD": 20,
    "WTICOUSD": 30, "BRENTUSD": 30,
}


def calculate_lot_size(
    asset: str,
    balance: float,
    settings: RiskSettings,
    sl_pips: float | None = None,
) -> float:
    """
    Calculate lot size based on % risk.
    Falls back to 0.01 (micro lot) if balance is unknown.
    """
    if balance <= 0:
        return 0.01

    risk_amount = balance * (settings.risk_percent / 100)
    pip_value = _PIP_VALUE_USD.get(asset.upper(), 10.0)
    sl = sl_pips or _DEFAULT_SL_PIPS.get(asset.upper(), 20.0)

    if sl <= 0:
        return 0.01

    lot = risk_amount / (sl * pip_value)
    # Round to nearest 0.01, clamp between 0.01 and 100
    lot = round(max(0.01, min(lot, 100.0)), 2)
    return lot

"""
MT5 REST API — supports both EA (desktop) and MetaAPI (mobile) modes.

EA endpoints (keyed by api_key, called by the MQL5 EA):
  GET  /mt5/ea/poll/{api_key}  — EA polls for pending orders
  POST /mt5/ea/confirm         — EA confirms trade opened
  POST /mt5/ea/close           — EA reports trade closed
  POST /mt5/ea/heartbeat       — EA sends account state

Website sync endpoints:
  POST /mt5/connect, GET /mt5/account/{id}
  POST /mt5/settings, GET /mt5/settings/{id}
  GET  /mt5/trades/{id}, /mt5/history/{id}, /mt5/performance/{id}
  POST /mt5/scalping/toggle
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.models.mt5 import TradeClose, TradeResult, EAHeartbeat
from app.storage import mt5_repo

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/mt5", tags=["mt5"])


# ── EA endpoints ───────────────────────────────────────────────────────────────

@router.get("/ea/poll/{api_key}")
async def ea_poll(api_key: str):
    account = await mt5_repo.get_account_by_key(api_key)
    if not account:
        raise HTTPException(status_code=404, detail="Unknown api_key")
    orders = await mt5_repo.get_pending_orders(api_key)
    return {"orders": orders}


@router.post("/ea/confirm")
async def ea_confirm(payload: TradeResult):
    if not payload.success:
        await mt5_repo.mark_trade_failed(payload.order_id, payload.error or "EA rejected")
        return {"ok": False}
    await mt5_repo.update_trade_opened(
        payload.api_key, payload.order_id,
        payload.ticket, payload.open_price or 0.0,
    )
    return {"ok": True}


@router.post("/ea/close")
async def ea_close(payload: TradeClose):
    await mt5_repo.close_trade(
        payload.api_key, payload.ticket,
        payload.close_price, payload.profit,
    )
    return {"ok": True}


@router.post("/ea/heartbeat")
async def ea_heartbeat(payload: EAHeartbeat):
    ok = await mt5_repo.update_heartbeat(
        payload.api_key, payload.broker,
        payload.account_number, payload.balance,
        payload.equity, payload.account_mode,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Unknown api_key")
    return {"ok": True}


# ── Account ────────────────────────────────────────────────────────────────────

class ConnectPayload(BaseModel):
    telegram_id: int
    account_mode: str = "demo"

@router.post("/connect")
async def connect(payload: ConnectPayload):
    api_key = await mt5_repo.upsert_mt5_account(payload.telegram_id, payload.account_mode)
    return {"api_key": api_key, "account_mode": payload.account_mode}

# ── MetaAPI (cloud) connect ───────────────────────────────────────────────────

class MetaApiConnectPayload(BaseModel):
    telegram_id: int
    mt5_login: str
    mt5_password: str
    mt5_server: str
    account_mode: str = 'demo'
    name: str = 'RainaAI User'

@router.post('/connect/metaapi')
async def connect_metaapi(payload: MetaApiConnectPayload):
    from app.mt5.metaapi_client import provision_account, get_account_info
    try:
        metaapi_id = await provision_account(
            mt5_login=payload.mt5_login,
            mt5_password=payload.mt5_password,
            mt5_server=payload.mt5_server,
            account_mode=payload.account_mode,
            name=payload.name,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f'MetaAPI provisioning failed: {str(e)}')
    info = await get_account_info(metaapi_id)
    broker_name = info.get('broker') or payload.mt5_server
    account_number = payload.mt5_login
    api_key = await mt5_repo.upsert_mt5_account_full(
        telegram_id=payload.telegram_id,
        account_mode=payload.account_mode,
        metaapi_id=metaapi_id,
        account_number=account_number,
        broker_name=broker_name,
    )
    return {
        'api_key': api_key,
        'metaapi_id': metaapi_id,
        'account_mode': payload.account_mode,
        'broker_name': broker_name,
        'account_number': account_number,
        'balance': info.get('balance'),
        'connected': info.get('connected', False),
    }


@router.get('/account/{telegram_id}')
async def get_account(telegram_id: int):
    account = await mt5_repo.get_mt5_account(telegram_id)
    if not account:
        raise HTTPException(status_code=404, detail="No MT5 account found")
    return account


# ── Settings ──────────────────────────────────────────────────────────────────

class SettingsPayload(BaseModel):
    telegram_id: int
    risk_percent: float = 1.0
    max_open_trades: int = 3
    scalping_enabled: bool = False
    min_confidence: float = 70.0
    daily_loss_limit: float = 5.0

@router.post("/settings")
async def save_settings(payload: SettingsPayload):
    settings = payload.model_dump(exclude={"telegram_id"})
    await mt5_repo.upsert_settings(payload.telegram_id, settings)
    return {"ok": True}

@router.get("/settings/{telegram_id}")
async def get_settings(telegram_id: int):
    return await mt5_repo.get_settings(telegram_id)


# ── Scalping toggle ───────────────────────────────────────────────────────────

class ScalpToggle(BaseModel):
    telegram_id: int

@router.post("/scalping/toggle")
async def toggle_scalping(payload: ScalpToggle):
    settings = await mt5_repo.get_settings(payload.telegram_id)
    settings["scalping_enabled"] = not settings.get("scalping_enabled", False)
    await mt5_repo.upsert_settings(payload.telegram_id, settings)
    return {"scalping_enabled": settings["scalping_enabled"]}


# ── Trades ────────────────────────────────────────────────────────────────────

@router.get("/trades/{telegram_id}")
async def get_trades(telegram_id: int):
    return {"trades": await mt5_repo.get_open_trades(telegram_id)}

@router.get("/history/{telegram_id}")
async def get_history(telegram_id: int, limit: int = 20):
    return {"history": await mt5_repo.get_trade_history(telegram_id, limit=limit)}

@router.get("/performance/{telegram_id}")
async def get_performance(telegram_id: int):
    return await mt5_repo.get_performance_summary(telegram_id)

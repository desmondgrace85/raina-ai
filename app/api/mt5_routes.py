"""
MT5 REST API — supports both EA (desktop) and MetaAPI (mobile) modes.
user_id = mt5_login (broker account number) — no Telegram dependency.

EA endpoints (keyed by api_key, called by the MQL5 EA):
  GET  /mt5/ea/poll/{api_key}  — EA polls for pending orders
  POST /mt5/ea/confirm         — EA confirms trade opened
  POST /mt5/ea/close           — EA reports trade closed
  POST /mt5/ea/heartbeat       — EA sends account state

Website sync endpoints:
  POST /mt5/connect/metaapi    — provision MetaAPI cloud account
  GET  /mt5/account/{user_id}  — poll connection status
  POST /mt5/settings           — save risk settings
  GET  /mt5/settings/{user_id}
  GET  /mt5/trades/{user_id}
  GET  /mt5/history/{user_id}
  GET  /mt5/performance/{user_id}
  POST /mt5/scalping/toggle
"""
import asyncio
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
        payload.api_key, payload.mt5_ticket,
        payload.close_price, payload.profit,
    )
    return {"ok": True}


@router.post("/ea/heartbeat")
async def ea_heartbeat(payload: EAHeartbeat):
    ok = await mt5_repo.update_heartbeat(
        payload.api_key, payload.broker_name,
        payload.account_number, payload.balance,
        payload.equity, payload.account_mode,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Unknown api_key")
    return {"ok": True}


# ── MetaAPI (cloud) connect ───────────────────────────────────────────────────

class MetaApiConnectPayload(BaseModel):
    mt5_login: str
    mt5_password: str
    mt5_server: str
    account_mode: str = 'demo'
    name: str = 'RainaAI User'


@router.post('/connect/metaapi')
async def connect_metaapi(payload: MetaApiConnectPayload):
    """
    Provision the MetaAPI cloud account and return immediately.
    user_id is derived from mt5_login — no Telegram ID needed.
    The frontend polls GET /mt5/account/{user_id} for live status.
    """
    from app.mt5.metaapi_client import provision_account, get_account_info
    user_id = payload.mt5_login  # broker account number is unique per user

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

    api_key = await mt5_repo.upsert_mt5_account_full(
        user_id=user_id,
        account_mode=payload.account_mode,
        metaapi_id=metaapi_id,
        account_number=payload.mt5_login,
        broker_name=payload.mt5_server,
    )

    # Background task: wait for broker sync then update balance/equity
    async def _sync_later():
        try:
            await asyncio.sleep(90)
            info = await get_account_info(metaapi_id)
            if info:
                await mt5_repo.upsert_mt5_account_full(
                    user_id=user_id,
                    account_mode=payload.account_mode,
                    metaapi_id=metaapi_id,
                    account_number=info.get("login") or payload.mt5_login,
                    broker_name=info.get("broker") or payload.mt5_server,
                )
                logger.info(f"MetaAPI account {metaapi_id} sync confirmed for user {user_id}")
        except Exception as ex:
            logger.warning(f"Background MetaAPI sync failed for {user_id}: {ex}")

    asyncio.create_task(_sync_later())

    return {
        'connected': True,
        'user_id': user_id,
        'api_key': api_key,
        'metaapi_id': metaapi_id,
        'broker_name': payload.mt5_server,
        'account_number': payload.mt5_login,
        'account_mode': payload.account_mode,
    }


# ── Account status ─────────────────────────────────────────────────────────────

@router.get('/account/{user_id}')
async def get_account(user_id: str):
    account = await mt5_repo.get_mt5_account(user_id)
    if not account:
        raise HTTPException(status_code=404, detail='Account not found')
    return account


# ── Settings ──────────────────────────────────────────────────────────────────

class SettingsPayload(BaseModel):
    user_id: str
    risk_percent: float = 1.0
    max_open_trades: int = 3
    scalping_enabled: bool = False
    min_confidence: float = 70.0
    daily_loss_limit: float = 5.0


@router.post("/settings")
async def save_settings(payload: SettingsPayload):
    settings = payload.model_dump(exclude={"user_id"})
    await mt5_repo.upsert_settings(payload.user_id, settings)
    return {"ok": True}


@router.get("/settings/{user_id}")
async def get_settings(user_id: str):
    return await mt5_repo.get_settings(user_id)


# ── Scalping toggle ───────────────────────────────────────────────────────────

class ScalpToggle(BaseModel):
    user_id: str


@router.post("/scalping/toggle")
async def toggle_scalping(payload: ScalpToggle):
    settings = await mt5_repo.get_settings(payload.user_id)
    settings["scalping_enabled"] = not settings.get("scalping_enabled", False)
    await mt5_repo.upsert_settings(payload.user_id, settings)
    return {"scalping_enabled": settings["scalping_enabled"]}


# ── Trades ────────────────────────────────────────────────────────────────────

@router.get("/trades/{user_id}")
async def get_trades(user_id: str):
    return {"trades": await mt5_repo.get_open_trades(user_id)}


@router.get("/history/{user_id}")
async def get_history(user_id: str, limit: int = 20):
    return {"history": await mt5_repo.get_trade_history(user_id, limit=limit)}


@router.get("/performance/{user_id}")
async def get_performance(user_id: str):
    return await mt5_repo.get_performance_summary(user_id)

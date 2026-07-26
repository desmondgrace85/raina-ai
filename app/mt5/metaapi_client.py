"""
MetaAPI cloud client — executes trades on users' MT5 accounts
via the internet. No EA or VPS needed by the user.

Balance fetch strategy:
  • wait_synchronized MUST be called with timeout_in_seconds as a keyword
    arg, NOT as a dict — the dict form silently uses a ~5s default.
  • The background poller (_sync_balance_with_retry in mt5_routes.py)
    retries with timeout_in_seconds=90 so Exness/ICMarkets demos have
    enough time to fully deploy their terminal.
  • GET /mt5/account/{id} only returns the stored value (fast path).
    Balance is refreshed by the background poller.
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_TOKEN = os.getenv("METAAPI_TOKEN", "")


def _get_api():
    from metaapi_cloud_sdk import MetaApi
    if not _TOKEN:
        raise RuntimeError("METAAPI_TOKEN not set")
    return MetaApi(_TOKEN)


async def provision_account(
    mt5_login: str,
    mt5_password: str,
    mt5_server: str,
    account_mode: str = "demo",
    name: str = "RainaAI User",
) -> str:
    """
    Register a user's MT5 account with MetaAPI.
    Returns the MetaAPI account ID.
    """
    api = _get_api()
    try:
        account = await api.metatrader_account_api.create_account({
            "name": name,
            "type": "cloud",
            "login": mt5_login,
            "password": mt5_password,
            "server": mt5_server,
            "platform": "mt5",
            "magic": 77777,
        })
        logger.info(f"Provisioned MetaAPI account {account.id} for login {mt5_login}")
        return account.id
    except Exception as e:
        err = str(e).lower()
        if "already" in err or "duplicate" in err or "exists" in err:
            try:
                accounts = await api.metatrader_account_api.get_accounts_with_infinite_scroll_pagination({})
                for acc in accounts.get("items", []):
                    if acc.login == mt5_login and acc.server == mt5_server:
                        logger.info(f"Re-using existing MetaAPI account {acc.id}")
                        return acc.id
            except Exception:
                pass
        raise


async def get_account_info(
    metaapi_id: str,
    sync_timeout_seconds: int = 60,
) -> dict:
    """
    Get live account balance/equity from MetaAPI via RPC connection.

    IMPORTANT: wait_synchronized MUST use the timeout_in_seconds keyword arg.
    Passing a dict like {"timeoutInSeconds": N} silently falls back to the SDK
    default (~5 seconds) and always times out on Exness/ICMarkets demo accounts.

    sync_timeout_seconds = 90 is the right value for background polling.
    Exness demo terminals typically need 30-90 s to fully deploy.
    """
    api = None
    conn = None
    try:
        api = _get_api()
        account = await api.metatrader_account_api.get_account(metaapi_id)

        state = getattr(account, "state", None)
        logger.info(f"[metaapi] account {metaapi_id} state={state}")

        # Deploy if needed — UNDEPLOYED/UNDEPLOYING happen after Railway restarts
        if state not in ("DEPLOYED", "DEPLOYING"):
            logger.info(f"[metaapi] deploying account {metaapi_id} (state was '{state}')…")
            await account.deploy()
            # Wait up to 90 s for the deploy to complete
            import asyncio
            for _ in range(18):
                await asyncio.sleep(5)
                refreshed = await api.metatrader_account_api.get_account(metaapi_id)
                state = getattr(refreshed, "state", None)
                logger.info(f"[metaapi] post-deploy state={state}")
                if state == "DEPLOYED":
                    account = refreshed
                    break
            if state != "DEPLOYED":
                return {
                    "connected": False,
                    "error": f"Account still in state '{state}' after deploy attempt",
                }

        # Open RPC connection with correct timeout keyword arg
        conn = account.get_rpc_connection()
        await conn.connect()

        try:
            # *** CRITICAL: use keyword arg, not a dict ***
            await conn.wait_synchronized(timeout_in_seconds=sync_timeout_seconds)
            info = await conn.get_account_information()

            # get_account_information() returns a dict OR an object — handle both
            if hasattr(info, "get"):
                balance  = info.get("balance")
                equity   = info.get("equity")
                broker   = info.get("broker")
                server   = info.get("server")
                login    = info.get("login")
                currency = info.get("currency")
            else:
                balance  = getattr(info, "balance", None)
                equity   = getattr(info, "equity", None)
                broker   = getattr(info, "broker", None)
                server   = getattr(info, "server", None)
                login    = getattr(info, "login", None)
                currency = getattr(info, "currency", None)

            result = {
                "balance": balance,
                "equity": equity,
                "broker": broker,
                "server": server,
                "login": login,
                "currency": currency,
                "connected": True,
            }
            logger.info(
                f"[metaapi] ✓ sync OK for {metaapi_id}: "
                f"balance={balance} currency={currency} broker={broker}"
            )
            return result

        finally:
            try:
                await conn.close()
            except Exception:
                pass

    except Exception as e:
        logger.warning(
            f"[metaapi] get_account_info FAILED for {metaapi_id} "
            f"(timeout={sync_timeout_seconds}s): {type(e).__name__}: {e}"
        )
        return {"connected": False, "error": str(e)}


async def place_trade(
    metaapi_id: str,
    symbol: str,
    direction: str,
    lot_size: float,
    stop_loss: Optional[float],
    take_profit: Optional[float],
) -> dict:
    """
    Place a market order on the user's MT5 account via MetaAPI.
    """
    conn = None
    try:
        api = _get_api()
        account = await api.metatrader_account_api.get_account(metaapi_id)
        conn = account.get_rpc_connection()
        await conn.connect()

        try:
            # 30 s is enough for trade execution — terminal is already deployed
            await conn.wait_synchronized(timeout_in_seconds=30)

            kwargs = {"volume": lot_size, "comment": "RainaAI"}
            if stop_loss:
                kwargs["stopLoss"] = stop_loss
            if take_profit:
                kwargs["takeProfit"] = take_profit

            if direction == "BUY":
                result = await conn.create_market_buy_order(symbol, **kwargs)
            else:
                result = await conn.create_market_sell_order(symbol, **kwargs)
        finally:
            try:
                await conn.close()
            except Exception:
                pass

        if result.get("numericCode") == 10009:  # TRADE_RETCODE_DONE
            return {
                "success": True,
                "ticket": result.get("orderId"),
                "open_price": result.get("openPrice"),
            }
        return {"success": False, "error": result.get("stringCode", "unknown")}

    except Exception as e:
        logger.error(f"[metaapi] place_trade failed for {metaapi_id}: {type(e).__name__}: {e}")
        return {"success": False, "error": str(e)}


async def close_trade(metaapi_id: str, ticket: str) -> dict:
    """Close a specific position by ticket."""
    conn = None
    try:
        api = _get_api()
        account = await api.metatrader_account_api.get_account(metaapi_id)
        conn = account.get_rpc_connection()
        await conn.connect()
        try:
            await conn.wait_synchronized(timeout_in_seconds=30)
            result = await conn.close_position(ticket)
        finally:
            try:
                await conn.close()
            except Exception:
                pass
        return {"success": result.get("numericCode") == 10009}
    except Exception as e:
        logger.error(f"[metaapi] close_trade failed: {type(e).__name__}: {e}")
        return {"success": False, "error": str(e)}


async def remove_account(metaapi_id: str) -> None:
    """Remove a MetaAPI account (called when user disconnects)."""
    try:
        api = _get_api()
        account = await api.metatrader_account_api.get_account(metaapi_id)
        await account.undeploy()
        await account.remove()
    except Exception as e:
        logger.warning(f"[metaapi] remove_account failed: {type(e).__name__}: {e}")

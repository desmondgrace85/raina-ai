"""
MetaAPI cloud client — executes trades on users' MT5 accounts
via the internet. No EA or VPS needed by the user.

Each user connects their MT5 credentials once. MetaAPI provisions
a cloud terminal that stays connected to their broker 24/7.
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
        # If account already exists, try to find it by querying accounts list
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


async def get_account_info(metaapi_id: str) -> dict:
    """Get live account balance/equity from MetaAPI."""
    try:
        api = _get_api()
        account = await api.metatrader_account_api.get_account(metaapi_id)
        await account.deploy()
        conn = account.get_rpc_connection()
        await conn.connect()
        await conn.wait_synchronized({"timeoutInSeconds": 30})
        info = await conn.get_account_information()
        await conn.close()
        return {
            "balance": info.get("balance"),
            "equity": info.get("equity"),
            "broker": info.get("broker"),
            "server": info.get("server"),
            "connected": True,
        }
    except Exception as e:
        logger.warning(f"get_account_info failed for {metaapi_id}: {e}")
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
    Returns dict with success, ticket, openPrice, error.
    """
    try:
        api = _get_api()
        account = await api.metatrader_account_api.get_account(metaapi_id)
        conn = account.get_rpc_connection()
        await conn.connect()
        await conn.wait_synchronized({"timeoutInSeconds": 30})

        kwargs = {"volume": lot_size, "comment": "RainaAI"}
        if stop_loss:
            kwargs["stopLoss"] = stop_loss
        if take_profit:
            kwargs["takeProfit"] = take_profit

        if direction == "BUY":
            result = await conn.create_market_buy_order(symbol, **kwargs)
        else:
            result = await conn.create_market_sell_order(symbol, **kwargs)

        await conn.close()

        if result.get("numericCode") == 10009:  # TRADE_RETCODE_DONE
            return {
                "success": True,
                "ticket": result.get("orderId"),
                "open_price": result.get("openPrice"),
            }
        return {"success": False, "error": result.get("stringCode", "unknown")}
    except Exception as e:
        logger.error(f"place_trade failed for {metaapi_id}: {e}")
        return {"success": False, "error": str(e)}


async def close_trade(metaapi_id: str, ticket: str) -> dict:
    """Close a specific position by ticket."""
    try:
        api = _get_api()
        account = await api.metatrader_account_api.get_account(metaapi_id)
        conn = account.get_rpc_connection()
        await conn.connect()
        await conn.wait_synchronized({"timeoutInSeconds": 30})
        result = await conn.close_position(ticket)
        await conn.close()
        return {"success": result.get("numericCode") == 10009}
    except Exception as e:
        logger.error(f"close_trade failed: {e}")
        return {"success": False, "error": str(e)}


async def remove_account(metaapi_id: str) -> None:
    """Remove a MetaAPI account (called when user disconnects)."""
    try:
        api = _get_api()
        account = await api.metatrader_account_api.get_account(metaapi_id)
        await account.undeploy()
        await account.remove()
    except Exception as e:
        logger.warning(f"remove_account failed: {e}")

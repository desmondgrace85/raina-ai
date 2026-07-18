"""
Trade execution — dual mode:
  MetaAPI users → cloud execution (mobile)
  EA users      → queue trade for EA to poll (desktop)
"""
import logging
from app.models.mt5 import TradeDirection, TradeOrder, RiskSettings
from app.models.signal import Signal
from app.mt5.risk_calculator import calculate_lot_size
from app.storage import mt5_repo

logger = logging.getLogger(__name__)


async def queue_signal_for_all(signal: Signal) -> int:
    """Execute or queue a signal for every eligible premium user."""
    if signal.direction.value == "HOLD":
        return 0

    users = await mt5_repo.get_scalping_users()
    executed = 0

    for user in users:
        settings = RiskSettings(**user["settings"])
        if signal.confidence < settings.min_confidence:
            continue
        if await mt5_repo.daily_loss_exceeded(user["telegram_id"], settings):
            continue
        if await mt5_repo.open_trade_count(user["telegram_id"]) >= settings.max_open_trades:
            continue

        balance = user.get("balance") or 1000.0
        sl_pips = None
        if signal.stop_loss and signal.entry_zone:
            mid = sum(signal.entry_zone) / 2
            sl_pips = abs(mid - signal.stop_loss) / 0.0001
        lot = calculate_lot_size(signal.asset, balance, settings, sl_pips)
        tp  = signal.take_profit[0] if signal.take_profit else None

        order = TradeOrder(
            telegram_id=user["telegram_id"],
            api_key=user.get("api_key", ""),
            asset=signal.asset,
            direction=TradeDirection(signal.direction.value),
            lot_size=lot,
            entry_price=(sum(signal.entry_zone)/2 if signal.entry_zone else None),
            stop_loss=signal.stop_loss,
            take_profit=tp,
            confidence=signal.confidence,
            timeframe=signal.timeframe,
        )
        order_id = await mt5_repo.insert_trade_order(order)

        metaapi_id = user.get("metaapi_id")

        if metaapi_id:
            # Mobile path — MetaAPI cloud execution
            try:
                from app.mt5.metaapi_client import place_trade
                result = await place_trade(
                    metaapi_id=metaapi_id,
                    symbol=signal.asset,
                    direction=signal.direction.value,
                    lot_size=lot,
                    stop_loss=signal.stop_loss,
                    take_profit=tp,
                )
                if result.get("success"):
                    await mt5_repo.update_trade_opened(
                        user.get("api_key", ""), order_id,
                        result["ticket"], result.get("open_price", 0),
                    )
                    executed += 1
                    logger.info(f"[MetaAPI] Trade opened for {user['telegram_id']}: {signal.asset} {signal.direction.value}")
                else:
                    await mt5_repo.mark_trade_failed(order_id, result.get("error", ""))
            except Exception as e:
                logger.error(f"[MetaAPI] Error for {user['telegram_id']}: {e}")
        else:
            # Desktop path — mark as pending, EA will poll and pick it up
            logger.info(f"[EA] Queued trade for {user['telegram_id']}: {signal.asset} {signal.direction.value}")
            executed += 1  # counted as queued

    return executed


async def confirm_trade(api_key: str, order_id: int, ticket: int, open_price: float) -> bool:
    """Called when the desktop EA confirms it opened a trade."""
    return await mt5_repo.update_trade_opened(api_key, order_id, ticket, open_price)


async def close_trade(api_key: str, ticket: int, close_price: float, profit: float) -> bool:
    """Called when the desktop EA reports a trade closed."""
    return await mt5_repo.close_trade(api_key, ticket, close_price, profit)


async def cancel_pending_trades(telegram_id: int) -> int:
    """Cancel all pending/queued trades for a user."""
    return await mt5_repo.cancel_user_pending_trades(telegram_id)

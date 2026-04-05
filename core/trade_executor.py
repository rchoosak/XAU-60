"""
Trade Executor for order management.
"""
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from datetime import datetime
import time
from loguru import logger

from .mt5_connector import MT5Connector
from .strategy_base import Signal, TradeSignal, Position
from .risk_manager import RiskManager
from utils.trade_journal import append_trade_event


@dataclass
class TradeRecord:
    """Record of an executed trade."""
    ticket: int
    symbol: str
    signal: Signal
    entry_price: float
    stop_loss: float
    take_profit: float
    lot_size: float
    strategy: str
    magic_number: int
    open_time: datetime
    close_time: Optional[datetime] = None
    close_price: Optional[float] = None
    profit: Optional[float] = None
    status: str = "OPEN"
    context: Dict[str, Any] = field(default_factory=dict)


class TradeExecutor:
    """
    Executes and manages trades.

    Handles:
    - Trade execution from signals
    - Position management (trailing stops, partial closes)
    - Trade tracking and history
    """

    def __init__(
        self,
        mt5: MT5Connector,
        risk_manager: RiskManager,
        default_magic: int = 123456,
        default_lot_size: float = 0.01,
        slippage: int = 10,
        min_rr: float = 1.0
    ):
        """
        Initialize trade executor.

        Args:
            mt5: MT5 connector instance
            risk_manager: Risk manager instance
            default_magic: Default magic number for orders
            default_lot_size: Default lot size from .env (fallback)
            slippage: Maximum slippage in points from .env
            min_rr: Minimum risk/reward ratio required for signals
        """
        self.mt5 = mt5
        self.risk_manager = risk_manager
        self.default_magic = default_magic
        self.default_lot_size = default_lot_size
        self.slippage = slippage
        self.min_rr = min_rr

        self._trade_history: List[TradeRecord] = []
        self._active_trades: Dict[int, TradeRecord] = {}
        self._warning_last_ts: Dict[str, float] = {}
        self._warning_last_msg: Dict[str, str] = {}

    def execute_signal(
        self,
        signal: TradeSignal,
        strategy_name: str = "",
        strategy_risk: Optional[Dict[str, Any]] = None,
        decision_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """
        Execute a trade signal.

        Args:
            signal: Trade signal to execute
            strategy_name: Name of the strategy generating the signal

        Returns:
            Ticket number if successful, None otherwise
        """
        if signal.signal == Signal.HOLD:
            return None

        # Validate signal meets minimum risk/reward
        if not signal.validate(self.min_rr):
            self._warn_throttled("invalid_rr", f"Signal RR below minimum ({self.min_rr}): {signal.risk_reward_ratio}", cooldown_seconds=15.0)
            return None

        # Check if we can open a trade
        can_trade, reason = self.risk_manager.can_open_trade(
            signal.symbol, signal.signal
        )
        if not can_trade:
            self._warn_throttled("cannot_open_trade", f"Cannot open trade: {reason}", cooldown_seconds=15.0)
            return None

        # Validate the signal
        is_valid, reason = self.risk_manager.validate_trade_signal(
            signal.symbol,
            signal.signal,
            signal.entry_price,
            signal.stop_loss,
            signal.take_profit,
        )
        if not is_valid:
            self._warn_throttled("invalid_trade_signal", f"Invalid trade signal: {reason}", cooldown_seconds=15.0)
            return None

        # Calculate lot size: strategy YAML > .env DEFAULT_LOT_SIZE > risk-based
        strategy_risk = strategy_risk or {}
        strategy_max_risk = strategy_risk.get("max_risk_percent")
        strategy_capital_base = strategy_risk.get("capital_base")
        risk_percent = (
            float(strategy_max_risk)
            if strategy_max_risk is not None and str(strategy_max_risk) != ""
            else None
        )
        capital_base = (
            float(strategy_capital_base)
            if strategy_capital_base is not None and str(strategy_capital_base) != ""
            else None
        )

        lot_size = signal.lot_size
        if lot_size <= 0:
            lot_size = self.default_lot_size  # Fallback to .env DEFAULT_LOT_SIZE
        if lot_size <= 0:
            sl_pips = self._calculate_sl_pips(
                signal.symbol, signal.entry_price, signal.stop_loss
            )
            lot_size = self.risk_manager.calculate_lot_size(
                signal.symbol,
                sl_pips,
                risk_percent=risk_percent,
                capital_base=capital_base,
            )

        # Execute the trade
        magic = signal.magic_number if signal.magic_number > 0 else self.default_magic
        comment = f"{strategy_name}:{signal.comment}" if strategy_name else signal.comment

        success, ticket = self.mt5.place_market_order(
            symbol=signal.symbol,
            order_type=signal.signal,
            volume=lot_size,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            magic=magic,
            comment=comment[:31],  # MT5 comment limit
            slippage=self.slippage,
        )

        if success:
            context = self._sanitize_context(decision_context)
            open_time = self._current_market_time(signal.symbol)
            # Record the trade
            record = TradeRecord(
                ticket=ticket,
                symbol=signal.symbol,
                signal=signal.signal,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                lot_size=lot_size,
                strategy=strategy_name,
                magic_number=magic,
                open_time=open_time,
                context=context,
            )
            self._active_trades[ticket] = record

            logger.info(
                f"Trade opened: {signal.signal.name} {lot_size} {signal.symbol} "
                f"@ {signal.entry_price} | SL: {signal.stop_loss} | TP: {signal.take_profit}"
            )
            append_trade_event({
                "mode": self._journal_mode(),
                "event": "OPEN",
                "ticket": ticket,
                "symbol": signal.symbol,
                "strategy": strategy_name or "unknown",
                "side": signal.signal.name,
                "entry_price": signal.entry_price,
                "stop_loss": signal.stop_loss,
                "take_profit": signal.take_profit,
                "lot_size": lot_size,
                "magic_number": magic,
                "comment": signal.comment,
                "reason": "Signal execution",
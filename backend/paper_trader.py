import math
import threading
from datetime import datetime, timezone
from typing import Any

from database import (
    delete_position,
    load_cash,
    load_portfolio_history,
    load_positions,
    load_trades,
    save_cash,
    save_portfolio_snapshot,
    save_position,
    save_trade,
)
from risk_manager import RiskManager


DEFAULT_STARTING_CASH = 100_000.0


class PaperTrader:
    """
    Manage a paper-trading account.

    The class handles:

    - Cash balances
    - Open positions
    - Buy and sell orders
    - Average entry prices
    - Trade history
    - Portfolio-value history
    - Risk-management rules
    - SQLite persistence
    """

    def __init__(
        self,
        risk_manager: RiskManager | None = None,
    ) -> None:
        self._lock = threading.RLock()

        self.starting_cash = DEFAULT_STARTING_CASH
        self.risk_manager = risk_manager or RiskManager()

        self.cash = self._load_safe_cash()

        self.positions = self._load_saved_positions()

        # Until live prices are refreshed, use the saved
        # average entry price for each position.
        self.current_prices: dict[str, float] = {
            symbol: float(position["entry_price"])
            for symbol, position in self.positions.items()
        }

        self.history = self._load_trade_history()
        self.portfolio_history = (
            self._load_portfolio_history()
        )

        # Create an initial snapshot only for a new database.
        if not self.portfolio_history:
            self.record_portfolio_value()

    # =====================================================
    # Trading
    # =====================================================

    def buy(
        self,
        symbol: Any,
        shares: Any,
        price: Any,
    ) -> dict[str, Any]:
        """
        Buy shares after validating cash and risk limits.
        """
        normalized_symbol = self.clean_symbol(symbol)
        normalized_shares = self._positive_integer(shares)
        normalized_price = self._positive_number(price)

        if not normalized_symbol:
            return self._error(
                "Enter a valid stock symbol."
            )

        if normalized_shares is None:
            return self._error(
                "Shares must be a whole number greater "
                "than zero."
            )

        if normalized_price is None:
            return self._error(
                "Price must be a number greater than zero."
            )

        normalized_price = round(
            normalized_price,
            2,
        )

        total_cost = round(
            normalized_shares * normalized_price,
            2,
        )

        with self._lock:
            if total_cost > self.cash:
                return self._error(
                    "Not enough cash for this trade."
                )

            existing_position = self.positions.get(
                normalized_symbol
            )

            existing_shares = 0

            if existing_position is not None:
                existing_shares = int(
                    existing_position["shares"]
                )

            projected_shares = (
                existing_shares + normalized_shares
            )

            portfolio_value = (
                self.calculate_portfolio_value()
            )

            risk_approved, risk_message = (
                self.risk_manager.validate_trade(
                    portfolio_value=portfolio_value,
                    shares=projected_shares,
                    price=normalized_price,
                )
            )

            if not risk_approved:
                return self._error(risk_message)

            if existing_position is not None:
                old_average_price = float(
                    existing_position["entry_price"]
                )

                new_average_price = (
                    (
                        existing_shares
                        * old_average_price
                    )
                    + (
                        normalized_shares
                        * normalized_price
                    )
                ) / projected_shares

                updated_position = {
                    "symbol": normalized_symbol,
                    "shares": projected_shares,
                    "entry_price": round(
                        new_average_price,
                        2,
                    ),
                }

            else:
                updated_position = {
                    "symbol": normalized_symbol,
                    "shares": normalized_shares,
                    "entry_price": normalized_price,
                }

            updated_cash = round(
                self.cash - total_cost,
                2,
            )

            timestamp = self.current_time()

            trade = {
                "action": "BUY",
                "symbol": normalized_symbol,
                "shares": normalized_shares,
                "price": normalized_price,
                "total": total_cost,
                "time": timestamp,
            }

            try:
                save_trade(
                    symbol=normalized_symbol,
                    shares=normalized_shares,
                    price=normalized_price,
                    action="BUY",
                    timestamp=timestamp,
                )

                save_cash(updated_cash)

                save_position(
                    symbol=normalized_symbol,
                    shares=updated_position["shares"],
                    average_cost=updated_position[
                        "entry_price"
                    ],
                )

            except Exception as error:
                print(
                    f"Could not save BUY order for "
                    f"{normalized_symbol}: {error}"
                )

                return self._error(
                    "The trade could not be saved."
                )

            self.cash = updated_cash

            self.positions[
                normalized_symbol
            ] = updated_position

            self.current_prices[
                normalized_symbol
            ] = normalized_price

            self.history.append(trade)

            self._record_snapshot_safely()

            return self._success(
                f"Bought {normalized_shares} share(s) of "
                f"{normalized_symbol} at "
                f"${normalized_price:.2f}."
            )

    def sell(
        self,
        symbol: Any,
        shares: Any,
        price: Any,
    ) -> dict[str, Any]:
        """
        Sell shares currently owned by the account.
        """
        normalized_symbol = self.clean_symbol(symbol)
        normalized_shares = self._positive_integer(shares)
        normalized_price = self._positive_number(price)

        if not normalized_symbol:
            return self._error(
                "Enter a valid stock symbol."
            )

        if normalized_shares is None:
            return self._error(
                "Shares must be a whole number greater "
                "than zero."
            )

        if normalized_price is None:
            return self._error(
                "Price must be a number greater than zero."
            )

        normalized_price = round(
            normalized_price,
            2,
        )

        with self._lock:
            position = self.positions.get(
                normalized_symbol
            )

            if position is None:
                return self._error(
                    f"You do not own {normalized_symbol}."
                )

            owned_shares = int(
                position["shares"]
            )

            if normalized_shares > owned_shares:
                return self._error(
                    f"You only own {owned_shares} share(s) "
                    f"of {normalized_symbol}."
                )

            entry_price = float(
                position["entry_price"]
            )

            remaining_shares = (
                owned_shares - normalized_shares
            )

            position_closed = remaining_shares == 0

            sale_total = round(
                normalized_shares * normalized_price,
                2,
            )

            realized_profit = round(
                (
                    normalized_price
                    - entry_price
                )
                * normalized_shares,
                2,
            )

            updated_cash = round(
                self.cash + sale_total,
                2,
            )

            timestamp = self.current_time()

            trade = {
                "action": "SELL",
                "symbol": normalized_symbol,
                "shares": normalized_shares,
                "price": normalized_price,
                "profit": realized_profit,
                "total": sale_total,
                "time": timestamp,
            }

            try:
                save_trade(
                    symbol=normalized_symbol,
                    shares=normalized_shares,
                    price=normalized_price,
                    action="SELL",
                    timestamp=timestamp,
                )

                save_cash(updated_cash)

                if position_closed:
                    delete_position(
                        normalized_symbol
                    )
                else:
                    save_position(
                        symbol=normalized_symbol,
                        shares=remaining_shares,
                        average_cost=entry_price,
                    )

            except Exception as error:
                print(
                    f"Could not save SELL order for "
                    f"{normalized_symbol}: {error}"
                )

                return self._error(
                    "The trade could not be saved."
                )

            self.cash = updated_cash
            self.history.append(trade)

            if position_closed:
                self.positions.pop(
                    normalized_symbol,
                    None,
                )

                self.current_prices.pop(
                    normalized_symbol,
                    None,
                )

            else:
                position["shares"] = remaining_shares

                self.current_prices[
                    normalized_symbol
                ] = normalized_price

            self._record_snapshot_safely()

            profit_sign = (
                "+" if realized_profit > 0 else ""
            )

            return self._success(
                f"Sold {normalized_shares} share(s) of "
                f"{normalized_symbol} at "
                f"${normalized_price:.2f}. "
                f"Profit/Loss: "
                f"{profit_sign}${realized_profit:.2f}"
            )

    # =====================================================
    # Risk management
    # =====================================================

    def get_trade_plan(
        self,
        symbol: Any,
        entry_price: Any,
    ) -> dict[str, Any]:
        """
        Return suggested position sizing, stop-loss,
        take-profit, and risk/reward information.
        """
        normalized_symbol = self.clean_symbol(symbol)
        normalized_price = self._positive_number(
            entry_price
        )

        if not normalized_symbol:
            return self._error(
                "Enter a valid stock symbol."
            )

        if normalized_price is None:
            return self._error(
                "Entry price must be greater than zero."
            )

        normalized_price = round(
            normalized_price,
            2,
        )

        with self._lock:
            portfolio_value = (
                self.calculate_portfolio_value()
            )

            stop_loss = (
                self.risk_manager.calculate_stop_loss(
                    normalized_price
                )
            )

            take_profit = (
                self.risk_manager.calculate_take_profit(
                    normalized_price
                )
            )

            recommended_shares = (
                self.risk_manager.calculate_position_size(
                    portfolio_value=portfolio_value,
                    entry_price=normalized_price,
                    stop_loss_price=stop_loss,
                )
            )

            affordable_shares = int(
                self.cash / normalized_price
            )

            recommended_shares = min(
                recommended_shares,
                affordable_shares,
            )

            risk_reward = (
                self.risk_manager.risk_reward_ratio(
                    entry_price=normalized_price,
                    stop_loss_price=stop_loss,
                    take_profit_price=take_profit,
                )
            )

            maximum_position_value = (
                self.risk_manager.max_position_size(
                    portfolio_value
                )
            )

            return {
                "success": True,
                "symbol": normalized_symbol,
                "entry_price": normalized_price,
                "recommended_shares": max(
                    recommended_shares,
                    0,
                ),
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "risk_reward_ratio": risk_reward,
                "maximum_position_value": round(
                    maximum_position_value,
                    2,
                ),
                "available_cash": round(
                    self.cash,
                    2,
                ),
                "portfolio_value": round(
                    portfolio_value,
                    2,
                ),
            }

    # =====================================================
    # Account information
    # =====================================================

    def account(self) -> dict[str, Any]:
        """
        Return account balances, positions, trade history, and
        portfolio-performance metrics.
        """
        with self._lock:
            positions_list: list[dict[str, Any]] = []

            invested_value = 0.0
            total_cost_basis = 0.0
            total_unrealized_profit = 0.0

            for symbol in sorted(self.positions):
                position = self.positions[symbol]
                shares = int(position["shares"])
                entry_price = float(position["entry_price"])
                current_price = self._valid_market_price(
                    self.current_prices.get(symbol),
                    fallback=entry_price,
                )

                cost_basis = round(shares * entry_price, 2)
                position_value = round(shares * current_price, 2)
                unrealized_profit = round(position_value - cost_basis, 2)
                unrealized_profit_percent = (
                    round((unrealized_profit / cost_basis) * 100, 2)
                    if cost_basis > 0
                    else 0.0
                )

                invested_value += position_value
                total_cost_basis += cost_basis
                total_unrealized_profit += unrealized_profit

                stop_loss = self.risk_manager.calculate_stop_loss(entry_price)
                take_profit = self.risk_manager.calculate_take_profit(entry_price)

                positions_list.append({
                    "symbol": symbol,
                    "shares": shares,
                    "entry_price": round(entry_price, 2),
                    "current_price": round(current_price, 2),
                    "cost_basis": cost_basis,
                    "position_value": position_value,
                    "unrealized_profit": unrealized_profit,
                    "unrealized_profit_percent": unrealized_profit_percent,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                })

            invested_value = round(invested_value, 2)
            total_cost_basis = round(total_cost_basis, 2)
            total_unrealized_profit = round(total_unrealized_profit, 2)
            portfolio_value = round(self.cash + invested_value, 2)
            profit_loss = round(portfolio_value - self.starting_cash, 2)
            profit_loss_percent = (
                round((profit_loss / self.starting_cash) * 100, 2)
                if self.starting_cash > 0
                else 0.0
            )

            realized_profit_loss = self._calculate_realized_profit_loss()
            trade_stats = self._calculate_trade_statistics()
            performance = self._calculate_performance_summary(
                current_value=portfolio_value
            )

            cash_percent = (
                round((self.cash / portfolio_value) * 100, 2)
                if portfolio_value > 0
                else 0.0
            )
            invested_percent = (
                round((invested_value / portfolio_value) * 100, 2)
                if portfolio_value > 0
                else 0.0
            )

            return {
                "starting_cash": round(self.starting_cash, 2),
                "cash": round(self.cash, 2),
                "cash_percent": cash_percent,
                "invested_value": invested_value,
                "invested_percent": invested_percent,
                "total_cost_basis": total_cost_basis,
                "portfolio_value": portfolio_value,
                "profit_loss": profit_loss,
                "profit_loss_percent": profit_loss_percent,
                "total_return_percent": profit_loss_percent,
                "realized_profit_loss": realized_profit_loss,
                "unrealized_profit_loss": total_unrealized_profit,
                "winning_trades": trade_stats["winning_trades"],
                "losing_trades": trade_stats["losing_trades"],
                "break_even_trades": trade_stats["break_even_trades"],
                "closed_trades": trade_stats["closed_trades"],
                "win_rate": trade_stats["win_rate"],
                "positions": positions_list,
                "history": [trade.copy() for trade in self.history],
                "performance": performance,
                "risk_settings": {
                    "max_position_percent": self.risk_manager.settings.max_position_percent,
                    "risk_per_trade_percent": self.risk_manager.settings.risk_per_trade_percent,
                    "stop_loss_percent": self.risk_manager.settings.stop_loss_percent,
                    "take_profit_percent": self.risk_manager.settings.take_profit_percent,
                },
            }

    def _calculate_realized_profit_loss(self) -> float:
        """Sum realized profit/loss from completed SELL trades."""
        realized_profit_loss = 0.0
        for trade in self.history:
            if str(trade.get("action", "")).upper() != "SELL":
                continue
            profit = self._finite_number(trade.get("profit"))
            if profit is not None:
                realized_profit_loss += profit
        return round(realized_profit_loss, 2)

    def _calculate_trade_statistics(self) -> dict[str, Any]:
        """Return win/loss statistics for completed SELL trades."""
        winning_trades = 0
        losing_trades = 0
        break_even_trades = 0

        for trade in self.history:
            if str(trade.get("action", "")).upper() != "SELL":
                continue
            profit = self._finite_number(trade.get("profit"))
            if profit is None:
                continue
            if profit > 0:
                winning_trades += 1
            elif profit < 0:
                losing_trades += 1
            else:
                break_even_trades += 1

        decided_trades = winning_trades + losing_trades
        win_rate = (
            round((winning_trades / decided_trades) * 100, 2)
            if decided_trades > 0
            else 0.0
        )

        return {
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "break_even_trades": break_even_trades,
            "closed_trades": winning_trades + losing_trades + break_even_trades,
            "win_rate": win_rate,
        }

    def _calculate_performance_summary(self, current_value: float) -> dict[str, Any]:
        """Return high-level portfolio performance information."""
        values: list[float] = []
        for snapshot in self.portfolio_history:
            value = self._non_negative_number(snapshot.get("value"))
            if value is not None:
                values.append(value)
        values.append(max(float(current_value), 0.0))

        highest_value = max(values)
        lowest_value = min(values)

        return {
            "starting_value": round(self.starting_cash, 2),
            "current_value": round(current_value, 2),
            "highest_value": round(highest_value, 2),
            "lowest_value": round(lowest_value, 2),
            "net_change": round(current_value - self.starting_cash, 2),
            "return_percent": (
                round(((current_value - self.starting_cash) / self.starting_cash) * 100, 2)
                if self.starting_cash > 0
                else 0.0
            ),
            "snapshot_count": len(self.portfolio_history),
        }

    def calculate_portfolio_value(self) -> float:
        """
        Calculate cash plus the current value of all
        open positions.
        """
        with self._lock:
            positions_value = 0.0

            for symbol, position in self.positions.items():
                entry_price = float(
                    position["entry_price"]
                )

                current_price = self._valid_market_price(
                    self.current_prices.get(symbol),
                    fallback=entry_price,
                )

                positions_value += (
                    int(position["shares"])
                    * current_price
                )

            return round(
                self.cash + positions_value,
                2,
            )

    def update_current_price(
        self,
        symbol: Any,
        price: Any,
    ) -> bool:
        """
        Update the latest known price for an open position.

        Returns True when the price was accepted.
        """
        normalized_symbol = self.clean_symbol(symbol)
        normalized_price = self._positive_number(price)

        if (
            not normalized_symbol
            or normalized_price is None
        ):
            return False

        with self._lock:
            if normalized_symbol not in self.positions:
                return False

            self.current_prices[
                normalized_symbol
            ] = round(
                normalized_price,
                2,
            )

        return True

    # =====================================================
    # Portfolio history
    # =====================================================

    def get_portfolio_history(
        self,
    ) -> list[dict[str, Any]]:
        """
        Return a copy so callers cannot modify internal data.
        """
        with self._lock:
            return [
                snapshot.copy()
                for snapshot in self.portfolio_history
            ]

    def record_portfolio_value(self) -> None:
        """
        Save the current account value as a portfolio snapshot.
        """
        with self._lock:
            timestamp = self.current_time()

            portfolio_value = (
                self.calculate_portfolio_value()
            )

            snapshot = {
                "time": timestamp,
                "value": portfolio_value,
            }

            save_portfolio_snapshot(
                timestamp=timestamp,
                value=portfolio_value,
            )

            self.portfolio_history.append(
                snapshot
            )

    def _record_snapshot_safely(self) -> None:
        """
        Record a snapshot without reversing an otherwise
        successful trade if snapshot saving fails.
        """
        try:
            self.record_portfolio_value()
        except Exception as error:
            print(
                "Could not save portfolio snapshot: "
                f"{error}"
            )

    # =====================================================
    # Database loading
    # =====================================================

    def _load_safe_cash(self) -> float:
        """
        Load a safe, non-negative cash value.
        """
        try:
            cash = float(
                load_cash(self.starting_cash)
            )
        except (TypeError, ValueError):
            return self.starting_cash

        if not math.isfinite(cash) or cash < 0:
            return self.starting_cash

        return round(cash, 2)

    def _load_saved_positions(
        self,
    ) -> dict[str, dict[str, Any]]:
        """
        Load and validate saved positions.
        """
        saved_positions = load_positions()
        positions: dict[str, dict[str, Any]] = {}

        if not isinstance(saved_positions, dict):
            return positions

        for raw_symbol, raw_position in (
            saved_positions.items()
        ):
            if not isinstance(raw_position, dict):
                continue

            symbol = self.clean_symbol(raw_symbol)

            shares = self._positive_integer(
                raw_position.get("shares")
            )

            average_cost = self._positive_number(
                raw_position.get("average_cost")
            )

            if (
                not symbol
                or shares is None
                or average_cost is None
            ):
                continue

            positions[symbol] = {
                "symbol": symbol,
                "shares": shares,
                "entry_price": round(
                    average_cost,
                    2,
                ),
            }

        return positions

    def _load_portfolio_history(
        self,
    ) -> list[dict[str, Any]]:
        """
        Load and validate saved portfolio snapshots.
        """
        saved_history = load_portfolio_history()
        cleaned_history: list[
            dict[str, Any]
        ] = []

        if not isinstance(saved_history, list):
            return cleaned_history

        for snapshot in saved_history:
            if not isinstance(snapshot, dict):
                continue

            timestamp = str(
                snapshot.get("time", "")
            ).strip()

            value = self._non_negative_number(
                snapshot.get("value")
            )

            if not timestamp or value is None:
                continue

            cleaned_history.append({
                "time": timestamp,
                "value": round(value, 2),
            })

        return cleaned_history

    def _load_trade_history(
        self,
    ) -> list[dict[str, Any]]:
        """
        Restore trades and reconstruct realized profit
        for historical sell orders.

        The current database stores action, symbol,
        shares, price, and timestamp. Average cost is
        reconstructed from earlier buy orders.
        """
        saved_trades = load_trades()
        history: list[dict[str, Any]] = []

        reconstructed_positions: dict[
            str,
            dict[str, float | int],
        ] = {}

        if not isinstance(saved_trades, list):
            return history

        for raw_trade in saved_trades:
            if not isinstance(raw_trade, dict):
                continue

            action = str(
                raw_trade.get("action", "")
            ).strip().upper()

            symbol = self.clean_symbol(
                raw_trade.get("symbol")
            )

            shares = self._positive_integer(
                raw_trade.get("shares")
            )

            price = self._positive_number(
                raw_trade.get("price")
            )

            timestamp = str(
                raw_trade.get("timestamp", "")
            ).strip()

            if (
                action not in {"BUY", "SELL"}
                or not symbol
                or shares is None
                or price is None
                or not timestamp
            ):
                continue

            price = round(price, 2)

            trade: dict[str, Any] = {
                "action": action,
                "symbol": symbol,
                "shares": shares,
                "price": price,
                "total": round(
                    shares * price,
                    2,
                ),
                "time": timestamp,
            }

            if action == "BUY":
                reconstructed = (
                    reconstructed_positions.get(symbol)
                )

                if reconstructed is None:
                    reconstructed_positions[symbol] = {
                        "shares": shares,
                        "average_cost": price,
                    }

                else:
                    old_shares = int(
                        reconstructed["shares"]
                    )

                    old_average_cost = float(
                        reconstructed["average_cost"]
                    )

                    new_total_shares = (
                        old_shares + shares
                    )

                    new_average_cost = (
                        (
                            old_shares
                            * old_average_cost
                        )
                        + (
                            shares
                            * price
                        )
                    ) / new_total_shares

                    reconstructed["shares"] = (
                        new_total_shares
                    )

                    reconstructed["average_cost"] = (
                        new_average_cost
                    )

            else:
                reconstructed = (
                    reconstructed_positions.get(symbol)
                )

                if reconstructed is not None:
                    owned_shares = int(
                        reconstructed["shares"]
                    )

                    average_cost = float(
                        reconstructed["average_cost"]
                    )

                    shares_used_for_profit = min(
                        shares,
                        owned_shares,
                    )

                    trade["profit"] = round(
                        (
                            price
                            - average_cost
                        )
                        * shares_used_for_profit,
                        2,
                    )

                    remaining_shares = (
                        owned_shares
                        - shares_used_for_profit
                    )

                    if remaining_shares <= 0:
                        reconstructed_positions.pop(
                            symbol,
                            None,
                        )
                    else:
                        reconstructed["shares"] = (
                            remaining_shares
                        )

            history.append(trade)

        return history

    # =====================================================
    # Validation helpers
    # =====================================================

    @staticmethod
    def clean_symbol(symbol: Any) -> str:
        """
        Convert symbols into Yahoo Finance format.

        Examples:
            BRK.B -> BRK-B
            BF.B  -> BF-B
        """
        return (
            str(symbol or "")
            .strip()
            .upper()
            .replace(".", "-")
        )

    @staticmethod
    def _positive_integer(
        value: Any,
    ) -> int | None:
        """
        Return a positive whole number.

        Values such as 1.5, NaN, infinity, and booleans
        are rejected.
        """
        if isinstance(value, bool):
            return None

        try:
            number = float(value)
        except (TypeError, ValueError):
            return None

        if (
            not math.isfinite(number)
            or number <= 0
            or not number.is_integer()
        ):
            return None

        return int(number)

    @staticmethod
    def _positive_number(
        value: Any,
    ) -> float | None:
        """
        Return a finite number greater than zero.
        """
        if isinstance(value, bool):
            return None

        try:
            number = float(value)
        except (TypeError, ValueError):
            return None

        if (
            not math.isfinite(number)
            or number <= 0
        ):
            return None

        return number

    @staticmethod
    def _finite_number(
        value: Any,
    ) -> float | None:
        """Return any finite numeric value, including zero and negatives."""
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        return number

    @staticmethod
    def _non_negative_number(
        value: Any,
    ) -> float | None:
        """
        Return a finite number greater than or equal to zero.
        """
        if isinstance(value, bool):
            return None

        try:
            number = float(value)
        except (TypeError, ValueError):
            return None

        if (
            not math.isfinite(number)
            or number < 0
        ):
            return None

        return number

    @classmethod
    def _valid_market_price(
        cls,
        value: Any,
        fallback: float,
    ) -> float:
        """
        Return a valid market price or a safe fallback.
        """
        price = cls._positive_number(value)

        if price is None:
            return float(fallback)

        return price

    @staticmethod
    def _error(
        message: str,
    ) -> dict[str, Any]:
        return {
            "success": False,
            "message": message,
        }

    @staticmethod
    def _success(
        message: str,
    ) -> dict[str, Any]:
        return {
            "success": True,
            "message": message,
        }

    @staticmethod
    def current_time() -> str:
        """
        Return the current UTC time in ISO-8601 format.
        """
        return datetime.now(
            timezone.utc
        ).isoformat()
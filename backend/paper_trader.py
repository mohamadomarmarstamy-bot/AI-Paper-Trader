from datetime import datetime, timezone

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


class PaperTrader:

    def __init__(self):
        self.starting_cash = 100000.0

        # Restore saved account data.
        self.cash = load_cash(self.starting_cash)

        saved_positions = load_positions()

        self.positions = {
            symbol: {
                "symbol": symbol,
                "shares": int(position["shares"]),
                "entry_price": float(position["average_cost"]),
            }
            for symbol, position in saved_positions.items()
        }

        # Until live quotes are fetched, use each position's average cost.
        self.current_prices = {
            symbol: position["entry_price"]
            for symbol, position in self.positions.items()
        }

        self.history = self._load_trade_history()
        self.portfolio_history = load_portfolio_history()

        # Only create a starting snapshot for a brand-new database.
        if not self.portfolio_history:
            self.record_portfolio_value()

    def buy(self, symbol, shares, price):
        symbol = str(symbol).strip().upper()

        try:
            shares = int(shares)
            price = float(price)
        except (TypeError, ValueError):
            return {
                "success": False,
                "message": "Enter valid trade information.",
            }

        if not symbol:
            return {
                "success": False,
                "message": "Enter a valid stock symbol.",
            }

        if shares <= 0 or price <= 0:
            return {
                "success": False,
                "message": "Shares and price must be greater than zero.",
            }

        total_cost = shares * price

        if total_cost > self.cash:
            return {
                "success": False,
                "message": "Not enough cash for this trade.",
            }

        if symbol in self.positions:
            position = self.positions[symbol]

            old_shares = position["shares"]
            old_average_price = position["entry_price"]
            new_total_shares = old_shares + shares

            new_average_price = (
                (old_shares * old_average_price)
                + (shares * price)
            ) / new_total_shares

            position["shares"] = new_total_shares
            position["entry_price"] = round(new_average_price, 2)

        else:
            self.positions[symbol] = {
                "symbol": symbol,
                "shares": shares,
                "entry_price": round(price, 2),
            }

        self.cash -= total_cost
        self.cash = round(self.cash, 2)
        self.current_prices[symbol] = price

        timestamp = self.current_time()

        trade = {
            "action": "BUY",
            "symbol": symbol,
            "shares": shares,
            "price": round(price, 2),
            "total": round(total_cost, 2),
            "time": timestamp,
        }

        self.history.append(trade)

        # Persist the successful transaction.
        save_trade(
            symbol=symbol,
            shares=shares,
            price=round(price, 2),
            action="BUY",
            timestamp=timestamp,
        )

        save_cash(self.cash)

        position = self.positions[symbol]

        save_position(
            symbol=symbol,
            shares=position["shares"],
            average_cost=position["entry_price"],
        )

        self.record_portfolio_value()

        return {
            "success": True,
            "message": (
                f"Bought {shares} share(s) of "
                f"{symbol} at ${price:.2f}."
            ),
        }

    def sell(self, symbol, shares, price):
        symbol = str(symbol).strip().upper()

        try:
            shares = int(shares)
            price = float(price)
        except (TypeError, ValueError):
            return {
                "success": False,
                "message": "Enter valid trade information.",
            }

        if not symbol:
            return {
                "success": False,
                "message": "Enter a valid stock symbol.",
            }

        if shares <= 0 or price <= 0:
            return {
                "success": False,
                "message": "Shares and price must be greater than zero.",
            }

        if symbol not in self.positions:
            return {
                "success": False,
                "message": f"You do not own {symbol}.",
            }

        position = self.positions[symbol]
        owned_shares = position["shares"]

        if shares > owned_shares:
            return {
                "success": False,
                "message": (
                    f"You only own {owned_shares} "
                    f"share(s) of {symbol}."
                ),
            }

        entry_price = position["entry_price"]
        sale_total = shares * price
        profit = (price - entry_price) * shares

        self.cash += sale_total
        self.cash = round(self.cash, 2)

        position["shares"] -= shares
        self.current_prices[symbol] = price

        position_closed = position["shares"] == 0

        if position_closed:
            del self.positions[symbol]
            self.current_prices.pop(symbol, None)

        timestamp = self.current_time()

        trade = {
            "action": "SELL",
            "symbol": symbol,
            "shares": shares,
            "price": round(price, 2),
            "profit": round(profit, 2),
            "total": round(sale_total, 2),
            "time": timestamp,
        }

        self.history.append(trade)

        # Persist the successful transaction.
        save_trade(
            symbol=symbol,
            shares=shares,
            price=round(price, 2),
            action="SELL",
            timestamp=timestamp,
        )

        save_cash(self.cash)

        if position_closed:
            delete_position(symbol)
        else:
            save_position(
                symbol=symbol,
                shares=position["shares"],
                average_cost=position["entry_price"],
            )

        self.record_portfolio_value()

        profit_sign = "+" if profit > 0 else ""

        return {
            "success": True,
            "message": (
                f"Sold {shares} share(s) of {symbol} "
                f"at ${price:.2f}. "
                f"Profit/Loss: {profit_sign}${profit:.2f}"
            ),
        }

    def account(self):
        positions_list = []
        invested_value = 0.0

        for symbol, position in self.positions.items():
            shares = position["shares"]
            entry_price = position["entry_price"]

            current_price = self.current_prices.get(
                symbol,
                entry_price,
            )

            position_value = shares * current_price
            unrealized_profit = (
                current_price - entry_price
            ) * shares

            invested_value += position_value

            positions_list.append({
                "symbol": symbol,
                "shares": shares,
                "entry_price": round(entry_price, 2),
                "current_price": round(current_price, 2),
                "position_value": round(position_value, 2),
                "unrealized_profit": round(
                    unrealized_profit,
                    2,
                ),
            })

        portfolio_value = self.cash + invested_value
        profit_loss = portfolio_value - self.starting_cash

        return {
            "starting_cash": round(self.starting_cash, 2),
            "cash": round(self.cash, 2),
            "portfolio_value": round(portfolio_value, 2),
            "profit_loss": round(profit_loss, 2),
            "positions": positions_list,
            "history": self.history,
        }

    def get_portfolio_history(self):
        return self.portfolio_history

    def calculate_portfolio_value(self):
        positions_value = 0.0

        for symbol, position in self.positions.items():
            current_price = self.current_prices.get(
                symbol,
                position["entry_price"],
            )

            positions_value += (
                position["shares"] * current_price
            )

        return self.cash + positions_value

    def record_portfolio_value(self):
        timestamp = self.current_time()

        portfolio_value = round(
            self.calculate_portfolio_value(),
            2,
        )

        snapshot = {
            "time": timestamp,
            "value": portfolio_value,
        }

        self.portfolio_history.append(snapshot)

        save_portfolio_snapshot(
            timestamp=timestamp,
            value=portfolio_value,
        )

    def _load_trade_history(self):
        saved_trades = load_trades()
        history = []

        for trade in saved_trades:
            price = float(trade["price"])
            shares = int(trade["shares"])

            history.append({
                "action": trade["action"],
                "symbol": trade["symbol"],
                "shares": shares,
                "price": round(price, 2),
                "total": round(shares * price, 2),
                "time": trade["timestamp"],
            })

        return history

    @staticmethod
    def current_time():
        return datetime.now(
            timezone.utc
        ).isoformat()
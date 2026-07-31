from dataclasses import dataclass


@dataclass
class RiskSettings:
    max_position_percent: float = 0.10      # 10% of portfolio
    risk_per_trade_percent: float = 0.01    # Risk 1% per trade
    stop_loss_percent: float = 0.05         # 5%
    take_profit_percent: float = 0.10       # 10%


class RiskManager:
    def __init__(self, settings=None):
        self.settings = settings or RiskSettings()

    def max_position_size(self, portfolio_value: float) -> float:
        return portfolio_value * self.settings.max_position_percent

    def calculate_stop_loss(self, entry_price: float) -> float:
        return round(
            entry_price * (1 - self.settings.stop_loss_percent),
            2,
        )

    def calculate_take_profit(self, entry_price: float) -> float:
        return round(
            entry_price * (1 + self.settings.take_profit_percent),
            2,
        )

    def calculate_position_size(
        self,
        portfolio_value: float,
        entry_price: float,
        stop_loss_price: float,
    ) -> int:
        """
        Returns the maximum number of shares that keeps
        risk within the configured limit.
        """
        risk_per_share = entry_price - stop_loss_price

        if risk_per_share <= 0:
            return 0

        max_risk = (
            portfolio_value
            * self.settings.risk_per_trade_percent
        )

        shares = int(max_risk / risk_per_share)

        max_position = self.max_position_size(
            portfolio_value
        )

        max_shares = int(max_position / entry_price)

        return max(0, min(shares, max_shares))

    def risk_reward_ratio(
        self,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float,
    ) -> float:
        risk = entry_price - stop_loss_price
        reward = take_profit_price - entry_price

        if risk <= 0:
            return 0.0

        return round(reward / risk, 2)

    def validate_trade(
        self,
        portfolio_value: float,
        shares: int,
        price: float,
    ) -> tuple[bool, str]:
        position_value = shares * price
        max_position = self.max_position_size(
            portfolio_value
        )

        if position_value > max_position:
            return (
                False,
                f"Trade exceeds the maximum position size of "
                f"${max_position:.2f}."
            )

        return True, "Trade approved."
"""订单管理模块

处理 AI 输出的买卖决策，验证约束条件并执行交易。
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional
import logging

logger = logging.getLogger("fund_ai.engine.orders")


@dataclass
class Order:
    """AI 生成的交易订单"""
    fund_code: str
    action: str        # "buy" | "sell" | "hold" | "increase" | "decrease"
    amount: float = 0.0       # 金额（元）或份额
    reasoning: str = ""       # AI 决策理由
    confidence: float = 0.5   # 置信度 0.0-1.0

    @property
    def is_trade(self) -> bool:
        return self.action in ("buy", "sell", "increase", "decrease")


@dataclass
class Constraints:
    """交易约束条件"""
    max_positions: int = 10
    max_single_position_pct: float = 0.30
    min_cash_reserve: float = 500.0
    min_trade_amount: float = 100.0


class OrderManager:
    """订单验证与执行管理器

    对 AI 生成的 order 进行约束检查，验证通过后执行。
    """

    def __init__(
        self,
        constraints: Optional[Constraints] = None,
        buy_rate: float = 0.0015,
        sell_rate: float = 0.0050,
        min_commission: float = 5.0,
    ):
        self.constraints = constraints or Constraints()
        self.buy_rate = buy_rate
        self.sell_rate = sell_rate
        self.min_commission = min_commission

    def validate(
        self,
        order: Order,
        portfolio,
        nav_map: Dict[str, float],
    ) -> List[str]:
        """验证订单是否违反约束

        Returns:
            违规列表（空列表表示通过）
        """
        violations = []

        if order.action == "hold":
            return violations  # 持有总是允许的

        nav = nav_map.get(order.fund_code, 0.0)
        if nav <= 0 and order.is_trade:
            violations.append(f"基金 {order.fund_code} 无有效净值")
            return violations

        if order.action in ("buy", "increase"):
            # 检查现金
            commission = self._calc_commission(order.amount, is_buy=True)
            total_cost = order.amount + commission
            if total_cost > portfolio.cash - self.constraints.min_cash_reserve:
                violations.append(
                    f"现金不足：需要 ¥{total_cost:.2f}，可用 ¥{portfolio.cash:.2f}"
                )
            # 检查最低交易金额
            if order.amount < self.constraints.min_trade_amount:
                violations.append(
                    f"交易金额 ¥{order.amount:.2f} 低于最低 ¥{self.constraints.min_trade_amount}"
                )
            # 检查单只仓位上限
            current_market_value = 0.0
            if order.fund_code in portfolio.positions:
                current_market_value = portfolio.positions[order.fund_code].market_value
            target_value = current_market_value + order.amount
            target_pct = target_value / portfolio.total_value() if portfolio.total_value() > 0 else 0
            if target_pct > self.constraints.max_single_position_pct:
                violations.append(
                    f"基金 {order.fund_code} 仓位将达 {target_pct:.1%}，超过 {self.constraints.max_single_position_pct:.1%} 上限"
                )
            # 检查持仓数量
            if order.fund_code not in portfolio.positions:
                if portfolio.position_count() >= self.constraints.max_positions:
                    violations.append(
                        f"持仓数已达 {self.constraints.max_positions} 上限"
                    )

        elif order.action in ("sell", "decrease"):
            # 检查是否持有
            if order.fund_code not in portfolio.positions:
                violations.append(f"未持有基金 {order.fund_code}")
            else:
                pos = portfolio.positions[order.fund_code]
                # 如果是按金额卖出，检查持仓是否足够
                if order.amount > 0:
                    shares_needed = order.amount / nav
                    if shares_needed > pos.shares:
                        violations.append(f"持仓不足：需要 {shares_needed:.2f} 份，持有 {pos.shares:.2f} 份")
                # 检查最低交易金额
                elif order.amount < self.constraints.min_trade_amount and order.amount > 0:
                    violations.append(f"交易金额低于最低限额")

        return violations

    def execute(
        self,
        order: Order,
        portfolio,
        nav_map: Dict[str, float],
        trade_date: date,
    ) -> Optional[object]:
        """执行订单

        Returns:
            Transaction 对象或 None
        """
        if order.action == "hold":
            return None

        nav = nav_map.get(order.fund_code, 0.0)
        if nav <= 0:
            logger.warning(f"基金 {order.fund_code} 无有效净值，跳过")
            return None

        if order.action in ("buy", "increase"):
            commission = self._calc_commission(order.amount, is_buy=True)
            return portfolio.buy(
                fund_code=order.fund_code,
                amount=order.amount,
                nav=nav,
                trade_date=trade_date,
                commission=commission,
                reason=order.reasoning,
            )

        elif order.action in ("sell", "decrease"):
            shares = order.amount / nav  # amount 作为卖出金额处理
            sell_amount = min(shares * nav, portfolio.get_position(order.fund_code).market_value if order.fund_code in portfolio.positions else 0)
            commission = self._calc_commission(sell_amount, is_buy=False)
            return portfolio.sell(
                fund_code=order.fund_code,
                shares=shares,
                nav=nav,
                trade_date=trade_date,
                commission=commission,
                reason=order.reasoning,
            )

        return None

    def apply_constraints(
        self,
        orders: List[Order],
        portfolio,
        nav_map: Dict[str, float],
    ) -> List[Order]:
        """过滤和修正订单，移除违反约束的订单"""
        valid_orders = []
        for order in orders:
            violations = self.validate(order, portfolio, nav_map)
            if violations:
                for v in violations:
                    logger.warning(f"订单 {order.fund_code}/{order.action}: {v}")
                # 置信度低的直接跳过
                if order.confidence < 0.6:
                    continue
            valid_orders.append(order)
        return valid_orders

    def _calc_commission(self, amount: float, is_buy: bool) -> float:
        """计算交易手续费"""
        rate = self.buy_rate if is_buy else self.sell_rate
        commission = amount * rate
        return max(commission, self.min_commission)

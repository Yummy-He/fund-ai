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
        fee_manager=None,  # FeeManager (可选，用于动态赎回费率)
    ):
        self.constraints = constraints or Constraints()
        self.buy_rate = buy_rate
        self.sell_rate = sell_rate
        self.min_commission = min_commission
        self.fee_manager = fee_manager

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
            # 申购费率（根据金额匹配阶梯）
            sub_rate = self.buy_rate
            if self.fee_manager:
                try:
                    sub_rate = self.fee_manager.get_fee(order.fund_code).get_subscription_fee(order.amount)
                except Exception:
                    pass
            commission = order.amount * sub_rate
            commission = max(commission, self.min_commission)
            total_cost = order.amount + commission
            if total_cost > portfolio.cash - self.constraints.min_cash_reserve:
                violations.append(
                    f"现金不足：需要 ¥{total_cost:.2f}，可用 ¥{portfolio.cash:.2f}"
                )
            # 检查最低交易金额（系统级）
            if order.amount < self.constraints.min_trade_amount:
                violations.append(
                    f"交易金额 ¥{order.amount:.2f} 低于系统最低 ¥{self.constraints.min_trade_amount}"
                )
            # 检查基金申购起点 / 首次购买 / 追加购买
            if self.fee_manager:
                try:
                    f = self.fee_manager.get_fee(order.fund_code)
                    already_holding = order.fund_code in portfolio.positions
                    min_required = f.min_additional_purchase if already_holding else f.min_first_purchase
                    if order.amount < min_required:
                        violations.append(
                            f"金额 ¥{order.amount:.2f} 低于 {order.fund_code} {'追加' if already_holding else '首次'}购买起点 ¥{min_required:.0f}"
                        )
                    # 日累计申购限额 (validate 没有 trade_date, 仅检查是否超过单笔)
                    if f.daily_purchase_limit and order.amount > f.daily_purchase_limit:
                        violations.append(
                            f"单笔 ¥{order.amount:,.0f} 超过 {order.fund_code} 日累计申购限额 ¥{f.daily_purchase_limit:,.0f}"
                        )
                    # 持仓上限
                    if f.max_holding_amount:
                        current_holding = portfolio.positions.get(order.fund_code, None)
                        current_value = current_holding.market_value if current_holding else 0
                        if current_value + order.amount > f.max_holding_amount:
                            violations.append(
                                f"超过 {order.fund_code} 持仓上限 ¥{f.max_holding_amount:,.0f}"
                            )
                except Exception:
                    pass
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
                shares_needed = order.amount / nav if order.amount > 0 else 0
                # 基金级约束
                if self.fee_manager:
                    try:
                        f = self.fee_manager.get_fee(order.fund_code)
                        # 最小赎回份额
                        if shares_needed > 0 and shares_needed < f.min_redemption_shares:
                            violations.append(
                                f"赎回 {shares_needed:.2f} 份低于最小赎回份额 {f.min_redemption_shares}"
                            )
                        # 部分赎回后最低保留份额
                        remaining = pos.shares - shares_needed
                        if 0 < remaining < f.min_retained_shares:
                            violations.append(
                                f"赎回后剩余 {remaining:.2f} 份低于最低保留份额 {f.min_retained_shares}（需全部赎回或保留至少 {f.min_retained_shares} 份）"
                            )
                    except Exception:
                        pass
                # 持仓是否足够
                if shares_needed > pos.shares:
                    violations.append(f"持仓不足：需要 {shares_needed:.2f} 份，持有 {pos.shares:.2f} 份")
                # 检查最低交易金额
                if order.amount > 0 and order.amount < self.constraints.min_trade_amount:
                    violations.append(f"交易金额 ¥{order.amount:.2f} 低于系统最低 ¥{self.constraints.min_trade_amount}")

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
            # 申购费率（根据金额匹配阶梯）
            sub_rate = self.buy_rate
            if self.fee_manager:
                try:
                    sub_rate = self.fee_manager.get_fee(order.fund_code).get_subscription_fee(order.amount)
                except Exception:
                    pass
            commission = max(order.amount * sub_rate, self.min_commission)
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
            pos = portfolio.get_position(order.fund_code)
            if not pos:
                return None
            sell_shares = min(shares, pos.shares)
            sell_amount = sell_shares * nav

            # 动态赎回费率: 根据 FIFO 持有天数
            holding_days = portfolio.get_holding_days(order.fund_code, sell_shares, trade_date)
            commission = self._calc_sell_commission(sell_amount, order.fund_code, holding_days)

            return portfolio.sell(
                fund_code=order.fund_code,
                shares=sell_shares,
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
        """计算交易手续费（买入时使用）"""
        rate = self.buy_rate if is_buy else self.sell_rate
        commission = amount * rate
        return max(commission, self.min_commission)

    def _calc_sell_commission(
        self,
        amount: float,
        fund_code: str,
        holding_days: int,
    ) -> float:
        """计算卖出手续费 — 基于持有天数的动态赎回费率

        关键规则:
        - 持有 < 7天: 1.5% 惩罚费率（最重要！AI 会学到不做短线）
        - 持有 7-30天: 0.5%-0.75%
        - 持有 ≥30天: 0.5% 逐步降低
        - 持有 ≥730天: 0%
        """
        # 如果配置了 FeeManager，查询基金的实际费率
        if self.fee_manager:
            try:
                fee = self.fee_manager.get_fee(fund_code)
                rate = fee.get_redemption_fee(holding_days)
                commission = amount * rate
                logger.debug(
                    f"基金 {fund_code} 持有 {holding_days} 天 → "
                    f"赎回费率 {rate*100:.2f}% → 费用 ¥{commission:.2f}"
                )
                return max(commission, self.min_commission)
            except Exception:
                pass

        # 默认阶梯费率（无 FeeManager 时使用）
        if holding_days < 7:
            rate = 0.015   # 1.5% 惩罚
        elif holding_days < 30:
            rate = 0.0075  # 0.75%
        elif holding_days < 365:
            rate = 0.005   # 0.5%
        elif holding_days < 730:
            rate = 0.003   # 0.3%
        else:
            rate = 0.0     # 免赎回费

        commission = amount * rate
        return max(commission, self.min_commission)

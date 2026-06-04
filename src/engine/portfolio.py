"""投资组合管理模块

跟踪现金、持仓、交易记录，计算组合总价值和配置比例。
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional


@dataclass
class Position:
    """单个基金持仓"""
    fund_code: str
    shares: float              # 持有份额
    avg_cost: float            # 平均成本（每份）
    current_nav: float = 0.0   # 当前净值

    @property
    def cost_basis(self) -> float:
        """买入总成本"""
        return self.shares * self.avg_cost

    @property
    def market_value(self) -> float:
        """当前市值"""
        return self.shares * self.current_nav

    @property
    def profit_loss(self) -> float:
        """浮动盈亏"""
        return self.market_value - self.cost_basis

    @property
    def profit_loss_pct(self) -> float:
        """浮动盈亏比例"""
        if self.cost_basis > 0:
            return self.profit_loss / self.cost_basis
        return 0.0


@dataclass
class Transaction:
    """单笔交易记录"""
    date: date
    fund_code: str
    action: str               # "buy" | "sell"
    shares: float             # 交易份额
    price: float              # 交易净值
    amount: float             # 交易金额
    commission: float         # 手续费
    reason: str = ""          # 交易理由


@dataclass
class PortfolioSnapshot:
    """某个时间点的组合快照"""
    date: date
    cash: float
    positions: Dict[str, Position]
    total_market_value: float
    total_value: float        # 现金 + 持仓市值
    total_return_pct: float   # 相对于初始资金的收益率


class Portfolio:
    """投资组合管理器

    追踪现金、持仓、交易记录。支持 FIFO 计算持有天数用于赎回费率。
    """

    def __init__(self, initial_capital: float = 10000.0, initial_cash: float = None):
        # 兼容两种参数名
        if initial_cash is not None:
            initial_capital = initial_cash
        self.initial_cash = initial_capital
        self.cash = initial_capital
        self.positions: Dict[str, Position] = {}
        self.transactions: List[Transaction] = []
        self.daily_values: List[float] = []  # 每日总净值记录
        # FIFO 买入队列: {fund_code: [(shares, cost_per_share, buy_date), ...]}
        self._buy_queue: Dict[str, list] = {}

    def buy(
        self,
        fund_code: str,
        amount: float,
        nav: float,
        trade_date: date,
        commission: float = 0.0,
        reason: str = "",
    ) -> Optional[Transaction]:
        """买入基金

        Args:
            fund_code: 基金代码
            amount: 买入金额（元）
            nav: 买入净值
            trade_date: 交易日期
            commission: 手续费
            reason: 交易理由
        Returns:
            Transaction 对象或 None（如果金额不足）
        """
        total_cost = amount + commission
        if total_cost > self.cash:
            return None  # 现金不足

        shares = amount / nav
        self.cash -= total_cost

        # 更新持仓
        if fund_code in self.positions:
            pos = self.positions[fund_code]
            # 计算新的平均成本
            total_shares = pos.shares + shares
            pos.avg_cost = (pos.cost_basis + amount) / total_shares
            pos.shares = total_shares
            pos.current_nav = nav
        else:
            self.positions[fund_code] = Position(
                fund_code=fund_code,
                shares=shares,
                avg_cost=nav,
                current_nav=nav,
            )

        # FIFO 买入队列（用于计算持有天数 → 赎回费率）
        if fund_code not in self._buy_queue:
            self._buy_queue[fund_code] = []
        self._buy_queue[fund_code].append((shares, nav, trade_date))

        txn = Transaction(
            date=trade_date,
            fund_code=fund_code,
            action="buy",
            shares=shares,
            price=nav,
            amount=amount,
            commission=commission,
            reason=reason,
        )
        self.transactions.append(txn)
        return txn

    def sell(
        self,
        fund_code: str,
        shares: Optional[float] = None,
        amount: Optional[float] = None,
        nav: float = 0.0,
        trade_date: Optional[date] = None,
        commission: float = 0.0,
        reason: str = "",
    ) -> Optional[Transaction]:
        """卖出基金

        Args:
            fund_code: 基金代码
            shares: 卖出份额（与 amount 二选一）
            amount: 卖出金额（与 shares 二选一）
            nav: 卖出净值
            trade_date: 交易日期
            commission: 手续费
            reason: 交易理由
        Returns:
            Transaction 对象或 None
        """
        if fund_code not in self.positions:
            return None

        pos = self.positions[fund_code]
        pos.current_nav = nav

        if shares is None and amount is not None:
            shares = amount / nav
        if shares is None:
            return None

        shares = min(shares, pos.shares)  # 不能卖出超过持仓
        sell_amount = shares * nav
        self.cash += sell_amount - commission

        # FIFO: 从买入队列中移除已卖出的份额
        self._consume_buy_queue(fund_code, shares)

        if shares >= pos.shares:
            # 全部卖出
            del self.positions[fund_code]
            self._buy_queue.pop(fund_code, None)
        else:
            pos.shares -= shares

        txn = Transaction(
            date=trade_date or date.today(),
            fund_code=fund_code,
            action="sell",
            shares=shares,
            price=nav,
            amount=sell_amount,
            commission=commission,
            reason=reason,
        )
        self.transactions.append(txn)
        return txn

    def update_navs(self, nav_map: Dict[str, float]) -> None:
        """更新所有持仓的净值"""
        for code, nav in nav_map.items():
            if code in self.positions:
                self.positions[code].current_nav = nav

    def total_market_value(self) -> float:
        """计算持仓总市值"""
        return sum(p.market_value for p in self.positions.values())

    def total_value(self) -> float:
        """计算组合总价值（现金+持仓）"""
        return self.cash + self.total_market_value()

    def total_return_pct(self) -> float:
        """计算总收益率"""
        tv = self.total_value()
        return (tv - self.initial_cash) / self.initial_cash * 100

    def allocation(self) -> Dict[str, float]:
        """计算当前各基金配置比例"""
        tv = self.total_value()
        if tv <= 0:
            return {"cash": 1.0}
        alloc = {"cash": self.cash / tv}
        for code, pos in self.positions.items():
            alloc[code] = pos.market_value / tv
        return alloc

    def position_count(self) -> int:
        """当前持仓基金数量"""
        return len(self.positions)

    def snapshot(self, trade_date: date) -> PortfolioSnapshot:
        """生成当前组合快照"""
        return PortfolioSnapshot(
            date=trade_date,
            cash=self.cash,
            positions=dict(self.positions),
            total_market_value=self.total_market_value(),
            total_value=self.total_value(),
            total_return_pct=self.total_return_pct(),
        )

    def record_daily_value(self) -> None:
        """记录当日组合净值"""
        self.daily_values.append(self.total_value())

    def get_position(self, fund_code: str) -> Optional[Position]:
        """获取某只基金的持仓"""
        return self.positions.get(fund_code)

    def is_holding(self, fund_code: str) -> bool:
        """是否持有某只基金"""
        return fund_code in self.positions

    def get_holding_days(self, fund_code: str, sell_shares: float = None, trade_date: date = None) -> int:
        """FIFO 计算持有天数（最早买入批次距 trade_date 的天数）

        用于确定赎回费率阶梯。
        """
        if fund_code not in self._buy_queue or not self._buy_queue[fund_code]:
            # 无买入记录，返回默认中等持有期
            return 30

        queue = self._buy_queue[fund_code]
        # 返回最早批次的持有天数
        _, _, first_buy_date = queue[0]
        if trade_date:
            return (trade_date - first_buy_date).days
        return 30

    def _consume_buy_queue(self, fund_code: str, sell_shares: float) -> None:
        """FIFO: 从买入队列中消耗已卖出的份额"""
        if fund_code not in self._buy_queue:
            return

        remaining = sell_shares
        new_queue = []
        for shares, cost, buy_date in self._buy_queue[fund_code]:
            if remaining <= 0:
                new_queue.append((shares, cost, buy_date))
            elif remaining >= shares:
                remaining -= shares
            else:
                new_queue.append((shares - remaining, cost, buy_date))
                remaining = 0

        self._buy_queue[fund_code] = new_queue

"""投资绩效指标计算

计算夏普比率、最大回撤、年化收益率等经典指标。
"""

from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional
import numpy as np


@dataclass
class BacktestResult:
    """单次回测的完整结果"""
    # 基本信息
    start_date: date
    end_date: date
    fund_pool: List[str] = field(default_factory=list)

    # 资金指标
    initial_capital: float = 10000.0
    final_value: float = 0.0
    total_return: float = 0.0        # 总收益率 %
    annualized_return: float = 0.0   # 年化收益率 (CAGR) %
    excess_return: float = 0.0       # 超额收益（相对基准）%

    # 风险指标
    max_drawdown: float = 0.0        # 最大回撤 %
    sharpe_ratio: float = 0.0        # 夏普比率
    sortino_ratio: float = 0.0       # 索提诺比率
    volatility: float = 0.0          # 年化波动率 %
    calmar_ratio: float = 0.0        # 卡玛比率 (年化收益/最大回撤)

    # 交易指标
    total_trades: int = 0
    win_rate: float = 0.0            # 胜率 %
    avg_win: float = 0.0             # 平均盈利 %
    avg_loss: float = 0.0            # 平均亏损 %
    profit_factor: float = 0.0       # 盈亏比
    turnover_rate: float = 0.0       # 换手率

    # 时序数据
    daily_values: List[float] = field(default_factory=list)
    daily_returns: List[float] = field(default_factory=list)

    # AI 决策记录
    decision_count: int = 0

    # 定性信息
    grade: str = ""                  # A/B/C/D/F
    summary: str = ""                # AI 总结


class MetricsCalculator:
    """投资绩效指标计算器"""

    def __init__(self, risk_free_rate: float = 0.02):
        """
        Args:
            risk_free_rate: 无风险利率（默认2%，约等于货币基金收益）
        """
        self.rf = risk_free_rate
        self.rf_daily = (1 + risk_free_rate) ** (1 / 252) - 1

    def compute(
        self,
        portfolio,
        start_date: date,
        end_date: date,
        fund_pool: List[str],
        decisions_made: int = 0,
        benchmark_values: Optional[List[float]] = None,
    ) -> BacktestResult:
        """计算所有关键指标"""
        daily_values = portfolio.daily_values
        initial = portfolio.initial_cash
        final = portfolio.total_value()
        total_return = (final - initial) / initial * 100

        # 计算日收益率序列
        if len(daily_values) >= 2:
            daily_returns = [
                (daily_values[i] - daily_values[i - 1]) / daily_values[i - 1]
                for i in range(1, len(daily_values))
                if daily_values[i - 1] > 0
            ]
        else:
            daily_returns = []

        # 年化收益率 (CAGR)
        days = (end_date - start_date).days
        annualized_return = 0.0
        if days > 0 and initial > 0:
            annualized_return = ((final / initial) ** (365.0 / days) - 1) * 100

        # 超额收益
        excess_return = 0.0
        benchmark_return = 0.0
        if benchmark_values and len(benchmark_values) == len(daily_values):
            bm_initial = benchmark_values[0]
            bm_final = benchmark_values[-1]
            if bm_initial > 0:
                benchmark_return = (bm_final - bm_initial) / bm_initial * 100
            excess_return = total_return - benchmark_return

        # 最大回撤
        max_dd = self.max_drawdown(daily_values)

        # 夏普比率
        sharpe = self.sharpe_ratio(daily_returns)

        # 索提诺比率
        sortino = self.sortino_ratio(daily_returns)

        # 波动率
        volatility = np.std(daily_returns) * np.sqrt(252) * 100 if daily_returns else 0.0

        # 卡玛比率
        calmar = annualized_return / abs(max_dd) if max_dd != 0 else 0.0

        # 交易指标
        trade_metrics = self._trade_metrics(portfolio.transactions)

        result = BacktestResult(
            start_date=start_date,
            end_date=end_date,
            fund_pool=fund_pool,
            initial_capital=initial,
            final_value=final,
            total_return=round(total_return, 2),
            annualized_return=round(annualized_return, 2),
            excess_return=round(excess_return, 2),
            max_drawdown=round(max_dd, 2),
            sharpe_ratio=round(sharpe, 3),
            sortino_ratio=round(sortino, 3),
            volatility=round(volatility, 2),
            calmar_ratio=round(calmar, 3),
            total_trades=trade_metrics["total"],
            win_rate=round(trade_metrics["win_rate"], 2),
            avg_win=round(trade_metrics["avg_win"], 2),
            avg_loss=round(trade_metrics["avg_loss"], 2),
            profit_factor=round(trade_metrics["profit_factor"], 3),
            daily_values=daily_values,
            daily_returns=daily_returns,
            decision_count=decisions_made,
        )
        return result

    def sharpe_ratio(self, daily_returns: List[float]) -> float:
        """计算夏普比率 (年化)"""
        if not daily_returns:
            return 0.0
        returns = np.array(daily_returns)
        excess = returns - self.rf_daily
        if len(excess) < 2:
            return 0.0
        std = np.std(excess, ddof=1)
        if std == 0:
            return 0.0
        return np.mean(excess) / std * np.sqrt(252)

    def sortino_ratio(self, daily_returns: List[float]) -> float:
        """计算索提诺比率 (仅考虑下行风险)"""
        if not daily_returns:
            return 0.0
        returns = np.array(daily_returns)
        downside = returns[returns < self.rf_daily]
        if len(downside) < 2:
            return 0.0
        downside_std = np.std(downside, ddof=1)
        if downside_std == 0:
            return 0.0
        excess = np.mean(returns) - self.rf_daily
        return excess / downside_std * np.sqrt(252)

    def max_drawdown(self, values: List[float]) -> float:
        """计算最大回撤百分比"""
        if not values or len(values) < 2:
            return 0.0
        peaks = np.maximum.accumulate(values)
        drawdowns = (np.array(values) - peaks) / peaks
        return float(np.min(drawdowns) * 100)

    def _trade_metrics(self, transactions) -> dict:
        """计算交易相关指标"""
        if not transactions:
            return {
                "total": 0, "win_rate": 0.0, "avg_win": 0.0,
                "avg_loss": 0.0, "profit_factor": 0.0,
            }

        # 按基金分组，计算每个"完整交易"（买入->卖出）的盈亏
        sells = [t for t in transactions if t.action == "sell"]
        total = len(transactions)

        if not sells:
            return {
                "total": total, "win_rate": 0.0, "avg_win": 0.0,
                "avg_loss": 0.0, "profit_factor": 0.0,
            }

        # 简化计算：每笔卖出视为一个"交易完成"
        # 实际系统可以根据 fund_code 匹配买卖对
        profits = []
        for t in sells:
            # 查找最近的同基金买入
            buys = [
                b for b in transactions
                if b.fund_code == t.fund_code and b.action == "buy" and b.date < t.date
            ]
            if buys:
                # 简化：用最近的买入成本
                latest_buy = buys[-1]
                profit = (t.price - latest_buy.price) / latest_buy.price * 100
                profits.append(profit)

        wins = [p for p in profits if p > 0]
        losses = [p for p in profits if p <= 0]

        win_rate = len(wins) / len(profits) * 100 if profits else 0.0
        avg_win = np.mean(wins) if wins else 0.0
        avg_loss = np.mean(losses) if losses else 0.0

        total_wins = sum(wins)
        total_losses = abs(sum(losses))
        profit_factor = total_wins / total_losses if total_losses > 0 else 0.0

        return {
            "total": total,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
        }

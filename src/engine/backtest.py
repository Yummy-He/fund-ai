"""回测引擎 - 核心编排器

将投资组合、时间步进器、AI 决策和订单管理整合为完整的回测循环。
"""

import logging
import uuid
from datetime import date, timedelta
from typing import Dict, List, Optional

import pandas as pd

from .portfolio import Portfolio
from .orders import OrderManager, Constraints
from .simulator import TimeSimulator
from .metrics import MetricsCalculator, BacktestResult
from .decision import FundDecisionMaker
from .ai_client import AIClient
from .prompt import PromptBuilder
from ..data.store import FundRepository
from ..data.models import MarketContext, MarketTrend
from ..learning.experience import Experience, ScenarioSnapshot, DecisionRecord, OutcomeRecord

logger = logging.getLogger("fund_ai.engine.backtest")


class BacktestEngine:
    """回测引擎 - 单次回测的完整编排

    核心循环:
      对每个交易日:
        1. 获取当前净值
        2. AI 决策买卖
        3. 验证并执行订单
        4. 记录组合价值
    """

    def __init__(
        self,
        config=None,
        ai_client: Optional[AIClient] = None,
        prompt_builder: Optional[PromptBuilder] = None,
        fund_repo: Optional[FundRepository] = None,
        experience_retriever=None,
    ):
        # 配置
        self.config = config

        # 从配置中提取回测参数
        if config:
            bt = getattr(config, "backtest", None)
            self.initial_capital = bt.initial_capital if bt else 10000.0
            if bt and hasattr(bt, "commission"):
                self.buy_rate = bt.commission.buy_rate
                self.sell_rate = bt.commission.sell_rate
            else:
                self.buy_rate, self.sell_rate = 0.0015, 0.0050
            if bt and hasattr(bt, "constraints"):
                self.constraints = Constraints(
                    max_positions=bt.constraints.max_positions,
                    max_single_position_pct=bt.constraints.max_single_position_pct,
                    min_cash_reserve=bt.constraints.min_cash_reserve,
                    min_trade_amount=bt.constraints.min_trade_amount,
                )
            else:
                self.constraints = Constraints()
            self.decision_freq = bt.decision_frequency if bt else "daily"
        else:
            self.initial_capital = 10000.0
            self.buy_rate, self.sell_rate = 0.0015, 0.0050
            self.constraints = Constraints()
            self.decision_freq = "daily"

        # AI 客户端
        self.ai_client = ai_client
        # 提示词构建器
        self.prompt_builder = prompt_builder or PromptBuilder()
        # 数据存储
        self.fund_repo = fund_repo or FundRepository()
        # 经验检索器（用于学习循环）
        self.experience_retriever = experience_retriever

        # 创建内部组件（含动态费率支持）
        from ..data.fees import FeeManager
        try:
            fee_mgr = FeeManager()
        except Exception:
            fee_mgr = None
        self.order_manager = OrderManager(
            constraints=self.constraints,
            buy_rate=self.buy_rate,
            sell_rate=self.sell_rate,
            fee_manager=fee_mgr,
        )
        self.metrics_calc = MetricsCalculator()

        # 当前回测的状态
        self.portfolio: Optional[Portfolio] = None
        self.simulator: Optional[TimeSimulator] = None
        self.decision_maker: Optional[FundDecisionMaker] = None
        self.decisions_made: int = 0
        self.experience_records: List[dict] = []

    def run(
        self,
        start_date: date,
        end_date: date,
        fund_pool: List[str],
        decision_interval: int = 1,
        require_ai: bool = True,
        strategy_patterns: Optional[List[dict]] = None,
    ) -> BacktestResult:
        """执行单次回测

        Args:
            start_date: 回测开始日期
            end_date: 回测结束日期
            fund_pool: 可投资基金代码列表
            decision_interval: 决策间隔（1=每日，5=每周）
            require_ai: 是否必须使用 AI 决策（False 时使用简单基准策略）

        Returns:
            BacktestResult 包含所有关键指标
        """
        backtest_id = f"bt_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}"

        logger.info(f"开始回测 [{backtest_id}]")
        logger.info(f"  时间段: {start_date} ~ {end_date}")
        logger.info(f"  初始资金: ¥{self.initial_capital:,.0f}")
        logger.info(f"  基金池: {fund_pool}")

        # 初始化
        self.portfolio = Portfolio(initial_capital=self.initial_capital)
        self.simulator = TimeSimulator(start_date, end_date)

        # 决策引擎
        if self.ai_client is None:
            raise ValueError("AI 客户端未初始化，请先设置 DEEPSEEK_API_KEY")

        self.decision_maker = FundDecisionMaker(
            ai_client=self.ai_client,
            prompt_builder=self.prompt_builder,
            fund_repo=self.fund_repo,
            experience_retriever=self.experience_retriever,
            strategy_patterns=strategy_patterns or [],
        )

        self.decisions_made = 0
        days_since_last_decision = 0

        logger.info(f"  交易日数: {self.simulator.total_trading_days}")

        # 记录初始净值
        self.portfolio.record_daily_value()

        # === 主循环 ===
        while not self.simulator.is_finished():
            current_date = self.simulator.current_date

            # 获取当日净值
            nav_map = {}
            for code in fund_pool:
                nav_rec = self.fund_repo.get_nav_on_date(code, current_date)
                if nav_rec:
                    nav_map[code] = nav_rec.nav

            # 更新组合净值
            if nav_map:
                self.portfolio.update_navs(nav_map)

            # 决策间隔判断
            days_since_last_decision += 1
            should_decide = days_since_last_decision >= decision_interval

            if should_decide and nav_map:
                try:
                    # AI 决策
                    orders = self.decision_maker.decide(
                        context_date=current_date,
                        portfolio=self.portfolio,
                        fund_pool=fund_pool,
                    )

                    # 验证并执行
                    valid_orders = self.order_manager.apply_constraints(
                        orders, self.portfolio, nav_map
                    )

                    for order in valid_orders:
                        self.order_manager.execute(
                            order, self.portfolio, nav_map, current_date
                        )

                    self.decisions_made += 1
                    days_since_last_decision = 0

                    # 捕获决策上下文（供后续构建经验）
                    if self.decision_maker and self.decision_maker.last_context:
                        self.experience_records.append(self.decision_maker.last_context)

                except Exception as e:
                    logger.error(f"日期 {current_date} 决策执行失败: {e}")

            # 记录每日净值
            self.portfolio.record_daily_value()

            # 步进
            if not self.simulator.next_day():
                break

            # 进度日志
            progress = self.simulator.progress()
            if progress % 0.1 < 0.01:  # 每10%汇报一次
                current_val = self.portfolio.total_value()
                ret = (current_val - self.initial_capital) / self.initial_capital * 100
                logger.info(
                    f"  进度 {progress:.0%} | 当前: ¥{current_val:,.2f} "
                    f"({ret:+.2f}%) | 决策: {self.decisions_made}"
                )

        # === 计算指标 ===
        result = self.metrics_calc.compute(
            portfolio=self.portfolio,
            start_date=start_date,
            end_date=end_date,
            fund_pool=fund_pool,
            decisions_made=self.decisions_made,
        )

        logger.info(
            f"回测完成 [{backtest_id}] | "
            f"收益: {result.total_return:+.2f}% | "
            f"夏普: {result.sharpe_ratio:.3f} | "
            f"最大回撤: {result.max_drawdown:.2f}% | "
            f"交易: {result.total_trades}次 | "
            f"胜率: {result.win_rate:.1f}%"
        )

        return result

    def _calc_forward_return(self, fund_code: str, from_date: date, days: int) -> float:
        """计算基金在决策日后 N 天的前向收益率（%）"""
        current = self.fund_repo.get_nav_on_date(fund_code, from_date)
        future = self.fund_repo.get_nav_on_date(fund_code, from_date + timedelta(days=days))
        if current and future and current.nav > 0:
            return (future.nav - current.nav) / current.nav * 100
        return 0.0

    def build_experiences(self, backtest_id: str) -> List[Experience]:
        """从捕获的决策记录构建 Experience 对象列表。必须在 run() 完成后调用。"""
        from datetime import datetime

        experiences = []
        for rec in self.experience_records:
            if not rec.get("orders"):
                continue
            context_date = rec["context_date"]
            market = rec["market"]
            snapshots = rec.get("fund_snapshots", [])
            orders = rec["orders"]
            nav_map = rec.get("nav_map", {})
            primary = snapshots[0] if snapshots else None

            scenario = ScenarioSnapshot(
                date=context_date.isoformat(),
                fund_code=primary.fund_code if primary else (rec.get("fund_pool", [""])[0] if rec.get("fund_pool") else ""),
                fund_type=primary.fund_type.value if primary else "MIXED",
                nav_current=nav_map.get(primary.fund_code, 0.0) if primary else 0.0,
                nav_change_7d=primary.change_7d if primary else 0.0,
                nav_change_30d=primary.change_30d if primary else 0.0,
                nav_change_90d=primary.change_90d if primary else 0.0,
                market_trend=market.market_trend.value,
                market_volatility=market.market_volatility,
                cash_ratio=rec.get("cash_ratio", 0.0),
                portfolio_return=rec.get("portfolio_return", 0.0),
            )

            trade_orders = [o for o in orders if o.is_trade]
            primary_action = trade_orders[0].action if trade_orders else "hold"
            total_amount = sum(o.amount for o in trade_orders)
            best_reasoning = ""
            best_confidence = 0.5
            for o in orders:
                if o.reasoning and o.confidence >= best_confidence:
                    best_reasoning, best_confidence = o.reasoning, o.confidence

            decision = DecisionRecord(
                action=primary_action,
                amount_rmb=total_amount,
                amount_pct=round(total_amount / total_val if (total_val := sum(nav_map.values())) > 0 else 0.0, 4),
                reasoning=best_reasoning[:500] if best_reasoning else "",
                confidence=best_confidence,
                model="",
            )

            ret_7d = ret_30d = ret_90d = 0.0
            count = 0
            for fcode, nv in nav_map.items():
                if nv > 0:
                    ret_7d += self._calc_forward_return(fcode, context_date, 7)
                    ret_30d += self._calc_forward_return(fcode, context_date, 30)
                    ret_90d += self._calc_forward_return(fcode, context_date, 90)
                    count += 1
            if count > 0:
                ret_7d /= count; ret_30d /= count; ret_90d /= count

            outcome = OutcomeRecord(
                return_7d=round(ret_7d, 2), return_30d=round(ret_30d, 2),
                return_90d=round(ret_90d, 2), was_profitable=ret_30d > 0,
                relative_to_benchmark=0.0,
            )

            experiences.append(Experience(
                backtest_id=backtest_id, timestamp=datetime.now().isoformat(),
                scenario=scenario, decision=decision, outcome=outcome,
            ))
        return experiences

    def run_simple_baseline(
        self,
        start_date: date,
        end_date: date,
        fund_pool: List[str],
    ) -> BacktestResult:
        """使用简单基准策略运行回测（等权买入持有）

        用于和 AI 策略做对比。
        """
        logger.info(f"开始基准策略回测 (等权买入持有): {start_date} ~ {end_date}")

        self.portfolio = Portfolio(initial_capital=self.initial_capital)
        self.simulator = TimeSimulator(start_date, end_date)

        # 获取初始净值
        nav_map = {}
        for code in fund_pool:
            nav_rec = self.fund_repo.get_nav_on_date(code, start_date)
            if nav_rec:
                nav_map[code] = nav_rec.nav

        if not nav_map:
            logger.warning("基准策略: 无有效净值数据")
            return BacktestResult(start_date=start_date, end_date=end_date)

        # 等权分配资金
        per_fund_amount = (self.initial_capital * 0.8) / len(nav_map)  # 80%仓位
        for code, nav in nav_map.items():
            self.portfolio.buy(
                fund_code=code,
                amount=per_fund_amount,
                nav=nav,
                trade_date=start_date,
                reason="基准策略: 等权买入持有",
            )

        self.portfolio.record_daily_value()

        # 步进到结束
        while not self.simulator.is_finished():
            current_date = self.simulator.current_date
            nav_map = {}
            for code in fund_pool:
                nav_rec = self.fund_repo.get_nav_on_date(code, current_date)
                if nav_rec:
                    nav_map[code] = nav_rec.nav
            self.portfolio.update_navs(nav_map)
            self.portfolio.record_daily_value()
            if not self.simulator.next_day():
                break

        result = self.metrics_calc.compute(
            portfolio=self.portfolio,
            start_date=start_date,
            end_date=end_date,
            fund_pool=fund_pool,
            decisions_made=1,
        )

        logger.info(
            f"基准策略完成 | 收益: {result.total_return:+.2f}% | "
            f"最大回撤: {result.max_drawdown:.2f}%"
        )
        return result

    def run_dca(
        self,
        start_date: date,
        end_date: date,
        fund_pool: List[str],
        amount_per_invest: float = 1000.0,
        invest_interval_days: int = 22,  # ~每月一次
        allocation_mode: str = "equal",  # equal | weighted
    ) -> BacktestResult:
        """定投策略（Dollar Cost Averaging）

        定期定额投资：无论市场涨跌，到了定投日就按计划买入。
        这是最经典、最简单的被动投资策略，作为 AI 策略的对比基线。

        Args:
            start_date: 回测开始日期
            end_date: 回测结束日期
            fund_pool: 定投基金池
            amount_per_invest: 每次定投总金额
            invest_interval_days: 定投间隔（交易日）
            allocation_mode: 分配方式
                - "equal": 等额分配（每只基金分 equally）
                - "weighted": 按市值加权
        """
        logger.info(
            f"开始定投策略回测: {start_date} ~ {end_date}, "
            f"每次 ¥{amount_per_invest:,.0f}, "
            f"间隔 {invest_interval_days} 交易日, {allocation_mode} 分配"
        )

        self.portfolio = Portfolio(initial_capital=self.initial_capital)
        self.simulator = TimeSimulator(start_date, end_date)

        per_fund = amount_per_invest / max(len(fund_pool), 1)
        days_since_invest = invest_interval_days  # 首日即投
        invest_count = 0

        self.portfolio.record_daily_value()

        while not self.simulator.is_finished():
            current_date = self.simulator.current_date

            # 获取当日净值
            nav_map = {}
            for code in fund_pool:
                nav_rec = self.fund_repo.get_nav_on_date(code, current_date)
                if nav_rec:
                    nav_map[code] = nav_rec.nav
            self.portfolio.update_navs(nav_map)

            # 到了定投日
            if days_since_invest >= invest_interval_days and nav_map:
                for code, nav in nav_map.items():
                    if self.portfolio.cash >= per_fund:
                        # 申购费率
                        commission = per_fund * self.buy_rate
                        self.portfolio.buy(
                            fund_code=code,
                            amount=per_fund,
                            nav=nav,
                            trade_date=current_date,
                            commission=commission,
                            reason=f"定投 #{invest_count+1}",
                        )
                invest_count += 1
                days_since_invest = 0

            days_since_invest += 1
            self.portfolio.record_daily_value()
            if not self.simulator.next_day():
                break

            if invest_count > 0 and invest_count % 10 == 0:
                val = self.portfolio.total_value()
                invested = invest_count * amount_per_invest
                logger.info(
                    f"  定投 {invest_count} 次 | 投入 ¥{invested:,.0f} | "
                    f"市值 ¥{val:,.2f} | 收益 {(val-invested)/invested*100:+.2f}%"
                )

        result = self.metrics_calc.compute(
            portfolio=self.portfolio,
            start_date=start_date,
            end_date=end_date,
            fund_pool=fund_pool,
            decisions_made=invest_count,
        )

        logger.info(
            f"定投策略完成 ({invest_count} 次定投) | "
            f"收益: {result.total_return:+.2f}% | "
            f"最大回撤: {result.max_drawdown:.2f}%"
        )
        return result

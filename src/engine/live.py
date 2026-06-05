"""实盘模拟交易引擎

每天运行一次：加载持仓状态 → 获取最新净值 → AI 决策 → 执行交易 → 保存状态 → 生成报告。
与 BacktestEngine 不同，LiveTrader 每次只处理一天，并将组合状态持久化到磁盘。
"""

import json
import logging
from dataclasses import dataclass, asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from .portfolio import Portfolio, Position, Transaction
from .orders import OrderManager, Constraints, Order
from .decision import FundDecisionMaker
from ..data.models import MarketContext, FundType
from ..utils.date_utils import beijing_today, get_trading_calendar

logger = logging.getLogger("fund_ai.engine.live")


@dataclass
class LiveResult:
    """实盘交易一天的结果"""
    date: date
    is_trading_day: bool
    skipped: bool = False
    skip_reason: str = ""
    snapshot_before: Optional[dict] = None
    snapshot_after: Optional[dict] = None
    decisions: List[dict] = None
    market_context: Optional[dict] = None
    pnl_day: float = 0.0
    pnl_total_pct: float = 0.0

    def __post_init__(self):
        if self.decisions is None:
            self.decisions = []


class LiveTrader:
    """实盘模拟交易引擎

    每个交易日：
    1. 加载持仓状态（首次运行初始 ¥10,000）
    2. 获取基金最新净值
    3. AI 决策（带经验检索）
    4. 验证并执行订单
    5. 保存状态到 data/live/portfolio.json
    """

    STATE_DIR = Path("data/live")
    STATE_FILE = Path("data/live/portfolio.json")

    def __init__(
        self,
        config=None,
        ai_client=None,
        prompt_builder=None,
        fund_repo=None,
        experience_retriever=None,
    ):
        self.config = config
        self.ai_client = ai_client
        self.prompt_builder = prompt_builder
        self.fund_repo = fund_repo
        self.experience_retriever = experience_retriever

        # 创建决策制定器
        self.decision_maker = FundDecisionMaker(
            ai_client=ai_client,
            prompt_builder=prompt_builder,
            fund_repo=fund_repo,
            experience_retriever=experience_retriever,
        )

        # 创建订单管理器（带费率支持）
        try:
            from ..data.fees import FeeManager
            fee_mgr = FeeManager()
        except Exception:
            fee_mgr = None

        constraints = Constraints(
            max_positions=getattr(config.backtest.constraints, "max_positions", 10) if config else 10,
            max_single_position_pct=getattr(config.backtest.constraints, "max_single_position_pct", 0.30) if config else 0.30,
            min_cash_reserve=getattr(config.backtest.constraints, "min_cash_reserve", 500.0) if config else 500.0,
            min_trade_amount=getattr(config.backtest.constraints, "min_trade_amount", 100.0) if config else 100.0,
        )
        self.order_manager = OrderManager(constraints=constraints, fee_manager=fee_mgr)

        # 确保状态目录存在
        self.STATE_DIR.mkdir(parents=True, exist_ok=True)

    # ── 状态持久化 ──────────────────────────────────────────────

    def load_portfolio(self) -> Portfolio:
        """从磁盘加载持仓状态，首次运行返回初始化的空组合"""
        if self.STATE_FILE.exists():
            try:
                with open(self.STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)

                portfolio = Portfolio(initial_capital=state.get("initial_capital", 10000.0))
                portfolio.cash = state.get("cash", portfolio.initial_cash)

                # 恢复持仓
                for code, pos_data in state.get("positions", {}).items():
                    portfolio.positions[code] = Position(
                        fund_code=code,
                        shares=pos_data.get("shares", 0),
                        avg_cost=pos_data.get("avg_cost", 0),
                        current_nav=pos_data.get("current_nav", 0),
                    )

                # 恢复买入队列（FIFO，用于持有天数→赎回费率）
                for code, queue in state.get("_buy_queue", {}).items():
                    portfolio._buy_queue[code] = [
                        (item[0], item[1], date.fromisoformat(item[2]))
                        for item in queue
                    ]

                # 恢复交易记录（用于审计追溯）
                for txn in state.get("transactions", []):
                    portfolio.transactions.append(Transaction(
                        date=date.fromisoformat(txn["date"]),
                        fund_code=txn["fund_code"],
                        action=txn["action"],
                        shares=txn["shares"],
                        price=txn["price"],
                        amount=txn["amount"],
                        commission=txn["commission"],
                        reason=txn.get("reason", ""),
                    ))

                logger.info(
                    f"已加载持仓状态: 现金 ¥{portfolio.cash:,.2f}, "
                    f"持仓 {len(portfolio.positions)} 只, "
                    f"总价值 ¥{portfolio.total_value():,.2f}"
                )
                return portfolio

            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.error(f"持仓状态文件损坏: {e}，将重新初始化")
                # 备份损坏文件
                backup = self.STATE_FILE.with_suffix(".json.bak")
                try:
                    self.STATE_FILE.rename(backup)
                    logger.warning(f"已备份损坏的状态文件到 {backup}")
                except Exception:
                    pass

        # 首次运行：初始化 ¥10,000
        logger.info("首次运行，初始化 ¥10,000 实盘模拟")
        return Portfolio(initial_capital=10000.0)

    def save_portfolio(self, portfolio: Portfolio, trade_date: date) -> None:
        """保存持仓状态到磁盘"""
        # 序列化持仓
        positions_data = {}
        for code, pos in portfolio.positions.items():
            positions_data[code] = {
                "shares": pos.shares,
                "avg_cost": pos.avg_cost,
                "current_nav": pos.current_nav,
            }

        # 序列化买入队列（date → str）
        buy_queue_data = {}
        for code, queue in portfolio._buy_queue.items():
            buy_queue_data[code] = [
                [shares, cost, buy_date.isoformat()]
                for shares, cost, buy_date in queue
            ]

        # 序列化交易记录
        transactions_data = []
        for txn in portfolio.transactions[-100:]:  # 只保留最近 100 条
            transactions_data.append({
                "date": txn.date.isoformat(),
                "fund_code": txn.fund_code,
                "action": txn.action,
                "shares": txn.shares,
                "price": txn.price,
                "amount": txn.amount,
                "commission": txn.commission,
                "reason": txn.reason,
            })

        state = {
            "initial_capital": portfolio.initial_cash,
            "start_date": getattr(self, "_start_date", trade_date.isoformat()),
            "last_updated": trade_date.isoformat(),
            "cash": portfolio.cash,
            "positions": positions_data,
            "_buy_queue": buy_queue_data,
            "transactions": transactions_data,
            "total_value": portfolio.total_value(),
            "total_return_pct": portfolio.total_return_pct(),
        }

        # 保留 start_date
        if self.STATE_FILE.exists():
            try:
                with open(self.STATE_FILE, "r", encoding="utf-8") as f:
                    old = json.load(f)
                    state["start_date"] = old.get("start_date", trade_date.isoformat())
            except Exception:
                pass
        self._start_date = state["start_date"]

        with open(self.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    # ── 每日运行 ─────────────────────────────────────────────────

    def run(
        self,
        target_date: date,
        fund_pool: Optional[List[str]] = None,
    ) -> LiveResult:
        """执行一天的实盘交易

        Args:
            target_date: 交易日期
            fund_pool: 可投资基金代码列表（默认：所有启用的基金）

        Returns:
            LiveResult 包含当日交易结果
        """
        # 1. 交易日检查
        calendar = get_trading_calendar()
        if not calendar.is_trading_day(target_date):
            return LiveResult(
                date=target_date,
                is_trading_day=False,
                skipped=True,
                skip_reason=f"{target_date} 非交易日",
            )

        # 2. 加载持仓
        portfolio = self.load_portfolio()
        total_before = portfolio.total_value()

        # 3. 获取基金净值
        if fund_pool is None:
            fund_pool = self._get_default_fund_pool()

        nav_map = {}
        unavailable_funds = []
        for code in fund_pool:
            nav_rec = self.fund_repo.get_nav_on_date(code, target_date)
            if nav_rec and nav_rec.nav and nav_rec.nav > 0:
                nav_map[code] = nav_rec.nav
            else:
                unavailable_funds.append(code)

        if unavailable_funds:
            logger.info(f"{len(unavailable_funds)} 只基金暂无 {target_date} 净值: {', '.join(unavailable_funds[:5])}...")

        if not nav_map:
            logger.warning(f"日期 {target_date}: 所有基金均无净值数据，跳过决策")
            return LiveResult(
                date=target_date,
                is_trading_day=True,
                skipped=True,
                skip_reason="无有效净值数据",
            )

        # 过滤 fund_pool 为有净值数据的基金
        available_pool = [c for c in fund_pool if c in nav_map]
        portfolio.update_navs(nav_map)

        # 4. 快照（决策前）
        snapshot_before = self._portfolio_to_dict(portfolio, target_date)

        # 5. AI 决策
        decisions = []
        market_ctx = None
        try:
            orders = self.decision_maker.decide(
                context_date=target_date,
                portfolio=portfolio,
                fund_pool=available_pool,
            )
            # 验证 + 执行
            valid_orders = self.order_manager.apply_constraints(
                orders, portfolio, nav_map
            )
            for order in valid_orders:
                if not order.is_trade:
                    continue
                try:
                    self.order_manager.execute(order, portfolio, nav_map, target_date)
                    decisions.append({
                        "fund_code": order.fund_code,
                        "action": order.action,
                        "amount": order.amount,
                        "reasoning": order.reasoning or "",
                        "confidence": order.confidence,
                    })
                except Exception as e:
                    logger.warning(f"执行订单失败 ({order.fund_code} {order.action}): {e}")
        except Exception as e:
            logger.error(f"AI 决策失败: {e}")
            # 决策失败不阻止快照更新和保存

        # 6. 快照（决策后）
        snapshot_after = self._portfolio_to_dict(portfolio, target_date)

        # 7. 保存状态
        try:
            self.save_portfolio(portfolio, target_date)
        except Exception as e:
            logger.error(f"保存持仓状态失败: {e}")

        # 8. 计算盈亏
        total_after = portfolio.total_value()
        pnl_day = total_after - total_before
        pnl_total_pct = portfolio.total_return_pct()

        return LiveResult(
            date=target_date,
            is_trading_day=True,
            snapshot_before=snapshot_before,
            snapshot_after=snapshot_after,
            decisions=decisions,
            market_context=self._market_to_dict(),
            pnl_day=pnl_day,
            pnl_total_pct=pnl_total_pct,
        )

    # ── 辅助方法 ─────────────────────────────────────────────────

    def _get_default_fund_pool(self) -> List[str]:
        """从配置中获取默认基金池"""
        try:
            from ..utils.config import ConfigLoader
            funds_cfg = ConfigLoader.load_funds()
            return [f["code"] for f in funds_cfg.get("funds", []) if f.get("enabled", True)]
        except Exception:
            logger.warning("无法加载基金配置，返回空列表")
            return []

    @staticmethod
    def _portfolio_to_dict(portfolio: Portfolio, trade_date: date) -> dict:
        """将持仓转换为可序列化的字典"""
        positions_list = []
        for code, pos in portfolio.positions.items():
            positions_list.append({
                "fund_code": code,
                "shares": round(pos.shares, 4),
                "avg_cost": round(pos.avg_cost, 4),
                "current_nav": round(pos.current_nav, 4) if pos.current_nav else 0,
                "market_value": round(pos.market_value, 2),
                "profit_loss_pct": round(pos.profit_loss_pct, 2),
            })

        return {
            "date": trade_date.isoformat(),
            "cash": round(portfolio.cash, 2),
            "total_market_value": round(portfolio.total_market_value(), 2),
            "total_value": round(portfolio.total_value(), 2),
            "total_return_pct": round(portfolio.total_return_pct(), 2),
            "position_count": portfolio.position_count(),
            "positions": positions_list,
        }

    @staticmethod
    def _market_to_dict() -> dict:
        """从决策制定器获取市场环境（简化版）"""
        # 市场环境在 decide() 内部构建，这里返回简化信息
        return {"note": "详见决策日志"}

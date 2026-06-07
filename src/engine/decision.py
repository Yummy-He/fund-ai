"""AI 决策引擎

整合 AI 客户端、提示词构建器和经验检索，生成买卖决策。
"""

import json
import logging
import os
from datetime import date, timedelta
from typing import Dict, List, Optional
import pandas as pd
import numpy as np

from .ai_client import AIClient
from .prompt import PromptBuilder
from .orders import Order
from ..data.models import FundSnapshot, MarketContext, MarketTrend, FundType, NAVRecord
from ..data.store import FundRepository

logger = logging.getLogger("fund_ai.engine.decision")


class FundDecisionMaker:
    """基金投资 AI 决策引擎

    核心职责:
    1. 收集当前时刻的所有数据（持仓、基金净值、市场信息）
    2. 检索相关的历史经验
    3. 构建 prompt 并调用 AI
    4. 解析 AI 回复中的交易订单
    """

    def __init__(
        self,
        ai_client: AIClient,
        prompt_builder: PromptBuilder,
        fund_repo: FundRepository,
        experience_retriever=None,  # 延迟引用，避免循环导入
        strategy_patterns: Optional[List[dict]] = None,
    ):
        self.ai = ai_client
        self.prompt = prompt_builder
        self.repo = fund_repo
        self.retriever = experience_retriever
        self.strategy_patterns = strategy_patterns or []
        self._last_context: dict = {}

    @property
    def last_context(self) -> dict:
        """最近一次决策的完整上下文（供经验记录使用）"""
        return self._last_context

    def decide(
        self,
        context_date: date,
        portfolio,
        fund_pool: List[str],
        market: Optional[MarketContext] = None,
    ) -> List[Order]:
        """在给定日期为投资组合做出决策

        Args:
            context_date: 当前回测日期
            portfolio: 当前投资组合
            fund_pool: 可投资的基金代码列表
            market: 市场环境（可选，如果为 None 则构建简单的）

        Returns:
            AI 生成的交易订单列表
        """
        # 1. 获取当前日期各基金的快照
        self._last_context = {}
        nav_map = self._get_current_navs(fund_pool, context_date)
        if not nav_map:
            logger.warning(f"日期 {context_date}: 无有效净值数据，跳过决策")
            return []

        # 更新持仓净值
        portfolio.update_navs(nav_map)

        # 2. 获取基金快照
        fund_snapshots = self._get_fund_snapshots(fund_pool, context_date, nav_map)

        # 3. 构建/获取市场环境
        if market is None:
            market = self._build_market_context(context_date)

        # 4. 格式化持仓数据
        portfolio_text = self.prompt.format_portfolio_status(portfolio, nav_map)
        fund_data_text = self.prompt.format_fund_snapshots(fund_snapshots, nav_map)

        # 5. 检索相似历史经验
        experiences_text = ""
        if self.retriever:
            try:
                current_scenario = {
                    "date": context_date,
                    "fund_type": fund_snapshots[0].fund_type.value if fund_snapshots else "MIXED",
                    "market_trend": market.market_trend.value,
                    "market_volatility": market.market_volatility,
                    "cash_ratio": portfolio.cash / portfolio.total_value() if portfolio.total_value() > 0 else 1.0,
                }
                retrieved = self.retriever.retrieve(current_scenario, {})
                experiences_text = self.prompt.format_experiences(retrieved)
            except Exception as e:
                logger.warning(f"经验检索失败: {e}")

        # 6. 构建 system prompt
        system_prompt = self.prompt.build_system_prompt(
            strategy_patterns=self.strategy_patterns,
            experiences_text=experiences_text,
        )

        # 7. 构建 user message
        user_message = self.prompt.build_decision_user_message(
            context_date=context_date,
            portfolio_status=portfolio_text,
            fund_data=fund_data_text,
            market=market,
            constraints={
                "max_positions": 10,
                "max_single_position_pct": 0.30,
                "min_cash_reserve": 500,
                "min_trade_amount": 100,
            },
            commission={
                "buy_rate": 0.0015,
                "sell_rate": 0.0050,
            },
        )

        # 8. 调用 AI
        try:
            response = self.ai.chat_json(
                system_prompt=system_prompt,
                user_message=user_message,
            )
        except Exception as e:
            logger.error(f"AI 决策调用失败: {e}")
            return []

        # 9. 解析订单
        orders = self._parse_orders(response)

        # 10. 捕获决策上下文（供后续经验记录）
        total_val = portfolio.total_value()
        self._last_context = {
            "context_date": context_date,
            "market": market,
            "fund_snapshots": fund_snapshots,
            "nav_map": nav_map,
            "cash_ratio": portfolio.cash / total_val if total_val > 0 else 1.0,
            "portfolio_return": portfolio.total_return_pct(),
            "fund_pool": fund_pool,
            "orders": orders,
        }

        logger.debug(
            f"日期 {context_date}: AI 生成 {len(orders)} 条决策 "
            f"(其中交易 {sum(1 for o in orders if o.is_trade)} 条)"
        )
        return orders

    def generate_lessons(self, result) -> dict:
        """回测结束后，用 Pro 模型深度总结策略教训"""
        try:
            user_message = self.prompt.build_summary_user_message(result)
            system_prompt = (
                "你是一位经验丰富的基金投资分析专家。"
                "请深入分析回测中每笔决策的得失，提炼可复用的策略模式。"
                "以 JSON 格式输出你的深度分析。"
            )

            # 使用 Pro 模型做深度分析
            return self.ai.chat_advanced(
                system_prompt=system_prompt,
                user_message=user_message,
                json_mode=True,
            )
        except Exception as e:
            logger.error(f"AI 策略总结失败: {e}")
            return {"overall_grade": "N/A", "key_lessons": [], "summary": str(e)}

    def _get_current_navs(
        self,
        fund_pool: List[str],
        target_date: date,
    ) -> Dict[str, float]:
        """获取所有基金在指定日期的最新净值

        基金净值是 T+1 公布的，这里查找 target_date 或之前最近的有效净值。
        """
        nav_map = {}
        for code in fund_pool:
            nav_record = self.repo.get_nav_on_date(code, target_date)
            if nav_record:
                nav_map[code] = nav_record.nav
        return nav_map

    def _get_fund_snapshots(
        self,
        fund_pool: List[str],
        target_date: date,
        nav_map: Dict[str, float],
    ) -> List[FundSnapshot]:
        """构建所有基金的当前快照"""
        snapshots = []
        for code in fund_pool:
            fund = self.repo.get_fund(code)
            fund_name = fund.name if fund else code
            fund_type = fund.fund_type if fund else FundType.MIXED

            nav = nav_map.get(code, 0.0)

            # 计算各时间段的收益率
            change_7d = self._calc_return(code, target_date, 7)
            change_30d = self._calc_return(code, target_date, 30)
            change_90d = self._calc_return(code, target_date, 90)
            change_180d = self._calc_return(code, target_date, 180)

            # 近30日波动率
            vol_30d = self._calc_volatility(code, target_date, 30)
            # 近90日最大回撤
            max_dd_90d = self._calc_max_drawdown(code, target_date, 90)

            snapshots.append(FundSnapshot(
                fund_code=code,
                fund_name=fund_name,
                fund_type=fund_type,
                current_nav=nav,
                change_7d=round(change_7d, 2),
                change_30d=round(change_30d, 2),
                change_90d=round(change_90d, 2),
                change_180d=round(change_180d, 2),
                volatility_30d=round(vol_30d, 2),
                max_drawdown_90d=round(max_dd_90d, 2),
            ))
        return snapshots

    def _build_market_context(self, target_date: date) -> MarketContext:
        """构建市场环境快照（从沪深300数据）"""
        try:
            import pandas as pd
            index_path = self.repo.index_dir / "000300.csv"
            if index_path.exists():
                # parse_dates + index_col 确保日期列直接转为 DatetimeIndex
                # 避免 df.index.dtype==object 时与 pd.Timestamp 比较失败
                index_df = pd.read_csv(
                    str(index_path), encoding="utf-8-sig",
                    parse_dates=["date"], index_col="date",
                )
                if not index_df.empty:
                    return self._extract_market_from_df(index_df, target_date)
        except Exception as e:
            logger.warning(
                "构建市场环境失败（将使用默认值 CSI300=3500）: %s | 路径=%s",
                e, self.repo.index_dir / "000300.csv"
            )

        # 简单回退（仅在数据文件缺失或解析失败时使用）
        logger.warning("⚠ 市场环境使用硬编码默认值: CSI300=3500, 涨跌幅=0, 波动率=15%")
        return MarketContext(
            date=target_date,
            csi300_level=3500.0,
            csi300_change_30d=0.0,
            csi300_change_90d=0.0,
            market_trend=MarketTrend.SIDEWAYS,
            market_volatility=0.15,
        )

    def _extract_market_from_df(
        self,
        df: pd.DataFrame,
        target_date: date,
    ) -> MarketContext:
        """从指数数据 DataFrame 提取市场环境"""
        # 确保 index 是 DatetimeIndex（兜底保护）
        if not isinstance(df.index, pd.DatetimeIndex):
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date")
            else:
                df.index = pd.to_datetime(df.index)

        df = df.sort_index()
        mask = df.index <= pd.Timestamp(target_date)
        if not mask.any():
            return MarketContext(date=target_date)

        recent = df[mask]
        latest_val = float(recent.iloc[-1]["close"]) if "close" in recent.columns else 3500.0

        # 计算各时间段变化
        def get_change(days: int) -> float:
            past_date = target_date - timedelta(days=days)
            past_mask = df.index <= pd.Timestamp(past_date)
            if past_mask.any():
                past_val = float(df[past_mask].iloc[-1]["close"]) if "close" in df.columns else latest_val
                if past_val > 0:
                    return (latest_val - past_val) / past_val * 100
            return 0.0

        change_30d = get_change(30)
        change_90d = get_change(90)

        # 判断市场趋势
        if change_30d > 5:
            trend = MarketTrend.BULL
        elif change_30d < -5:
            trend = MarketTrend.BEAR
        else:
            trend = MarketTrend.SIDEWAYS

        # 计算波动率
        recent_30 = recent.tail(30) if len(recent) >= 30 else recent
        if len(recent_30) >= 5 and "close" in recent_30.columns:
            returns = recent_30["close"].pct_change().dropna()
            volatility = float(returns.std())
        else:
            volatility = 0.15

        return MarketContext(
            date=target_date,
            csi300_level=latest_val,
            csi300_change_30d=round(change_30d, 2),
            csi300_change_90d=round(change_90d, 2),
            market_trend=trend,
            market_volatility=round(volatility, 4),
        )

    def _calc_return(self, fund_code: str, target_date: date, lookback: int) -> float:
        """计算基金在 target_date 回溯 lookback 天的收益率"""
        df = self.repo.get_nav_history(fund_code)
        if df.empty or "单位净值" not in df.columns:
            return 0.0

        # 兜底：确保日期列是 datetime（GH Actions 某些 pandas 版本 get_nav_history 可能转换失败）
        if "净值日期" in df.columns and df["净值日期"].dtype == object:
            df["净值日期"] = pd.to_datetime(df["净值日期"])

        df = df[df["净值日期"] <= pd.Timestamp(target_date)]
        if len(df) < 2:
            return 0.0

        current_nav = float(df.iloc[-1]["单位净值"])
        past_date = target_date - timedelta(days=lookback)
        past_df = df[df["净值日期"] <= pd.Timestamp(past_date)]
        if past_df.empty:
            past_nav = float(df.iloc[0]["单位净值"])
        else:
            past_nav = float(past_df.iloc[-1]["单位净值"])

        if past_nav > 0:
            return (current_nav - past_nav) / past_nav * 100
        return 0.0

    def _calc_volatility(self, fund_code: str, target_date: date, lookback: int) -> float:
        """计算基金在 target_date 回溯 lookback 天的年化波动率"""
        df = self.repo.get_nav_history(fund_code)
        if df.empty or "日增长率" not in df.columns:
            return 0.0

        if "净值日期" in df.columns and df["净值日期"].dtype == object:
            df["净值日期"] = pd.to_datetime(df["净值日期"])

        df = df[df["净值日期"] <= pd.Timestamp(target_date)]
        recent = df.tail(lookback) if len(df) >= lookback else df
        if len(recent) < 5:
            return 0.0

        daily_returns = recent["日增长率"].dropna().astype(float) / 100.0
        if len(daily_returns) < 2:
            return 0.0

        return float(daily_returns.std() * np.sqrt(252) * 100)

    def _calc_max_drawdown(self, fund_code: str, target_date: date, lookback: int) -> float:
        """计算基金在 target_date 回溯 lookback 天的最大回撤"""
        df = self.repo.get_nav_history(fund_code)
        if df.empty or "单位净值" not in df.columns:
            return 0.0

        if "净值日期" in df.columns and df["净值日期"].dtype == object:
            df["净值日期"] = pd.to_datetime(df["净值日期"])

        df = df[df["净值日期"] <= pd.Timestamp(target_date)]
        recent = df.tail(lookback) if len(df) >= lookback else df
        if len(recent) < 2:
            return 0.0

        navs = recent["单位净值"].values.astype(float)
        peaks = np.maximum.accumulate(navs)
        drawdowns = (navs - peaks) / peaks
        return float(np.min(drawdowns) * 100)

    @staticmethod
    def _parse_orders(response: dict) -> List[Order]:
        """从 AI 回复的 JSON 中解析交易订单"""
        orders = []

        # 处理不同的 JSON 格式
        decisions = response.get("decisions", response.get("orders", []))
        if not decisions:
            # 尝试整个 response 作为 decisions 列表
            if isinstance(response, list):
                decisions = response

        for item in decisions:
            if not isinstance(item, dict):
                continue

            action = item.get("action", "hold").lower()
            if action not in ("buy", "sell", "hold", "increase", "decrease"):
                action = "hold"

            orders.append(Order(
                fund_code=str(item.get("fund_code", "")),
                action=action,
                amount=float(item.get("amount", 0)),
                reasoning=str(item.get("reasoning", "")),
                confidence=float(item.get("confidence", 0.5)),
            ))

        return orders

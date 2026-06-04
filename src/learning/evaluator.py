"""策略评估器 - 跨回测分析和模式提取

从多次回测中提取经验教训，生成高级策略模式。
这些模式将被注入到后续回测的 system prompt 中。
"""

import json
import logging
from typing import Dict, List, Optional
from collections import defaultdict

from .experience import Experience, ExperienceStore

logger = logging.getLogger("fund_ai.learning.evaluator")


class StrategyEvaluator:
    """跨回测策略评估器

    分析多轮回测的结果，提炼可复用的投资策略模式。
    """

    def __init__(self, store: ExperienceStore):
        self.store = store

    def compare_backtests(
        self,
        results: list,
    ) -> dict:
        """比较多次回测的结果"""
        if not results:
            return {"best": None, "worst": None, "avg_return": 0.0, "improvement": 0.0}

        returns = [r.total_return for r in results if hasattr(r, 'total_return')]
        sharpes = [r.sharpe_ratio for r in results if hasattr(r, 'sharpe_ratio')]

        best_idx = max(range(len(returns)), key=lambda i: returns[i]) if returns else -1
        worst_idx = min(range(len(returns)), key=lambda i: returns[i]) if returns else -1

        comparison = {
            "count": len(results),
            "avg_return": sum(returns) / len(returns) if returns else 0.0,
            "avg_sharpe": sum(sharpes) / len(sharpes) if sharpes else 0.0,
            "best_return": returns[best_idx] if best_idx >= 0 else 0.0,
            "worst_return": returns[worst_idx] if worst_idx >= 0 else 0.0,
            "returns_series": returns,
        }

        # 计算改进趋势（后几次回报 vs 前几次回报）
        n = len(returns)
        if n >= 4:
            first_half = sum(returns[:n//2]) / (n//2)
            second_half = sum(returns[n//2:]) / (n - n//2)
            comparison["improvement"] = second_half - first_half
        else:
            comparison["improvement"] = 0.0

        return comparison

    def identify_patterns(
        self,
        experiences: Optional[List[Experience]] = None,
    ) -> List[dict]:
        """从经验中识别盈利策略模式

        按 基金类型 + 市场状态 + 决策动作 聚合分析经验，
        找出高频盈利的组合模式。
        """
        if experiences is None:
            experiences = self.store.load_all()

        if not experiences:
            return []

        # 聚合
        groups = defaultdict(list)
        for exp in experiences:
            key = (
                exp.scenario.fund_type or "UNKNOWN",
                exp.scenario.market_trend or "unknown",
                exp.decision.action or "hold",
            )
            groups[key].append(exp)

        patterns = []
        for (fund_type, trend, action), exps in groups.items():
            if len(exps) < 3:  # 至少3条经验才算一个模式
                continue

            profitable = [e for e in exps if e.outcome.was_profitable]
            if not profitable:
                continue

            win_rate = len(profitable) / len(exps)
            if win_rate < 0.5:  # 胜率低于50%的不算有效模式
                continue

            avg_return_30d = sum(e.outcome.return_30d for e in exps) / len(exps)

            market_cn = {"bull": "牛市", "bear": "熊市", "sideways": "震荡市"}.get(trend, trend)
            action_cn = {"buy": "买入", "sell": "卖出", "hold": "持有", "increase": "加仓", "decrease": "减仓"}.get(action, action)

            patterns.append({
                "pattern": f"{market_cn}中{action_cn}{fund_type}类基金",
                "description": (
                    f"在{market_cn}环境下，对{fund_type}类基金执行{action_cn}操作："
                    f"{len(exps)}次经验，胜率 {win_rate:.0%}，平均30日回报 {avg_return_30d:+.2f}%"
                ),
                "fund_type": fund_type,
                "market_trend": trend,
                "action": action,
                "sample_count": len(exps),
                "win_rate": round(win_rate, 2),
                "avg_return": round(avg_return_30d, 2),
                "confidence": "high" if win_rate > 0.7 else "medium",
            })

        # 按样本数降序排列
        patterns.sort(key=lambda p: p["sample_count"], reverse=True)
        return patterns[:10]

    def generate_strategy_summary(
        self,
        ai_client=None,
        prompt_builder=None,
    ) -> str:
        """生成策略总结文本

        Args:
            ai_client: AI 客户端（可选，用于生成更优质的总结）
            prompt_builder: 提示词构建器

        Returns:
            策略总结文本（中文自然语言）
        """
        patterns = self.identify_patterns()
        stats = self.store.stats()

        lines = []
        lines.append(f"## 投资经验总览")
        lines.append(f"- 总经验数: {stats['total']}")
        lines.append(f"- 回测次数: {len(stats.get('backtests', []))}")
        lines.append(f"- 覆盖基金类型: {', '.join(stats.get('by_fund_type', {}).keys())}")

        if patterns:
            lines.append(f"\n## 已验证策略模式 ({len(patterns)} 条)")
            for i, p in enumerate(patterns, 1):
                conf_emoji = "🟢" if p["confidence"] == "high" else "🟡"
                lines.append(
                    f"{i}. {conf_emoji} **{p['pattern']}** "
                    f"(样本: {p['sample_count']}, 胜率: {p['win_rate']:.0%}, "
                    f"均收益: {p['avg_return']:+.1f}%)"
                )
        else:
            lines.append("\n（暂无足够数据识别策略模式，继续回测积累）")

        return "\n".join(lines)

    def detect_improvement(self, results: list) -> bool:
        """检测 AI 决策是否在改进"""
        if len(results) < 3:
            return False

        recent_3 = [r.total_return for r in results[-3:] if hasattr(r, 'total_return')]
        earlier_3 = [r.total_return for r in results[:3] if hasattr(r, 'total_return')]

        if not recent_3 or not earlier_3:
            return False

        return sum(recent_3) / 3 > sum(earlier_3) / 3

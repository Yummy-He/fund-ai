"""经验检索器 - 多因子相似度评分

当 AI 需要在某个时点做决策时，检索器从经验库中找到与当前场景最相似的过去经验。

相似度维度:
  1. 基金类型匹配 (0.25)
  2. 市场状态匹配 (0.15)
  3. 净值动量相似度 (0.20)
  4. 组合状态相似度 (0.10)
  5. 结果质量加分 (0.15) -- 优先参考盈利经验
  6. 时效性 (0.10) -- 近期经验更有参考价值
  7. 多样性提升 (0.05) -- 确保包含不同角度的经验
"""

import json
import logging
from datetime import date, datetime
from typing import Dict, List, Optional

from .experience import Experience, ExperienceStore

logger = logging.getLogger("fund_ai.learning.retriever")


class ExperienceRetriever:
    """多因子经验检索器

    不使用向量数据库，而是基于多因子加权评分进行检索。
    在经验数 < 10,000 的规模下，直接评分即可满足性能需求。
    """

    # 各维度权重
    WEIGHTS = {
        "fund_type": 0.25,      # 基金类型匹配
        "market_trend": 0.20,   # 市场状态匹配
        "momentum": 0.20,       # 净值动量相似
        "portfolio_state": 0.10, # 组合状态相似
        "outcome_quality": 0.10, # 结果质量
        "recency": 0.10,        # 时效性
        "diversity": 0.05,      # 多样性
    }

    def __init__(
        self,
        store: ExperienceStore,
        top_k: int = 10,
        always_include_failures: int = 2,
        similarity_threshold: float = 0.3,
    ):
        self.store = store
        self.top_k = top_k
        self.always_include_failures = always_include_failures
        self.similarity_threshold = similarity_threshold

    def retrieve(
        self,
        scenario: dict,
        portfolio_state: dict,
        top_k: Optional[int] = None,
    ) -> List[Experience]:
        """检索与当前场景最相似的历史经验

        Args:
            scenario: 当前场景 (包含 fund_type, market_trend, market_volatility 等)
            portfolio_state: 当前组合状态
            top_k: 返回经验数

        Returns:
            排序后的经验列表（最相似的在前）
        """
        top_k = top_k or self.top_k

        # 加载所有经验
        all_experiences = self.store.load_all()
        if not all_experiences:
            logger.info("经验库为空，无法检索")
            return []

        # 计算每条经验的相似度
        scored = []
        for exp in all_experiences:
            score = self._compute_similarity(exp, scenario, portfolio_state)
            if score >= self.similarity_threshold:
                scored.append((exp, score))

        # 按分数降序排列
        scored.sort(key=lambda x: x[1], reverse=True)

        # 选取结果
        selected = []
        failures = [(e, s) for e, s in scored
                     if hasattr(e, 'outcome') and not e.outcome.was_profitable]
        successes = [(e, s) for e, s in scored if e not in dict(failures)]

        # 始终包含一些失败案例（避免只看到成功经验）
        n_failures = min(self.always_include_failures, len(failures))
        selected.extend(failures[:n_failures])

        # 剩余选择成功经验
        remaining = top_k - len(selected)
        selected.extend(successes[:remaining])

        # 去重并排序
        seen_ids = set()
        unique = []
        for exp, score in selected:
            if exp.id not in seen_ids:
                seen_ids.add(exp.id)
                unique.append(exp)

        return unique[:top_k]

    def _compute_similarity(
        self,
        exp: Experience,
        scenario: dict,
        portfolio_state: dict,
    ) -> float:
        """计算经验与当前场景的加权相似度分数 0-1"""
        scores = {}

        # 1. 基金类型匹配
        exp_fund_type = exp.scenario.fund_type or ""
        cur_fund_type = scenario.get("fund_type", "")
        scores["fund_type"] = 1.0 if exp_fund_type == cur_fund_type else 0.3

        # 2. 市场状态匹配
        exp_trend = exp.scenario.market_trend or "sideways"
        cur_trend = scenario.get("market_trend", "sideways")
        if exp_trend == cur_trend:
            scores["market_trend"] = 1.0
        elif (exp_trend, cur_trend) in [("bull", "bear"), ("bear", "bull")]:
            scores["market_trend"] = 0.1  # 相反市场，参考价值低
        else:
            scores["market_trend"] = 0.5  # 部分相关

        # 3. 净值动量相似度（波动率接近程度）
        exp_vol = exp.scenario.market_volatility or 0.15
        cur_vol = scenario.get("market_volatility", 0.15)
        if max(exp_vol, cur_vol) > 0:
            vol_diff = abs(exp_vol - cur_vol) / max(exp_vol, cur_vol, 0.01)
            scores["momentum"] = max(0.0, 1.0 - vol_diff)
        else:
            scores["momentum"] = 1.0

        # 4. 组合状态相似度（现金比例）
        exp_cash = exp.scenario.cash_ratio or 0.5
        cur_cash = scenario.get("cash_ratio", 0.5)
        cash_diff = abs(exp_cash - cur_cash)
        scores["portfolio_state"] = max(0.0, 1.0 - cash_diff)

        # 5. 结果质量
        if exp.outcome.was_profitable:
            scores["outcome_quality"] = 1.0
        else:
            scores["outcome_quality"] = 0.3  # 失败经验也有参考价值，但权重较低

        # 6. 时效性（更近期的经验分数更高）
        try:
            exp_date = datetime.fromisoformat(exp.timestamp[:10])
            days_ago = (datetime.now() - exp_date).days
            if days_ago <= 30:
                scores["recency"] = 1.0
            elif days_ago <= 180:
                scores["recency"] = 0.7
            elif days_ago <= 365:
                scores["recency"] = 0.5
            else:
                scores["recency"] = 0.3
        except (ValueError, TypeError):
            scores["recency"] = 0.5

        # 7. 多样性暂不计算（在选择阶段通过包含失败案例实现）

        # 加权求和
        total = sum(
            scores.get(k, 0) * self.WEIGHTS.get(k, 0)
            for k in self.WEIGHTS
        )
        return round(total, 4)

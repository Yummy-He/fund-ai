"""经验合并裁剪器

当经验库超过阈值时，按 (fund_type, market_trend, action) 分桶，
每桶内按质量分排序，保留 top N 条，裁剪冗余/低质量经验。

质量分维度:
  - recency 40%: 越新越高
  - outcome 30%: 盈利 + 收益幅度（大赢大亏都有参考价值）
  - confidence 20%: AI 决策时的信心度
  - diversity 10%: 小幅随机噪声，防止同级经验排序完全一致
"""

import logging
import random
from datetime import datetime
from typing import List

from .experience import Experience, ExperienceStore
from ..utils.date_utils import beijing_now

logger = logging.getLogger("fund_ai.learning.consolidator")


class ExperienceConsolidator:
    """经验裁剪器 — 保持经验库在可控规模"""

    def __init__(self, store: ExperienceStore, max_experiences: int = 2000):
        self.store = store
        self.max_experiences = max_experiences

    def consolidate(self) -> int:
        """裁剪经验库到 max_experiences 以内。

        返回被删除的经验条数。
        """
        all_exp = self.store.load_all()
        original_count = len(all_exp)

        if original_count <= self.max_experiences:
            logger.info(f"经验库 {original_count} 条未超阈值 {self.max_experiences}，跳过裁剪")
            return 0

        # 按 (fund_type, market_trend, action) 分桶
        buckets: dict = {}
        for exp in all_exp:
            key = (
                exp.scenario.fund_type or "UNKNOWN",
                exp.scenario.market_trend or "unknown",
                exp.decision.action or "hold",
            )
            if key not in buckets:
                buckets[key] = []
            buckets[key].append(exp)

        # 每桶按质量分排序
        for key in buckets:
            buckets[key].sort(key=self.quality_score, reverse=True)

        # 计算每桶保留名额（按桶大小比例分配）
        kept = []
        total_quota = self.max_experiences
        # 每个桶至少保留 2 条代表性经验
        min_per_bucket = 2
        guaranteed = min_per_bucket * len(buckets)
        remaining_quota = max(0, total_quota - guaranteed)

        # 分配剩余名额（按桶大小比例）
        for i, (key, exps) in enumerate(buckets.items()):
            bucket_size = len(exps)
            if i < len(buckets) - 1:
                proportion = bucket_size / original_count
                quota = min_per_bucket + int(remaining_quota * proportion)
            else:
                # 最后一个桶拿剩余所有名额（处理取整误差）
                quota = total_quota - len(kept)
            quota = max(min_per_bucket, min(quota, bucket_size))
            kept.extend(exps[:quota])

        # 裁剪后写入
        self.store.replace_all(kept)
        removed = original_count - len(kept)
        logger.info(
            f"经验裁剪完成: {original_count} → {len(kept)} 条 "
            f"(移除 {removed} 条, {len(buckets)} 个分桶)"
        )
        return removed

    @staticmethod
    def quality_score(exp: Experience) -> float:
        """综合质量分 0-100。

        - recency 40%: 越新分数越高（180天内满分，之后线性衰减）
        - outcome 30%: 盈利 30 分，且收益幅度越大加分越多
        - confidence 20%: AI 当时的信心度
        - diversity 10%: 小幅随机噪声
        """
        score = 0.0

        # recency (40)
        try:
            if exp.timestamp:
                exp_dt = datetime.fromisoformat(exp.timestamp[:19])
                days_ago = (beijing_now() - exp_dt).days
                if days_ago <= 180:
                    score += 40.0
                elif days_ago <= 365:
                    score += 40.0 * (365 - days_ago) / 185  # 线性衰减
                else:
                    score += 5.0  # 1年以上保底
        except (ValueError, TypeError):
            score += 20.0

        # outcome (30)
        if exp.outcome.was_profitable:
            score += 20.0
            # 收益幅度加分（上限 10）
            bonus = min(abs(exp.outcome.return_30d) / 2, 10.0)
            score += bonus
        else:
            # 大亏也有参考价值
            loss_magnitude = min(abs(exp.outcome.return_30d), 30.0)
            score += loss_magnitude / 3  # 最多 10 分

        # confidence (20)
        score += exp.decision.confidence * 20.0

        # diversity noise (10)
        score += random.uniform(0, 10.0)

        return round(score, 2)

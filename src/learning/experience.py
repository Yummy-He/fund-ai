"""经验存储模块

每次 AI 决策及其结果被存储为一条"经验"。
所有回测的经验汇总，构成 AI 的"投资记忆"。

存储结构:
  experiences/
  ├── index.json              # 经验索引（总览）
  ├── decisions/               # 每条决策细节
  │   └── {backtest_id}_decisions.json
  └── summaries/               # 每次回测的策略总结
      └── {backtest_id}_summary.json
"""

import json
import os
import uuid
import logging
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..utils.date_utils import beijing_now

logger = logging.getLogger("fund_ai.learning.experience")


@dataclass
class ScenarioSnapshot:
    """场景快照 - 决策时的市场状态"""
    date: str = ""
    fund_code: str = ""
    fund_type: str = ""
    nav_current: float = 0.0
    nav_change_7d: float = 0.0
    nav_change_30d: float = 0.0
    nav_change_90d: float = 0.0
    market_trend: str = "sideways"
    market_volatility: float = 0.0
    cash_ratio: float = 0.0
    portfolio_return: float = 0.0


@dataclass
class DecisionRecord:
    """决策记录"""
    action: str = "hold"
    amount_rmb: float = 0.0
    amount_pct: float = 0.0
    reasoning: str = ""
    confidence: float = 0.5
    model: str = ""


@dataclass
class OutcomeRecord:
    """结果记录 - 决策后的市场表现"""
    return_7d: float = 0.0
    return_30d: float = 0.0
    return_90d: float = 0.0
    was_profitable: bool = False
    relative_to_benchmark: float = 0.0


@dataclass
class Experience:
    """一条完整的经验记录

    scenario + decision + outcome + lesson = 一个完整的投资经验
    """
    id: str = ""
    backtest_id: str = ""
    timestamp: str = ""
    scenario: ScenarioSnapshot = field(default_factory=ScenarioSnapshot)
    decision: DecisionRecord = field(default_factory=DecisionRecord)
    outcome: OutcomeRecord = field(default_factory=OutcomeRecord)
    lesson: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())[:12]

    def to_dict(self) -> dict:
        """转换为字典用于 JSON 序列化"""
        return {
            "id": self.id,
            "backtest_id": self.backtest_id,
            "timestamp": self.timestamp,
            "scenario": asdict(self.scenario),
            "decision": asdict(self.decision),
            "outcome": asdict(self.outcome),
            "lesson": self.lesson,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Experience":
        """从字典恢复"""
        return cls(
            id=data.get("id", ""),
            backtest_id=data.get("backtest_id", ""),
            timestamp=data.get("timestamp", ""),
            scenario=ScenarioSnapshot(**data.get("scenario", {})),
            decision=DecisionRecord(**data.get("decision", {})),
            outcome=OutcomeRecord(**data.get("outcome", {})),
            lesson=data.get("lesson", ""),
        )


class ExperienceStore:
    """经验持久化存储

    管理所有回测经验的生命周期：新增、查询、统计。
    """

    def __init__(self, base_dir: str = "experiences"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.decisions_dir = self.base_dir / "decisions"
        self.decisions_dir.mkdir(parents=True, exist_ok=True)
        self.summaries_dir = self.base_dir / "summaries"
        self.summaries_dir.mkdir(parents=True, exist_ok=True)

        # 经验索引
        self.index_path = self.base_dir / "index.json"
        self.index = self._load_index()

    def add(self, exp: Experience) -> None:
        """添加一条经验"""
        # 追加到决策文件
        bt_file = self.decisions_dir / f"{exp.backtest_id}_decisions.json"
        existing = []
        if bt_file.exists():
            try:
                with open(bt_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except json.JSONDecodeError:
                existing = []

        existing.append(exp.to_dict())
        with open(bt_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        # 更新索引
        self._update_index(exp)
        self._save_index()

    def add_batch(self, experiences: List[Experience]) -> None:
        """批量添加经验（高性能：按 backtest_id 分组，每文件一次读写）"""
        from collections import defaultdict
        grouped = defaultdict(list)
        for exp in experiences:
            if not exp.id:
                exp.id = str(uuid.uuid4())[:12]
            grouped[exp.backtest_id].append(exp)
        for bt_id, exps in grouped.items():
            bt_file = self.decisions_dir / f"{bt_id}_decisions.json"
            existing = []
            if bt_file.exists():
                try:
                    with open(bt_file, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                except json.JSONDecodeError:
                    pass
            existing.extend([exp.to_dict() for exp in exps])
            with open(bt_file, "w", encoding="utf-8") as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
        for exp in experiences:
            self._update_index(exp)
        self._save_index()

    def replace_all(self, experiences: List[Experience]) -> int:
        """替换全部经验（consolidation 后写入）。返回写入条数。"""
        for f in self.decisions_dir.glob("*_decisions.json"):
            f.unlink()
        self.index = {"version": 1, "total_experiences": 0, "backtests": [],
                       "by_fund_type": {}, "last_updated": ""}
        self.add_batch(experiences)
        return len(experiences)

    def load_all(self) -> List[Experience]:
        """加载所有经验"""
        all_experiences = []
        for f in self.decisions_dir.glob("*_decisions.json"):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                for item in data:
                    all_experiences.append(Experience.from_dict(item))
            except Exception as e:
                logger.warning(f"加载经验文件 {f} 失败: {e}")
        return all_experiences

    def get_by_backtest(self, backtest_id: str) -> List[Experience]:
        """获取某次回测的所有经验"""
        file_path = self.decisions_dir / f"{backtest_id}_decisions.json"
        if not file_path.exists():
            return []
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [Experience.from_dict(item) for item in data]

    def save_summary(self, backtest_id: str, summary: dict) -> None:
        """保存回测策略总结"""
        summary["backtest_id"] = backtest_id
        summary["saved_at"] = beijing_now().isoformat()
        path = self.summaries_dir / f"{backtest_id}_summary.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

    def get_latest_summaries(self, n: int = 5) -> List[dict]:
        """获取最近的 N 份总结"""
        files = sorted(
            self.summaries_dir.glob("*_summary.json"),
            key=lambda p: p.stat().st_mtime, reverse=True
        )
        summaries = []
        for f in files[:n]:
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    summaries.append(json.load(fh))
            except Exception:
                pass
        return summaries

    def total_count(self) -> int:
        """总经验数"""
        return self.index.get("total_experiences", 0)

    def stats(self) -> dict:
        """经验统计"""
        self.index = self._load_index()
        return {
            "total": self.index.get("total_experiences", 0),
            "backtests": self.index.get("backtests", []),
            "by_fund_type": self.index.get("by_fund_type", {}),
        }

    def _load_index(self) -> dict:
        if self.index_path.exists():
            try:
                with open(self.index_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                pass
        return {
            "version": 1,
            "total_experiences": 0,
            "backtests": [],
            "by_fund_type": {},
            "last_updated": "",
        }

    def _update_index(self, exp: Experience) -> None:
        """更新内存中索引计数（不写盘，由调用方负责保存）"""
        self.index["total_experiences"] = self.index.get("total_experiences", 0) + 1
        if exp.backtest_id not in self.index.get("backtests", []):
            self.index.setdefault("backtests", []).append(exp.backtest_id)
        fund_type = exp.scenario.fund_type
        self.index.setdefault("by_fund_type", {})
        self.index["by_fund_type"][fund_type] = self.index["by_fund_type"].get(fund_type, 0) + 1
        self.index["last_updated"] = beijing_now().isoformat()

    def _save_index(self) -> None:
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(self.index, f, ensure_ascii=False, indent=2)

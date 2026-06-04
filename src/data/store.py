"""CSV 数据存储模块

基金净值数据以 CSV 格式存储在 data/nav/{fund_code}.csv
每行包含: 日期,单位净值,累计净值,日增长率
"""

import os
import json
import logging
from pathlib import Path
from datetime import date
from typing import Optional, Dict, List
import pandas as pd

from .models import Fund, NAVRecord

logger = logging.getLogger("fund_ai.data.store")


class CSVStore:
    """通用 CSV 文件读写"""

    @staticmethod
    def save(df: pd.DataFrame, path: str) -> None:
        """保存 DataFrame 到 CSV"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        df.to_csv(path, index=False, encoding="utf-8-sig")
        logger.debug(f"已保存 {len(df)} 行到 {path}")

    @staticmethod
    def load(path: str) -> pd.DataFrame:
        """从 CSV 加载 DataFrame"""
        if not os.path.exists(path):
            return pd.DataFrame()
        return pd.read_csv(path, encoding="utf-8-sig", dtype={"基金代码": str})

    @staticmethod
    def append(path: str, df: pd.DataFrame) -> None:
        """追加数据到 CSV（去重后追加）"""
        existing = CSVStore.load(path)
        if existing.empty:
            CSVStore.save(df, path)
            return

        # 合并去重（基于日期+基金代码，如果有的话）
        combined = pd.concat([existing, df], ignore_index=True)
        dedup_cols = ["净值日期"] if "净值日期" in combined.columns else None
        if dedup_cols:
            combined = combined.drop_duplicates(subset=dedup_cols, keep="last")
        else:
            combined = combined.drop_duplicates(keep="last")

        CSVStore.save(combined, path)


class FundRepository:
    """基金数据仓储 - 统一的基金数据访问接口"""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.nav_dir = self.data_dir / "nav"
        self.funds_dir = self.data_dir / "funds"
        self.index_dir = self.data_dir / "index"
        self._nav_cache: Dict[str, pd.DataFrame] = {}

    def get_funds(self) -> List[Fund]:
        """获取所有基金列表"""
        path = self.funds_dir / "funds.json"
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        funds = []
        for item in data:
            from .models import FundType, RiskLevel
            funds.append(Fund(
                code=item["code"],
                name=item["name"],
                fund_type=FundType(item["fund_type"]),
                manager=item.get("manager"),
                company=item.get("company"),
                risk_level=RiskLevel(item.get("risk_level", "MEDIUM")),
            ))
        return funds

    def get_fund(self, code: str) -> Optional[Fund]:
        """获取单个基金信息"""
        funds = self.get_funds()
        for f in funds:
            if f.code == code:
                return f
        return None

    def save_funds(self, funds: List[Fund]) -> None:
        """保存基金列表"""
        os.makedirs(self.funds_dir, exist_ok=True)
        data = []
        for f in funds:
            data.append({
                "code": f.code,
                "name": f.name,
                "fund_type": f.fund_type.value,
                "manager": f.manager,
                "company": f.company,
                "inception_date": str(f.inception_date) if f.inception_date else None,
                "aum": f.aum,
                "risk_level": f.risk_level.value,
            })
        with open(self.funds_dir / "funds.json", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"已保存 {len(funds)} 只基金")

    def get_nav_history(
        self,
        fund_code: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> pd.DataFrame:
        """获取基金净值历史"""
        if fund_code in self._nav_cache:
            df = self._nav_cache[fund_code]
        else:
            path = self.nav_dir / f"{fund_code}.csv"
            if not path.exists():
                logger.warning(f"未找到基金 {fund_code} 的净值数据: {path}")
                return pd.DataFrame()
            df = pd.read_csv(path, encoding="utf-8-sig")
            if "净值日期" in df.columns:
                df["净值日期"] = pd.to_datetime(df["净值日期"])
            self._nav_cache[fund_code] = df

        if df.empty:
            return df

        if start and "净值日期" in df.columns:
            df = df[df["净值日期"] >= pd.Timestamp(start)]
        if end and "净值日期" in df.columns:
            df = df[df["净值日期"] <= pd.Timestamp(end)]

        return df.sort_values("净值日期") if "净值日期" in df.columns else df

    def save_nav(self, fund_code: str, df: pd.DataFrame) -> None:
        """保存基金净值数据"""
        os.makedirs(self.nav_dir, exist_ok=True)
        path = self.nav_dir / f"{fund_code}.csv"
        CSVStore.append(str(path), df)
        # 清除缓存
        self._nav_cache.pop(fund_code, None)

    def get_nav_on_date(self, fund_code: str, target_date: date) -> Optional[NAVRecord]:
        """获取基金在指定日期的净值"""
        df = self.get_nav_history(fund_code, end=target_date)
        if df.empty or "净值日期" not in df.columns:
            return None
        # 找到该日期或之前最近的有效净值
        df = df[df["净值日期"] <= pd.Timestamp(target_date)]
        if df.empty:
            return None
        latest = df.iloc[-1]
        return NAVRecord(
            fund_code=fund_code,
            date=latest["净值日期"].date() if hasattr(latest["净值日期"], "date") else latest["净值日期"],
            nav=float(latest["单位净值"]),
            acc_nav=float(latest.get("累计净值", latest["单位净值"])),
            daily_return=float(latest.get("日增长率", 0)) / 100.0 if "日增长率" in latest else 0.0,
        )

    def clear_cache(self) -> None:
        """清除内存缓存"""
        self._nav_cache.clear()

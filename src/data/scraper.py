"""数据抓取编排器

协调多个数据源完成数据抓取任务，支持自动 fallback。
"""

import logging
from datetime import date, timedelta
from typing import Optional, List
import pandas as pd

from .models import Fund, FundType
from .sources.akshare_source import AkshareSource
from .sources.eastmoney_source import EastMoneySource
from .store import FundRepository

logger = logging.getLogger("fund_ai.data.scraper")


class FundDataScraper:
    """基金数据抓取编排器"""

    def __init__(self, config=None):
        """
        Args:
            config: ScraperConfig 对象或 None（使用默认值）
        """
        self.config = config
        self.request_delay = getattr(config, "request_delay", 1.0) if config else 1.0

        # 初始化数据源
        self.akshare = AkshareSource(request_delay=self.request_delay)
        self.eastmoney = EastMoneySource(request_delay=self.request_delay)

        # 数据存储
        self.repo = FundRepository()
        self._use_akshare = True  # 尝试主数据源

    def scrape_fund_list(self) -> List[Fund]:
        """抓取基金列表"""
        logger.info("开始抓取基金列表...")

        # 尝试主数据源
        funds = self.akshare.fetch_all_funds()
        if funds:
            self.repo.save_funds(funds)
            logger.info(f"基金列表抓取完成: {len(funds)} 只基金")
            return funds

        # 备用数据源
        logger.warning("akshare 抓取基金列表失败，尝试东方财富...")
        self._use_akshare = False
        raw_list = self.eastmoney.fetch_fund_list()
        funds = []
        for item in raw_list:
            if len(item) >= 4:
                funds.append(Fund(
                    code=str(item[0]),
                    name=str(item[1]),
                    fund_type=self.eastmoney._classify_fund_type(str(item[2])),
                    risk_level=None,
                ))
        if funds:
            self.repo.save_funds(funds)
        logger.info(f"基金列表抓取完成: {len(funds)} 只基金")
        return funds

    def scrape_nav_history(
        self,
        fund_code: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> pd.DataFrame:
        """抓取单只基金的历史净值"""
        if end is None:
            end = date.today()
        if start is None:
            history_days = getattr(self.config, "default_history_days", 1095) if self.config else 1095
            start = end - timedelta(days=history_days)

        logger.info(f"抓取基金 {fund_code} 净值: {start} ~ {end}")

        # 主数据源
        df = self.akshare.fetch_nav_history(fund_code, start, end)
        if not df.empty:
            self.repo.save_nav(fund_code, df)
            return df

        # 备用数据源
        logger.warning(f"akshare 抓取基金 {fund_code} 失败，尝试东方财富...")
        df = self.eastmoney.fetch_nav_history(fund_code, start, end)
        if not df.empty:
            self.repo.save_nav(fund_code, df)
        return df

    def scrape_funds_from_config(self, config_dir: Optional[str] = None) -> List[Fund]:
        """根据 config/funds.yaml 的配置抓取指定基金数据"""
        from ..utils.config import ConfigLoader
        funds_config = ConfigLoader.load_funds(config_dir)
        fund_list = funds_config.get("funds", [])

        if not fund_list:
            logger.warning("配置中未找到基金列表，将抓取全部基金")
            return self.scrape_fund_list()

        funds = []
        enabled_funds = [f for f in fund_list if f.get("enabled", True)]

        logger.info(f"开始抓取 {len(enabled_funds)} 只基金的净值数据...")

        for i, item in enumerate(enabled_funds):
            code = str(item["code"])
            name = item.get("name", "")
            fund_type_str = item.get("type", "MIXED")

            logger.info(f"[{i+1}/{len(enabled_funds)}] 抓取基金: {name}({code})")

            # 抓取净值历史
            df = self.scrape_nav_history(code)

            fund = Fund(
                code=code,
                name=name,
                fund_type=FundType(fund_type_str),
            )
            funds.append(fund)

            # 间隔控制
            if i < len(enabled_funds) - 1:
                import time
                time.sleep(self.request_delay)

        # 保存基金列表
        self.repo.save_funds(funds)
        logger.info(f"全部抓取完成: {len(funds)} 只基金")
        return funds

    def scrape_daily_update(self, fund_codes: Optional[List[str]] = None) -> dict:
        """每日增量更新 - 只拉取最近几天的数据"""
        from ..utils.date_utils import get_today

        today = get_today()
        if fund_codes is None:
            existing_funds = self.repo.get_funds()
            fund_codes = [f.code for f in existing_funds]

        results = {}
        for code in fund_codes:
            try:
                # 只拉最近30天（增量）
                df = self.scrape_nav_history(code, start=today - timedelta(days=30))
                results[code] = not df.empty
            except Exception as e:
                logger.error(f"增量更新基金 {code} 失败: {e}")
                results[code] = False

        success = sum(1 for v in results.values() if v)
        logger.info(f"每日更新完成: {success}/{len(results)} 只基金成功")

        return results

    def scrape_index_data(self):
        """抓取基准指数数据（沪深300、上证50等）"""
        from ..utils.config import ConfigLoader
        try:
            funds_config = ConfigLoader.load_funds()
            benchmarks = funds_config.get("benchmarks", [])
        except Exception:
            benchmarks = [
                {"code": "000300", "name": "沪深300"},
                {"code": "000016", "name": "上证50"},
            ]

        import os

        for bm in benchmarks:
            code = bm["code"]
            name = bm.get("name", code)
            logger.info(f"抓取指数: {name}({code})")

            df = self.akshare.fetch_index_data(code)
            if not df.empty:
                os.makedirs("data/index", exist_ok=True)
                df.to_csv(f"data/index/{code}.csv", index=False, encoding="utf-8-sig")
                logger.info(f"指数 {name} 数据已保存: {len(df)} 条")

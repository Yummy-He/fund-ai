"""akshare 数据源 - 主数据源

akshare 是一个开源的金融数据接口库，封装了大量中国金融数据 API。

接口说明:
- fund_open_fund_daily_em: 场外开放式基金净值（东方财富数据）
- fund_open_fund_info_em: 场外基金基本信息
- fund_etf_fund_daily_em: ETF 基金净值
- fund_etf_category_sina: ETF 分类信息
- tool_trade_date_hist_sina: A股交易日历
"""

import time
import logging
from datetime import date, datetime, timedelta
from typing import Optional, List, Dict
import pandas as pd

from ..models import Fund, NAVRecord, FundType, RiskLevel

logger = logging.getLogger("fund_ai.data.akshare")


class AkshareSource:
    """akshare 数据源"""

    def __init__(self, request_delay: float = 1.0):
        self.request_delay = request_delay
        self._last_request_time = 0.0

    def _rate_limit(self):
        """请求频率控制"""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)
        self._last_request_time = time.time()

    def fetch_all_funds(self) -> List[Fund]:
        """获取所有开放式基金列表"""
        try:
            import akshare as ak
            self._rate_limit()
            # 获取天天基金所有开放式基金
            df = ak.fund_open_fund_rank_em(symbol="全部")
            logger.info(f"获取到 {len(df)} 只基金排名数据")

            funds = []
            for _, row in df.iterrows():
                code = str(row.get("基金代码", ""))
                name = str(row.get("基金简称", ""))
                if not code or not name:
                    continue

                fund_type_str = str(row.get("基金类型", ""))
                fund_type = self._classify_fund_type(name, fund_type_str)

                funds.append(Fund(
                    code=code,
                    name=name,
                    fund_type=fund_type,
                    risk_level=RiskLevel.MEDIUM,
                ))
            return funds
        except ImportError:
            logger.error("akshare 未安装，请运行: pip install akshare")
            return []
        except Exception as e:
            logger.error(f"获取基金列表失败: {e}")
            return []

    def fetch_nav_history(
        self,
        fund_code: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> pd.DataFrame:
        """获取单只基金的历史净值数据

        返回 DataFrame 包含列: 净值日期, 单位净值, 累计净值, 日增长率

        akshare v1.18+ 使用 fund_open_fund_info_em(symbol, indicator, period)
        """
        try:
            import akshare as ak
            self._rate_limit()

            # akshare v1.18+ API: fund_open_fund_info_em
            df = ak.fund_open_fund_info_em(
                symbol=fund_code,
                indicator="单位净值走势",
                period="交易日",
            )

            if df.empty:
                logger.warning(f"基金 {fund_code} 无数据")
                return pd.DataFrame()

            # 标准化列名
            # 新版 akshare 返回: 净值日期, 单位净值, 累计净值
            df = df.rename(columns={
                "净值日期": "净值日期",
                "单位净值": "单位净值",
                "累计净值": "累计净值",
            })

            # 计算日增长率（从净值变化计算）
            if "日增长率" not in df.columns:
                df["单位净值"] = df["单位净值"].astype(float)
                df["日增长率"] = df["单位净值"].pct_change() * 100
                df.loc[df["日增长率"].isna(), "日增长率"] = 0.0

            # 确保所需列存在
            required_cols = ["净值日期", "单位净值"]
            for col in required_cols:
                if col not in df.columns:
                    logger.error(f"基金 {fund_code} 数据缺少 {col} 列，现有列: {list(df.columns)}")
                    return pd.DataFrame()

            # 转换日期并排序（兼容 datetime.date 和 str 两种类型）
            df["净值日期"] = pd.to_datetime(df["净值日期"], format="mixed", dayfirst=False)
            df = df.sort_values("净值日期")

            # 过滤日期范围
            if start:
                df = df[df["净值日期"] >= pd.Timestamp(start)]
            if end:
                df = df[df["净值日期"] <= pd.Timestamp(end)]

            # 补充累计净值（如果缺失）
            if "累计净值" not in df.columns or df["累计净值"].isna().all():
                df["累计净值"] = df["单位净值"]

            logger.info(f"获取基金 {fund_code} 净值: {len(df)} 条记录 "
                       f"({df['净值日期'].min().strftime('%Y-%m-%d')} ~ "
                       f"{df['净值日期'].max().strftime('%Y-%m-%d')})")
            return df

        except Exception as e:
            logger.error(f"获取基金 {fund_code} 净值失败: {e}")
            return pd.DataFrame()

    def fetch_fund_info(self, fund_code: str) -> Optional[dict]:
        """获取单只基金的详细信息"""
        try:
            import akshare as ak
            self._rate_limit()
            df = ak.fund_open_fund_info_em(fund=fund_code, indicator="单位净值走势")
            if df.empty:
                return None
            # 提取基本信息
            return {
                "code": fund_code,
                "rows": len(df),
                "latest_nav": float(df.iloc[-1]["单位净值"]) if "单位净值" in df.columns else None,
                "date_range": f"{df.iloc[0]['净值日期']} ~ {df.iloc[-1]['净值日期']}",
            }
        except Exception as e:
            logger.warning(f"获取基金 {fund_code} 信息失败: {e}")
            return None

    @staticmethod
    def _index_exchange(index_code: str) -> str:
        """根据指数代码判断交易所前缀: 0xx→sh(上证), 3xx→sz(深证), 8xx→bj(北证)"""
        if index_code.startswith("0"):
            return "sh"
        elif index_code.startswith("3"):
            return "sz"
        elif index_code.startswith("8"):
            return "bj"
        return "sh"  # 默认

    def fetch_index_data(self, index_code: str = "000300",
                         start: Optional[date] = None,
                         end: Optional[date] = None) -> pd.DataFrame:
        """获取指数历史数据（沪深300等）

        index_code: "000300"=沪深300, "000016"=上证50, "399006"=创业板指
        """
        try:
            import akshare as ak
            self._rate_limit()
            df = ak.stock_zh_index_daily(symbol=f"{self._index_exchange(index_code)}{index_code}")

            if df.empty or "date" not in df.columns:
                logger.warning(f"指数 {index_code} 无数据或缺少 date 列, 列名: {list(df.columns) if not df.empty else '空'}")
                return pd.DataFrame()

            df["date"] = pd.to_datetime(df["date"])

            if start:
                df = df[df["date"] >= pd.Timestamp(start)]
            if end:
                df = df[df["date"] <= pd.Timestamp(end)]

            logger.info(f"获取指数 {index_code}: {len(df)} 条记录")
            return df.sort_values("date")
        except Exception as e:
            logger.error(f"获取指数 {index_code} 失败: {e}")
            return pd.DataFrame()

    def fetch_trading_calendar(self, start: date, end: date) -> List[date]:
        """获取交易日历

        tool_trade_date_hist_sina() 返回格式 YYYY-MM-DD。
        """
        try:
            import akshare as ak
            self._rate_limit()
            df = ak.tool_trade_date_hist_sina()
            if df.empty:
                return self._simple_trading_days(start, end)

            days = []
            for _, row in df.iterrows():
                d_str = str(row["trade_date"])
                try:
                    # 格式: YYYY-MM-DD
                    d = datetime.strptime(d_str, "%Y-%m-%d").date()
                    if start <= d <= end:
                        days.append(d)
                except ValueError:
                    continue
            return sorted(days)
        except Exception:
            return self._simple_trading_days(start, end)

    @staticmethod
    def _simple_trading_days(start: date, end: date) -> List[date]:
        """简单工作日列表（不含节假日过滤）"""
        days = []
        current = start
        while current <= end:
            if current.weekday() < 5:
                days.append(current)
            current += timedelta(days=1)
        return days

    @staticmethod
    def _classify_fund_type(name: str, type_str: str) -> FundType:
        """根据基金名称和类型字符串分类"""
        type_str = type_str.lower()
        name_lower = name.lower()

        if "etf" in type_str or "etf" in name_lower or "etf" in name:
            return FundType.ETF
        if "指数" in type_str or "指数" in name:
            return FundType.INDEX
        if "债" in type_str or "债券" in name_lower or "债" in name:
            return FundType.BOND
        if "货币" in type_str or "货币" in name or "货基" in name_lower:
            return FundType.MONEY
        if "qdii" in type_str.lower() or "qdii" in name_lower or "海外" in name:
            return FundType.QDII
        if "混合" in type_str or "混合" in name:
            return FundType.MIXED
        if "股票" in type_str or "股票" in name:
            return FundType.STOCK
        if "fof" in type_str.lower() or "fof" in name_lower:
            return FundType.FOF

        return FundType.MIXED  # 默认混合型

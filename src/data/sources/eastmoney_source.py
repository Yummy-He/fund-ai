"""东方财富数据源 - 备用数据源

当 akshare 不可用时，直接通过东方财富 HTTP API 获取数据。
东方财富基金 API 是 akshare 的底层数据源，这里直接调用作为 fallback。

关键 API 端点:
- 基金列表: http://fund.eastmoney.com/js/fundcode_search.js
- 基金净值: https://api.fund.eastmoney.com/f10/lsjz
- 基金详情: https://fundf10.eastmoney.com/jjjz_{fund_code}.html
"""

import time
import json
import logging
from datetime import date, datetime
from typing import Optional, List
import requests
import pandas as pd

from ..models import Fund, FundType, RiskLevel

logger = logging.getLogger("fund_ai.data.eastmoney")

# 请求头，模拟浏览器访问
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://fund.eastmoney.com/",
    "Accept": "application/json, text/plain, */*",
}


class EastMoneySource:
    """东方财富备用数据源"""

    BASE_URL = "https://fund.eastmoney.com"
    API_URL = "https://api.fund.eastmoney.com"

    def __init__(self, request_delay: float = 1.5):
        self.request_delay = request_delay
        self._last_request_time = 0.0

    def _rate_limit(self):
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)
        self._last_request_time = time.time()

    def fetch_fund_list(self) -> List[dict]:
        """获取所有基金列表（从东方财富的 JS 文件）"""
        try:
            self._rate_limit()
            url = "http://fund.eastmoney.com/js/fundcode_search.js"
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()

            # 响应格式: var r = [["000001","基金名称","类型","代码"],...]
            text = resp.text
            json_str = text[text.index("[") : text.rindex("]") + 1]
            data = json.loads(json_str)

            logger.info(f"从东方财富获取到 {len(data)} 只基金")
            return data
        except Exception as e:
            logger.error(f"获取基金列表失败: {e}")
            return []

    def fetch_nav_history(
        self,
        fund_code: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
        page_size: int = 100,
    ) -> pd.DataFrame:
        """获取基金历史净值

        东方财富 API 分页返回，每页最多 49 条（f10接口限制）
        """
        records = []
        page = 1

        try:
            while True:
                self._rate_limit()
                url = f"{self.API_URL}/f10/lsjz"
                params = {
                    "callback": "jQuery",
                    "fundCode": fund_code,
                    "pageIndex": page,
                    "pageSize": 49,
                    "startDate": start.strftime("%Y-%m-%d") if start else "",
                    "endDate": end.strftime("%Y-%m-%d") if end else "",
                    "_": int(time.time() * 1000),
                }
                resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
                resp.raise_for_status()

                # 解析 JSONP 响应: jQuery({...})
                text = resp.text
                if "jQuery(" in text:
                    json_str = text[text.index("(") + 1 : text.rindex(")")]
                    data = json.loads(json_str)
                else:
                    data = json.loads(text)

                lsjz_list = data.get("Data", {}).get("LSJZList", [])
                if not lsjz_list:
                    break

                for item in lsjz_list:
                    records.append({
                        "净值日期": item.get("FSRQ", ""),
                        "单位净值": float(item.get("DWJZ", 0)),
                        "累计净值": float(item.get("LJJZ", 0)),
                        "日增长率": float(item.get("JZZZL", 0)) if item.get("JZZZL") else "0",
                    })

                if len(lsjz_list) < 49:
                    break
                page += 1
                time.sleep(0.3)  # 减少请求频率

            if records:
                df = pd.DataFrame(records)
                df["净值日期"] = pd.to_datetime(df["净值日期"])
                df = df.sort_values("净值日期")
                logger.info(f"获取基金 {fund_code} 净值: {len(df)} 条记录 (东方财富)")
                return df

        except Exception as e:
            logger.error(f"东方财富获取基金 {fund_code} 净值失败: {e}")

        return pd.DataFrame()

    def fetch_daily_all(self, fund_codes: List[str]) -> dict:
        """批量获取多只基金的最新净值"""
        results = {}
        for code in fund_codes:
            try:
                df = self.fetch_nav_history(code)
                if not df.empty:
                    latest = df.iloc[-1]
                    results[code] = {
                        "date": str(latest["净值日期"]),
                        "nav": float(latest["单位净值"]),
                        "acc_nav": float(latest["累计净值"]),
                    }
            except Exception as e:
                logger.warning(f"获取基金 {code} 失败: {e}")
        return results

    @staticmethod
    def _classify_fund_type(type_str: str) -> FundType:
        """分类基金类型"""
        # 东方财富类型编码映射
        type_map = {
            "股票型": FundType.STOCK,
            "混合型": FundType.MIXED,
            "混合-偏股": FundType.MIXED,
            "混合-偏债": FundType.MIXED,
            "混合-灵活": FundType.MIXED,
            "混合-平衡": FundType.MIXED,
            "债券型": FundType.BOND,
            "指数型": FundType.INDEX,
            "指数型-股票": FundType.INDEX,
            "ETF-场内": FundType.ETF,
            "LOF": FundType.ETF,
            "货币型": FundType.MONEY,
            "QDII": FundType.QDII,
            "FOF": FundType.FOF,
        }
        for key, value in type_map.items():
            if key in type_str:
                return value
        return FundType.MIXED

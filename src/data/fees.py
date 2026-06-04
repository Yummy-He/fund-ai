"""基金费率模块

从 akshare fund_fee_em 获取各基金的实际费率信息，
用于回测中精确计算交易成本。

核心数据:
  - 赎回费率阶梯: 按持有天数分档
  - 运作费用: 管理费/托管费/销售服务费（年化）

注意:
  - ETF 使用不同的费率结构（券商佣金），此处暂用默认值
  - 申购费率与购买渠道相关（支付宝/天天基金/官网不同），默认0.15%
"""

import json
import os
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("fund_ai.data.fees")


@dataclass
class RedemptionFeeTier:
    """赎回费率阶梯"""
    min_days: int        # 最少持有天数（含）
    max_days: Optional[int]  # 最多持有天数（不含），None 表示无上限
    rate: float          # 费率（小数，如 0.015 = 1.5%）

    def matches(self, holding_days: int) -> bool:
        if holding_days < self.min_days:
            return False
        if self.max_days is not None and holding_days >= self.max_days:
            return False
        return True

    def describe(self) -> str:
        if self.max_days is None:
            return f">={self.min_days}天: {self.rate*100:.2f}%"
        return f"{self.min_days}-{self.max_days}天: {self.rate*100:.2f}%"


@dataclass
class SubscriptionFeeTier:
    """申购费率阶梯（按金额分档）"""
    min_amount: float       # 最低金额（含）
    max_amount: Optional[float]  # 最高金额（不含），None 表示无上限
    rate: float             # 费率（小数）

    def matches(self, amount: float) -> bool:
        if amount < self.min_amount:
            return False
        if self.max_amount is not None and amount >= self.max_amount:
            return False
        return True


@dataclass
class FundFee:
    """基金完整费率信息（每只基金独立）"""
    fund_code: str
    # 运作费用（年化，从基金净值中每日扣除）
    management_fee: float = 0.015     # 管理费
    custody_fee: float = 0.0025       # 托管费
    sales_service_fee: float = 0.0    # 销售服务费（C类份额通常>0）
    # 申购费率阶梯（按购买金额分档）
    subscription_tiers: List[SubscriptionFeeTier] = field(default_factory=list)
    # 赎回费率阶梯（按持有天数分档）
    redemption_tiers: List[RedemptionFeeTier] = field(default_factory=list)
    # 交易门槛
    min_subscription: float = 1.0      # 申购起点（元）
    min_auto_invest: float = 1.0       # 定投起点（元）
    min_first_purchase: float = 1.0    # 首次购买最低（元）
    min_additional_purchase: float = 1.0  # 追加购买最低（元）
    daily_purchase_limit: Optional[float] = None  # 日累计申购限额（元），None=无限
    max_holding_amount: Optional[float] = None    # 持仓上限（元），None=无限
    min_redemption_shares: float = 0.01  # 最小赎回份额
    min_retained_shares: float = 0.01    # 部分赎回后最低保留份额
    # 交易确认
    confirmation_days: str = "T+1"       # 交易确认日

    @property
    def annual_operating_fee(self) -> float:
        """年化运作费率合计"""
        return self.management_fee + self.custody_fee + self.sales_service_fee

    def get_subscription_fee(self, amount: float) -> float:
        """根据申购金额匹配费率（默认费率 0.15% 为平台折扣后）"""
        if not self.subscription_tiers:
            return 0.0015  # 默认 0.15%（支付宝/天天基金折扣）
        for tier in self.subscription_tiers:
            if tier.matches(amount):
                return tier.rate
        return 0.0015

    def get_redemption_fee(self, holding_days: int) -> float:
        """根据持有天数获取赎回费率"""
        if not self.redemption_tiers:
            # 默认阶梯
            if holding_days < 7:
                return 0.015    # 1.5% 惩罚
            elif holding_days < 30:
                return 0.0075   # 0.75%
            elif holding_days < 365:
                return 0.005    # 0.5%
            elif holding_days < 730:
                return 0.003    # 0.3%
            else:
                return 0.0

        for tier in self.redemption_tiers:
            if tier.matches(holding_days):
                return tier.rate
        return 0.0

    def annual_operating_fee(self) -> float:
        """年化运作费率合计"""
        return self.management_fee + self.custody_fee + self.sales_service_fee


class FeeManager:
    """费率管理器 — 加载/缓存/查询基金费率

    费率数据存在 data/funds/fees.json，每次 scrape 时更新。
    """

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.cache_file = self.data_dir / "funds" / "fees.json"
        self._fees: Dict[str, FundFee] = {}
        self._load_cache()

    def get_fee(self, fund_code: str) -> FundFee:
        """获取基金费率，找不到返回默认值"""
        if fund_code in self._fees:
            return self._fees[fund_code]
        return FundFee(fund_code=fund_code)

    def fetch_and_store(self, fund_code: str) -> Optional[FundFee]:
        """从 akshare 抓取并缓存基金费率"""
        try:
            import akshare as ak
            import time
            time.sleep(0.5)

            fee = FundFee(fund_code=fund_code)

            # 赎回费率
            try:
                df = ak.fund_fee_em(symbol=fund_code, indicator="赎回费率")
                if not df.empty:
                    tiers = []
                    for _, row in df.iterrows():
                        desc = str(row.iloc[0])
                        rate_str = str(row.iloc[1]).replace("%", "")
                        rate = float(rate_str) / 100.0 if rate_str else 0.0

                        # 解析持有天数范围
                        min_days, max_days = self._parse_holding_period(desc)
                        tiers.append(RedemptionFeeTier(
                            min_days=min_days,
                            max_days=max_days,
                            rate=rate,
                        ))
                    if tiers:
                        fee.redemption_tiers = tiers
            except Exception:
                pass  # ETF 等可能没有赎回费率表

            # 运作费用
            try:
                df = ak.fund_fee_em(symbol=fund_code, indicator="运作费用")
                if not df.empty and len(df.columns) >= 6:
                    # 列: 费用类型1, 费率1, 费用类型2, 费率2, 费用类型3, 费率3
                    for i in range(0, min(6, len(df.columns)), 2):
                        fee_type = str(df.iloc[0, i])
                        rate_str = str(df.iloc[0, i+1]) if i+1 < len(df.columns) else ""
                        rate_str = rate_str.replace("%", "").replace("（每年）", "").strip()
                        try:
                            rate_val = float(rate_str) / 100.0
                        except ValueError:
                            rate_val = 0.0

                        if "管理费" in fee_type:
                            fee.management_fee = rate_val
                        elif "托管费" in fee_type:
                            fee.custody_fee = rate_val
                        elif "销售服务费" in fee_type:
                            fee.sales_service_fee = rate_val
            except Exception:
                pass

            # 东方财富基金详情页 — 交易规则和全面费率
            try:
                import requests
                html = self._fetch_fund_page(fund_code)
                self._parse_page_fees(fee, html)
            except Exception:
                pass

            # 存入缓存
            self._fees[fund_code] = fee
            self._save_cache()
            logger.info(f"基金 {fund_code} 费率: 管理{fee.management_fee:.2%} "
                        f"托管{fee.custody_fee:.2%} "
                        f"销售{fee.sales_service_fee:.2%} "
                        f"赎回档{len(fee.redemption_tiers)} 申购起点{fee.min_subscription:.0f}")
            return fee

        except Exception as e:
            logger.warning(f"获取基金 {fund_code} 费率失败: {e}")
            return None

    def _fetch_fund_page(self, fund_code: str) -> str:
        import requests
        url = f"https://fundf10.eastmoney.com/jjfl_{fund_code}.html"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        return resp.text

    def _parse_page_fees(self, fee: FundFee, html: str) -> None:
        """从东方财富基金详情页解析完整的交易规则和费率"""
        import re
        # 去掉 HTML 标签方便正则
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)

        # --- 交易门槛 ---
        fee.min_subscription = self._re_float(text, r"申购起点\s*(\d+\.?\d*)\s*元", fee.min_subscription)
        fee.min_auto_invest = self._re_float(text, r"定投起点\s*(\d+\.?\d*)\s*元", fee.min_auto_invest)
        fee.min_first_purchase = self._re_float(text, r"首次购买\s*(\d+\.?\d*)\s*元", fee.min_first_purchase)
        fee.min_additional_purchase = self._re_float(text, r"追加购买\s*(\d+\.?\d*)\s*元", fee.min_additional_purchase)

        # 日累计申购限额
        dpl = re.search(r"日累[计計]申购限额\s*(\d+\.?\d*)\s*元", text)
        if dpl:
            fee.daily_purchase_limit = float(dpl.group(1))
        else:
            # 查是否有无限或暂无等字样
            if re.search(r"日累[计計].{0,10}(无限|暂无|不限)", text):
                fee.daily_purchase_limit = None

        # 持仓上限
        mha = re.search(r"持仓上限\s*(\d+\.?\d*)\s*元", text)
        if mha:
            fee.max_holding_amount = float(mha.group(1))

        # --- 赎回规则 ---
        fee.min_redemption_shares = self._re_float(
            text, r"赎回份额\s*(\d+\.?\d*)\s*份", fee.min_redemption_shares
        )
        fee.min_retained_shares = self._re_float(
            text, r"保留份额\s*(\d+\.?\d*)\s*份", fee.min_retained_shares
        )

        # --- 确认日 ---
        cd = re.search(r"确认日[期期]?\s*(T\+\d+)", text)
        if cd:
            fee.confirmation_days = cd.group(1)

        # --- 运作费用（从页面冗余提取更准确的） ---
        # 页面格式: 管理费率 1.20%（每年） 托管费率 0.20%（每年） 销售服务费率 0.00%（每年）
        mgmt = re.search(r"管理费[率率]?\s*(\d+\.?\d*)\s*%", text)
        if mgmt:
            fee.management_fee = float(mgmt.group(1)) / 100.0
        cus = re.search(r"托管费[率率]?\s*(\d+\.?\d*)\s*%", text)
        if cus:
            fee.custody_fee = float(cus.group(1)) / 100.0
        ssf = re.search(r"销售服务费[率率]?\s*(\d+\.?\d*)\s*%", text)
        if ssf:
            fee.sales_service_fee = float(ssf.group(1)) / 100.0

        # --- 申购费率阶梯（从页面表格提取） ---
        # 页面表格格式: 申购金额<100万 费率0.15% | 100万≤金额<500万 费率0.10% ...
        sub_tiers = self._parse_amount_tiers(text, r"申购金额", r"申购费率")
        if sub_tiers:
            fee.subscription_tiers = sub_tiers

    @staticmethod
    def _re_float(text: str, pattern: str, default: float) -> float:
        import re
        m = re.search(pattern, text)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return default
        return default

    @staticmethod
    def _parse_amount_tiers(text: str, amount_keyword: str, rate_keyword: str) -> list:
        """从页面文本解析按金额分档的费率阶梯"""
        import re
        # 简单实现：找金额档位和费率
        tiers = []
        # 匹配类似 "100万 0.15%", "500万 0.10%" 等
        pattern = rf"{amount_keyword}.*?(\d+\.?\d*).*?(\d+\.?\d*)%"
        matches = re.findall(pattern, text)
        if not matches:
            # 更宽松的匹配
            pattern = r"(\d+)万.*?(\d+\.?\d*)\s*%"
            matches = re.findall(pattern, text)

        prev_max = 0.0
        for m in matches:
            try:
                rate = float(m[-1]) / 100.0
                # 第一个数字通常是金额档（万为单位）
                amount_val = float(m[0])
                amount = amount_val * 10000 if amount_val < 1000 else amount_val
                tiers.append(SubscriptionFeeTier(
                    min_amount=prev_max,
                    max_amount=amount,
                    rate=rate,
                ))
                prev_max = amount
            except (ValueError, IndexError):
                continue

        return tiers

    def fetch_all(self, fund_codes: List[str]) -> Dict[str, FundFee]:
        """批量抓取并缓存费率"""
        results = {}
        for code in fund_codes:
            fee = self.fetch_and_store(code)
            if fee:
                results[code] = fee
        return results

    def _parse_holding_period(self, desc: str) -> Tuple[int, Optional[int]]:
        """解析持有期描述文字
        "小于7天" → (0, 7)
        "大于等于7天，小于30天" → (7, 30)
        "大于等于365天，小于730天" → (365, 730)
        "大于等于730天" → (730, None)
        """
        import re

        # 提取所有数字
        numbers = re.findall(r'\d+', desc)
        if not numbers:
            return (0, None)

        nums = [int(n) for n in numbers]

        if "小于" in desc and "大于" not in desc:
            # "小于N天"
            return (0, nums[0])
        elif "大于等于" in desc or "大于" in desc:
            if len(nums) >= 2:
                # "大于等于N天，小于M天"
                return (nums[0], nums[1])
            else:
                # "大于等于N天"
                return (nums[0], None)

        # 兜底
        return (0, None)

    def _load_cache(self):
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for code, d in data.items():
                    self._fees[code] = FundFee(
                        fund_code=code,
                        management_fee=d.get("management_fee", 0.015),
                        custody_fee=d.get("custody_fee", 0.0025),
                        sales_service_fee=d.get("sales_service_fee", 0.0),
                        subscription_tiers=[
                            SubscriptionFeeTier(t["min_amount"], t.get("max_amount"), t["rate"])
                            for t in d.get("subscription_tiers", [])
                        ],
                        redemption_tiers=[
                            RedemptionFeeTier(t["min_days"], t.get("max_days"), t["rate"])
                            for t in d.get("redemption_tiers", [])
                        ],
                        min_subscription=d.get("min_subscription", 1.0),
                        min_auto_invest=d.get("min_auto_invest", 1.0),
                        min_first_purchase=d.get("min_first_purchase", 1.0),
                        min_additional_purchase=d.get("min_additional_purchase", 1.0),
                        daily_purchase_limit=d.get("daily_purchase_limit"),
                        max_holding_amount=d.get("max_holding_amount"),
                        min_redemption_shares=d.get("min_redemption_shares", 0.01),
                        min_retained_shares=d.get("min_retained_shares", 0.01),
                        confirmation_days=d.get("confirmation_days", "T+1"),
                    )
                logger.debug(f"已加载 {len(self._fees)} 只基金费率缓存")
            except Exception as e:
                logger.warning(f"费率缓存加载失败: {e}")

    def _save_cache(self):
        os.makedirs(self.cache_file.parent, exist_ok=True)
        data = {}
        for code, fee in self._fees.items():
            data[code] = {
                "management_fee": fee.management_fee,
                "custody_fee": fee.custody_fee,
                "sales_service_fee": fee.sales_service_fee,
                "subscription_tiers": [
                    {"min_amount": t.min_amount, "max_amount": t.max_amount, "rate": t.rate}
                    for t in fee.subscription_tiers
                ],
                "redemption_tiers": [
                    {"min_days": t.min_days, "max_days": t.max_days, "rate": t.rate}
                    for t in fee.redemption_tiers
                ],
                "min_subscription": fee.min_subscription,
                "min_auto_invest": fee.min_auto_invest,
                "min_first_purchase": fee.min_first_purchase,
                "min_additional_purchase": fee.min_additional_purchase,
                "daily_purchase_limit": fee.daily_purchase_limit,
                "max_holding_amount": fee.max_holding_amount,
                "min_redemption_shares": fee.min_redemption_shares,
                "min_retained_shares": fee.min_retained_shares,
                "confirmation_days": fee.confirmation_days,
            }
        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

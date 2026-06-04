"""日期工具模块 - 中国A股交易日历

基金净值在每个交易日晚上更新。回测使用 T+1 规则：
当天做决策，看到的净值是 T-1 日的（最新的已公布净值）。
"""

from datetime import date, datetime, timedelta
from typing import Optional, List
import time


def get_today() -> date:
    """获取今天的日期"""
    return date.today()


def parse_date(date_str: str) -> date:
    """解析日期字符串 (支持 YYYY-MM-DD 格式)"""
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def fmt_date(d: date) -> str:
    """格式化日期为字符串"""
    return d.strftime("%Y-%m-%d")


class TradingCalendar:
    """中国A股交易日历

    简化实现：周末一定非交易日，法定节假日通过 akshare 获取。
    """

    def __init__(self):
        self._trading_days: Optional[List[date]] = None
        self._trading_days_set: Optional[set] = None

    @property
    def trading_days(self) -> List[date]:
        """返回已知的交易日列表（惰性加载）"""
        if self._trading_days is None:
            self._load_trading_days()
        return self._trading_days  # type: ignore

    def _load_trading_days(self) -> None:
        """从 akshare 加载交易日历"""
        try:
            import akshare as ak
            # 获取 A 股交易日历
            df = ak.tool_trade_date_hist_sina()
            if "trade_date" in df.columns:
                days = sorted({
                    datetime.strptime(str(d), "%Y%m%d").date()
                    for d in df["trade_date"]
                })
                self._trading_days = days
                self._trading_days_set = set(days)
        except Exception:
            # 如果 akshare 不可用，回退到简单的周末过滤
            self._trading_days = []
            self._trading_days_set = set()

    def is_trading_day(self, d: date) -> bool:
        """判断是否为交易日"""
        if self._trading_days_set is None:
            self._load_trading_days()
        if self._trading_days_set:
            return d in self._trading_days_set
        # 回退：周末一定非交易日
        return d.weekday() < 5

    def next_trading_day(self, d: date) -> Optional[date]:
        """获取下一个交易日"""
        next_day = d + timedelta(days=1)
        max_iter = 30  # 防止无限循环（如春节长假）
        for _ in range(max_iter):
            if self.is_trading_day(next_day):
                return next_day
            next_day += timedelta(days=1)
        return None

    def prev_trading_day(self, d: date) -> Optional[date]:
        """获取上一个交易日"""
        prev_day = d - timedelta(days=1)
        max_iter = 30
        for _ in range(max_iter):
            if self.is_trading_day(prev_day):
                return prev_day
            prev_day -= timedelta(days=1)
        return None

    def trading_days_between(self, start: date, end: date) -> List[date]:
        """返回 start 到 end 之间的所有交易日（包含两端）"""
        if self._trading_days is None:
            self._load_trading_days()
        if self._trading_days:
            return [d for d in self._trading_days if start <= d <= end]
        # 回退：包含所有工作日
        days = []
        current = start
        while current <= end:
            if current.weekday() < 5:
                days.append(current)
            current += timedelta(days=1)
        return days

    def trading_day_offset(self, start: date, offset: int) -> date:
        """从 start 开始，偏移 offset 个交易日（正向前，负向后）"""
        if offset == 0:
            return start
        if offset > 0:
            d = start
            for _ in range(offset):
                nxt = self.next_trading_day(d)
                if nxt is None:
                    break
                d = nxt
            return d
        else:
            d = start
            for _ in range(-offset):
                prv = self.prev_trading_day(d)
                if prv is None:
                    break
                d = prv
            return d

    @staticmethod
    def days_between_trading_days(start: date, end: date) -> int:
        """计算两个交易日之间的自然天数"""
        return (end - start).days


# 全局交易日历实例
_trading_calendar: Optional[TradingCalendar] = None


def get_trading_calendar() -> TradingCalendar:
    global _trading_calendar
    if _trading_calendar is None:
        _trading_calendar = TradingCalendar()
    return _trading_calendar

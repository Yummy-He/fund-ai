"""时间步进器

管理回测中的时间推进/倒退，处理交易日历。
"""

from datetime import date, timedelta
from typing import List, Optional
import random

from ..utils.date_utils import TradingCalendar, get_trading_calendar


class TimeSimulator:
    """回测时间步进器

    支持向前和向后步进（模拟时光倒流/快进）。
    - 正常回测: start -> end, step_days=1 (每个交易日一个步进)
    - 快速回测: 可以设置 step_days=7 按周步进
    """

    def __init__(
        self,
        start_date: date,
        end_date: date,
        step_days: int = 1,
    ):
        self.start_date = start_date
        self.end_date = end_date
        self.step_days = step_days  # 1 = 逐日
        self.current_date = start_date
        self.cal = get_trading_calendar()
        self._all_trading_days = self.cal.trading_days_between(start_date, end_date)
        self._current_index = 0
        self.days_processed = 0

    def next_day(self) -> Optional[date]:
        """步进到下一个交易日"""
        if not self._all_trading_days:
            return None

        if self._current_index + self.step_days < len(self._all_trading_days):
            self._current_index += self.step_days
            self.current_date = self._all_trading_days[self._current_index]
            self.days_processed += 1
            return self.current_date
        return None

    def prev_day(self) -> Optional[date]:
        """回退到上一个交易日"""
        if self._current_index - self.step_days >= 0:
            self._current_index -= self.step_days
            self.current_date = self._all_trading_days[self._current_index]
            self.days_processed -= 1
            return self.current_date
        return None

    def peek_forward(self, n_days: int = 30) -> Optional[date]:
        """查看未来第 n 个交易日（不实际操作）"""
        idx = self._current_index + n_days
        if idx < len(self._all_trading_days):
            return self._all_trading_days[idx]
        return None

    def is_finished(self) -> bool:
        """是否已完成回测"""
        return self._current_index >= len(self._all_trading_days) - 1

    def progress(self) -> float:
        """回测进度 0.0-1.0"""
        if not self._all_trading_days:
            return 0.0
        return min(self._current_index / max(len(self._all_trading_days) - 1, 1), 1.0)

    def reset(self) -> None:
        """重置到起始日期"""
        self.current_date = self.start_date
        self._current_index = 0
        self.days_processed = 0

    @property
    def total_trading_days(self) -> int:
        return len(self._all_trading_days)

    @classmethod
    def random_time_window(
        cls,
        lookback_days: int = 365,
        end_buffer_days: int = 30,
    ) -> tuple:
        """生成随机的时间窗口用于回测

        Args:
            lookback_days: 回测跨度（自然日）
            end_buffer_days: 结束日期距今天的缓冲

        Returns:
            (start_date, end_date)
        """
        today = date.today()
        # 最晚结束日期：today - buffer
        latest_end = today - timedelta(days=end_buffer_days)
        # 最早开始日期：5年前
        earliest_start = today - timedelta(days=5 * 365)

        # 在有效范围内随机选择结束日期
        cal = get_trading_calendar()
        trading_days = cal.trading_days_between(earliest_start, latest_end)

        if not trading_days:
            # 回退：简单的天数回退
            end = latest_end
            start = end - timedelta(days=lookback_days)
            return start, end

        # 随机选择结束日期（确保有足够的回测长度）
        min_trading_days = 180  # 最少180个交易日
        if len(trading_days) > min_trading_days:
            end = random.choice(trading_days[min_trading_days:])
        else:
            end = trading_days[-1]

        # 向前找大约 lookback_days 的起始点
        cal_start = end - timedelta(days=lookback_days)
        # 找到 cal_start 附近的第一个交易日
        if cal.is_trading_day(cal_start):
            start = cal_start
        else:
            start = cal.next_trading_day(cal_start) or cal_start

        return start, end

    @classmethod
    def random_fund_pool(
        cls,
        all_funds: List[str],
        min_funds: int = 3,
        max_funds: int = 10,
    ) -> List[str]:
        """随机选择基金组合"""
        n = min(len(all_funds), random.randint(min_funds, min(max_funds, len(all_funds))))
        return random.sample(all_funds, n)

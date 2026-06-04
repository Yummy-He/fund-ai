"""基金数据模型"""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional


class FundType(str, Enum):
    """基金类型"""
    STOCK = "STOCK"       # 股票型
    MIXED = "MIXED"       # 混合型
    BOND = "BOND"         # 债券型
    INDEX = "INDEX"       # 指数型
    ETF = "ETF"           # ETF
    MONEY = "MONEY"       # 货币型
    QDII = "QDII"         # QDII（海外投资）
    FOF = "FOF"           # FOF（基金中的基金）


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM_LOW = "MEDIUM_LOW"
    MEDIUM = "MEDIUM"
    MEDIUM_HIGH = "MEDIUM_HIGH"
    HIGH = "HIGH"


class MarketTrend(str, Enum):
    BULL = "bull"         # 牛市（持续上涨）
    BEAR = "bear"         # 熊市（持续下跌）
    SIDEWAYS = "sideways" # 震荡市


@dataclass
class Fund:
    """基金基本信息"""
    code: str                     # 基金代码，如 "000001"
    name: str                     # 基金名称
    fund_type: FundType           # 基金类型
    manager: Optional[str] = None # 基金经理
    company: Optional[str] = None # 基金公司
    inception_date: Optional[date] = None  # 成立日期
    aum: Optional[float] = None   # 资产规模（亿元）
    risk_level: RiskLevel = RiskLevel.MEDIUM

    @property
    def display_name(self) -> str:
        return f"{self.name}({self.code})"


@dataclass
class NAVRecord:
    """单日净值记录"""
    fund_code: str
    date: date
    nav: float                   # 单位净值
    acc_nav: float               # 累计净值
    daily_return: float = 0.0    # 日收益率（小数形式，如0.01=1%）

    @classmethod
    def from_row(cls, fund_code: str, row: dict) -> "NAVRecord":
        """从一行数据创建 NAVRecord（兼容不同数据源格式）"""
        nav_date = row.get("净值日期", row.get("date", row.get("trade_date")))
        if isinstance(nav_date, str):
            from datetime import datetime
            nav_date = datetime.strptime(str(nav_date)[:10], "%Y-%m-%d").date()

        nav = float(row.get("单位净值", row.get("nav", row.get("net_value", 0))))
        acc_nav = float(row.get("累计净值", row.get("acc_nav", row.get("accumulated_net_value", nav))))

        daily_return = 0.0
        if "日增长率" in row:
            daily_return = float(row["日增长率"]) / 100.0 if row["日增长率"] else 0.0
        elif "daily_return" in row:
            daily_return = float(row["daily_return"])

        return cls(
            fund_code=fund_code,
            date=nav_date,
            nav=nav,
            acc_nav=acc_nav,
            daily_return=daily_return,
        )


@dataclass
class FundManager:
    """基金经理信息"""
    name: str
    fund_codes: list = field(default_factory=list)
    tenure_days: int = 0
    historical_return: float = 0.0


@dataclass
class FundSnapshot:
    """某个时间点的基金快照（用于AI决策上下文）"""
    fund_code: str
    fund_name: str
    fund_type: FundType
    current_nav: float
    change_7d: float     # 近7日收益率
    change_30d: float    # 近30日收益率
    change_90d: float    # 近90日收益率
    change_180d: float   # 近180日收益率
    volatility_30d: float  # 近30日波动率
    max_drawdown_90d: float  # 近90日最大回撤


@dataclass
class MarketContext:
    """市场环境快照"""
    date: date
    csi300_level: float = 0.0        # 沪深300点位
    csi300_change_30d: float = 0.0   # 沪深300近30日变化
    csi300_change_90d: float = 0.0   # 沪深300近90日变化
    market_trend: MarketTrend = MarketTrend.SIDEWAYS
    market_volatility: float = 0.0   # 市场波动率

    @classmethod
    def empty(cls, d: date) -> "MarketContext":
        return cls(date=d)

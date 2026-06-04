"""配置加载模块"""

import os
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AIConfig:
    """AI 模型配置 — 双层模型策略

    model (flash):  高频日常任务 — 回测逐日买卖决策
    advanced_model (pro): 深度分析任务 — 策略总结、经验提炼、投资建议
    """
    base_url: str = "https://api.deepseek.com/anthropic"
    api_key_env: str = "DEEPSEEK_API_KEY"
    model: str = "deepseek-v4-flash"       # Flash 模型: 日常决策
    advanced_model: str = "deepseek-v4-pro"  # Pro 模型: 深度分析
    max_tokens: int = 4096
    temperature: float = 0.3
    pro_temperature: float = 0.2  # Pro 模型专用温度


@dataclass
class CommissionConfig:
    buy_rate: float = 0.0015
    sell_rate: float = 0.0050


@dataclass
class ConstraintsConfig:
    max_positions: int = 10
    max_single_position_pct: float = 0.30
    min_cash_reserve: float = 500.0
    min_trade_amount: float = 100.0


@dataclass
class BacktestConfig:
    initial_capital: float = 10000.0
    commission: CommissionConfig = field(default_factory=CommissionConfig)
    constraints: ConstraintsConfig = field(default_factory=ConstraintsConfig)
    decision_frequency: str = "daily"


@dataclass
class RetrievalConfig:
    top_k: int = 10
    always_include_failures: int = 2
    similarity_threshold: float = 0.5


@dataclass
class LearningConfig:
    iterations: int = 10
    random_seed: int = 42
    backtest_duration_days: int = 365
    backtest_end_buffer_days: int = 30
    min_funds_per_backtest: int = 3
    max_funds_per_backtest: int = 10
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)


@dataclass
class ScraperConfig:
    primary_source: str = "akshare"
    fallback_source: str = "eastmoney"
    request_delay: float = 1.0
    batch_size: int = 20
    default_history_days: int = 1095
    cache_ttl_hours: int = 24


@dataclass
class ReportConfig:
    output_dir: str = "reports"
    formats: list = field(default_factory=lambda: ["markdown"])
    include_charts: bool = False
    recommendation_max_funds: int = 5
    recommendation_include_risk: bool = True
    recommendation_benchmark: str = "CSI300"


@dataclass
class GithubConfig:
    auto_commit: bool = True
    create_issue_for_recommendations: bool = True


@dataclass
class AppConfig:
    ai: AIConfig = field(default_factory=AIConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    learning: LearningConfig = field(default_factory=LearningConfig)
    scraper: ScraperConfig = field(default_factory=ScraperConfig)
    report: ReportConfig = field(default_factory=ReportConfig)
    github: GithubConfig = field(default_factory=GithubConfig)

    @property
    def api_key(self) -> str:
        key = os.environ.get(self.ai.api_key_env, "")
        if not key:
            raise ValueError(
                f"未找到 API Key！请设置环境变量 {self.ai.api_key_env}。\n"
                f"  export {self.ai.api_key_env}=\"sk-xxxx\""
            )
        return key


class ConfigLoader:
    """加载 YAML 配置文件并生成 AppConfig 对象"""

    @classmethod
    def load(cls, config_dir: Optional[str] = None) -> AppConfig:
        if config_dir is None:
            # 从当前目录或项目根目录查找 config/
            config_dir = cls._find_config_dir()

        config_path = Path(config_dir) / "default.yaml"
        if not config_path.exists():
            # 使用默认配置
            return AppConfig()

        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        return cls._parse_config(raw)

    @classmethod
    def load_funds(cls, config_dir: Optional[str] = None) -> dict:
        """加载基金池配置"""
        if config_dir is None:
            config_dir = cls._find_config_dir()

        funds_path = Path(config_dir) / "funds.yaml"
        if not funds_path.exists():
            return {"funds": []}

        with open(funds_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    @classmethod
    def _find_config_dir(cls) -> str:
        """查找配置目录"""
        # 向上查找 config/ 目录
        current = Path.cwd()
        for _ in range(10):
            if (current / "config").is_dir():
                return str(current / "config")
            current = current.parent
        # 默认使用当前目录下的 config
        return "config"

    @classmethod
    def _parse_config(cls, raw: dict) -> AppConfig:
        """将 YAML 字典解析为 AppConfig 对象"""
        ai_raw = raw.get("ai", {})
        ai = AIConfig(
            base_url=ai_raw.get("base_url", "https://api.deepseek.com/anthropic"),
            api_key_env=ai_raw.get("api_key_env", "DEEPSEEK_API_KEY"),
            # flash_model → model (高频日常决策), pro_model → advanced_model (深度分析)
            model=ai_raw.get("flash_model", ai_raw.get("model", "deepseek-v4-flash")),
            advanced_model=ai_raw.get("pro_model", ai_raw.get("advanced_model", "deepseek-v4-pro")),
            max_tokens=ai_raw.get("max_tokens", 4096),
            temperature=ai_raw.get("temperature", 0.3),
            pro_temperature=ai_raw.get("pro_temperature", 0.2),
        )

        bt_raw = raw.get("backtest", {})
        comm_raw = bt_raw.get("commission", {})
        const_raw = bt_raw.get("constraints", {})
        backtest = BacktestConfig(
            initial_capital=bt_raw.get("initial_capital", 10000.0),
            commission=CommissionConfig(
                buy_rate=comm_raw.get("buy_rate", 0.0015),
                sell_rate=comm_raw.get("sell_rate", 0.0050),
            ),
            constraints=ConstraintsConfig(
                max_positions=const_raw.get("max_positions", 10),
                max_single_position_pct=const_raw.get("max_single_position_pct", 0.30),
                min_cash_reserve=const_raw.get("min_cash_reserve", 500.0),
                min_trade_amount=const_raw.get("min_trade_amount", 100.0),
            ),
            decision_frequency=bt_raw.get("decision_frequency", "daily"),
        )

        lr_raw = raw.get("learning", {})
        ret_raw = lr_raw.get("retrieval", {})
        learning = LearningConfig(
            iterations=lr_raw.get("iterations", 10),
            random_seed=lr_raw.get("random_seed", 42),
            backtest_duration_days=lr_raw.get("backtest_duration_days", 365),
            backtest_end_buffer_days=lr_raw.get("backtest_end_buffer_days", 30),
            min_funds_per_backtest=lr_raw.get("min_funds_per_backtest", 3),
            max_funds_per_backtest=lr_raw.get("max_funds_per_backtest", 10),
            retrieval=RetrievalConfig(
                top_k=ret_raw.get("top_k", 10),
                always_include_failures=ret_raw.get("always_include_failures", 2),
                similarity_threshold=ret_raw.get("similarity_threshold", 0.5),
            ),
        )

        sc_raw = raw.get("scraper", {})
        scraper = ScraperConfig(
            primary_source=sc_raw.get("primary_source", "akshare"),
            fallback_source=sc_raw.get("fallback_source", "eastmoney"),
            request_delay=sc_raw.get("request_delay", 1.0),
            batch_size=sc_raw.get("batch_size", 20),
            default_history_days=sc_raw.get("default_history_days", 1095),
            cache_ttl_hours=sc_raw.get("cache_ttl_hours", 24),
        )

        rp_raw = raw.get("report", {})
        report = ReportConfig(
            output_dir=rp_raw.get("output_dir", "reports"),
            include_charts=rp_raw.get("include_charts", False),
            recommendation_max_funds=rp_raw.get("recommendation", {}).get("max_funds_recommended", 5),
        )

        gh_raw = raw.get("github", {})
        github = GithubConfig(
            auto_commit=gh_raw.get("auto_commit", True),
            create_issue_for_recommendations=gh_raw.get("create_issue_for_recommendations", True),
        )

        return AppConfig(ai=ai, backtest=backtest, learning=learning,
                         scraper=scraper, report=report, github=github)

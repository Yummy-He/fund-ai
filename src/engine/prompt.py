"""提示词构建器

将市场数据、持仓状态、历史经验组装成 AI 可理解的 prompt。
支持模板变量替换和经验格式化。
"""

import os
import json
import logging
from pathlib import Path
from datetime import date
from typing import Dict, List, Optional

from ..data.models import FundSnapshot, MarketContext, FundType, MarketTrend

logger = logging.getLogger("fund_ai.engine.prompt")


class PromptBuilder:
    """提示词构建器

    负责构建发送给 AI 的 system prompt 和 user message。
    """

    def __init__(self, template_dir: Optional[str] = None):
        """
        Args:
            template_dir: 提示词模板目录路径
        """
        if template_dir is None:
            # 自动查找 config/prompt_templates/
            cur = Path.cwd()
            for _ in range(5):
                candidate = cur / "config" / "prompt_templates"
                if candidate.is_dir():
                    template_dir = str(candidate)
                    break
                cur = cur.parent
            if template_dir is None:
                template_dir = "config/prompt_templates"

        self.template_dir = template_dir
        self._templates: Dict[str, str] = {}
        self._load_templates()

    def _load_templates(self) -> None:
        """加载所有提示词模板"""
        template_files = {
            "decision": "decision_prompt.txt",
            "summary": "summary_prompt.txt",
            "recommend": "recommend_prompt.txt",
        }
        for name, filename in template_files.items():
            path = os.path.join(self.template_dir, filename)
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    self._templates[name] = f.read()
                logger.debug(f"已加载模板: {name}")
            else:
                logger.warning(f"模板文件不存在: {path}")

    def build_system_prompt(
        self,
        strategy_patterns: Optional[List[dict]] = None,
        experiences_text: str = "",
    ) -> str:
        """构建 system prompt（角色定义 + 策略模式 + 历史经验）

        system prompt 在每次回测中保持不变，设置 AI 的角色和行为规则。
        """
        parts = []

        # 角色定义
        parts.append("你是一位专业的中国公募基金经理 AI 助手。")
        parts.append("你的目标是最大化风险调整后的投资收益（夏普比率）。")
        parts.append("你通过分析基金历史净值、市场趋势和回测经验来做出买卖决策。")

        # 投资理念
        parts.append("\n## 投资理念")
        parts.append('- 以概率思维看待投资，每笔交易都是一次有期望值的"赌注"')
        parts.append("- 顺势而为，但控制仓位，永远不要 All-in")
        parts.append("- 在别人恐惧时贪婪，在别人贪婪时恐惧")
        parts.append("- 承认错误，及时止损比幻想回本更重要")

        # 策略模式（从历史回测中总结的）
        if strategy_patterns:
            parts.append("\n## 已验证的投资策略模式")
            for i, p in enumerate(strategy_patterns, 1):
                win_rate = p.get("win_rate", 0)
                conf = p.get("confidence", "medium")
                desc = p.get("description", "")
                avg_ret = p.get("avg_return", 0)
                parts.append(f"{i}. {desc}")
                parts.append(f"   胜率: {win_rate:.0%} | 平均收益: {avg_ret:+.1f}% | 置信度: {conf}")

        # 历史经验（具体案例）
        if experiences_text:
            parts.append("\n## 历史回测经验")
            parts.append(experiences_text)

        return "\n".join(parts)

    def build_decision_user_message(
        self,
        context_date: date,
        portfolio_status: str,
        fund_data: str,
        market: MarketContext,
        constraints: dict,
        commission: dict,
    ) -> str:
        """构建决策请求的 user message

        包含当前市场数据、持仓状态、可投资基金列表。
        """
        # 使用模板
        template = self._templates.get("decision", self._default_decision_template())

        # 变量替换
        msg = template
        msg = msg.replace("{{date}}", context_date.strftime("%Y-%m-%d"))
        msg = msg.replace("{{csi300_level}}", f"{market.csi300_level:.2f}")
        msg = msg.replace("{{csi300_30d}}", f"{market.csi300_change_30d:+.2f}")
        msg = msg.replace("{{csi300_90d}}", f"{market.csi300_change_90d:+.2f}")
        msg = msg.replace("{{market_trend}}", market.market_trend.value)
        msg = msg.replace("{{market_volatility}}", f"{market.market_volatility:.1%}")
        msg = msg.replace("{{portfolio_status}}", portfolio_status)
        msg = msg.replace("{{fund_data}}", fund_data)
        msg = msg.replace("{{max_positions}}", str(constraints.get("max_positions", 10)))
        msg = msg.replace("{{max_single_pct}}", f"{constraints.get('max_single_position_pct', 0.30):.0%}")
        msg = msg.replace("{{min_cash}}", str(constraints.get("min_cash_reserve", 500)))
        msg = msg.replace("{{min_trade}}", str(constraints.get("min_trade_amount", 100)))
        msg = msg.replace("{{buy_rate}}", f"{commission.get('buy_rate', 0.0015) * 100:.2f}")
        msg = msg.replace("{{sell_rate}}", f"{commission.get('sell_rate', 0.005) * 100:.2f}")
        msg = msg.replace("{{experiences}}", "")  # 经验已在 system prompt 中

        return msg

    def build_summary_user_message(self, result) -> str:
        """构建回测总结请求的 user message"""
        template = self._templates.get("summary", self._default_summary_template())

        decisions_text = ""
        if hasattr(result, "decisions") and result.decisions:
            for d in result.decisions[:50]:  # 最多显示50条决策
                decisions_text += f"- [{d.date}] {d.fund_code}: {d.action} (置信度: {d.confidence:.0%})\n"

        msg = template
        msg = msg.replace("{{start_date}}", str(result.start_date))
        msg = msg.replace("{{end_date}}", str(result.end_date))
        msg = msg.replace("{{initial_capital}}", f"{result.initial_capital:,.0f}")
        msg = msg.replace("{{final_value}}", f"{result.final_value:,.0f}")
        msg = msg.replace("{{total_return}}", f"{result.total_return:+.2f}")
        msg = msg.replace("{{annualized_return}}", f"{result.annualized_return:+.2f}")
        msg = msg.replace("{{max_drawdown}}", f"{result.max_drawdown:.2f}")
        msg = msg.replace("{{sharpe_ratio}}", f"{result.sharpe_ratio:.3f}")
        msg = msg.replace("{{total_trades}}", str(result.total_trades))
        msg = msg.replace("{{win_rate}}", f"{result.win_rate:.1f}")
        msg = msg.replace("{{decision_summary}}", decisions_text or "（无详细决策记录）")

        return msg

    def build_recommend_user_message(
        self,
        context_date: date,
        strategy_summary: str,
        market: MarketContext,
        fund_snapshots: List[FundSnapshot],
    ) -> str:
        """构建投资建议的 user message"""
        template = self._templates.get("recommend", self._default_recommend_template())

        fund_text = ""
        for snap in fund_snapshots:
            fund_text += (
                f"- **{snap.fund_name}** ({snap.fund_code}) | 类型: {snap.fund_type.value}\n"
                f"  净值: {snap.current_nav:.4f} | "
                f"近7日: {snap.change_7d:+.2f}% | "
                f"近30日: {snap.change_30d:+.2f}% | "
                f"近90日: {snap.change_90d:+.2f}%\n"
                f"  波动率: {snap.volatility_30d:.2f}% | "
                f"最大回撤: {snap.max_drawdown_90d:.2f}%\n"
            )

        msg = template
        msg = msg.replace("{{strategy_summary}}", strategy_summary)
        msg = msg.replace("{{date}}", context_date.strftime("%Y-%m-%d"))
        msg = msg.replace("{{csi300_level}}", f"{market.csi300_level:.2f}")
        msg = msg.replace("{{csi300_30d}}", f"{market.csi300_change_30d:+.2f}")
        msg = msg.replace("{{csi300_90d}}", f"{market.csi300_change_90d:+.2f}")
        msg = msg.replace("{{market_trend}}", market.market_trend.value)
        msg = msg.replace("{{market_volatility}}", f"{market.market_volatility:.1%}")
        msg = msg.replace("{{fund_snapshots}}", fund_text)

        return msg

    @staticmethod
    def format_portfolio_status(portfolio, nav_map: dict) -> str:
        """格式化当前持仓状态为可读文本"""
        lines = []
        total = portfolio.total_value()
        lines.append(f"现金: ¥{portfolio.cash:,.2f} (占比 {portfolio.cash/total*100:.1f}%)")
        lines.append(f"持仓市值: ¥{portfolio.total_market_value():,.2f}")
        lines.append(f"总资产: ¥{total:,.2f}")
        lines.append(f"总收益率: {portfolio.total_return_pct():+.2f}%")

        if portfolio.positions:
            lines.append("\n具体持仓:")
            for code, pos in portfolio.positions.items():
                lines.append(
                    f"  {code}: {pos.shares:.2f}份 "
                    f"(成本 ¥{pos.avg_cost:.4f}, 现价 ¥{pos.current_nav:.4f}, "
                    f"盈亏 {pos.profit_loss_pct:+.2f}%)"
                )
        else:
            lines.append("\n（空仓）")

        return "\n".join(lines)

    @staticmethod
    def format_fund_snapshots(
        snapshots: List[FundSnapshot],
        nav_map: Dict[str, float],
    ) -> str:
        """格式化基金快照数据为可读文本"""
        lines = []
        for snap in snapshots:
            nav = nav_map.get(snap.fund_code, snap.current_nav)
            lines.append(
                f"- **{snap.fund_name}** ({snap.fund_code}) `{snap.fund_type.value}`: "
                f"净值 {nav:.4f} | "
                f"7d: {snap.change_7d:+.2f}% | "
                f"30d: {snap.change_30d:+.2f}% | "
                f"90d: {snap.change_90d:+.2f}% | "
                f"波动率: {snap.volatility_30d:.2f}%"
            )
        return "\n".join(lines)

    @staticmethod
    def format_experiences(experiences: list) -> str:
        """将经验列表格式化为可读文本（注入 system prompt）"""
        if not experiences:
            return "（暂无历史经验，这是首次回测）"

        parts = []
        for i, exp in enumerate(experiences, 1):
            if not exp:
                continue

            # 从 Experience 对象中提取关键信息
            scenario = getattr(exp, "scenario", {})
            decision = getattr(exp, "decision", {})
            outcome = getattr(exp, "outcome", {})
            lesson = getattr(exp, "lesson", "")

            if isinstance(scenario, dict):
                fund_type = scenario.get("fund_type", "UNKNOWN")
                market = scenario.get("market_trend", "unknown")
                volatility = scenario.get("market_volatility", 0)
            else:
                fund_type = getattr(scenario, "fund_type", "UNKNOWN")
                market = getattr(scenario, "market_trend", "unknown")
                volatility = getattr(scenario, "market_volatility", 0)

            if isinstance(decision, dict):
                action = decision.get("action", "hold")
                reasoning = decision.get("reasoning", "")
            else:
                action = getattr(decision, "action", "hold")
                reasoning = getattr(decision, "reasoning", "")

            if isinstance(outcome, dict):
                ret_30d = outcome.get("return_30d", 0)
                profitable = outcome.get("was_profitable", False)
            else:
                ret_30d = getattr(outcome, "return_30d", 0)
                profitable = getattr(outcome, "was_profitable", False)

            tag = "✓ 盈利" if profitable else "✗ 亏损"
            parts.append(
                f"[案例 {i}] 类型: {fund_type} | 市场: {market} | 波动率: {volatility:.0%}\n"
                f"  决策: {action} | 理由: {reasoning}\n"
                f"  结果: 30日回报 {ret_30d:+.2f}% | {tag}\n"
                f"  教训: {lesson}"
            )

        return "\n\n".join(parts)

    @staticmethod
    def _default_decision_template() -> str:
        return """你是一位专业的基金经理 AI 助手。你需要根据当前市场数据和投资组合状态，为每只可投资的基金做出买/卖/持决策。

## 当前市场环境
- 日期: {{date}}
- 市场状态: {{market_trend}}
- 沪深300 近30日: {{csi300_30d}}%
- 市场波动率: {{market_volatility}}

## 当前持仓
{{portfolio_status}}

## 可投资基金
{{fund_data}}

## 决策约束
- 最多持有 {{max_positions}} 只基金
- 单只仓位上限 {{max_single_pct}}
- 保留至少 ¥{{min_cash}} 现金
- 最小交易金额 ¥{{min_trade}}
- 买入费率 {{buy_rate}}%, 卖出费率 {{sell_rate}}%

请以 JSON 格式输出你的决策。"""

    @staticmethod
    def _default_summary_template() -> str:
        return """请对以下回测结果进行总结分析。

回测时间: {{start_date}} ~ {{end_date}}
初始资金: ¥{{initial_capital}}
最终资金: ¥{{final_value}}
总收益: {{total_return}}%
年化收益: {{annualized_return}}%
最大回撤: {{max_drawdown}}%
夏普比率: {{sharpe_ratio}}
交易次数: {{total_trades}}
胜率: {{win_rate}}%

决策记录:
{{decision_summary}}

请以 JSON 格式输出策略总结。"""

    @staticmethod
    def _default_recommend_template() -> str:
        return """请基于你的投资经验，对当前市场给出投资建议。

{{strategy_summary}}

当前市场 ({{date}}):
沪深300: {{csi300_level}} (30日: {{csi300_30d}}%, 90日: {{csi300_90d}}%)
市场状态: {{market_trend}}
波动率: {{market_volatility}}

基金数据:
{{fund_snapshots}}

请以 JSON 格式输出投资建议。"""

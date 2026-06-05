"""报告生成器

生成 Markdown 格式的各类报告:
- 回测报告
- 投资建议报告
- 每日市场快报
- 月度综合报告
"""

import os
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..utils.date_utils import beijing_now

logger = logging.getLogger("fund_ai.report.generator")


class MarkdownReportGenerator:
    """Markdown 报告生成器"""

    def __init__(self, output_dir: str = "reports"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_backtest_report(
        self,
        result,
        output_path: Optional[str] = None,
    ) -> str:
        """生成单次回测报告

        Args:
            result: BacktestResult 对象
            output_path: 输出文件路径（可选）
        Returns:
            Markdown 文本
        """
        lines = []
        lines.append(f"# 基金回测报告")
        lines.append(f"\n> 生成时间: {beijing_now().strftime('%Y-%m-%d %H:%M:%S')} (北京时间)\n")

        # 基本信息
        lines.append("## 📋 基本信息")
        lines.append(f"| 项目 | 详情 |")
        lines.append(f"|------|------|")
        lines.append(f"| 回测期间 | {result.start_date} ~ {result.end_date} |")
        funds = ", ".join(result.fund_pool[:5])
        if len(result.fund_pool) > 5:
            funds += f" 等 {len(result.fund_pool)} 只基金"
        lines.append(f"| 基金池 | {funds} |")
        lines.append(f"| 初始资金 | ¥{result.initial_capital:,.2f} |")

        # 收益指标
        lines.append("\n## 💰 收益指标")
        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 最终资金 | ¥{result.final_value:,.2f} |")
        return_emoji = "🟢" if result.total_return > 0 else "🔴"
        lines.append(f"| 总收益率 | {return_emoji} {result.total_return:+.2f}% |")
        lines.append(f"| 年化收益率 | {result.annualized_return:+.2f}% |")
        if hasattr(result, 'excess_return'):
            exc_emoji = "🟢" if result.excess_return > 0 else "🔴"
            lines.append(f"| 超额收益(相对基准) | {exc_emoji} {result.excess_return:+.2f}% |")

        # 风险指标
        lines.append("\n## ⚠️ 风险指标")
        lines.append(f"| 指标 | 数值 | 评价 |")
        lines.append(f"|------|------|------|")
        lines.append(f"| 最大回撤 | {result.max_drawdown:.2f}% | {self._grade_drawdown(result.max_drawdown)} |")
        lines.append(f"| 夏普比率 | {result.sharpe_ratio:.3f} | {self._grade_sharpe(result.sharpe_ratio)} |")
        lines.append(f"| 索提诺比率 | {result.sortino_ratio:.3f} | |")
        lines.append(f"| 年化波动率 | {result.volatility:.2f}% | {self._grade_volatility(result.volatility)} |")
        lines.append(f"| 卡玛比率 | {result.calmar_ratio:.3f} | |")

        # 交易指标
        lines.append("\n## 📊 交易分析")
        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 总交易次数 | {result.total_trades} |")
        lines.append(f"| 胜率 | {result.win_rate:.1f}% |")
        lines.append(f"| 平均盈利 | {result.avg_win:+.2f}% |")
        lines.append(f"| 平均亏损 | {result.avg_loss:+.2f}% |")
        lines.append(f"| 盈亏比 | {result.profit_factor:.3f} |")
        lines.append(f"| AI决策次数 | {result.decision_count} |")

        # AI 总结
        if result.summary:
            lines.append("\n## 🤖 AI 策略总结")
            lines.append(result.summary)

        # 评价
        if result.grade:
            lines.append(f"\n## 综合评级: **{result.grade}**")

        md = "\n".join(lines)

        # 写入文件
        if output_path:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(md)
            logger.info(f"回测报告已保存: {output_path}")

        return md

    def generate_recommendation_report(
        self,
        analysis: dict,
        output_path: Optional[str] = None,
    ) -> str:
        """生成投资建议报告

        Args:
            analysis: AI 分析结果（来自 recommend_prompt 的 JSON 输出）
            output_path: 输出路径
        """
        lines = []
        lines.append(f"# 🎯 基金投资建议报告")
        lines.append(f"\n> 生成时间: {beijing_now().strftime('%Y-%m-%d %H:%M:%S')} (北京时间)")
        lines.append(f"\n> ⚠️ 免责声明: 本报告由 AI 基于历史回测生成，不构成投资建议。投资有风险，入市需谨慎。\n")

        # 市场分析
        market_analysis = analysis.get("market_analysis", "（暂无）")
        lines.append(f"## 📈 市场分析")
        lines.append(market_analysis)

        # 整体策略
        strategy = analysis.get("overall_strategy", {})
        if strategy:
            lines.append(f"\n## 🎯 建议投资策略")
            eq_ratio = strategy.get("recommended_equity_ratio", 50)
            risk = strategy.get("risk_level", "moderate")
            reasoning = strategy.get("reasoning", "")
            lines.append(f"- **建议权益仓位**: {eq_ratio}%")
            lines.append(f"- **风险偏好**: {self._translate_risk(risk)}")
            lines.append(f"- **策略理由**: {reasoning}")

        # 具体建议
        recommendations = analysis.get("recommendations", [])
        if recommendations:
            lines.append(f"\n## 📋 具体基金推荐")
            lines.append(f"\n| 基金代码 | 基金名称 | 操作 | 建议配置 | 置信度 | 推荐理由 | 风险提示 |")
            lines.append(f"|----------|----------|------|----------|--------|----------|----------|")
            for rec in recommendations:
                code = rec.get("fund_code", "")
                name = rec.get("fund_name", "")
                action = rec.get("action", "持有")
                allocation = rec.get("suggested_allocation", 0)
                confidence = rec.get("confidence", 0.5)
                reasoning = rec.get("reasoning", "")
                risk_warn = rec.get("risk_warning", "")
                lines.append(
                    f"| {code} | {name} | {action} | {allocation}% | {confidence:.0%} | "
                    f"{reasoning[:30]}... | {risk_warn[:20]} |"
                )

        # 详细建议
        for i, rec in enumerate(recommendations, 1):
            lines.append(f"\n### {i}. {rec.get('fund_name', '')} ({rec.get('fund_code', '')})")
            lines.append(f"- **操作**: {rec.get('action', '持有')}")
            lines.append(f"- **建议配置**: {rec.get('suggested_allocation', 0)}%")
            lines.append(f"- **置信度**: {rec.get('confidence', 0.5):.0%}")
            lines.append(f"- **理由**: {rec.get('reasoning', '')}")
            lines.append(f"- **风险提示**: {rec.get('risk_warning', '')}")

        # 风险提示
        key_risks = analysis.get("key_risks", [])
        if key_risks:
            lines.append(f"\n## ⚠️ 重点关注风险")
            for risk in key_risks:
                lines.append(f"- {risk}")

        # 免责声明
        disclaimer = analysis.get("disclaimer", "")
        if disclaimer:
            lines.append(f"\n---\n{disclaimer}")

        md = "\n".join(lines)

        if output_path:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(md)
            logger.info(f"投资建议报告已保存: {output_path}")

        return md

    def generate_learning_report(
        self,
        results: list,
        comparison: dict,
        output_path: Optional[str] = None,
    ) -> str:
        """生成学习进度报告"""
        lines = []
        lines.append(f"# 📚 AI 投资学习报告")
        lines.append(f"\n> 生成时间: {beijing_now().strftime('%Y-%m-%d %H:%M:%S')} (北京时间)\n")

        # 概览
        lines.append(f"## 概览")
        lines.append(f"- 回测次数: {comparison.get('count', 0)}")
        lines.append(f"- 平均收益率: {comparison.get('avg_return', 0):+.2f}%")
        lines.append(f"- 平均夏普比率: {comparison.get('avg_sharpe', 0):.3f}")

        improvement = comparison.get("improvement", 0)
        trend_emoji = "🟢" if improvement > 0 else "🔴"
        lines.append(f"- 学习进步: {trend_emoji} {improvement:+.2f}%（后半段 vs 前半段）")

        # 各轮回测明细
        if results:
            lines.append(f"\n## 各轮回测明细")
            lines.append(f"\n| # | 期间 | 收益率 | 夏普 | 最大回撤 | 交易数 | 胜率 |")
            lines.append(f"|---|------|--------|------|----------|--------|------|")
            for i, r in enumerate(results, 1):
                lines.append(
                    f"| {i} | {r.start_date}~{r.end_date} | "
                    f"{r.total_return:+.2f}% | {r.sharpe_ratio:.3f} | "
                    f"{r.max_drawdown:.2f}% | {r.total_trades} | {r.win_rate:.1f}% |"
                )

        # 最好/最差
        if results:
            best_idx = max(range(len(results)), key=lambda i: results[i].total_return)
            worst_idx = min(range(len(results)), key=lambda i: results[i].total_return)
            lines.append(f"\n- 🏆 **最佳回测**: #{best_idx+1} (收益率 {results[best_idx].total_return:+.2f}%)")
            lines.append(f"- 📉 **最差回测**: #{worst_idx+1} (收益率 {results[worst_idx].total_return:+.2f}%)")

        md = "\n".join(lines)

        if output_path:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(md)

        return md

    def generate_daily_brief(
        self,
        context_date: date,
        fund_data: List[dict],
        output_path: Optional[str] = None,
    ) -> str:
        """生成每日市场简报"""
        lines = []
        lines.append(f"# 📊 每日基金简报")
        date_str = context_date.strftime("%Y年%m月%d日")
        lines.append(f"\n> {date_str}\n")

        if not fund_data:
            lines.append("暂无数据")
            return "\n".join(lines)

        lines.append("| 基金 | 代码 | 净值 | 日涨跌 | 近7日 | 近30日 |")
        lines.append("|------|------|------|--------|--------|-------|")
        for f in fund_data:
            change_emoji = "🔴" if f.get("daily_change", 0) < 0 else "🟢"
            lines.append(
                f"| {f.get('name', '')} | {f.get('code', '')} | "
                f"{f.get('nav', 0):.4f} | {change_emoji} {f.get('daily_change', 0):+.2f}% | "
                f"{f.get('change_7d', 0):+.2f}% | {f.get('change_30d', 0):+.2f}% |"
            )

        md = "\n".join(lines)

        if output_path:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(md)

        return md

    def generate_live_report(
        self,
        target_date,
        snapshot_before: dict,
        snapshot_after: dict,
        decisions: list,
        market_context: Optional[dict] = None,
        output_path: Optional[str] = None,
    ) -> str:
        """生成实盘交易日报

        Args:
            target_date: 交易日期
            snapshot_before: 决策前持仓快照
            snapshot_after: 决策后持仓快照
            decisions: 当日的交易决策列表
            market_context: 市场环境（可选）
            output_path: 输出路径
        """
        from ..utils.date_utils import beijing_now as _beijing_now2

        lines = []
        lines.append(f"# 📊 实盘交易日报")
        lines.append(f"\n> 生成时间: {_beijing_now2().strftime('%Y-%m-%d %H:%M:%S')} (北京时间)")
        lines.append(f"> 交易日期: {target_date.strftime('%Y年%m月%d日')}")
        lines.append("")

        # 市场概况
        lines.append("## 📈 市场概况")
        lines.append("")
        if market_context:
            lines.append("| 指标 | 数值 |")
            lines.append("|------|------|")
            for k, v in market_context.items():
                lines.append(f"| {k} | {v} |")
        else:
            lines.append("（市场数据由 AI 决策时实时获取）")
        lines.append("")

        # 当前持仓
        lines.append("## 💼 当前持仓")
        lines.append("")
        positions = snapshot_after.get("positions", [])
        if positions:
            lines.append("| 基金代码 | 持有份额 | 成本价 | 当前净值 | 市值(¥) | 浮动盈亏 | 盈亏% |")
            lines.append("|----------|----------|--------|----------|----------|----------|-------|")
            for pos in positions:
                pnl = pos.get("market_value", 0) * pos.get("profit_loss_pct", 0) / 100
                pnl_pct = pos.get("profit_loss_pct", 0)
                emoji = "🟢" if pnl_pct > 0 else ("🔴" if pnl_pct < 0 else "⚪")
                lines.append(
                    f"| {pos['fund_code']} | {pos['shares']:.2f} | {pos['avg_cost']:.4f} | "
                    f"{pos['current_nav']:.4f} | {pos['market_value']:,.2f} | "
                    f"{emoji} ¥{pnl:+,.2f} | {pnl_pct:+.2f}% |"
                )
            lines.append("")
        else:
            lines.append("暂无持仓")
            lines.append("")

        # 今日操作明细
        lines.append("## 🔧 今日操作")
        lines.append("")
        if decisions:
            lines.append("| 操作 | 基金 | 金额(¥) | 净值 | 份额 | 手续费(¥) | 置信度 | 理由 |")
            lines.append("|------|------|----------|------|------|-----------|--------|------|")
            for d in decisions:
                action_str = {"buy": "🟢 买入", "sell": "🔴 卖出", "increase": "🟢 加仓", "decrease": "🔴 减仓"}.get(
                    d.get("action", ""), d.get("action", "")
                )
                shares = d.get("shares", "")
                price = d.get("price", "")
                commission = d.get("commission", 0)
                lines.append(
                    f"| {action_str} | {d['fund_code']} | {d.get('amount', 0):,.2f} | "
                    f"{f'{price:.4f}' if price else '-'} | "
                    f"{f'{shares:.2f}' if shares else '-'} | "
                    f"{commission:.2f} | "
                    f"{d.get('confidence', 0):.0%} | {d.get('reasoning', '')[:50]} |"
                )
            lines.append("")

            # 手续费汇总
            total_buy_commission = sum(d.get("commission", 0) for d in decisions if d.get("action") in ("buy", "increase"))
            total_sell_commission = sum(d.get("commission", 0) for d in decisions if d.get("action") in ("sell", "decrease"))
            total_commission = total_buy_commission + total_sell_commission
            if total_commission > 0:
                lines.append("### 💸 手续费明细")
                lines.append("")
                lines.append("| 类型 | 金额(¥) |")
                lines.append("|------|----------|")
                if total_buy_commission > 0:
                    lines.append(f"| 申购费 | {total_buy_commission:.2f} |")
                if total_sell_commission > 0:
                    lines.append(f"| 赎回费 | {total_sell_commission:.2f} |")
                lines.append(f"| **合计** | **{total_commission:.2f}** |")
                lines.append("")
        else:
            lines.append("今日无操作，维持现有持仓")
            lines.append("")

        # AI 决策分析
        if decisions:
            lines.append("## 🤖 AI 决策分析")
            lines.append("")
            for d in decisions:
                if d.get("reasoning"):
                    lines.append(f"- **{d['fund_code']}** ({d['action']}): {d['reasoning']}")
            lines.append("")

        # 投资组合概况
        lines.append("## 📊 投资组合概况")
        lines.append("")
        lines.append("| 指标 | 数值 |")
        lines.append("|------|------|")
        lines.append(f"| 初始资金 | ¥10,000.00 |")
        lines.append(f"| 当前总价值 | ¥{snapshot_after.get('total_value', 0):,.2f} |")
        lines.append(f"| 现金余额 | ¥{snapshot_after.get('cash', 0):,.2f} |")
        lines.append(f"| 持仓市值 | ¥{snapshot_after.get('total_market_value', 0):,.2f} |")
        lines.append(f"| 累计收益率 | {snapshot_after.get('total_return_pct', 0):+.2f}% |")
        lines.append(f"| 持有基金数 | {snapshot_after.get('position_count', 0)} |")
        lines.append("")

        # 本日盈亏明细
        lines.append("## 📉 本日盈亏明细")
        lines.append("")
        day_pnl = 0.0
        if snapshot_before and snapshot_after:
            day_pnl = snapshot_after.get("total_value", 0) - snapshot_before.get("total_value", 0)

        # 已有持仓的市值涨跌
        pos_before = {p["fund_code"]: p for p in snapshot_before.get("positions", [])}
        pos_after = {p["fund_code"]: p for p in snapshot_after.get("positions", [])}
        market_change = 0.0
        for code, pos in pos_after.items():
            if code in pos_before:
                before_mv = pos_before[code].get("market_value", 0)
                after_mv = pos.get("market_value", 0)
                market_change += (after_mv - before_mv)

        total_buy_fee = sum(d.get("commission", 0) for d in decisions if d.get("action") in ("buy", "increase"))
        total_sell_fee = sum(d.get("commission", 0) for d in decisions if d.get("action") in ("sell", "decrease"))
        total_fee = total_buy_fee + total_sell_fee

        # 其他（新买入净值波动等）
        other = day_pnl - market_change + total_fee

        pnl_emoji = "🟢" if day_pnl >= 0 else "🔴"
        lines.append("| 项目 | 金额(¥) | 说明 |")
        lines.append("|------|----------|------|")
        lines.append(f"| 📊 总变动 | {pnl_emoji} {day_pnl:+,.2f} | 今日总价值 - 前一日总价值 |")
        if abs(market_change) > 0.01:
            m_emoji = "🟢" if market_change >= 0 else "🔴"
            lines.append(f"| 📈 持仓涨跌 | {m_emoji} {market_change:+,.2f} | 已有持仓的市值变动 |")
        if total_buy_fee > 0:
            lines.append(f"| 💸 申购手续费 | -{total_buy_fee:.2f} | 今日买入/加仓产生 |")
        if total_sell_fee > 0:
            lines.append(f"| 💸 赎回手续费 | -{total_sell_fee:.2f} | 今日卖出/减仓产生 |")
        if abs(other) > 0.01:
            lines.append(f"| 🔄 其他 | {other:+,.2f} | 新买入基金净值波动等 |")
        if not decisions:
            if abs(market_change) > 0.01:
                lines.append(f"| 📈 持仓涨跌 | {market_change:+,.2f} | 无操作，纯持仓波动 |")
            else:
                lines.append(f"| — | 0.00 | 无操作，净值未更新 |")
        lines.append("")

        # 免责声明
        lines.append("---")
        lines.append("> ⚠️ 免责声明: 本报告由 AI 基于历史经验生成，为模拟交易记录，不构成投资建议。投资有风险，入市需谨慎。")

        md = "\n".join(lines)

        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(md)

        return md

    @staticmethod
    def _grade_sharpe(sharpe: float) -> str:
        if sharpe > 2.0:
            return "🏆 极佳"
        elif sharpe > 1.0:
            return "✅ 良好"
        elif sharpe > 0.5:
            return "⚠️ 一般"
        elif sharpe > 0:
            return "⚠️ 偏低"
        else:
            return "❌ 亏损"

    @staticmethod
    def _grade_drawdown(dd: float) -> str:
        dd = abs(dd)
        if dd < 5:
            return "✅ 极低"
        elif dd < 10:
            return "✅ 较低"
        elif dd < 20:
            return "⚠️ 中等"
        elif dd < 30:
            return "⚠️ 较高"
        else:
            return "❌ 极高"

    @staticmethod
    def _grade_volatility(vol: float) -> str:
        if vol < 10:
            return "✅ 低波动"
        elif vol < 20:
            return "⚠️ 中等波动"
        elif vol < 30:
            return "⚠️ 高波动"
        else:
            return "❌ 极高波动"

    @staticmethod
    def _translate_risk(risk: str) -> str:
        return {
            "conservative": "🛡️ 保守型",
            "moderate": "⚖️ 稳健型",
            "aggressive": "🚀 进取型",
        }.get(risk, risk)

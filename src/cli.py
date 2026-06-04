"""CLI 命令行入口

提供 scrape / backtest / learn / recommend / report 五个主命令。
"""

import os
import sys

# 加载 .env 文件（如果存在）
def _load_dotenv():
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("\"'")
                    if key and val and key not in os.environ:
                        os.environ[key] = val
_load_dotenv()

# 也尝试从项目根目录加载
import os as _os2
for _try_dir in [os.getcwd(), os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]:
    _env_file = os.path.join(_try_dir, ".env")
    if os.path.exists(_env_file):
        with open(_env_file, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line or _line.startswith("#"):
                    continue
                if "=" in _line:
                    _k, _v = _line.split("=", 1)
                    _k, _v = _k.strip(), _v.strip().strip("\"'")
                    if _k and _v and _k not in os.environ:
                        os.environ[_k] = _v
del _os2

# Windows 终端 UTF-8 修复
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

import random
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.panel import Panel

from .utils.config import ConfigLoader, AppConfig
from .utils.logging import setup_logging
from .data.scraper import FundDataScraper
from .data.store import FundRepository
from .engine.ai_client import AIClient
from .engine.prompt import PromptBuilder
from .engine.backtest import BacktestEngine
from .engine.decision import FundDecisionMaker
from .engine.metrics import MetricsCalculator
from .learning.experience import ExperienceStore, Experience, ScenarioSnapshot, DecisionRecord, OutcomeRecord
from .learning.retriever import ExperienceRetriever
from .learning.evaluator import StrategyEvaluator
from .report.generator import MarkdownReportGenerator

console = Console()
logger = logging.getLogger("fund_ai.cli")


def load_config() -> AppConfig:
    """加载配置"""
    try:
        return ConfigLoader.load()
    except Exception as e:
        console.print(f"[yellow]⚠ 配置加载失败: {e}，使用默认配置[/yellow]")
        return AppConfig()


@click.group()
@click.option("--config", "-c", default=None, help="配置文件目录")
@click.option("--verbose", "-v", is_flag=True, help="详细输出")
@click.pass_context
def main(ctx, config, verbose):
    """基金AI分析系统 - AI驱动的基金投资学习与建议"""
    ctx.ensure_object(dict)
    ctx.obj["config_dir"] = config
    setup_logging(level="DEBUG" if verbose else "INFO")


@main.command()
@click.option("--funds", "-f", multiple=True, help="指定基金代码（可多次指定）")
@click.option("--all", "all_funds", is_flag=True, help="抓取全部基金")
@click.pass_context
def scrape(ctx, funds, all_funds):
    """抓取基金净值数据"""
    console.print(Panel.fit("[Scrape] 基金数据抓取", style="bold blue"))

    config = load_config()
    scraper = FundDataScraper(config=config.scraper)

    if funds:
        console.print(f"抓取指定基金: {', '.join(funds)}")
        for code in funds:
            df = scraper.scrape_nav_history(code)
            if not df.empty:
                console.print(f"  ✅ {code}: {len(df)} 条记录")
            else:
                console.print(f"  ❌ {code}: 无数据")
    elif all_funds:
        scraper.scrape_fund_list()
    else:
        # 根据 config/funds.yaml 抓取
        funds_list = scraper.scrape_funds_from_config(config_dir=ctx.obj.get("config_dir"))
        if funds_list:
            table = Table(title="已抓取基金")
            table.add_column("代码", style="cyan")
            table.add_column("名称", style="green")
            table.add_column("类型", style="yellow")
            for f in funds_list:
                table.add_row(f.code, f.name, f.fund_type.value)
            console.print(table)

    # 也抓取基准指数
    scraper.scrape_index_data()
    console.print("[green]✅ 数据抓取完成[/green]")


@main.command()
@click.option("--start", "-s", required=True, help="回测开始日期 YYYY-MM-DD")
@click.option("--end", "-e", required=True, help="回测结束日期 YYYY-MM-DD")
@click.option("--funds", "-f", multiple=True, help="基金代码")
@click.option("--capital", "-c", default=10000.0, help="初始资金（元）")
@click.option("--interval", "-i", default=1, help="决策间隔（交易日）")
@click.option("--baseline", is_flag=True, help="运行基准策略对比")
@click.pass_context
def backtest(ctx, start, end, funds, capital, interval, baseline):
    """运行单次回测"""
    console.print(Panel.fit(f"Backtest: {start} ~ {end}", style="bold blue"))

    config = load_config()
    repo = FundRepository()

    # 确定基金池
    if not funds:
        cfg_funds = ConfigLoader.load_funds(ctx.obj.get("config_dir"))
        enabled = [f for f in cfg_funds.get("funds", []) if f.get("enabled", True)]
        fund_pool = [f["code"] for f in enabled[:5]]  # 默认取前5只
    else:
        fund_pool = list(funds)

    console.print(f"基金池: {', '.join(fund_pool)}")
    console.print(f"初始资金: ¥{capital:,.2f}")

    # 初始化 AI 客户端
    ai = AIClient(config=config.ai)
    prompt = PromptBuilder(template_dir=ctx.obj.get("config_dir") and
                           f"{ctx.obj['config_dir']}/prompt_templates")

    engine = BacktestEngine(config=config, ai_client=ai, prompt_builder=prompt, fund_repo=repo)
    engine.initial_capital = capital

    from datetime import datetime
    start_date = datetime.strptime(start, "%Y-%m-%d").date()
    end_date = datetime.strptime(end, "%Y-%m-%d").date()

    # 基准策略
    if baseline:
        console.print("[bold]基准策略 (等权买入持有)[/bold] ...")
        base_result = engine.run_simple_baseline(start_date, end_date, fund_pool)
        _print_result(base_result, "基准策略")

    # AI 策略
    console.print(f"\n[AI] 运行 AI 策略（决策间隔: {interval}天）...")
    ai_result = engine.run(start_date, end_date, fund_pool, decision_interval=interval)
    _print_result(ai_result, "AI 策略")

    # 生成报告
    reporter = MarkdownReportGenerator()
    path = f"reports/backtests/bt_{start}_{end}.md"
    reporter.generate_backtest_report(ai_result, output_path=path)
    console.print(f"[green]✅ 报告已保存: {path}[/green]")


@main.command()
@click.option("--iterations", "-n", default=5, help="学习迭代次数")
@click.option("--funds", "-f", multiple=True, help="基金代码")
@click.option("--seed", default=None, type=int, help="随机种子")
@click.pass_context
def learn(ctx, iterations, funds, seed):
    """多轮回测学习 - AI 通过反复回测积累投资经验"""
    console.print(Panel.fit(f"[Learn] AI 学习循环 ({iterations} 轮)", style="bold blue"))

    config = load_config()
    random.seed(seed or config.learning.random_seed)

    repo = FundRepository()

    # 基金池
    if not funds:
        cfg_funds = ConfigLoader.load_funds(ctx.obj.get("config_dir"))
        enabled = [f for f in cfg_funds.get("funds", []) if f.get("enabled", True)]
        all_codes = [f["code"] for f in enabled]
    else:
        all_codes = list(funds)

    if len(all_codes) < config.learning.min_funds_per_backtest:
        console.print(f"[red]❌ 基金不足: 需要至少 {config.learning.min_funds_per_backtest} 只，当前 {len(all_codes)} 只[/red]")
        return

    console.print(f"可用基金: {', '.join(all_codes)}")

    # 初始化组件
    ai = AIClient(config=config.ai)
    prompt = PromptBuilder()
    exp_store = ExperienceStore()
    retriever = ExperienceRetriever(
        store=exp_store,
        top_k=config.learning.retrieval.top_k,
        always_include_failures=config.learning.retrieval.always_include_failures,
    )
    evaluator = StrategyEvaluator(exp_store)
    reporter = MarkdownReportGenerator()

    # 学习循环
    results = []
    strategy_patterns = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("学习进行中...", total=iterations)

        for i in range(iterations):
            progress.update(task, description=f"回测 {i+1}/{iterations}...")

            # 随机选择时间段
            start_date, end_date = _random_time_window()

            # 随机选择基金
            n_funds = random.randint(
                config.learning.min_funds_per_backtest,
                min(config.learning.max_funds_per_backtest, len(all_codes)),
            )
            fund_pool = random.sample(all_codes, n_funds)

            # 初始化回测引擎（带经验检索）
            engine = BacktestEngine(
                config=config,
                ai_client=ai,
                prompt_builder=prompt,
                fund_repo=repo,
                experience_retriever=retriever,
            )

            # 注入当前策略模式到决策引擎
            if engine.decision_maker is None:
                engine.decision_maker = FundDecisionMaker(
                    ai_client=ai,
                    prompt_builder=prompt,
                    fund_repo=repo,
                    experience_retriever=retriever,
                    strategy_patterns=strategy_patterns,
                )

            # 运行回测
            try:
                result = engine.run(
                    start_date=start_date,
                    end_date=end_date,
                    fund_pool=fund_pool,
                    decision_interval=3,  # 每3个交易日决策一次降低API调用
                )
            except Exception as e:
                logger.error(f"回测 {i+1} 失败: {e}")
                progress.advance(task)
                continue

            results.append(result)
            console.print(
                f"  [{i+1}/{iterations}] {start_date}~{end_date} | "
                f"收益: {result.total_return:+.2f}% | "
                f"夏普: {result.sharpe_ratio:.3f} | "
                f"回撤: {result.max_drawdown:.2f}%"
            )

            # AI 总结教训
            try:
                lessons = engine.decision_maker.generate_lessons(result)
            except Exception:
                lessons = {"key_lessons": [], "summary": ""}

            # 保存经验
            exp_store.save_summary(
                backtest_id=f"bt_{start_date}_{end_date}",
                summary=lessons,
            )

            # 更新策略模式
            strategy_patterns = evaluator.identify_patterns()
            if strategy_patterns:
                console.print(f"  识别到 {len(strategy_patterns)} 条策略模式")

            progress.advance(task)

    # 评估学习效果
    comparison = evaluator.compare_backtests(results)
    improved = evaluator.detect_improvement(results)

    console.print(f"\n## 学习总结")
    console.print(f"回测次数: {len(results)}")
    if results:
        avg_ret = sum(r.total_return for r in results) / len(results)
        console.print(f"平均收益: {avg_ret:+.2f}%")
    console.print(f"学习进步: {'进步 是' if improved else '[否] 尚未明显'}")

    # 生成学习报告
    report_md = reporter.generate_learning_report(
        results=results,
        comparison=comparison,
        output_path="reports/backtests/learning_report.md",
    )

    # 更新策略总结
    strategy_summary = evaluator.generate_strategy_summary()
    console.print(f"\n[bold]策略总结:[/bold]")
    console.print(strategy_summary)

    console.print(f"\n[green]✅ 学习完成！报告: reports/backtests/learning_report.md[/green]")


@main.command()
@click.option("--output", "-o", default=None, help="输出文件路径")
@click.option("--detailed", is_flag=True, help="生成详细报告")
@click.pass_context
def recommend(ctx, output, detailed):
    """生成当前投资建议"""
    console.print(Panel.fit("[Recommend] 投资建议生成", style="bold blue"))

    config = load_config()
    repo = FundRepository()
    ai = AIClient(config=config.ai)
    prompt = PromptBuilder()

    # 获取经验总结
    exp_store = ExperienceStore()
    evaluator = StrategyEvaluator(exp_store)
    strategy_summary = evaluator.generate_strategy_summary()

    # 获取当前基金数据
    from datetime import date
    today = date.today()

    funds = repo.get_funds()
    if not funds:
        console.print("[yellow]⚠ 未找到基金数据，请先运行 scrape[/yellow]")
        return

    # 获取最新净值和快照
    from .engine.decision import FundDecisionMaker
    dm = FundDecisionMaker(ai_client=ai, prompt_builder=prompt, fund_repo=repo)
    nav_map = dm._get_current_navs([f.code for f in funds], today)
    snapshots = dm._get_fund_snapshots([f.code for f in funds], today, nav_map)
    market = dm._build_market_context(today)

    # 构建提示词
    user_msg = prompt.build_recommend_user_message(
        context_date=today,
        strategy_summary=strategy_summary,
        market=market,
        fund_snapshots=snapshots,
    )

    system_prompt = (
        "你是一位经验丰富的基金投资顾问。"
        "你通过大量历史回测积累了丰富的投资经验。"
        "请基于这些经验和当前市场状况，给出具体的投资建议。"
    )

    console.print("正在生成投资建议...")
    try:
        analysis = ai.chat_advanced(
            system_prompt=system_prompt,
            user_message=user_msg,
            json_mode=True,
        )
    except Exception as e:
        console.print(f"[red]❌ AI 调用失败: {e}[/red]")
        return

    # 生成报告
    reporter = MarkdownReportGenerator()
    output_path = output or f"reports/recommendations/{today}.md"
    report_md = reporter.generate_recommendation_report(analysis, output_path=output_path)

    console.print(report_md)
    console.print(f"[green]✅ 投资建议已生成: {output_path}[/green]")


@main.command()
@click.option("--type", "-t", "report_type", default="backtest", help="报告类型: backtest/learning/daily/monthly")
@click.option("--output", "-o", default=None, help="输出路径")
@click.pass_context
def report(ctx, report_type, output):
    """生成各类报告"""
    console.print(f"[Report] 生成 {report_type} 报告...")

    reporter = MarkdownReportGenerator()
    repo = FundRepository()

    if report_type == "daily":
        today = date.today()
        funds = repo.get_funds()[:10]
        fund_data = []
        for f in funds:
            nav_rec = repo.get_nav_on_date(f.code, today)
            if nav_rec:
                fund_data.append({
                    "code": f.code,
                    "name": f.name,
                    "nav": nav_rec.nav,
                    "daily_change": nav_rec.daily_return * 100,
                })
        path = output or f"reports/daily/{today}.md"
        reporter.generate_daily_brief(today, fund_data, output_path=path)
    elif report_type == "monthly":
        # 汇总本月内的回测和学习结果
        exp_store = ExperienceStore()
        stats = exp_store.stats()
        evaluator = StrategyEvaluator(exp_store)
        strategy = evaluator.generate_strategy_summary()
        # 写入文件
        today = date.today()
        path = output or f"reports/recommendations/{today.strftime('%Y-%m')}.md"
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# 月度报告 - {today.strftime('%Y年%m月')}\n\n")
            f.write(strategy)
            f.write(f"\n\n经验总数: {stats['total']}\n")
        console.print(f"[green]✅ 月度报告已保存: {path}[/green]")
    else:
        console.print(f"[yellow]未知报告类型: {report_type}[/yellow]")


def _print_result(result, label: str):
    """打印回测结果表格"""
    table = Table(title=f"{label} 结果")
    table.add_column("指标", style="cyan")
    table.add_column("数值", style="green")

    table.add_row("总收益率", f"{result.total_return:+.2f}%")
    table.add_row("年化收益率", f"{result.annualized_return:+.2f}%")
    table.add_row("最大回撤", f"{result.max_drawdown:.2f}%")
    table.add_row("夏普比率", f"{result.sharpe_ratio:.3f}")
    table.add_row("波动率", f"{result.volatility:.2f}%")
    table.add_row("交易次数", str(result.total_trades))
    table.add_row("胜率", f"{result.win_rate:.1f}%")

    console.print(table)


def _random_time_window():
    """生成随机回测时间窗口"""
    today = date.today()
    # 从 2020-01-01 到 6个月前 之间随机
    earliest = date(2020, 1, 1)
    latest_end = today - timedelta(days=180)

    days_range = (latest_end - earliest).days
    random_offset = random.randint(0, days_range - 365)
    start = earliest + timedelta(days=random_offset)
    end = start + timedelta(days=random.randint(180, 730))  # 6个月到2年

    if end > today:
        end = today - timedelta(days=30)

    return start, end


if __name__ == "__main__":
    main()

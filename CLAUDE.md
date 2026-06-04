# CLAUDE.md — 基金 AI 分析系统

> **如果你是 Claude Code，请先阅读此文件和 AI_MUST_READ.md。**

## 项目概述

AI 驱动的中国公募基金投资分析系统。AI 通过 DeepSeek API 读取历史净值，反复回测（"穿越"到过去用 1 万元逐日决策买卖），积累经验，学会分析基金并给出投资建议。

## 当前状态（v0.2.7 | 2026-06-04）

- 10 只基金 ~727 条/只净值数据（2023-06 ~ 今）
- 每只基金独立真实费率（从东方财富 + akshare 抓取）
- 双层 AI 模型：Flash（日常回测决策）+ Pro（策略总结/建议）
- 三种策略：AI 主动决策 / 等权买入持有 / 每月定投（DCA）
- GitHub Actions 自动化：每日抓取+回测 / 每周学习+报告 / 月度深度分析
- 完整操作手册：https://github.com/Yummy-He/fund-ai/wiki

## 关键约束

1. 数据只增：`data/nav/*.csv` 只追加不覆盖
2. 经验只增：`experiences/` 只追加不删除
3. 净值 T+1：当天决策看到的是前一交易日净值
4. 手续费无最低收费：中国公募基金按百分比收费，无每笔最低 ¥5 这种规则
5. DeepSeek API：base_url=`https://api.deepseek.com/anthropic`，不支持 cache_control
6. akshare v1.18+：用 `fund_open_fund_info_em(symbol, indicator, period)` 获取净值
7. 交易日判断：三层检测 — tool_trade_date_hist_sina()（格式 YYYY-MM-DD）→ stock_zh_index_daily 上证最新日 → 工作日回退
8. Wiki 单独仓库：`docs/WIKI.md` 是源，需手动同步到 `https://github.com/Yummy-He/fund-ai.wiki.git` 的 `Home.md`
9. **推送前必须测试**：任何 Python 代码改动，commit 前必须跑语法检查 + 至少 import 测试 + 核心函数单元验证。禁止不经测试直接推送。
10. 推送冲突：多个 workflow 可能同时运行（push 触发器），每次 push 前要 `git pull --rebase`

## 项目结构

```
fund-ai/
├── config/
│   ├── default.yaml           # 主配置（模型/回测/学习/费率参数）
│   ├── funds.yaml             # 基金池（10只）
│   └── prompt_templates/      # AI 提示词模板
├── src/
│   ├── cli.py                 # CLI入口（scrape/backtest/learn/recommend/report）
│   ├── data/
│   │   ├── fees.py            # 费率模型 + FeeManager（每只基金独立费率）
│   │   ├── models.py          # Fund/NAVRecord/FundType 等数据模型
│   │   ├── scraper.py         # 数据抓取编排
│   │   ├── store.py           # CSV 存储 + FundRepository
│   │   └── sources/
│   │       ├── akshare_source.py   # 主数据源
│   │       └── eastmoney_source.py # 备用数据源
│   ├── engine/
│   │   ├── ai_client.py       # DeepSeek API（Anthropic格式）
│   │   ├── backtest.py        # 回测引擎（含 run/run_simple_baseline/run_dca）
│   │   ├── decision.py        # AI 决策编排
│   │   ├── metrics.py         # 指标计算（夏普/回撤/CAGR）
│   │   ├── orders.py          # 订单验证执行（含动态费率）
│   │   ├── portfolio.py       # 持仓管理（含 FIFO 持有天数）
│   │   ├── prompt.py          # 提示词构建器
│   │   └── simulator.py       # 时间步进器
│   ├── learning/
│   │   ├── experience.py      # 经验存储（Experience + ExperienceStore）
│   │   ├── evaluator.py       # 策略评估（跨回测模式提取）
│   │   └── retriever.py       # 多因子相似度检索（7维加权）
│   ├── report/
│   │   └── generator.py       # Markdown 报告生成
│   └── utils/
│       ├── config.py          # 配置加载（YAML → AppConfig）
│       ├── date_utils.py      # 交易日历
│       └── logging.py         # 日志
├── data/                      # 净值+费率数据（Git追踪）
│   ├── nav/                   # 每只基金一个CSV
│   ├── funds/fees.json        # 所有基金费率缓存
│   └── index/                 # 基准指数
├── experiences/               # AI经验库（Git追踪）
│   ├── index.json             # 经验索引
│   ├── decisions/             # 决策详情
│   └── summaries/             # 策略总结
├── reports/                   # 报告（Git追踪）
│   ├── backtests/             # 回测报告（.md）
│   ├── recommendations/       # 投资建议
│   └── daily/                 # 每日简报
├── docs/WIKI.md               # 操作手册源文件
└── .github/workflows/         # CI/CD
    ├── daily-decision.yml     # 每日：判交易日→抓数据→回测
    ├── weekly-report.yml      # 每周六：学习+周报
    └── monthly-report.yml     # 每月1日：深度学习+月报
```

## CLI 命令

```bash
python -m src.cli scrape                         # 抓数据+费率
python -m src.cli backtest -s 2024-01-01 -e 2025-01-01  # AI回测
python -m src.cli backtest ... --baseline --dca   # 三策略对比
python -m src.cli learn -n 5                     # 5轮学习
python -m src.cli recommend                       # 投资建议
```

## 工作流（设计节奏）

| 频率 | 内容 |
|------|------|
| 每个交易日 17:30 | 先调 akshare 判交易日 → 抓净值 → 60天回测 |
| 每周六 10:00 | 5 轮学习回测 + 出本周投资建议 |
| 每月1日 12:00 | 20 轮深度学习 + 月度综合推荐 |

## 常见修改热点

| 想改什么 | 文件 | 改后 |
|---------|------|------|
| 加/减基金 | `config/funds.yaml` | `scrape` → push |
| 调模型 | `config/default.yaml` → `ai.flash_model/pro_model` | push 即生效 |
| 调回测参数 | `config/default.yaml` → `backtest.*` | push 即生效 |
| 改提示词 | `config/prompt_templates/*.txt` | push 即生效 |
| 加数据源 | `src/data/sources/` | 新类 + 注册到 scraper |
| 改 WIKI | `docs/WIKI.md` | 需同步 push 到 wiki 仓库 |

## Wiki 同步

`docs/WIKI.md` 需手动同步到 GitHub Wiki 仓库：

```bash
git clone https://github.com/Yummy-He/fund-ai.wiki.git .wiki-temp
cp docs/WIKI.md .wiki-temp/Home.md
cd .wiki-temp && git add Home.md && git commit -m "Sync WIKI" && git push
cd .. && rm -rf .wiki-temp
```

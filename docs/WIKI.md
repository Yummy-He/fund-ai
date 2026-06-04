# 基金 AI 分析系统 — 操作手册

> 版本 v0.1.0 | 2026-06-04

## 目录

1. [项目概览](#1-项目概览)
2. [快速开始](#2-快速开始)
3. [CLI 命令详解](#3-cli-命令详解)
4. [配置文件说明](#4-配置文件说明)
5. [基金管理（添加/删除/修改）](#5-基金管理)
6. [交易规则与费率](#6-交易规则与费率)
7. [AI 学习机制详解](#7-ai-学习机制详解)
8. [GitHub Actions 自动化](#8-github-actions-自动化)
9. [常见问题排查](#9-常见问题排查)
10. [开发指南](#10-开发指南)

---

## 1. 项目概览

### 这是什么？

一个 AI 驱动的中国公募基金投资分析系统。核心流程：

```
抓取基金净值数据 → 历史回测模拟(1万元起) → AI逐日决策买卖 → 
积累投资经验 → 多轮回测学习 → 生成投资建议
```

### 核心概念

| 概念 | 说明 |
|------|------|
| **回测** | AI "穿越"到过去某天，用 1 万虚拟资金逐日决策，直到"现在" |
| **经验** | 每次决策 + 后续结果 = 一条经验（存在 `experiences/`） |
| **学习** | 多轮不同时段回测，AI 从经验中总结策略模式 |
| **RAG 检索** | 新决策时自动找相似场景的历史经验做参考 |

### 技术栈

- **数据**: akshare（东方财富底层数据）
- **AI**: DeepSeek V4-Flash (日常决策) + V4-Pro (深度分析)
- **存储**: CSV 文件（可直接在 GitHub 上查看）
- **自动化**: GitHub Actions

---

## 2. 快速开始

### 安装

```powershell
cd H:\基金
pip install -e "."
```

### 配置 API Key

在项目根目录创建 `.env` 文件：

```
DEEPSEEK_API_KEY=sk-你的密钥
```

> 获取密钥: [platform.deepseek.com](https://platform.deepseek.com) → API Keys

### 验证安装

```powershell
python -m src.cli --help
```

---

## 3. CLI 命令详解

### scrape — 抓取基金数据

```powershell
# 根据 config/funds.yaml 抓取所有已启用的基金
python -m src.cli scrape

# 抓取指定基金
python -m src.cli scrape --funds 110011 --funds 000001
```

### backtest — 单次回测

```powershell
# 基本用法: 时间段 + 决策间隔
python -m src.cli backtest --start 2024-01-01 --end 2025-01-01 --interval 10

# 指定初始资金和基金
python -m src.cli backtest \
  --start 2023-06-01 --end 2024-06-01 \
  --funds 001714 --funds 260108 \
  --capital 20000 --interval 5

# 包含基准策略对比（等权买入持有）
python -m src.cli backtest --start 2024-01-01 --end 2025-01-01 --baseline
```

**参数说明：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--start` | 回测开始日期 | 必填 |
| `--end` | 回测结束日期 | 必填 |
| `--funds` | 基金代码（可多选） | 配置前5只 |
| `--capital` | 初始资金 | 10000 |
| `--interval` | 决策间隔（交易日） | 1 |
| `--baseline` | 运行基准对照 | 否 |

**输出：** 终端显示收益/夏普/回撤等指标，同时生成 `reports/backtests/bt_*.md`

### learn — 多轮回测学习

```powershell
# 5轮学习（默认）
python -m src.cli learn --iterations 5

# 20轮深度学习（月度用）
python -m src.cli learn --iterations 20

# 指定基金池 + 固定随机种子
python -m src.cli learn --iterations 10 --funds 001714 --funds 260108 --seed 42
```

**学习过程：**
1. 每轮随机选一个时间段（2020年至今）和基金子集
2. Flash 模型逐日决策买卖
3. Pro 模型总结本轮策略教训
4. 经验存入 `experiences/`
5. 下轮自动检索相似场景经验，决策更聪明

### recommend — 生成投资建议

```powershell
# 标准建议
python -m src.cli recommend

# 输出到指定文件
python -m src.cli recommend --output reports/my_advice.md
```

### report — 生成报告

```powershell
# 每日简报
python -m src.cli report --type daily

# 月度汇总
python -m src.cli report --type monthly
```

---

## 4. 配置文件说明

### config/default.yaml — 主配置

```yaml
ai:
  flash_model: "deepseek-v4-flash"   # 日常决策（快速低价）
  pro_model: "deepseek-v4-pro"       # 深度分析（策略总结/建议）
  temperature: 0.3                    # AI 随机度（越低越保守）
  pro_temperature: 0.2               # Pro 模型温度

backtest:
  initial_capital: 10000.0           # 初始资金
  commission:
    buy_rate: 0.0015                 # 买入费率 0.15%
    sell_rate: 0.0050                # 卖出费率 0.5%
  constraints:
    max_positions: 10                # 最多持仓数
    max_single_position_pct: 0.30    # 单只仓位上限 30%
    min_cash_reserve: 500            # 最低保留现金
    min_trade_amount: 100            # 最低交易金额

learning:
  iterations: 10                     # 学习迭代次数
  backtest_duration_days: 365        # 每次回测时长
  retrieval:
    top_k: 10                        # 每次决策参考几条经验
    always_include_failures: 2       # 始终包含的失败案例数

scraper:
  request_delay: 1.0                 # API 请求间隔
  default_history_days: 1095         # 默认抓取3年数据
```

### config/funds.yaml — 基金池

```yaml
funds:
  - code: "110011"              # 基金代码（天天基金网可查）
    name: "易方达中小盘混合"     # 基金名称（仅用于显示）
    type: "MIXED"               # STOCK/MIXED/BOND/INDEX/ETF/QDII
    enabled: true               # false = 暂时跳过

  - code: "510300"
    name: "华泰柏瑞沪深300ETF"
    type: "INDEX"
    enabled: true
```

---

## 5. 基金管理

### 5.1 添加新基金

**步骤：**

1. 找到基金代码
   - 打开 [fund.eastmoney.com](https://fund.eastmoney.com)
   - 搜索基金名称
   - URL 中的数字即代码，如 `110011`

2. 编辑 `config/funds.yaml`，在 `funds:` 列表下添加：
   ```yaml
     - code: "110011"
       name: "易方达中小盘混合"
       type: "MIXED"
       enabled: true
   ```

3. 抓取数据：
   ```powershell
   python -m src.cli scrape
   ```

4. 验证数据：
   ```powershell
   python -m src.cli backtest --start 2024-06-01 --end 2024-12-01 --interval 15
   ```

5. 提交到 Git：
   ```powershell
   git add config/funds.yaml data/
   git commit -m "添加基金: 易方达中小盘混合 110011"
   git push
   ```

### 5.2 移除基金

编辑 `config/funds.yaml`，将对应基金的 `enabled` 设为 `false` 即可。无需删除数据和代码。

### 5.3 基金类型对照

| type | 含义 | 典型特征 |
|------|------|---------|
| `STOCK` | 股票型 | 仓位 ≥80% 股票 |
| `MIXED` | 混合型 | 股票+债券灵活配置 |
| `BOND` | 债券型 | 主投债券，低波动 |
| `INDEX` | 指数型 | 跟踪某个指数 |
| `ETF` | ETF | 场内交易型开放式 |
| `QDII` | QDII | 投资海外市场 |
| `MONEY` | 货币型 | 类似余额宝 |

### 5.4 建议基金组合

分散配置，不同类型都要有：

```yaml
funds:
  # 1-2 只混合型（主动管理，阿尔法来源）
  - code: "260108"    # 景顺长城新兴成长
  - code: "166002"    # 中欧新蓝筹

  # 1-2 只指数型（市场贝塔）
  - code: "510300"    # 沪深300ETF
  - code: "510050"    # 上证50ETF

  # 1 只债券型（防御配置）
  - code: "000744"    # 稳定收益债券

  # 可选: 行业/主题基金
  - code: "002190"    # 新能源主题
```

---

## 6. 交易规则与费率

### 6.1 当前已考虑的因素（已完整实现）

| 因素 | 实现方式 | 说明 |
|------|---------|------|
| **买入费率** | 固定 0.15% | 主流平台折扣费率（基本一致） |
| **卖出费率** | **动态阶梯式** | 根据 **FIFO 实际持有天数** 查询该基金的真实费率 |
| ├ <7 天 | 1.5% 惩罚 | 每只基金从 akshare 获取实际阶梯 |
| ├ 7-30 天 | 0.5%-0.75% | |
| ├ 30-365 天 | 0.5% | |
| ├ 365-730 天 | 0.25%-0.3% | |
| └ ≥730 天 | 0% | 长期持有免赎回费 |
| 最低手续费 | ¥5 | 单笔最低 |
| 最低交易额 | ¥100 | AI 单笔不低于此 |
| 仓位限制 | 单只 ≤30% | 控制集中度 |
| 申购起点 | **基金实际值** | 从东方财富页面抓取（通常10-100元） |
| 现金保留 | ≥¥500 | 留底资金 |

### 6.2 基金实际费率结构（已自动加载）

运行 `scrape` 时，系统自动通过 `akshare.fund_fee_em()` 抓取每只基金的真实费率并缓存到 `data/funds/fees.json`。回测中 AI 每次卖出时自动按 FIFO 持有天数匹配对应阶梯费率。

**赎回费率示例（各基金略有差异）：**
```
持有 < 7天:   1.50%（惩罚性费率 ⚠ AI 会学到不做短线）
持有 7-30天:  0.50%-0.75%
持有 30-365天: 0.50%
持有 365-730天: 0.25%-0.30%
持有 ≥730天:  0.00%
```

**运作费用（每年从净值中扣除）：**
```
管理费:   1.20%/年（主动基金）
托管费:   0.20%/年
销售服务费: 0.00%/年（A类份额）
```

> ETF 等场内基金没有赎回费率表，使用默认阶梯计算。

### 6.3 尚未考虑的因素（影响评估）

| 因素 | 影响 | 不实现的原因 |
|------|------|-------------|
| 申购限额 | 部分热门基金限额购买 | 动态变化，爬取成本高；1万虚拟资金基本不触发 |
| 大额赎回限制 | 单日赎回上限 | 仅百万级资金涉及 |
| 暂停申购 | 基金临时关闭 | 偶发事件，影响小 |
| ETF 交易佣金 | ETF 另有券商佣金 | 费率极低(万0.5起)，对结果影响可忽略 |
| 分红方式 | 现金分红 vs 红利再投 | 默认现金分红，差异通常 <0.2%/年 |
| 基金规模 | 规模过大影响收益 | 定性因素，不适合量化回测 |

> **结论**: 以上因素对 ¥10,000 虚拟回测的影响合计 <0.3%/年，忽略不影响决策质量。

### 6.4 调整费率配置

编辑 `config/default.yaml`：

```yaml
backtest:
  commission:
    buy_rate: 0.0015    # 改为实际使用的平台费率
    sell_rate: 0.0050   # 可改
    min_commission: 5.0
```

---

## 7. AI 学习机制详解

### 7.1 学习 ≠ 微调模型

AI **不会**更新自己的神经网络权重。学习完全通过上下文实现：

```
第 1 轮回测
  → 200 条决策 + 结果 → 存入经验库
  → Pro 模型总结: "动量买入 + 低波动 = 胜率 65%"

第 2 轮回测  
  → 每次决策前检索相似场景的过去经验
  → 提示词中包含了第1轮总结的策略模式
  → 决策质量优于第1轮

第 5 轮回测
  → 经验库有 ~1000 条经验
  → 覆盖牛市/熊市/震荡市多种场景
  → 决策质量继续提升
```

### 7.2 经验存储结构

```
experiences/
├── index.json                          # 总览: 多少条经验/哪种类型
├── decisions/
│   └── bt_20230101_20240101_decisions.json  # 每条决策详情
└── summaries/
    └── bt_20230101_20240101_summary.json    # AI 策略总结
```

### 7.3 经验检索（7 维相似度）

每次 AI 做决策前，系统用 7 个维度在经验库中找最相似的场景：

| 维度 | 权重 | 说明 |
|------|------|------|
| 基金类型 | 25% | 同类型经验优先 |
| 市场趋势 | 20% | 牛市/熊市/震荡 |
| 净值动量 | 20% | 波动率接近 |
| 组合状态 | 10% | 现金比例接近 |
| 结果质量 | 10% | 盈利经验优先 |
| 时效性 | 10% | 近期经验稍高权重 |
| 多样性 | 5% | 确保包含失败案例 |

---

## 8. GitHub Actions 自动化

### 8.1 三个工作流

| 工作流 | 频率 | 功能 |
|--------|------|------|
| `daily-scrape.yml` | 每个交易日 17:30 | 增量抓取净值 + 自动提交 |
| `weekly-backtest.yml` | 每周六 10:00 | 5轮学习回测 + 更新建议 |
| `monthly-report.yml` | 每月1日 12:00 | 20轮深度学习 + GitHub Issue 报告 |

### 8.2 推送到 GitHub

```powershell
cd H:\基金
git remote add origin https://github.com/Yummy-He/fund-ai.git
git push -u origin main
```

### 8.3 设置 Secret（必须）

1. 打开 `https://github.com/Yummy-He/fund-ai/settings/secrets/actions`
2. 点击 `New repository secret`
3. Name: `DEEPSEEK_API_KEY`
4. Value: `sk-你的密钥`
5. 点击 `Add secret`

### 8.4 手动触发

在 GitHub 仓库页面 → Actions → 选工作流 → `Run workflow`

---

## 9. 常见问题排查

### 9.1 `未设置 DEEPSEEK_API_KEY`

在项目根目录创建 `.env` 文件，内容：
```
DEEPSEEK_API_KEY=sk-你的密钥
```

### 9.2 `未找到基金的净值数据`

先运行 scrape 抓取数据：
```powershell
python -m src.cli scrape
```

### 9.3 akshare 接口报错

akshare 偶尔会因上游接口变动而报错。等待几小时再试通常自动恢复。
也可以在配置中切换数据源方向。

### 9.4 API 调用量太大

- 增大决策间隔: `--interval 10`（每10个交易日决策一次）
- 减少学习轮次: `--iterations 3`
- 减少基金池: 在 `funds.yaml` 中禁用一些基金

### 9.5 Windows 终端显示乱码

不影响实际功能。Markdown 报告中的中文正常显示。可以：
```powershell
chcp 65001  # 切换终端到 UTF-8
```

---

## 10. 开发指南

### 项目结构

```
fund-ai/
├── config/                    # 所有配置集中在这里
│   ├── default.yaml           # 主配置
│   ├── funds.yaml             # 基金池
│   └── prompt_templates/      # AI 提示词模板
├── src/
│   ├── cli.py                 # 命令行入口
│   ├── data/                  # 数据层: 抓取 + 存储
│   ├── engine/                # 回测引擎: 模拟 + AI决策
│   ├── learning/              # 学习层: 经验 + 检索 + 评估
│   ├── report/                # 报告生成
│   └── utils/                 # 工具函数
├── data/                      # 数据文件 (Git 追踪)
│   ├── nav/                   # 每只基金一个 CSV
│   ├── funds/                 # 基金元数据
│   └── index/                 # 基准指数
├── experiences/               # AI 经验库 (Git 追踪)
├── reports/                   # 输出报告 (Git 追踪)
└── .github/workflows/         # CI/CD 自动化
```

### 主要类关系

```
BacktestEngine (回测主循环)
  ├── Portfolio        (资金 + 持仓)
  ├── TimeSimulator    (交易日步进器)
  ├── FundDecisionMaker (AI 决策)
  │     ├── AIClient           (DeepSeek API)
  │     ├── PromptBuilder      (提示词构建)
  │     ├── FundRepository     (净值查询)
  │     └── ExperienceRetriever (经验检索)
  ├── OrderManager     (订单验证执行)
  └── MetricsCalculator (指标计算)
```

### 修改提示词

编辑 `config/prompt_templates/` 下的 `.txt` 文件：
- `decision_prompt.txt` — AI 做买卖决策时看到的内容
- `summary_prompt.txt` — 回测结束后策略总结
- `recommend_prompt.txt` — 生成投资建议

修改后立即生效，无需重启。

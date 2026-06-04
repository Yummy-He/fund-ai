# CLAUDE.md — 基金 AI 分析系统

## 项目概述

这是一个 AI 驱动的中国公募基金投资分析系统。核心思路：让 AI（通过 DeepSeek API）读取历史基金净值数据，反复进行模拟回测（"穿越"到过去某天用1万元开始投资，逐日决策买卖直到"现在"），通过积累大量决策经验，学会分析基金并给出投资建议。

## 架构

```
数据抓取(akshare) → CSV存储 → 回测引擎(逐日AI决策) → 经验积累(RAG检索) → 投资建议报告
```

## 关键设计理念

### 学习 = 上下文积累，不是微调
AI 不会微调权重。而是在每次回测中，每个决策+结果被存为"经验"。下次遇到相似场景时，系统检索相关经验注入提示词。经过10+轮回测，AI积累了数千条经验，决策越来越准。

### 回测粒度 = 交易日
中国大陆每月约20个交易日，基金净值每日晚间更新。回测按 T+1 规则：当天看到的是前一交易日的净值。

### AI 后端 = DeepSeek API（Anthropic格式）
- Base URL: `https://api.deepseek.com/anthropic`
- 模型: deepseek-v4-flash（日常）/ deepseek-v4-pro（高级分析）
- 成本极低: 一次完整回测约 ¥2-10

## 项目结构

```
fund-ai/
├── config/            # YAML 配置文件
│   ├── default.yaml   # 主配置
│   ├── funds.yaml     # 基金池定义
│   └── prompt_templates/  # AI 提示词模板
├── src/               # 源代码
│   ├── cli.py         # CLI 入口（click）
│   ├── data/          # 数据层：抓取+存储
│   ├── engine/        # 回测引擎：模拟+AI决策+指标
│   ├── learning/      # 学习层：经验存储+检索+评估
│   ├── report/        # 报告层：Markdown 生成
│   └── utils/         # 工具：配置+日志+日期
├── data/              # 数据文件（Git追踪）
├── experiences/       # 经验存储（Git追踪）
├── reports/           # 报告输出（Git追踪）
├── tests/             # 测试
└── .github/workflows/ # CI/CD 自动化
```

## 开发指南

### 安装
```bash
pip install -e ".[dev]"
```

### 环境变量
```bash
export DEEPSEEK_API_KEY="sk-xxxx"
```

### 命令
```bash
fund-ai scrape                    # 抓取基金数据
fund-ai backtest --start 2023-01-01 --end 2024-01-01  # 单次回测
fund-ai learn --iterations 10     # 多轮回测学习
fund-ai recommend                 # 生成投资建议
fund-ai report --type monthly     # 生成月度报告
```

### 基金代码规则
- 场内基金（ETF/LOF）：以 5 开头
- 场外基金：以 0 开头（股票/混合/债券型）
- akshare 使用 `fund_open_fund_daily_em` 获取场外基金净值

## 当前状态（2026-06-04）

✅ 项目框架已完成搭建（v0.1.0）
✅ 数据层: 9只基金 ~727条/只 净值 + 沪深300/上证50指数
✅ 回测引擎: 端到端验证通过 — AI 已成功进行真实回测决策
✅ 学习层: 经验存储 / 多因子检索 / 策略评估
✅ 报告层: Markdown 报告生成
✅ CLI: 5个命令全部可用
✅ GitHub Actions: 3个工作流已配置
🔜 下一步: git push → 设置 GitHub Secrets → 运行 learn 学习循环

### 已修复的问题
- Portfolio 参数名 initial_capital vs initial_cash 不一致
- DeepSeek ThinkingBlock 解析失败 → 禁用 thinking + 遍历 content blocks
- 指数数据读取 → 直接读 data/index/ 而非 FundRepository
- Windows 终端 emoji 编码 → 替换为纯文本 + UTF-8 强制
- .env 文件支持 → 优先于环境变量加载

## 注意事项

- 基金代码中可能包含特殊前缀，akshare 数据接口返回的代码格式需要适配
- 中国大陆交易日历：需考虑春节、国庆等长假休市
- CSV 文件大小：每只基金3年数据约几千行，10只基金总计可控
- DeepSeek API 不支持 `cache_control` 和 extended thinking

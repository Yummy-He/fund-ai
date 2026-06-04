# 基金 AI 分析系统

AI 驱动的中国公募基金投资分析与学习系统。

## 工作原理

1. **数据抓取**: 从 akshare/东方财富获取基金历史净值数据
2. **模拟投资**: AI "穿越"到过去，用 1 万元虚拟资金逐日决策买卖
3. **经验学习**: 每次决策 + 结果被存储，下次相似场景时检索参考
4. **持续改进**: 经过多轮不同时间段的回测，AI 积累数千条经验
5. **投资建议**: 基于学到的经验生成具体的基金投资建议

## 快速开始

### 安装

```bash
pip install -e ".[dev]"
```

### 配置 API Key

```bash
# Linux/Mac
export DEEPSEEK_API_KEY="sk-xxxx"

# Windows PowerShell
$env:DEEPSEEK_API_KEY="sk-xxxx"
```

### 使用

```bash
# 抓取基金数据
fund-ai scrape

# 单次回测
fund-ai backtest --start 2023-01-01 --end 2024-01-01

# 多轮回测学习（10轮）
fund-ai learn --iterations 10

# 生成投资建议
fund-ai recommend

# 生成月度报告
fund-ai report --type monthly
```

## 技术栈

- **语言**: Python 3.11+
- **数据源**: akshare（主）、东方财富（备用）
- **AI 后端**: DeepSeek API（Anthropic 兼容格式）
- **存储**: CSV 文件（数据）+ JSON 文件（经验）
- **自动化**: GitHub Actions（每日抓取/每周学习/每月报告）

## 项目结构

```
fund-ai/
├── config/              # 配置文件
├── src/                 # 源代码
│   ├── cli.py           # CLI 入口
│   ├── data/            # 数据抓取与存储
│   ├── engine/          # 回测引擎 + AI 决策
│   ├── learning/        # 经验学习层
│   ├── report/          # 报告生成
│   └── utils/           # 工具函数
├── data/                # 基金数据 (Git)
├── experiences/         # 经验记录 (Git)
├── reports/             # 报告输出 (Git)
├── tests/               # 测试
└── .github/workflows/   # CI/CD
```

## 许可证

MIT

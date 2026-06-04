# AI_MUST_READ.md — 基金AI系统核心信息

> ⚠️ **给未来 AI 的关键信息。开始操作前请先读 CLAUDE.md 和本文件。**

## 项目本质

**RAG 增强的 LLM 基金投资决策系统**。不是经典的回测框架：

1. LLM 扮演基金经理，阅读持仓+市场数据，决定买/卖/持
2. 回测是"健身房"：几百次回测让 AI 积累经验
3. 经验是"老师"：每次决策结果存入经验库，下次相似场景检索参考
4. 不微调模型：学习完全通过检索增强提示词实现
5. 双层模型：Flash（日常决策）+ Pro（策略总结/建议）

## 核心文件职责

| 文件 | 职责 | 重要度 |
|------|------|--------|
| `config/default.yaml` | 所有可调参数 | ⭐⭐⭐ |
| `config/funds.yaml` | 基金池（10只） | ⭐⭐⭐ |
| `src/engine/backtest.py` | 回测主循环 + 三种策略 | ⭐⭐⭐ |
| `src/engine/decision.py` | AI 决策编排 | ⭐⭐⭐ |
| `src/engine/ai_client.py` | DeepSeek API 封装 | ⭐⭐⭐ |
| `src/learning/retriever.py` | 经验检索（7维评分） | ⭐⭐⭐ |
| `src/data/fees.py` | 每只基金独立费率 | ⭐⭐ |
| `src/cli.py` | CLI 入口 | ⭐⭐ |
| `data/nav/*.csv` | 净值历史（只增不改） | ⭐⭐⭐ |
| `data/funds/fees.json` | 费率缓存 | ⭐⭐ |
| `experiences/` | AI 经验库（只增不改） | ⭐⭐⭐ |
| `reports/` | 报告输出 | ⭐⭐ |

## 当前环境

- **AI后端**: DeepSeek API, Anthropic 兼容格式
- **日常模型**: deepseek-v4-flash（回测决策）
- **深度模型**: deepseek-v4-pro（策略总结/投资建议）
- **数据源**: akshare v1.18+, fund_open_fund_info_em
- **费率来源**: akshare fund_fee_em + 东方财富页面
- **存储**: CSV（数据）+ JSON（经验/费率）
- **自动化**: GitHub Actions（每日/每周/每月）
- **平台**: Windows 11 + PowerShell
- **仓库**: https://github.com/Yummy-He/fund-ai
- **Wiki**: https://github.com/Yummy-He/fund-ai/wiki

## 关键约束

1. 数据只增：`data/nav/*.csv` 只追加
2. 经验只增：`experiences/` 只追加
3. 净值 T+1：当天决策基于 T-1 净值
4. **费率无最低收费**：公募基金纯百分比计费，无每笔 ¥5 这种规则
5. DeepSeek 不支持 cache_control 和 thinking
6. akshare 接口列名可能变化，需兼容处理
7. ETF 和场外基金使用不同的 akshare 接口
8. 交易日通过 `akshare.tool_trade_date_hist_sina()` 判断

## 费率系统说明

每只基金在 `data/funds/fees.json` 中有独立费率：
- **运作费用**：管理费/托管费/销售服务费（年化）
- **赎回阶梯**：按 FIFO 持有天数真实阶梯（含 <7 天 1.5% 惩罚）
- **申购阶梯**：按购买金额分档
- **交易门槛**：申购起点/定投起点/首次购买/追加购买/日累计限额/持仓上限
- **赎回规则**：最小赎回份额/最低保留份额/确认日(T+1或T+2)
- **注意**：订阅费 tier 的爬虫解析偶有噪音（如 040046），不影响主要费率

## 常见陷阱

- ⚠️ 前视偏差：不能使用"未来"数据做决策
- ⚠️ 生存偏差：只回测现存基金会高估
- ⚠️ LLM 可能从噪声中"发现"不存在的规律
- ⚠️ 上下文长度：经验太多可能撑爆 prompt
- ⚠️ 基金分红/拆分会影响净值，需关注累计净值

## 如何扩展

| 目标 | 方法 |
|------|------|
| 加新基金 | 编辑 `config/funds.yaml`，运行 `scrape`，push |
| 调学习参数 | 编辑 `config/default.yaml` → `learning.*`，push |
| 改提示词 | 编辑 `config/prompt_templates/*.txt`，push |
| 换 AI 模型 | 修改 `ai.flash_model` / `ai.pro_model`，push |
| 加新数据源 | `src/data/sources/` 新增类，注册到 `scraper.py` |
| 加新指标 | 在 `src/engine/metrics.py` 添加 |

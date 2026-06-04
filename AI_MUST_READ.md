# AI_MUST_READ.md — 基金AI系统核心信息

> ⚠️ **重要**：这是给未来 AI（包括 Claude Code 和其他工具）的关键信息。
> 在开始任何操作前，**请先阅读此文件和 CLAUDE.md**。

## 项目本质

这是一个 **RAG 增强的 LLM 基金投资决策系统**。不是经典的回测框架，而是：

1. **LLM 扮演基金经理**：AI 阅读当前持仓+市场数据,决定买/卖/持
2. **回测是健身房**：通过几百次回测让 AI 积累"肌肉记忆"
3. **经验是老师**：每次决策的结果存入经验库，下次相似场景检索参考
4. **不微调**：学习完全通过检索增强提示词实现

## 文件和目录职责

| 路径 | 职责 | 重要程度 |
|------|------|---------|
| `config/default.yaml` | 所有可调参数 | ⭐⭐⭐ |
| `config/funds.yaml` | 关注的基金列表 | ⭐⭐⭐ |
| `src/engine/backtest.py` | 回测主循环（核心） | ⭐⭐⭐ |
| `src/engine/decision.py` | AI 决策编排 | ⭐⭐⭐ |
| `src/engine/prompt.py` | 提示词构建（核心） | ⭐⭐⭐ |
| `src/learning/retriever.py` | 经验相似度检索（核心） | ⭐⭐⭐ |
| `src/learning/experience.py` | 经验存储结构 | ⭐⭐ |
| `src/data/scraper.py` | 数据抓取编排 | ⭐⭐ |
| `src/cli.py` | CLI 命令入口 | ⭐⭐ |
| `data/nav/*.csv` | 净值历史（只增不改） | ⭐⭐⭐ |
| `experiences/decisions/` | 所有决策记录（只增不改） | ⭐⭐⭐ |
| `experiences/index.json` | 经验索引 | ⭐⭐ |
| `reports/recommendations/` | 投资建议（滚动更新） | ⭐⭐ |

## 关键约束

1. **数据是只增的**：`data/nav/*.csv` 只追加新日期，不覆盖已有数据
2. **经验是只增的**：`experiences/decisions/*.json` 只追加，不删除
3. **净值=T+1**：当天决策基于今天的净值（实际是昨天收盘的），基金净值每日晚间更新
4. **动态费率**：买入 0.15%，卖出按 FIFO 持有天数阶梯计算（<7天 1.5% 惩罚 → ≥730天 0%），费率来自各基金实际数据
5. **DeepSeek 限制**：不支持 cache_control 和 thinking，但支持 system prompt 和 JSON output
6. **交易日历**：中国大陆市场，非周六日+非节假日，可用 akshare 获取交易日历
7. **akshare 版本**: 1.18.64+ — `fund_open_fund_info_em(symbol, indicator, period)` 获取历史净值
8. **当前数据**: 已抓取 9 只配置基金 ~727 条/只（2023-06 ~ 2026-06）

## 常见陷阱

### 数据层面
- ⚠️ akshare 接口返回的列名可能变化，需要做兼容处理
- ⚠️ ETF 和场外基金使用不同的 akshare 接口
- ⚠️ 基金分红/拆分会影响净值计算，需要检查累计净值(acc_nav)
- ⚠️ 新基金可能没有足够长的历史数据

### 回测层面
- ⚠️ 前视偏差：不能使用"未来"数据做决策
- ⚠️ 生存偏差：只回测当前存活的基金会高估收益
- ⚠️ 交易成本：忘记算手续费会显著高估
- ⚠️ 流动性：小规模基金会限制大额交易

### AI 决策层面
- ⚠️ LLM 可能过度自信：低 temperature 也不完全保证决策一致
- ⚠️ 最近偏好：AI 可能过于关注最近的经验而忽略早期教训
- ⚠️ 市场规律：AI 可能从噪声中"发现"不存在的规律
- ⚠️ 上下文长度：经验太多可能撑爆 prompt

## 如何扩展

1. **加新基金**：编辑 `config/funds.yaml`，运行 `fund-ai scrape`
2. **调学习参数**：编辑 `config/default.yaml` 的 `learning` 段
3. **改提示词**：编辑 `config/prompt_templates/` 下的模板文件
4. **换 AI 模型**：修改 `ai.model` 配置项
5. **加新数据源**：在 `src/data/sources/` 下新增类，注册到 `scraper.py`
6. **加新指标**：在 `src/engine/metrics.py` 中添加

## 当前状态 (2026-06-04)

- ✅ 项目框架完成 (v0.1.0) — 所有核心模块已实现
- ✅ 数据层: scraper + akshare + eastmoney fallback + CSV store
- ✅ 回测引擎: portfolio + orders + simulator + metrics + AI client + decision + backtest loop
- ✅ 学习层: experience store + multi-factor retriever + strategy evaluator
- ✅ 报告层: Markdown 报告 + 回测/学习/建议/每日简报
- ✅ CLI: scrape / backtest / learn / recommend / report
- ✅ GitHub Actions: daily-scrape / weekly-backtest / monthly-report
- 🔜 下一步: 安装依赖 → 抓取真实数据 → 执行回测

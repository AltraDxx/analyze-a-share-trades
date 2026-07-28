# A 股短中波段决策 Skill

一个面向 Codex 的 A 股交易分析 Skill，用于沪深主板普通 A 股约 2—22 个交易日的短中波段决策支持。

它可以辅助完成：

- 当前市场、资金、情绪和主线判断；
- 沪深主板候选股票筛选；
- 指定股票是否值得买的分析；
- 持仓的持有、减仓或卖出判断；
- 公告、业绩、政策和其他事件的交易影响分析；
- 日线、分时、量价、筹码与风险管理分析。

本项目只提供分析与决策支持，不自动下单，也不承诺收益。

## 项目结构

```text
.
├── .agents/skills/analyze-a-share-trades/
│   ├── SKILL.md
│   ├── agents/
│   ├── references/
│   └── scripts/
├── .gitignore
└── README.md
```

## 安装

克隆仓库并进入项目：

```bash
git clone https://github.com/AltraDxx/analyze-a-share-trades.git
cd analyze-a-share-trades
```

在项目内创建 Python 虚拟环境并安装辅助脚本依赖：

```bash
python3 -m venv .venv
./.venv/bin/pip install -r .agents/skills/analyze-a-share-trades/scripts/requirements.txt
```

使用 Codex 打开此仓库。项目内的 Skill 位于：

```text
.agents/skills/analyze-a-share-trades
```

## 必须自行配置凭证

仓库不包含任何 API Key、Token、私有 MCP 配置或已经初始化的连接信息。每位用户必须在自己的设备上配置凭证。

### 方式一：环境变量

辅助脚本优先读取以下环境变量：

```bash
export TUSHARE_TOKEN="YOUR_TUSHARE_TOKEN"
export DEEPSEEK_API_KEY="YOUR_DEEPSEEK_API_KEY"
```

`DEEPSEEK_API_KEY` 只供可选的语义分析脚本使用；不使用该脚本时可以不配置。

### 方式二：本地 `my_api_key.md`

也可以在仓库根目录自行创建 `my_api_key.md`：

```markdown
TUSHARE_TOKEN: YOUR_TUSHARE_TOKEN
DEEPSEEK_API_KEY: YOUR_DEEPSEEK_API_KEY
```

`my_api_key.md` 已加入 `.gitignore`，不得提交或分享。不要把真实凭证填写进 README、Skill 文件、日志、截图或 issue。

## 初始化远程 Tushare MCP

这个 Skill 使用的是远程 Streamable HTTP MCP，不是在仓库中启动的本地 MCP 服务。每位用户需要在自己的 Codex 环境中完成一次初始化。

为避免把真实 Token 直接写入 Shell 历史，可以先静默读取，再注册远程 MCP：

```bash
read -s "TUSHARE_TOKEN?Tushare Token: "
echo
codex mcp add tushare --url "https://api.tushare.pro/mcp/?token=${TUSHARE_TOKEN}"
unset TUSHARE_TOKEN
```

检查是否已注册：

```bash
codex mcp get tushare
```

初始化后请重启 Codex，或新建一个任务，让新的 MCP 配置生效。

初始化结果由 Codex 保存在用户本机配置中，通常位于 `~/.codex/config.toml`。该文件中的 MCP URL 会包含真实 Tushare Token：

- 不要复制到本仓库；
- 不要创建或提交项目级 `.codex/config.toml`；
- 不要把 `codex mcp get tushare --json` 的完整输出粘贴到公开位置；
- Token 泄露后应立即在 Tushare 侧轮换。

如果已存在同名 MCP 配置，请先自行确认旧配置是否仍需保留，再决定是否移除并重新初始化。

## 使用

在 Codex 中提出相关问题，例如：

```text
使用 $analyze-a-share-trades 分析当前沪深主板有哪些短中波段机会。
```

```text
使用 $analyze-a-share-trades 分析我持有的股票现在应该持有、减仓还是卖出。
```

正式分析会以北京时间记录 `analysis_as_of`，重新核验当前市场与事件，不把历史对话中的时效性结论直接当作当前事实。

## 离线自检

```bash
./.venv/bin/python .agents/skills/analyze-a-share-trades/scripts/self_test.py
```

自检使用模拟凭证，不需要读取真实 Token。

## 数据与风险说明

- 结构化行情主要来自 AkShare，必要时使用 Tushare 兜底；
- 公告和事件事实应以交易所、上市公司及其他权威来源为准；
- 数据缺失、延迟、接口变更和市场极端波动都可能影响分析；
- 本项目不构成任何收益保证或自动交易授权，实际交易责任由使用者自行承担。

---
name: analyze-a-share-trades
description: Analyze current Shanghai and Shenzhen main-board A-share swing trades over roughly 2 to 22 trading days. Use when the user asks which eligible stocks are worth buying, whether a named stock is buyable, whether a holding should be held, reduced, or sold, what the current market or main line is, how daily or intraday price-volume and dominant-fund behavior should be interpreted, how long a proposed trade may be held, or how an announcement, earnings preview, policy, or other event changes the current trade conclusion. Exclude ChiNext, STAR Market, ST names, halted names, and materially illiquid stocks; produce evidence-linked, time-stamped, conditional decisions rather than automatic orders.
---

# A股短中波段决策

## 目标

为沪深主板普通 A 股提供可执行的短中波段决策支持，回答：

1. 当前有哪些股票值得买，以及每只股票为什么值得、采用什么买卖策略。
2. 某只股票当前是否值得买。
3. 某只持仓现在应继续持有、减仓还是卖出。
4. 公告、业绩预告、政策或其他事件如何改变当前交易分析和最终动作。

不要自动下单，不承诺收益。允许结论为没有值得买的股票、等待、持有、减仓或卖出。

## 固定约束

- 只推荐沪深主板普通 A 股；排除创业板、科创板、ST、退市整理、停牌和明显缺乏流动性的股票。不可推荐的股票仍可作为主线、产业或情绪观察锚。
- 默认普通现金账户，不建议做空、期权、融资或用户未声明可交易的工具。
- 交易窗口以约 2 至 22 个交易日为主，不用长期估值修复为短中波段交易续命。
- 30% 是组合净值灾难回撤上限，不是单股止损幅度或可接受常态。
- 每次正式分析必须记录北京时间 `analysis_as_of`。先前对话中的“当前主线”“现在可以买”“主力正在吸筹”等时效性结论只作历史语境，不得直接当作本次当前事实；按新的 `analysis_as_of` 重新核验可能变化的市场、板块、事件和个股状态。
- 不计算两次问询相隔的交易日，不维护旧策略是否到期、旧触发状态或新旧策略编号。回答中也不要把连续问询组织成“原策略失效/恢复/到期 → 新策略”的生命周期叙事；直接依据本次基准给出当前判断和当前动作。
- D1 的用户表达保持不变：提问日是交易日时 D1 为当日；非交易日时 D1 为其后的首个交易日。收盘后最早只能在下一交易日执行时写 D2，不重新编号 D1。
- D1 只定义本次未来行动、持有和复核窗口，不是历史数据截断点。使用 `analysis_as_of` 之前全部相关且可得的长、中、短周期和历史事件数据。
- 已有持仓结合成本、仓位、原始理由和完整历史判断未来；不计算、不询问也不输出已持有天数。
- 持有与复核安排只使用 `D1—Dn`、`D2`、`D3` 等相对交易日标签，不向用户换算未来自然日期。
- 不输出主观指定的胜率、上涨概率、赔率百分比或固定综合分。风险收益只作定性和可执行性判断。
- 不为完成任务强行推荐、强行排名或强行选出首选。买入筛选可以得到零只、一只或多只值得买的股票。

## 构造最小证据计划

先识别用户要做的决策、分析时点、交易窗口和已有资料，再决定需要哪些证据、参考文件和工具：

- 只补充缺失后可能改变结论的事实；已有且时效、口径合格的数据不重复抓取。
- 先形成最小证据集；遇到关键缺失、冲突或低置信度时再扩展来源和分析模块。
- 同一事实默认使用一个合格来源；关键事实、来源冲突或质量存疑时再交叉核验。
- 全市场选股必须沿“市场 → 资金与情绪 → 主线/板块 → 产业环节 → 公司角色 → 硬过滤”生成少量待深析候选，不得让大模型逐只自由扫描数千只股票。
- 数据完整度只决定能否正式下结论以及结论强度，不作为候选股票的固定排序优先级。
- 内部检查与结论相冲突的证据，防止确认偏误；最终答复只展示真正影响动作的冲突和风险，不机械输出“最大反证”等栏目。

按实际需要读取参考文件：

- 涉及数据、时效、来源、特殊交易时段参与者或模型边界时，读取 [data-and-models.md](references/data-and-models.md)。
- 市场环境会实质影响结论时，读取 [market-regime.md](references/market-regime.md)。
- 需要识别主线、产业环节、龙头角色或构造候选池时，读取 [mainline-industry-map.md](references/mainline-industry-map.md)。
- 用户提供公告、业绩预告、政策或其他事件，或事件可能改变结论时，读取 [event-evidence.md](references/event-evidence.md)。
- 需要为候选或持仓选择交易策略时，读取 [strategy-playbooks.md](references/strategy-playbooks.md)。
- 当日分时、竞价、量价、人性与筹码博弈会实质影响结论时，读取 [intraday-game.md](references/intraday-game.md)。
- 输出买卖、仓位、涨跌停尝试或组合判断时，读取 [risk-and-portfolio.md](references/risk-and-portfolio.md)。
- 形成最终答复时，读取 [output-contract.md](references/output-contract.md)，只输出本次决策真正需要的部分。

## 核心工作流

以下模块按决策需要组合。全市场选股必须执行步骤 1—6、8—11；指定股票和持仓分析可跳过无关的全市场候选生成，但不得跳过时间、关键事实和最终决策。

### 1. 锁定本次分析基准

记录：

- `analysis_as_of`；
- 盘前、竞价、盘中、收盘后或非交易日状态；
- 关键行情和事件各自的截止时点；
- 已发生事实、市场预期、模型推断和数据缺失。

禁止使用 `analysis_as_of` 之后的数据。发现先前对话结论时，保留用户偏好、成本和原始理由等稳定信息，重新核验所有时效性判断；不计算两次问询相隔天数。

### 2. 获取事实并检查完整度

按照 [data-and-models.md](references/data-and-models.md) 动态选择 Tushare、AkShare、交易所与公司公告、权威网页或用户截图：

- 结构化数据负责行情和计算；公告原文负责事件事实；截图补足缺失的可见盘面。
- Agent 不读取任何密钥文件。本地脚本在进程内按既定规则解析凭证，且不得打印凭证。
- 分钟或竞价数据不可得但会显著改变即时动作时，先给日线级暂定判断，再精确请求缺少的截图。
- 请求资料前盘点已有信息，不重复索要合格资料。

### 3. 执行股票池硬过滤

先使用 [compute_liquidity_median.py](scripts/compute_liquidity_median.py) 和 [filter_universe.py](scripts/filter_universe.py) 或等价规则确认：

- 沪深主板普通 A 股；
- 非 ST、非退市整理、非停牌；
- 最近 20 个已完成交易日流动性达到门槛且记录充分。

过滤在主题映射、候选生成和推荐之前完成。资格或流动性数据不足时只能列为待补证线索，不能写成值得买。

### 4. 判断市场、资金、情绪和外围

按照 [market-regime.md](references/market-regime.md) 综合判断：

- 指数结构与风格；
- 成交、市场宽度、涨跌停、炸板和赚钱效应；
- 可验证资金与杠杆数据、数据商资金代理及其价格响应；
- 宏观流动性、政策、外围市场和产业传导；
- 风险偏好、一致、分歧、恐慌、踏空和兑现等人性状态。

不要机械加权或用单一指标定义牛熊。市场状态限定可用策略和风险环境，不直接决定具体股票。

### 5. 识别主线、产业链和龙头梯队

按照 [mainline-industry-map.md](references/mainline-industry-map.md) 使用滚动 5/10/20 日证据识别主线、次主线、轮动、退潮或无明确主线，并建立：

`叙事 → 产业簇 → 细分环节 → 瓶颈/利润池 → 公司暴露`

区分产业龙头、盘面龙头、情绪龙头、趋势核心/中军、阶段性新核心、弹性、补涨和弱映射。龙头是动态而非布尔标签，也不等于当前一定适合买入。

### 6. 生成少量待深析候选

只从已经确认的关注方向、产业环节、独立公司事件和允许股票池生成候选：

1. 合并硬过滤结果与已核验的方向—公司关系。
2. 使用 [build_candidate_pool.py](scripts/build_candidate_pool.py) 去重、聚合多重公司角色并执行容量保护；脚本不打分、不排名、不截断 Top N，也不生成买卖结论。
3. 候选过多时回到上游继续按主线阶段、细分环节和真实公司角色收窄，不用任意分数砍掉股票。
4. 创业板、科创板或其他不可推荐的龙头只作观察锚；主板没有真实映射时允许输出无候选。

### 7. 将事件作为综合分析维度

用户提供公告或当前存在重大事件时，按照 [event-evidence.md](references/event-evidence.md) 判断事实变化、相对预期、持续性、是否已定价以及对产业和公司角色的影响。

- 不把事件另立为与交易分析割裂的任务或结论。
- 先定义历史事件筛选规则，再纳入 `analysis_as_of` 之前所有符合条件的样本；不得因结果不合意而挑选或删除样本。
- 事件结果必须回到市场、板块、龙头、量价、主力/人性博弈和风险中，改变或保持最终的买入、等待、持有、减仓或卖出判断。

### 8. 对候选逐只综合分析

对每只候选综合检查：

- 市场和主线阶段；
- 产业暴露、公司基本面、事件与预期差；
- 龙头/核心/中军/跟随角色及其动态变化；
- 资金注意力、日周线、成交、筹码和当日分时；
- 历史主导资金行为与当前人性博弈；
- 风险、持有窗口和执行条件。

不设“证据完整度优先”等字典序，不设固定权重或综合分。先形成每只股票自己的完整交易逻辑，再判断它是否值得买。除非用户明确要求比较唯一标的，否则不强制候选之间排名。

### 9. 为每只股票锁定一个主策略

按照 [strategy-playbooks.md](references/strategy-playbooks.md)：

1. 由市场状态限定当前可用的策略集合。
2. 为每只值得买的候选或持仓分别选择一个主策略。
3. 同一批候选允许采用不同策略；同一只股票不得把多个策略的宽松条件拼接起来。
4. 入场后不得因下跌把右侧交易临时改写成左侧长期持有。

### 10. 用历史、日线和分时检验主力与人性博弈

按照 [intraday-game.md](references/intraday-game.md)：

- 建立长、中、短周期和预先定义的全量可比样本；
- 比较个股、细分环节、板块、龙头/中军和指数；
- 将试盘、洗盘、吸筹、诱多、诱空、派发和普通波动作为内部竞争性解释；
- “主力风格”只指公开量价中重复出现的主导资金与筹码行为，不代表识别真实账户或确定意图；
- 将一致、分歧、恐慌、追涨、踏空、获利兑现和套牢盘行为纳入判断；
- 先核验盘后或特殊时段的交易机制与参与者范围，再解释其信息含量；不得仅凭时段把成交归因于散户或主力。

对 2—5 日策略提高分时权重；对 10—22 日策略主要用分时优化执行。内部保留替代解释，最终只输出影响交易动作的博弈结论。

### 11. 评估风险、D1 动作与实际执行

按照 [risk-and-portfolio.md](references/risk-and-portfolio.md) 检查 T+1、停牌、组合暴露、回撤、入场风险和硬性限制。使用可靠交易日历确认 D1；持有窗口来源于每只股票的策略与催化节奏。

涨跌停必须遵循“先决策、后尝试”的顺序：

1. 把涨跌停作为强弱、拥挤、事件定价和风险证据，但先假设能够成交，判断 D1 应买、卖、持有还是不交易。
2. 若 D1 应交易，给出尝试交易的策略；不保证成交，也不把提交委托当作成交。
3. 若实际成交，执行成交后的策略；若未成交或只部分成交，按用户实际仓位进入 D2 条件预案。
4. 若 D1 本来不应交易，或经核验 D1 已无用户可使用的适用交易时段，直接给 D2 开始的条件预案。常规连续竞价收盘不自动等于 D1 不可执行；先检查盘后固定价格等仍开放机制。
5. 不为涨跌停预设可交易性状态分支。停牌、普通 A 股当日买入后不能卖出等确定性规则仍作为硬限制。

### 12. 输出明确而轻量的结论

严格遵循 [output-contract.md](references/output-contract.md)：

- 置顶写明 `analysis_as_of`、D1 和动作。
- 全市场筛选列出所有经深析后值得买或条件值得买的股票，也允许明确“当前没有值得买的股票”。
- 每只股票写名称、代码、是否值得买、主策略、值在哪里、关键理由以及买入/持有/卖出安排。
- 不强制首选/备选、名次、最大反证、固定三情景或伪精确概率。
- 事件结论、龙头地位、主力与人性博弈融入每只股票的交易理由和动作，不另起割裂结论。
- 涨跌停先写假设可成交时的 D1 动作，再写成交后的策略和今日未执行时的 D2 预案。
- 把内容分成事实、综合判断和行动；只披露真正影响决策的数据缺口与风险。

## 辅助脚本

从仓库根目录运行时优先使用 `./.venv/bin/python`；环境不存在或依赖缺失时只在仓库内创建 `.venv`，不要修改全局 Python 环境。

- [fetch_market_data.py](scripts/fetch_market_data.py)：按需获取 AkShare 数据，或通过 Tushare Python API 执行兜底抓取并保留来源和时点。
- [compute_liquidity_median.py](scripts/compute_liquidity_median.py)：计算最近 20 个已完成交易日成交额中位数。
- [filter_universe.py](scripts/filter_universe.py)：执行沪深主板、ST、停牌和流动性硬过滤。
- [scan_mainlines.py](scripts/scan_mainlines.py)：计算板块/行业 5/10/20 日描述性强度；其顺序只是强度证据，不直接命名主线或决定股票。
- [build_candidate_pool.py](scripts/build_candidate_pool.py)：合并硬过滤结果与已选方向—公司关系，稳定生成无评分、无排名的待深析候选池。
- [derive_price_features.py](scripts/derive_price_features.py)：计算日线或分钟基础事实特征，不生成买卖结论。
- [trade_window.py](scripts/trade_window.py)：按可靠交易日历生成 D1—Dn 相对标签，不输出未来自然日期。
- [deepseek_semantic.py](scripts/deepseek_semantic.py)：可选的文本去重、事件抽取、产业映射和内部反方审查；不得作为事实源或决策器。
- [self_test.py](scripts/self_test.py)：离线检查数据、股票池、候选池、主线、D1、量价、事件/输出契约和模型边界。

脚本失败时先诊断数据格式、字段、权限和时效，再降级；不得把失败或缺失解释为中性信号。

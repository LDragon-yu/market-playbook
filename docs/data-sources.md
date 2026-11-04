# 数据源申请与配置

## 1. Tushare Pro（必选）

申请地址：[Tushare Pro](https://tushare.pro/)。登录后复制个人 Token，写入：

```dotenv
TUSHARE_TOKEN=你的个人Token
TUSHARE_POINTS_TIER=15000
```

本项目调用的主要接口如下：

| 接口 | 用途 | 本项目降级策略 |
|---|---|---|
| `trade_cal` | 最近/下一交易日 | 无法获取时任务报错，不猜测节假日 |
| `daily` | 全市场日线 | 核心必需 |
| `daily_basic` | 换手、市值 | 缺失时少一部分个股评分 |
| `stk_limit` | 当日涨跌停价 | 用于从日线校验涨跌停 |
| `limit_list_ths` | 涨停池、炸板、封板时间、原因 | 缺失时从日线推导，但板型信息减少 |
| `limit_step` | 连板高度 | 缺失时尝试从涨停标签提取 |
| `limit_cpt_list` | 强势概念板块 | 缺失时使用板块资金流排序 |
| `moneyflow_cnt_ths` | 概念资金流 | 缺失时仍可依据强势板块表生成梯队 |
| `index_daily` | 主要指数 | 缺失时市场评分中的指数权重退化 |

你提到的 **15000 积分在功能上足以支撑此 MVP**：这些高级涨停/连板接口当前文档门槛通常不高于 8000 积分；实际权限仍应在你自己的 Tushare 账户中逐个调用确认。接口权限、积分规则和更新时间可能调整，上线前以官方接口页为准。

不建议购买共享 Token、账号或来历不明的“积分权限”。Tushare 服务条款限制账号/数据转售与共享；共享凭据还存在失效、限流、数据泄露和服务随时中断风险。若购买的是在你本人账户内合法增加积分的服务，应先向官方核实。

## 2. Alpha Vantage（可选、盘前外围）

在 [Alpha Vantage](https://www.alphavantage.co/support/#api-key) 申请 API Key：

```dotenv
ALPHA_VANTAGE_API_KEY=你的Key
ALPHA_VANTAGE_SYMBOLS=SPY,QQQ,DIA,SOXX,FXI,KWEB
```

免费额度适合每天一次的 MVP。默认观察美股宽基、半导体和中概 ETF；它们是风险背景，不作为 A 股个股的直接买入信号。免费接口受频率限制，任务已限制并发；若没有配置，预案会明确提示人工核对而不是伪造数据。

## 3. 截图 OCR（两种方案）

### 百度智能云 OCR

在[百度智能云文字识别](https://cloud.baidu.com/product/ocr)创建应用，配置：

```dotenv
OCR_MODE=baidu
BAIDU_OCR_API_KEY=你的APIKey
BAIDU_OCR_SECRET_KEY=你的SecretKey
```

适合长截图纯文字识别。OCR 后再由规则或大模型提取板块、代码、催化和风险。

### 视觉大模型

如果所选 OpenAI-compatible 服务及模型确实支持图片输入：

```dotenv
OCR_MODE=vision
LLM_ENABLED=true
LLM_BASE_URL=https://服务商地址/v1
LLM_API_KEY=你的Key
LLM_MODEL=支持视觉的模型名
```

注意：DeepSeek 的文本模型配置不代表必然支持图片；请按服务商模型说明选择。若只配置文本模型，推荐 `OCR_MODE=baidu`。

## 4. 文本大模型（可选）

```dotenv
LLM_ENABLED=true
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=你的Key
LLM_MODEL=deepseek-chat
```

模型负责把公告、自媒体文本拆成结构化情报，并在盘中重要事件和个股问答中做有证据约束的解释。未配置模型或模型故障时自动使用规则模式。模型动作有白名单，不能覆盖行情时效、T+1、市场结构或空仓门槛。

当前代码使用 OpenAI-compatible Chat Completions JSON 接口。若服务商不支持 `response_format: {"type":"json_object"}`，需要选择其 JSON 模式模型或调整适配器。

## 5. AKShare（可选、板块跷跷板）

```powershell
python -m pip install -r requirements-optional.txt
```

```dotenv
AKSHARE_ENABLED=true
SEESAW_PAIRS=半导体:医药;高股息:科技
```

仅靠每日收盘数据可以识别中期负相关和“当日一强一弱”，但不能确认盘中谁先启动、谁后回落。要验证板块跷跷板的先后顺序，需要板块 5 分钟数据；通常不需要 Level-2，也不需要逐笔成交。本项目预留了 AKShare 东方财富概念/行业 5 分钟接口，免费源不承诺稳定性，失败时不影响核心盘后报告。

## 6. 数据合规与保留

- Token 只放在服务器 `.env`，不要提交版本库或发到前端。
- 截图应确认你有权保存和处理；自媒体结论应保留来源和时间。
- 购买或部署商业行情前核对展示、存储和再分发许可；个人使用权限不当然等于可公开提供 Web 服务。

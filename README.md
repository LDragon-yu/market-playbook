# 见势：A股盘后复盘与盘前预案

这是一个面向短线交易研究的 MVP。它在收盘后把市场强弱、板块梯队、连板梯队和个股辨识度结构化，于下一交易日盘前合并持仓、外围市场、消息催化及自媒体情报，生成带触发条件、失效条件和空仓门槛的冻结预案；盘中只对小规模高优先级股票池提供快照哨兵与受约束决策辅助。

> 本项目用于研究和计划管理，不构成投资建议。演示股票名称与行情均为虚构数据。

## 已实现

- Tushare 盘后数据：日线、每日指标、涨跌停价、同花顺涨停池、连板天梯、强势板块、板块资金流。
- 市场情绪状态：冰点、退潮、分歧、修复、发酵、高潮；明确输出空仓与仓位上限。
- 热门板块梯队、连板梯队、强势个股角色与条件候选池。
- 盘前预案：合理高开、超预期高开、弱转强、分歧、失效五种情景。
- Alpha Vantage 外围 ETF 行情；文本情报和截图情报导入。
- 百度 OCR 或兼容 Chat Completions 的视觉模型二选一。
- 行业/多题材归类、持仓与 T+1 可卖数量、持仓感知的盘前处置。
- 持仓、情绪龙、板块确认、二选一、跨板块卡位和空间板的逐股快照监控。
- 事件触发的大模型决策与个股问答；模型故障时自动回退硬规则。
- SQLite 持久化、任务日志、响应式 Web 界面、Basic Auth、Docker 部署。
- 预案冻结：历史或盘中不能静默覆盖，`--force` 覆盖时旧版本自动归档。
- 次日验证回填：候选胜率、平均收益、MFE/MAE、空仓建议有效性与次日市场广度展示在预案页。
- 涨停宇宙按日结构化入库（`limit_up_daily`、`sector_daily`），为一字板过滤、溢价率与后续回测打底。
- 无密钥时自动加载明确标注的演示数据。
- 飞书自建应用双向机器人：确认式持仓录入、个股提问、文案/链接/字幕收件；复盘、预案及监控提醒统一推送，支持其他 agent 调用同一出站接口。

## 本地启动

```powershell
cd C:\Users\ycl\Desktop\a-share-review
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m uvicorn app.main:app --reload
```

打开 <http://127.0.0.1:8000>。先用演示数据检查流程，再在 `.env` 中填入自己的 Token。

## 任务命令

```powershell
# 盘后；留空日期时从交易日历获取最近交易日
python -m scripts.job post-market

# 盘前；汇总最近盘后复盘、外围行情和情报库
python -m scripts.job pre-market

# 显式覆盖已冻结/历史预案（旧版本自动归档；盘中慎用）
python -m scripts.job pre-market --force

# 演示数据
python -m scripts.job post-market --demo
python -m scripts.job pre-market --demo
```

建议调度时间（北京时间）：交易日 17:10 运行盘后任务，交易日 08:20 运行盘前任务。首次上线应人工观察一周，确认接口更新时间后再固定时刻。

## 文档

- [数据源申请与配置](docs/data-sources.md)
- [持仓、盘中哨兵与个股问答](docs/holdings-monitoring.md)
- [通过 Web 配置 API 与 Token](docs/web-configuration.md)
- [飞书双向通知与消息收件](docs/feishu-channel.md)
- [云服务器部署](docs/deployment.md)
- API 文档：应用启动后访问 `/docs`

## 项目结构

```text
app/
  engines/       确定性复盘、预案、跷跷板与验证引擎
  sources/       Tushare、外围市场、OCR 与大模型适配器
  services/      任务流水线
  web/           原生响应式 Web UI
  db.py          SQLite 数据层
  main.py        FastAPI
scripts/job.py   定时任务入口
```

核心评分和风险门槛是可解释的规则，不让大模型自行编造行情或绕过 T+1。大模型用于非结构化情报、事件解释和个股问答；市场门槛、候选排序、空仓、行情时效和验证仍由确定性规则控制。

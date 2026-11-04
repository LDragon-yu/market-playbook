# 第三方 Tushare 兼容代理配置

本项目不需要安装供应商给出的 Tushare Python SDK。服务端已经按 Tushare HTTP 协议直接调用，供应商示例中对 `pro._DataApi__http_url` 的修改等价于本项目的 `TUSHARE_BASE_URL`。

在不会提交版本库的 `.env` 中配置：

```dotenv
TUSHARE_TOKEN=供应商分配的Token
TUSHARE_BASE_URL=https://供应商地址/
TUSHARE_POINTS_TIER=15000
```

供应商给出的 MCP URL 是给支持 MCP 的桌面客户端或智能体使用的，不是 Web 应用定时任务的必需组件。部署后的盘后任务继续使用 HTTP 数据适配器，更容易调度、记录错误和做数据降级。

首次配置必须验证以下接口：

- `trade_cal`
- `daily`
- `daily_basic`
- `stk_limit`
- `limit_list_ths`
- `limit_step`
- `limit_cpt_list`
- `moneyflow_cnt_ths`
- `index_daily`

持仓监控和题材归类还会按需使用：

- `stock_basic`、`index_member_all`、`ths_index`、`ths_member`
- 单股票代码调用的 `rt_k`

当前验证结果是该代理允许单股 `rt_k`，但不允许全市场或多代码批量实时快照。因此监控池被限制为少量高优先级标的，并采用顺序请求；不要据此假设已经获得 Level-2、逐笔成交或全市场实时授权。

若单项失败，系统会把错误写入复盘报告的 `warnings` 和任务日志，其余成功数据仍可用于降级分析。

## 安全与稳定性

- 不要把真实 Token 写入 `.env.example`、README、截图、前端代码或版本库。
- 对已经发到聊天、群聊或截图中的 Token，应联系供应商更换。
- 第三方代理的权限、限流、数据完整性和服务寿命由供应商决定；应定期抽样与交易所或可信行情终端核对。
- 若未来切回官方 Tushare，只需把 `TUSHARE_BASE_URL` 改回 `http://api.tushare.pro` 并更换 Token，分析层无需修改。

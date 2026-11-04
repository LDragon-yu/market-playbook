# 云服务器部署

## 用 Git 维护与升级

服务器上以仓库为准，`.env`、`data/` 不进入版本库：

```bash
git clone <你的仓库地址> /opt/a-share-review
cd /opt/a-share-review
cp .env.example .env
# 编辑 .env（至少 TUSHARE_TOKEN、APP_ENV=production、APP_USERNAME、APP_PASSWORD）
docker compose up -d --build
```

日常升级前先备份数据，再拉取新版本重建：

```bash
cd /opt/a-share-review
cp -r data data.bak.$(date +%F)
git pull
docker compose up -d --build
```

`data/runtime.env` 保存网页端修改的密钥，`data/market_review.db` 保存全部业务数据，二者都不在 Git 里，升级不会丢失；回滚时恢复上一镜像和备份的 `data` 目录即可。CI 会在每次 push 时运行 lint 与测试（`.github/workflows/ci.yml`）。

## Docker Compose

服务器安装 Docker 后：

```bash
cp .env.example .env
# 编辑 .env，至少设置 TUSHARE_TOKEN、APP_USERNAME、APP_PASSWORD
docker compose up -d --build
curl http://127.0.0.1:8000/health
```

默认只将服务映射到 `127.0.0.1:8000`，再由 Nginx/Caddy 提供 HTTPS。不要把未设置密码的服务直接暴露到公网。

## 定时任务

容器名默认为 `jianshi`。Linux 主机 `crontab -e` 示例：

```cron
CRON_TZ=Asia/Shanghai
10 17 * * 1-5 cd /opt/a-share-review && docker compose exec -T app python -m scripts.job post-market >> data/cron.log 2>&1
20 8  * * 1-5 cd /opt/a-share-review && docker compose exec -T app python -m scripts.job pre-market >> data/cron.log 2>&1
```

节假日运行不会凭空产生报告：盘后任务会读取最近交易日，数据库按交易日覆盖保存。盘前最好结合中国节假日由云平台调度器控制。

## 反向代理

Nginx 核心配置：

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 16m;
}
```

生产环境应启用 HTTPS、防火墙、强密码和定期备份 `data/market_review.db`。上传截图位于 `data/uploads`，也应按需备份或定期清理。

网页配置 Token 前，必须先在服务器设置 `APP_ENV=production`、`APP_USERNAME` 和 `APP_PASSWORD`。未启用登录认证的生产环境会拒绝配置写入。详见[通过 Web 配置 API 与 Token](web-configuration.md)。

如需盘中自动哨兵，在 `.env` 设置 `MONITOR_ENABLED=true` 后重启容器。只运行一个应用进程；当前调度器位于 Web 进程内，多 worker 会造成重复轮询和重复预警。详细限制见[持仓、盘中哨兵与个股问答](holdings-monitoring.md)。

## 升级与回滚

升级前备份整个 `data` 目录；拉取新版本后执行 `docker compose up -d --build`。应用启动时会创建缺失表，不删除已有报告。出现问题时恢复上一镜像和备份的 SQLite 文件。

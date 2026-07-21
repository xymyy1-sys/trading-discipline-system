# 运行、升级与回滚

## 本地开发

后端：

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

前端：

```bash
npm ci
npm run dev -- --host 127.0.0.1 --port 5173
```

## 首次安全部署

1. 复制 `.env.example` 为 `.env`。
2. 设置独立的长密码和至少 32 位随机 `AUTH_SECRET`。
   如果旧系统数据库位于仓库的 `backend/data`，设置 `BACKEND_DATA_PATH=./backend/data`，Compose 会原地迁移该数据库；不要复制空库覆盖它。
3. 保持服务器公网 8000 端口关闭；Compose 默认把前端 5173 也只绑定到 `127.0.0.1`，供宿主机 TLS 代理访问。
4. 用云负载均衡、Caddy 或宿主机 Nginx 将 HTTPS 反向代理到 `127.0.0.1:5173`。
5. HTTPS 生效后设置 `AUTH_COOKIE_SECURE=true`，并把 HTTPS 站点地址加入 `CORS_ORIGINS`。
6. 执行 `docker compose up -d --build`。后端启动前会自动运行 `alembic upgrade head`，不会删除已有数据。

## 保留数据的升级流程

服务器上的 SQLite 数据位于 Docker 命名卷 `backend-data`，不在 GitHub 仓库中。代码更新不会覆盖持仓、交易记录或历史证据。

升级前必须做一致性备份：

```bash
mkdir -p backups
docker compose exec -T backend python -c "import sqlite3; src=sqlite3.connect('/app/data/trading_discipline.db'); dst=sqlite3.connect('/app/data/predeploy-backup.db'); src.backup(dst); dst.close(); src.close()"
docker compose cp backend:/app/data/predeploy-backup.db backups/predeploy-backup.db
```

然后升级：

```bash
git pull --ff-only
docker compose up -d --build
docker compose ps
docker compose logs --tail=100 backend
```

也可以直接执行带备份、数据量对比、健康检查和迁移检查的流程：

```bash
bash scripts/server_upgrade.sh
```

该脚本同时兼容 `docker compose` 与 `docker-compose`。它会先通过 SQLite 在线备份 API 生成一致性副本，再按后端容器 ID 使用 `docker cp` 复制到宿主机，并在宿主机再次执行 `PRAGMA quick_check`。每次升级的证据目录包括：

- `code-before.txt` / `code-after.txt`：升级前后提交号；
- `trading_discipline.db` / `backup-check.json`：可恢复备份及宿主机完整性检查；
- `counts-before.json`：升级前关键表行数和当时尚不存在的表；
- `counts-after.json`：升级后行数、增量和完整性结果。

升级前允许记录尚未由新迁移创建的关键表；升级完成后，任何关键表仍缺失、关键表行数减少、数据库完整性失败或 Alembic 未到 head，脚本都会失败退出并保留证据目录。

验收：

- 打开站点时先出现登录页，未登录不能读取 `/api/holdings`。
- 登录后持仓和交易记录数量与升级前一致。
- `/api/health` 正常，`/api/acceptance/report` 的迁移版本为当前 head。
- 新增/修改一条测试数据后，`/api/audit-log` 的 `chain_valid` 为 `true`。
- SSE 状态为已连接，分钟线来源和降级标记符合实际。

配置 HTTPS 后执行自动化生产冒烟：

```bash
BASE_URL=https://trade.example.com AUTH_USERNAME=admin AUTH_PASSWORD='你的密码' bash scripts/production_smoke.sh
```

## 可审计资金与跨市场适配器

板块六态模型不会把东方财富“主力资金”等订单方向估算冒充为非杠杆资金，也不会用价格涨跌反推出 ETF 真实申赎、韩国外资或杠杆产品规模。未配置授权数据源时，对应指标显示“数据不足”，且不参与高风险确认。

板块非杠杆资金适配器配置：

```dotenv
SECTOR_AUDITED_FLOW_URL=https://licensed-provider.example/sector-flow
SECTOR_AUDITED_FLOW_TOKEN_FILE=/run/secrets/sector-flow-token
```

服务端会以 `GET ?trade_date=YYYY-MM-DD` 请求该地址，并携带 Bearer Token。返回值必须与请求交易日一致，且每条记录至少包含：

```json
{
  "trade_date": "2026-07-21",
  "data_quality": "audited",
  "items": [
    {
      "board_name": "半导体",
      "board_code": "BK1036",
      "non_leveraged_net_inflow": 12.34,
      "non_leveraged_net_inflow_unit": "亿元",
      "methodology_id": "licensed-sector-flow-v1",
      "new_high_count": 18,
      "constituent_count": 120,
      "new_high_window": 20,
      "etf_share_net_change": 3200000,
      "etf_share_change_pct": 1.25,
      "etf_id": "510300",
      "etf_share_unit": "份",
      "etf_share_base": 256000000,
      "etf_methodology_id": "official-etf-shares-v1",
      "source": "授权供应商",
      "source_url": "https://licensed-provider.example/evidence/123",
      "published_at": "2026-07-21T15:30:00+08:00"
    }
  ]
}
```

`non_leveraged_net_inflow` 是必填的可审计非杠杆净流入，必须显式以 `non_leveraged_net_inflow_unit=亿元` 声明单位，并提供可追踪口径版本的 `methodology_id`；不能用东方财富订单方向估算替代。其余字段为可选证据，但必须遵守成组契约：

- `new_high_count` 与 `constituent_count` 必须同时提供，且 `new_high_window` 必须为 20；前者不能大于后者。
- ETF 证据必须同时提供 `etf_id`、`etf_share_net_change`、`etf_share_change_pct`、`etf_share_unit=份`、正数 `etf_share_base` 与 `etf_methodology_id`。系统会校验净变化、基准份额与变化率的一致性；不得使用 ETF 价格、成交额或订单流估算替代。
- 未取得任一可选证据时省略字段或返回空值；系统保持“数据不足”，不会按 0 写入，也不会让缺失证据参与六态高风险确认。

接口必须使用 HTTPS；来源链接也必须是无嵌入凭据的 HTTPS；`published_at` 或 `observed_at` 必须带时区，换算上海时间后必须等于请求交易日，不能晚于服务器时间或超过 36 小时；`data_quality` 只能明确标记为 `audited`、`official` 或 `official_audited`。任一记录跨日、过期、重复、缺少溯源、字段只提供一半或契约不合格时，整批数据会被拒绝，避免部分旧数据混入模型。只有交易日一致且板块名称或代码精确匹配的记录才会合并；未匹配板块继续保持空值。

六态审计接口：

- `GET /api/market/sector-temperature/history?scope=daily`：按交易日保存的兼容汇总。
- `GET /api/market/sector-temperature/history?scope=intraday`：不可变的盘中事实样本。
- `GET /api/market/sector-temperature/history?scope=evolution`：严格六态的连续采样/跨交易日确认路径。

可使用 `board_type`、`board_code`、`board_name`、`start_date`、`end_date` 和 `limit` 缩小审计范围。刷新时间本身不会制造新事实样本；盘中持续性确认默认要求有效样本至少间隔 5 分钟。

跨市场授权适配器配置：

```dotenv
GLOBAL_ETF_FLOW_URL=https://licensed-provider.example/etf-flow
GLOBAL_KOREA_FOREIGN_FLOW_URL=https://licensed-provider.example/korea-foreign-flow
GLOBAL_KOREA_LEVERAGE_URL=https://licensed-provider.example/korea-leverage
GLOBAL_KOREA_RATE_URL=https://official-provider.example/korea-rate
GLOBAL_OFFICIAL_ADAPTER_TOKEN=服务端令牌
```

每个接口返回 JSON 数组，或含 `items` / `data` / `records` 的对象。每项必须至少包含 `metric_id`、`name`、`value`、`source`、`published_at`、`source_url` 和 `data_quality`，可选 `unit`、`direction`、`change_pct`、`related_a_share_sectors`。`data_quality` 只能是 `audited`、`official` 或 `official_audited`；`published_at` 必须带时区、不得晚于采集时间且不得超过 120 小时；`source_url` 必须是无嵌入账号密码的 HTTPS。指标类别由所配置的接口固定，返回记录中的 `metric_kind` 无权覆盖。没有真实份额申赎、外资净买卖或杠杆产品规模时必须保持空值，禁止用 ETF 价格、成交额或指数涨跌替代。

密钥只放服务器 `.env` 或只读 Secret 文件，不写入 GitHub。升级后需执行数据库迁移；板块盘中状态和跨市场证据会保存为不可变快照，供持续性确认和重启后的审计读取。

## HTTPS 与防火墙

应用容器不负责签发公网证书。建议将域名解析到服务器，并由宿主机反向代理自动签发 Let's Encrypt 证书。没有域名时可先使用云厂商 HTTPS 负载均衡；不要为了 `Secure` Cookie 使用自签名证书给普通浏览器访问。

防火墙只开放 SSH、80、443；5173 可限制为本机或反向代理来源，8000 必须保持关闭。确认 HTTPS 后启用 HSTS（应在最外层 TLS 代理配置，不在当前 HTTP 容器中伪装启用）。

仓库提供 `deploy/Caddyfile.example`。替换域名后可作为最小 TLS 反向代理配置；正式启用前须确保域名已解析到服务器。

## 回滚

1. 保留升级前 Git 提交号和数据库备份。
2. 停止当前服务，切回上一提交并重建镜像。
3. 如果迁移不可向后兼容，停止后端，恢复 `predeploy-backup.db` 到命名卷中的 `trading_discipline.db`。
4. 启动旧版本并核对持仓、交易数量。不要执行 `git reset --hard` 或删除 Docker volume。

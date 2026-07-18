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

## HTTPS 与防火墙

应用容器不负责签发公网证书。建议将域名解析到服务器，并由宿主机反向代理自动签发 Let's Encrypt 证书。没有域名时可先使用云厂商 HTTPS 负载均衡；不要为了 `Secure` Cookie 使用自签名证书给普通浏览器访问。

防火墙只开放 SSH、80、443；5173 可限制为本机或反向代理来源，8000 必须保持关闭。确认 HTTPS 后启用 HSTS（应在最外层 TLS 代理配置，不在当前 HTTP 容器中伪装启用）。

仓库提供 `deploy/Caddyfile.example`。替换域名后可作为最小 TLS 反向代理配置；正式启用前须确保域名已解析到服务器。

## 回滚

1. 保留升级前 Git 提交号和数据库备份。
2. 停止当前服务，切回上一提交并重建镜像。
3. 如果迁移不可向后兼容，停止后端，恢复 `predeploy-backup.db` 到命名卷中的 `trading_discipline.db`。
4. 启动旧版本并核对持仓、交易数量。不要执行 `git reset --hard` 或删除 Docker volume。

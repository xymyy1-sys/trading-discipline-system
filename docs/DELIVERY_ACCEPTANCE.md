# V2.0 / V2.1 / V2.2 交付验收清单

## 当前代码基线

- 分支：`main`
- 最新数据库迁移：`k7a4c9d2f1b3`
- 需求追踪：`docs/REQUIREMENTS_TRACEABILITY.md`
- 部署与回滚：`docs/RUNBOOK.md`
- 自动升级脚本：`scripts/server_upgrade.sh`
- 生产冒烟脚本：`scripts/production_smoke.sh`

## 已由仓库证据证明

- 后端完整测试集覆盖认证、规则、持仓、交易、T+1、SSE、回放、校准、审计、行情降级和迁移语义。
- 前端通过 TypeScript 构建、静态检查和 Vite 生产构建。
- 空 SQLite 数据库可从初始版本连续升级至 `k7a4c9d2f1b3`。
- Docker 后端启动前执行 `alembic upgrade head`；代码更新不会覆盖命名卷中的业务数据。
- 公网后端端口未发布，前端默认只绑定宿主机回环地址。
- 所有业务接口需要签名 HttpOnly 会话；写操作具有来源校验、限流和哈希链审计。

## 目标服务器必须生成的证据

以下项目不能用本地单元测试代替，必须在服务器完成后才可签署生产验收：

1. 升级目录中存在时间戳数据库备份、升级前提交号及升级前后业务表数量文件。
2. `docker compose ps` 中前后端健康，后端日志无迁移失败。
3. 公网只能访问 80/443；8000、5173 均不可从公网直连。
4. HTTPS 证书有效，`AUTH_COOKIE_SECURE=true`，响应包含 HSTS 和安全头。
5. 未登录访问 `/api/holdings` 返回 401；登录后原持仓、交易和计划数量不变。
6. `/api/acceptance/report` 返回迁移 `k7a4c9d2f1b3`、审计链有效、T+1 校验通过。
7. 浏览器中 SSE 成功连接；断网恢复后显示恢复次数和最新事件时间。
8. 至少一个真实持仓能展示分钟数据来源、VWAP 可靠性、主动买卖来源及降级说明。

其中 1、2、5、6 可由仓库脚本自动检查；3、4 仍需结合云防火墙和域名证书配置核对。

## 发布顺序

1. 将本地提交推送到 GitHub `main`，等待 CI 全绿。
2. 在服务器仓库执行 `bash scripts/server_upgrade.sh`。
3. 配置 TLS 代理并关闭公网 5173/8000。
4. 设置 `AUTH_COOKIE_SECURE=true` 后重建服务。
5. 执行 `scripts/production_smoke.sh` 并保存输出。
6. 按上述目标服务器证据逐项签署。

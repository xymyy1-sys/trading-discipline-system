# V2.0 / V2.1 / V2.2 交付验收清单

## 当前代码基线

- 分支：`main`
- 最新数据库迁移：`v2f6g7h8i9j0`
- 需求追踪：`docs/REQUIREMENTS_TRACEABILITY.md`
- 部署与回滚：`docs/RUNBOOK.md`
- 自动升级脚本：`scripts/server_upgrade.sh`
- 生产冒烟脚本：`scripts/production_smoke.sh`

## 已由仓库证据证明

- 后端完整测试集覆盖认证、规则、持仓、交易、T+1、SSE、回放、校准、审计、行情降级和迁移语义。
- 前端通过 TypeScript 构建、静态检查和 Vite 生产构建。
- 空 SQLite 数据库可从初始版本连续升级至 `v2f6g7h8i9j0`；另有包含重复旧建议、版本、反馈和结果的旧库升级回归。
- Docker 后端启动前执行 `alembic upgrade head`；代码更新不会覆盖命名卷中的业务数据。
- 公网后端端口未发布，前端默认只绑定宿主机回环地址。
- 所有业务接口需要签名 HttpOnly 会话；写操作具有来源校验、限流和哈希链审计。
- 页面初始化 GET 只读取缓存或持久化快照，不再隐式访问外部行情源；用户点击刷新时才通过对应 POST `/refresh` 采集并更新快照。
- 旧 Service Worker 已进入退役流程，入口页和 `sw.js` 禁止缓存，缺失的版本化静态资源直接返回 404，避免菜单切换加载已删除分块而出现空白页。
- 建议、建议版本、反馈和结果形成可追溯闭环；反馈带客户端幂等键并绑定不可变建议版本，观察池刷新结果完整持久化。

## 2026-07-19 发布前验证

- 后端完整测试：`440 passed`。
- 前端组件测试：18 个测试文件、`43 passed`。
- 前端 TypeScript、Oxlint 与 Vite 生产构建通过。
- 空 SQLite 数据库迁移到唯一 head `v2f6g7h8i9j0`，`PRAGMA quick_check=ok`，共 42 张表。
- `git diff --check` 通过；最终代码复核未发现 P0/P1 阻断项。
- 非阻断维护项：逐步迁移 Pydantic 旧式配置和 `datetime.utcnow()`，并在不重新引入旧分块缓存问题的前提下优化约 1.1 MB 的前端主包。

## 目标服务器必须生成的证据

以下项目不能用本地单元测试代替，必须在服务器完成后才可签署生产验收：

1. 升级目录中存在时间戳数据库备份、宿主机 `PRAGMA quick_check` 结果、升级前后提交号及升级前后业务表数量文件；升级后关键表不得缺失或减少。
2. `docker compose ps` 中前后端健康，后端日志无迁移失败。
3. 公网只能访问 80/443；8000、5173 均不可从公网直连。
4. 有域名后验证 HTTPS 证书、`AUTH_COOKIE_SECURE=true`、HSTS 和安全头；当前无域名，此项属于外部条件阻塞，不影响 HTTP 内网/单用户基线验收。
5. 未登录访问 `/api/holdings` 返回 401；登录后原持仓、交易和计划数量不变。
6. `/api/acceptance/report` 返回迁移 `v2f6g7h8i9j0`、审计链有效、T+1 校验通过。
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

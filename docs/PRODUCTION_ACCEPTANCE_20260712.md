# 2026-07-12 生产部署验收记录

## 部署结果

- 服务器：`1.12.222.27`
- 部署提交：`b085592`
- 数据库迁移：`k7a4c9d2f1b3`
- 一致性备份：`/root/tds-backups/20260712-181621/trading_discipline.db`
- Nginx 配置备份：`/root/tds-backups/nginx.conf.20260712-183111`
- 后端容器：健康，不发布宿主机 8000 端口
- 前端容器：仅绑定 `127.0.0.1:5173`
- 公网入口：Nginx 80 端口

## 数据完整性

升级前后数量一致：

| 业务表 | 数量 |
| --- | ---: |
| 持仓 `holdings` | 5 |
| 交易 `trade_logs` | 4 |
| 次日计划 `next_day_plans` | 19 |

SQLite 在线备份在停止旧服务前完成；迁移直接作用于原 `backend/data/trading_discipline.db`，未创建空库替代原数据。

## 安全与功能验收

- 公网首页返回 200。
- `/api/health` 返回 200。
- 未登录 `/api/holdings` 返回 401。
- 登录后验收报告确认审计链有效、T+1 校验通过。
- 公网 5173、8000 均不可直接访问。
- 响应包含 CSP、`X-Frame-Options: DENY`、`X-Content-Type-Options: nosniff`、Referrer Policy 和 Permissions Policy。
- 原 Git 远端 URL 中的明文 PAT 已清除。

## 尚待外部条件

- 当前只有 IP 地址且仍使用 HTTP。要保护登录口令和会话的传输安全，需要提供已解析到服务器的域名，配置受信任 HTTPS 证书后启用 `AUTH_COOKIE_SECURE=true` 和 HSTS。
- GitHub 原 PAT 缺少 `workflow` 权限，GitHub 拒绝包含 `.github/workflows/ci.yml` 的推送。需要新的 GitHub 写入凭据并授予工作流写权限，随后推送本地/服务器完整提交并验证 CI。

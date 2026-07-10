# 运行与部署

## 本地开发

后端：

```bash
cd /root/.openclaw/workspace-coding/trading-discipline-system/backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

前端：

```bash
cd /root/.openclaw/workspace-coding/trading-discipline-system
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

访问：

```text
http://localhost:5173
```

关键接口：

```text
http://localhost:8000/api/market/sector-flow
http://localhost:8000/api/intel/daily
```

## 数据源降级

- 资金流优先使用东方财富接口，失败后尝试 AkShare，仍失败则返回诊断数据。
- 信息差优先使用东方财富快讯和 CCTV 新闻联播页面，外部资讯无 A 股关键词命中时返回诊断样例并给出说明。
- 所有降级都会通过接口中的 `source` 或 `data_notes` 显示，前端不能把诊断数据伪装成真实行情。

## Docker Compose

当前机器支持 `docker-compose`：

```bash
cd /root/.openclaw/workspace-coding/trading-discipline-system
docker-compose up --build
```

访问：

```text
http://localhost:5173
```

## 回滚

- 前端和后端都是独立镜像，升级时只替换业务镜像。
- SQLite 数据在 `backend-data` volume 中，不随镜像删除。
- 如需回滚，停止当前容器后切回上一版镜像即可。

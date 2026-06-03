# blivedm-api

B 站直播间监控的 API 调度端。

## 职责

- 管理直播间和后台页面。
- 轮询 B 站直播间 HTTP 接口。
- 生成包含 host list、token、uid、buvid 的 WebSocket 采集任务。
- 接收采集客户端回传的原始事件批次。
- 解析事件并写入 MySQL。

## 模块备注

- `main.py`：FastAPI 路由、应用生命周期，以及采集客户端使用的内部 API。
- `coordinator.py`：定时轮询房间、判断直播状态、清理过期采集任务、处理任务认领。
- `bili_api.py`：调用 B 站 HTTP 接口，解析 `live_time` 开播时间，并借助 `blivedm` 初始化房间连接参数。
- `events.py`：解析 B 站原始命令，并定义哪些业务事件入库、哪些命令忽略；启动时会把历史 `unknown` 中已能识别的命令重分类。
- `db.py`：MySQL 表结构迁移和全部持久化操作。`live_sessions` 记录每场直播，`monitor_runs` 只记录采集连接运行。
- `room_parser.py`：支持从管理页输入纯房间号或 B 站直播间 URL。

```sh
cp .env.example .env
uv sync --extra test
uv run python -m blivedm_api.main
```

管理页：`http://127.0.0.1:8000/`

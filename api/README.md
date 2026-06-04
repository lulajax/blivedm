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
- `events.py`：解析 B 站原始命令，只返回已知业务事件；未知命令和解析失败会被忽略。启动时会把历史 `unknown` 中已能识别的命令重分类。
- `event_projector.py`：独立增量投影进程，把 `room_events` 中的已知事件写入分析专表和 `event_time_details`，用于用户、主播、场次和时间维度统计。
- `db.py`：MySQL 表结构迁移和全部持久化操作。`collector_clients` 按 `client_id` 保存采集端配置，`live_sessions` 记录每场直播，`room_collect_leases` 负责单房间互斥采集，`monitor_runs` 只记录采集连接运行历史；`room_events` 保留原始事实，事件专表只做分析投影。
- `room_parser.py`：支持从管理页输入纯房间号或 B 站直播间 URL。

`SESSDATA` 不再从 API 全局环境变量读取。管理页的“采集客户端 Cookie”区域会按 `client_id` 保存每个采集客户端自己的 `SESSDATA`、启用状态和最大采集房间数；client 拉取任务时，API 使用该 `client_id` 对应的配置去初始化 B 站 WebSocket 采集参数。未配置 Cookie 时按匿名方式运行。

```sh
cp .env.example .env
uv sync --extra test
uv run python -m blivedm_api.main
```

分析专表投影进程单独启动：

```sh
uv run python -m blivedm_api.event_projector
```

管理页：`http://127.0.0.1:8000/`

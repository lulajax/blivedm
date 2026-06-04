# blivedm-client

B 站直播间 WebSocket 消息采集客户端。

## 职责

- 从 `blivedm-api` 拉取采集任务。
- 使用 API 下发的参数连接 B 站弹幕 WebSocket。
- 通过 `blivedm` 解码和解压消息。
- 把原始命令回传给 API。

client 不调用 B 站 HTTP 接口，也不连接 MySQL。

每个采集端都应该配置一个稳定且唯一的 `COLLECTOR_CLIENT_ID`。API 管理页会按这个 ID 保存该客户端自己的 B 站 `SESSDATA`、启用状态和最大采集房间数；client 拉任务时只带 `client_id`，不会直接持有或提交 Cookie。

client 按间隔轮询任务。API 会用 `room_collect_leases` 保证同一个真实房间同一时间只下发给一个 client；如果某个 client 停止心跳超过 `COLLECTOR_STALE_SECONDS`，其他 client 下一轮轮询时可以接管。

## 模块备注

- `main.py`：client 进程入口和生命周期。
- `monitor.py`：任务轮询循环、活跃 WebSocket 运行管理、心跳上报和停机清理。
- `api_client.py`：API 调度端接口的类型化封装。
- `task_client.py`：`blivedm.BLiveClient` 子类，直接使用 API 下发的 WebSocket 配置，不在 client 侧重复调用 B 站 HTTP 接口。
- `collector.py`：原始命令批量缓存，并转发到 `/internal/events/batch`。
- `config.py`：client 环境变量和派生出来的 API endpoint URL。

```sh
cp .env.example .env
# 编辑 .env，固定 COLLECTOR_CLIENT_ID，和管理页里的客户端配置保持一致。
uv sync
uv run python -m blivedm_client.main
```

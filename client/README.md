# blivedm-client

B 站直播间 WebSocket 消息采集客户端。

## 职责

- 从 `blivedm-api` 拉取采集任务。
- 使用 API 下发的参数连接 B 站弹幕 WebSocket。
- 通过 `blivedm` 解码和解压消息。
- 把原始命令回传给 API。

client 不调用 B 站 HTTP 接口，也不连接 MySQL。

## 模块备注

- `main.py`：client 进程入口和生命周期。
- `monitor.py`：任务轮询循环、活跃 WebSocket 运行管理、心跳上报和停机清理。
- `api_client.py`：API 调度端接口的类型化封装。
- `task_client.py`：`blivedm.BLiveClient` 子类，直接使用 API 下发的 WebSocket 配置，不在 client 侧重复调用 B 站 HTTP 接口。
- `collector.py`：原始命令批量缓存，并转发到 `/internal/events/batch`。
- `config.py`：client 环境变量和派生出来的 API endpoint URL。

```sh
cp .env.example .env
uv sync
uv run python -m blivedm_client.main
```

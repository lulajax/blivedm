# 部署说明（OSS + install.sh 模式）

参照 tiktoklive-system 的发布方式：本地打包上传 OSS，服务器从 OSS 拉包部署。

- **目标机**：tiktok-99（内网 `172.22.214.63` / 外网 `8.220.197.99`），root 用户
- **部署目录**：`/app/blivedm-api`、`/app/blivedm-client`
- **OSS**：`oss://mifeng-wehub-pic/tiktok/blivedm/blivedm-{api,client}.tar.gz`
- **管理后台**（tiktoklive 那套）在 hw-tiktok-86，手动部署，与此无关

> api 调度端自带管理页，部署后访问 `http://172.22.214.63:8000/` 配置直播间和采集客户端 Cookie。

## 角色

| 进程 | 目录 | 说明 |
| --- | --- | --- |
| api | `/app/blivedm-api` | FastAPI 调度端，连 MySQL + B 站 HTTP，`:8000` 管理页 |
| projector | `/app/blivedm-api` | 事件投影进程，与 api 同目录同包，由 `run.sh` 一起管理 |
| client | `/app/blivedm-client` | 采集端，只依赖 `API_BASE_URL` + 唯一 `COLLECTOR_CLIENT_ID` |

## 脚本一览

| 脚本 | 位置 | 作用 |
| --- | --- | --- |
| `deploy/upload-to-oss.sh` | 本地仓库 | 打包 api/client 上传 OSS，需本地装好 `ossutil` |
| `install.sh` | 服务器服务目录 | 从 OSS 拉包→解压→`uv sync`→重启，保留 `.env` |
| `run.sh` | 服务器服务目录（随包发布） | `start/stop/restart/status/logs` |
| `rollback.sh` | 服务器服务目录（随包发布） | 回滚到上一版本 |

`install.sh` 不进 tar 包（避免运行时自我覆盖），首次手动放到服务器，之后很少改动。
`run.sh` / `rollback.sh` 打进 tar 包，随代码版本更新。

---

## 首次部署

### 1. 本地：上传包

```sh
# 仓库根目录
./deploy/upload-to-oss.sh all      # 或 api / client 单独传
```

### 2. 服务器 tiktok-99：放好目录和 install.sh

通过跳板机进入 tiktok-99 交互式会话后：

```sh
mkdir -p /app/blivedm-api /app/blivedm-client
```

把 `deploy/install-api.sh` 内容写到 `/app/blivedm-api/install.sh`，
把 `deploy/install-client.sh` 内容写到 `/app/blivedm-client/install.sh`，然后：

```sh
chmod +x /app/blivedm-api/install.sh /app/blivedm-client/install.sh
```

### 3. 服务器：部署 api

```sh
cd /app/blivedm-api
./install.sh                       # 首次会生成 .env 模板并退出
vi .env                            # 填 MYSQL_HOST/USER/PASSWORD 等（参考 api/.env.prod）
./install.sh                       # 再次执行：uv sync + 起 api、projector
```

> 前置：服务器需有 `uv`、`git`（git 依赖 blivedm 要联网拉）、`ossutil`。
> 数据库表会在 api 启动时自动建（`CREATE TABLE IF NOT EXISTS`），但 database `blivedm` 需先存在。

### 4. 服务器：部署 client

```sh
cd /app/blivedm-client
./install.sh                       # 首次生成 .env 模板并退出
vi .env                            # API_BASE_URL=http://127.0.0.1:8000，COLLECTOR_CLIENT_ID 取唯一值
./install.sh                       # 再次执行：起 client
```

### 5. 验证

```sh
/app/blivedm-api/run.sh status
/app/blivedm-client/run.sh status
```

浏览器开 `http://172.22.214.63:8000/`，加直播间、给采集客户端配 SESSDATA。

---

## 日常更新

```sh
# 本地：改完代码后
./deploy/upload-to-oss.sh all      # 只改了一侧就传 api 或 client

# 服务器：拉新包重启
cd /app/blivedm-api && ./install.sh
cd /app/blivedm-client && ./install.sh
```

`.env` 不在包里，更新不会动它。表结构变更随 api 重启自动迁移。

## 回滚

```sh
cd /app/blivedm-api && ./rollback.sh      # 回到上一版本
cd /app/blivedm-client && ./rollback.sh
```

`install.sh` 每次部署会把旧包留为 `*.tar.gz.prev`，`rollback.sh` 据此回滚（保留一个历史版本，可来回切）。

## 进程管理速查

```sh
./run.sh status           # 看进程状态
./run.sh logs api         # 跟踪日志（api 侧可选 api/projector）
./run.sh restart all      # api 侧重启全部；client 侧用 ./run.sh restart
./run.sh stop             # 停
```

> nohup 方式不带开机自启/崩溃自愈。若需长期守护，可在 systemd 里直接调 `run.sh`，
> 或加 cron 每分钟 `./run.sh start`（已运行会自动跳过）。

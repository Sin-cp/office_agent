# Docker 部署：个人测试环境

本阶段只面向桌面 Web。此配置不含登录或 HTTPS，默认仅绑定宿主机 127.0.0.1:8080；ECS 上通过 SSH 隧道测试，不直接公开文件列表。

## 配置文件怎么配合

- `frontend/Dockerfile`：第一阶段用 Node 安装依赖并构建 Vue；第二阶段只保留 Nginx 和 dist，运行时不启动 Vite。
- `frontend/nginx.conf`：提供页面，保留 `/api` 路径转发到 `backend:8000`。Docker DNS 自动刷新后端地址，后端重建后不会长期指向旧 IP。
- `backend/Dockerfile`：Python 3.15 环境，安装运行依赖，使用非 root 用户 UID/GID 10001 运行一个 Uvicorn 进程，不启用 reload。
- `compose.yaml`：组织两个容器，后端健康后启动前端，配置日志轮转和重启策略；不发布后端 8000 端口。
- 两端 `.dockerignore`：白名单只允许构建需要的源码进入上下文，不包含本地 `.env`、上传数据、SQLite、虚拟环境或 node_modules。
- `.env.example`：Compose 配置示例；修改宿主机监听地址、端口、数据目录。

宿主机是运行 Docker 的电脑或服务器。容器内 `/data` 和宿主机数据目录是同一份挂载数据。容器内 localhost 指向自身，所以 Nginx 用服务名 backend 访问 API。

Nginx 接收上限是 11 MiB 的整个请求，后端仍只允许 10 MiB 文件，为 multipart 头部留余量。接口错误返回 JSON，前端可以显示。

## 第一次在 ECS 启动

以下命令在 **ECS 服务器终端**执行，需要先把完整项目传到 `/srv/office-agent/app`。目前尚未上传服务器，也没有推送 GitHub。不要复制本地 `.venv`、`node_modules` 或真实数据作为程序包。

### 1. 准备独立数据目录

在服务器以 root 执行（此目录为本应用专用）：

```bash
install -d -m 0750 -o 10001 -g 10001 /srv/office-agent/data
cd /srv/office-agent/app
```

后端容器用户 ID 为 10001，所以宿主机目录必须允许它写入。无需 chmod 777。本轮从空数据库开始，启动时自动创建表。

### 2. 配置 Compose

首次创建 `.env`（已有文件则编辑，不要覆盖）：

```bash
cp -n .env.example .env
nano .env
```

填写：

```dotenv
OFFICE_AGENT_BIND_IP=127.0.0.1
OFFICE_AGENT_HTTP_PORT=8080
OFFICE_AGENT_HOST_DATA_DIR=/srv/office-agent/data
```

`.env` 由 Compose 读取作变量替换，不是前端配置，也不会自动把全部变量传给后端；后端数据目录由 Compose 显式设置为 `/data`。

### 3. 检查、构建、启动

```bash
docker compose config --quiet
docker compose build backend
docker compose build web
docker compose up -d --wait --wait-timeout 120
docker compose ps
curl --fail http://127.0.0.1:8080/api/health
```

分开构建可减少 2 GB 内存服务器同时构建带来的压力。首次构建需要访问镜像仓库、PyPI 和 npm；hello-world 拉取成功不保证所有仓库都畅通。报网络或依赖错误时保留完整日志，不要忽略错误继续启动。

`build` 生成应用镜像；`up -d` 后台运行；`--wait` 等待健康检查。`restart: unless-stopped` 会在进程异常退出后重启，也允许服务随 Docker 启动恢复；单纯 unhealthy 状态不会自动重启。

### 4. 从自己的 Mac 浏览器访问

在 **Mac 终端**运行并保持连接，登录账号以实际配置为准：

```bash
ssh -N -L 127.0.0.1:8080:127.0.0.1:8080 root@60.205.124.226
```

然后在 Mac 浏览器访问 `http://127.0.0.1:8080`。流量经过加密 SSH 隧道到达 ECS。安全组只需允许你的 SSH 来源访问 22，无需开放 8080/8000/5173。

如果本地 8080 已占用，只修改隧道左侧端口为 18080，然后浏览器打开本机 18080。Ctrl+C 只关闭 SSH 隧道，不会停止后台应用。

公网 IP 后续发生变化时替换为实际地址。如果要通过域名面向其他用户访问，再安排登录权限、HTTPS、备案和安全组，不要直接将无认证文件服务开放。

## 在本地验证（可选）

先手动启动本地 Docker 环境。项目根目录执行：

```bash
mkdir -p deploy-data
docker compose config --quiet
docker compose build backend
docker compose build web
docker compose up -d --wait
```

访问 `http://127.0.0.1:8080`。如果在 Linux 本机测试，先将专用数据目录属主设置为 10001:10001；Mac Docker Desktop 通过文件共享处理权限，如有写入错误检查目录共享和权限。

本地 ARM Mac 构建的镜像不要直接当作 ECS 的 amd64 镜像传输。初次建议在 ECS 本机构建；后续本地交叉构建需显式设置 linux/amd64。

## 验收与关闭

1. 页面、静态资源加载正常，API 返回 version 0.2.0。
2. 上传自制 DOCX；抽屉显示记录；下载文件与原文件一致。
3. 无效文档、超限文件有错误提示。
4. `docker compose restart` 后文件仍可下载。
5. `docker compose down` 后重新 `up -d --wait`，记录与文件仍保留。

查看日志：

```bash
docker compose logs --tail=100 backend web
```

测试完成关闭：

```bash
docker compose down
```

该命令删除本项目容器和网络，不删除绑定挂载的宿主机数据。不要手动删除数据目录。

当前 Docker 健康检查不要求浏览器登录，健康接口只证明服务能够响应，不等于全功能测试成功。

## 更新、备份与排错

更新确认过的源码后，重新执行两次 build 和 up。首次部署之前保留可回退的代码版本；基础镜像使用发行系列标签，后续可在成功验证后固定镜像 digest 实现更严格的复现。

备份时短暂停止应用，保证数据库与文件一致（下面使用 root，默认数据目录）：

```bash
install -d -m 0700 /srv/office-agent/backups
docker compose stop
tar -czf /srv/office-agent/backups/data-$(date +%Y%m%d-%H%M%S).tar.gz -C /srv/office-agent data
docker compose start
```

确认备份成功且服务恢复后，把备份保存到独立位置。恢复需先停服务，将备份恢复到相同路径并保留 UID/GID 10001 的访问权限。新部署默认不会迁移本地旧数据。

- 无法连接 Docker daemon：本机 Docker 未运行，或当前用户权限不足。
- 后端 Permission denied：检查宿主机数据目录是否存在且对 UID 10001 可写。
- Nginx 502：查看 backend 健康状态及日志，不要把 proxy_pass 改成容器内 localhost。
- 8080 无法通过公网访问：这是默认设计，使用 SSH 隧道。
- 浏览器返回首页而不是 /docs：当前只代理 /api，未公开 Swagger 页面。
- 构建失败：优先看实际错误（网络、软件版本、内存）；不要删除业务数据重试。

## 本次验证边界

本地进行了 Compose 静态配置检查与前端构建。由于本地 Docker daemon 未运行，尚未完成 Docker 镜像构建、Nginx 运行和容器端到端验证；ECS 未执行任何部署操作。正式使用前按上述步骤完成验收。

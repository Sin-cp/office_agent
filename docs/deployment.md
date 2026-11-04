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

以下命令在 **ECS 服务器终端**执行，需要先把完整项目传到 `/srv/office-agent/app`。已有线上环境请使用本文末尾的一键部署脚本进行更新。不要复制本地 `.venv`、`node_modules` 或真实数据作为程序包。

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

1. 页面、静态资源加载正常，API 返回 version 0.4.0。
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

## DeepSeek 配置更新

在服务器现有 `.env` 中补充 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL=deepseek-flash`、`DEEPSEEK_BASE_URL=https://api.deepseek.com`。不要覆盖已有的数据目录配置，不将密钥传给前端。Compose 会显式传入后端容器；避免分享 `docker compose config` 的展开输出，其中可能包含密钥，检查配置使用 `--quiet`。

备份数据后依次执行 `docker compose build backend`、`docker compose build web`、`docker compose up -d --wait --wait-timeout 120`。任务调度仅支持单进程后端，不配置多个 workers 或副本。更新会中断运行中的任务，请待任务完成或停止后部署。


## 一键同步并部署脚本

入口为 `scripts/deploy.sh`，配套的两个 Python 文件必须保留在同一目录。本地脚本从自身位置定位项目，因此可从任意工作目录调用。它同步当前文件内容，包括未提交的修改和新增源码，不要求先推送 GitHub。

### 首次准备 SSH

本地需要 Bash、Python 3.8+、SSH、rsync；服务器需要 Python 3.8+、rsync、tar、Docker 和支持 `up --wait`、`config --format json` 的 Compose v2。服务器必须已有运行中的单个 backend 和 web 容器、项目目录及 `.env`；脚本不是首次安装工具。

先通过正常 SSH 登录一次，核对服务器指纹并确认密钥可用。脚本启用 `BatchMode=yes` 和 `StrictHostKeyChecking=yes`，不会保存密码、跳过指纹检查或在自动部署中等待密码输入。加密私钥可提前载入 ssh-agent。

可在本机 `~/.ssh/config` 中配置别名（私钥路径按实际修改）：

```sshconfig
Host office-agent-prod
    HostName 60.205.124.226
    User root
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
```

```bash
ssh office-agent-prod
```

SSH 端口和密钥都通过该配置管理，部署脚本不另存认证信息。

### 日常执行

默认服务器为 `root@60.205.124.226`，项目目录 `/srv/office-agent/app`，备份目录 `/srv/office-agent/backups`：

```bash
./scripts/deploy.sh --dry-run
./scripts/deploy.sh
```

使用别名或其他专用目录：

```bash
./scripts/deploy.sh --host office-agent-prod --dry-run
./scripts/deploy.sh --host office-agent-prod \
  --remote-dir /srv/office-agent/app \
  --backup-dir /srv/office-agent/backups
```

`--dry-run` 仅检查环境、权限和活动任务并执行 rsync 预览：不获取远程锁、不上传文件、不创建远程备份目录，也不构建或重启容器。它会在本地临时目录准备同步清单；预览不能替代实际构建和启动验证。

### 同步范围与数据保护

- 后端：`backend/app` 内的 Python 源码、requirements.txt、Dockerfile、.dockerignore。
- 前端：`frontend/src` 内允许的源码和静态资源扩展名，以及 package.json、锁文件、index.html、tsconfig.json、vite.config.ts、nginx.conf、Dockerfile、.dockerignore。
- 根目录：compose.yaml。
- 不上传 `.env`、Git 信息、本地练习、测试、虚拟环境、依赖目录、dist、数据库、上传文件、日志及密钥文件。隐藏目录也不会作为源码上传。
- 仅在 `backend/app` 和 `frontend/src` 内删除已移除且属于白名单的源码文件；被排除的配置与数据在接收端受到保护。其他目录没有删除同步。
- 不跟随源码符号链接。新增运行资源类型或 Docker COPY 路径时，需要同步更新脚本白名单；否则新资源不会自动发布。
- 服务器现有 `.env` 保持原样。实际数据路径从当前后端容器的 `/data` 绑定挂载读取，不假设它一定是默认路径；禁止数据挂载与源码目录重叠。

### 执行顺序

脚本使用持续 SSH 会话和服务器文件锁串行化部署；锁文件位于项目目录旁，例如 `/srv/office-agent/.app.deploy.lock`，会话退出后自动释放锁。锁文件保留是正常现象，不应通过删除锁文件解除正在执行的锁。该锁仅协调本脚本，不限制人工 Docker 操作。

预检使用后端容器的默认运行用户检查 `/data`、`/data/uploads` 和数据库文件的访问权限，并只读检查活动任务。发现之前遇到的 root 所有上传目录问题会停止，不会自动递归修改权限。通过后开始同步，验证 Compose 配置，并依次构建后端、前端镜像；构建期间现有容器继续运行。

构建完成后再次检查任务，然后先停止 web 入口、再次检查任务，确认没有新任务进入后停止 backend。这段时间网站暂时不可访问。数据目录整体打包为带时间与随机标识的 `.tar.gz`，同时包含 SQLite、上传文件和其他业务状态。备份目录须为 `0700`；新建时自动按此权限创建，已有目录权限不符时提示后停止。归档文件只允许所有者访问，不自动删除旧备份。

备份成功后执行 `docker compose up -d --wait --wait-timeout 120`。成功还要求 web 容器经 Nginx 访问 `/api/health` 返回 `status=ok`，以及新后端的数据写入权限预检通过。最后输出监听地址和备份路径。监听为 `0.0.0.0:8080` 时从外部使用服务器 IP；监听为 `127.0.0.1:8080` 时沿用 SSH 隧道。

### 失败处理

- SSH/预检失败：尚未同步，按错误提示检查依赖、指纹、密钥、任务或权限。
- rsync 或构建失败：不停止现有容器，但服务器源码可能已部分或全部更新；修正后重新运行脚本。
- 暂停入口后发现任务，或备份失败：尝试启动原 backend/web 容器恢复服务，停止发布；失败的临时备份清理，不生成成功提示。
- 更新或健康验证失败：返回非零退出码，保留备份。先查看 `docker compose ps` 和 `docker compose logs --tail=100 backend web`。运行状态可能已发生变化，不能假定仍为旧版本。
- 发布中断：SSH 正常退出或断开后锁会释放；构建命令可能需要结束后才能释放。若发生服务器断电或强制终止，需手工检查容器和 `.partial` 备份，不把不完整归档当作可恢复备份。

更新失败不自动降级数据库，也不自动删除服务器数据。数据库结构已经迁移时，恢复旧代码前应停止相关容器、另存当前数据，并将选定的完整备份恢复到实际挂载目录，再使用与备份匹配的旧代码或旧镜像启动。恢复时确保 UID/GID 10001 的权限；仅更新镜像或重启不能解决目录属主问题。数据备份不等于源码备份，发布前应保留可回退的代码版本。

### 本地验证与线上验证边界

```bash
bash -n scripts/deploy.sh
python3 -m unittest discover -s scripts/tests -v
```

测试将 SSH、Docker、tar 和远程 rsync 替换为临时命令，验证锁、同步保护、构建顺序、活动任务、权限、备份和启动失败；另外使用本机真实 rsync 在临时目录验证过滤和删除规则。不连接线上服务器，不调用真实 Docker，不读取本地 `.env`。

本次实现仅完成这些本地验证。首次线上使用先执行 `--dry-run`，再实际部署；线上权限、网络、镜像构建及运行健康结果以该次运行输出为准。

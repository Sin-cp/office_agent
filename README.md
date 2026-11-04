# Office Agent

通过实际项目学习 Agent 开发：Vue 3 + TypeScript 提供交互，Python + FastAPI 提供文档与模型工具。

## 当前功能：项目对话与多文件处理

- 中间输入框支持多行草稿和自动增高，附件按钮或拖拽区域可以上传 Word。
- 支持直接聊天及多选上传 `.docx`，每份最大 **10 MiB**；检查 ZIP 结构、正文内容类型和宏标记。
- 项目管理按首条提示词命名，支持重命名、继续聊天和源文件列表预览下载。
- 文件和记录持久保存，刷新或重启后仍可使用，同名文件互不覆盖。
- 支持文档总结、问答、追问、正文与常规表格增删改，以及会话记忆和多文件整体提交。

这是本地单用户学习应用，尚未加入账号和访问权限，不应直接作为多人公网文件服务。

## 环境与启动

Node.js 22（本地环境 22.8.0；依赖也兼容 Node 20），Python 3.15（本地 3.15.1）。前端用 npm 与 package-lock.json，后端用独立虚拟环境。

已有项目无需重新克隆。第一次获取：

```bash
git clone https://github.com/Sin-cp/office_agent.git
cd office_agent
```

终端一，从项目根目录启动后端：

```bash
cd backend
python3 -m venv .venv              # 首次创建即可
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Windows PowerShell 使用 `python -m venv .venv` 创建环境，使用 `.venv\Scripts\Activate.ps1` 激活。

终端二，从项目根目录启动前端：

```bash
cd frontend
npm ci
npm run dev
```

- 页面：http://127.0.0.1:5173
- API 文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/api/health

Vite 将 `/api` 请求代理到 8000 端口。生产部署需要 Nginx 配置相应代理。服务停止时在各自终端按 Ctrl+C；如果端口占用，先检查已有服务，不要重复启动或随意停止其他程序。

从 v0.1.0 更新后，需要重新安装后端 requirements（新增 python-multipart）。原先正在运行的后端也需要重启，以执行数据库初始化。

## 文件与数据库

```text
backend/data/
├── office_agent.db       SQLite 元信息：ID、名称、大小、存储键、UTC 时间
└── uploads/
    └── <uuid>.docx       原文件二进制内容
```

目录在后端启动时自动创建，位置相对于代码固定，不受工作目录影响。`OFFICE_AGENT_DATA_DIR` 环境变量可指定其他目录，测试使用临时目录。数据目录全部被 Git 忽略。

数据库不存 Word 内容；本地文件不使用用户文件名作为路径。以后可以把文件存储替换为 OSS，保留文件 ID 和元信息接口。备份时应同时备份数据库和 uploads 目录。

## API

| 接口 | 作用 |
| --- | --- |
| `POST /api/files` | multipart 字段 `file` 上传单文件，成功 201 |
| `GET /api/files?limit=20&offset=0` | 倒序列表，返回 `items` 和 `total`；limit 范围 1–100 |
| `GET /api/files/{id}/download` | 下载原文件，使用原文件名 |
| `GET /api/health` | 进程健康检查，版本 0.4.0 |

文件对象字段：`id`、`name`、`size`（字节）、`created_at`（UTC ISO 时间）；内部 storage_key 不暴露。

无效文件返回 400，超限返回 413，文件不存在或磁盘文件丢失返回 404，保存失败返回 500。当前大小校验在应用读取上传临时文件时执行；公网部署还需网关请求大小限制和用户权限。

## 验证

前端：

```bash
cd frontend
npm run test
npm run build
```

后端（激活虚拟环境后）：

```bash
cd backend
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

测试会使用临时 SQLite 和临时文件，不需要启动端口，不会改变实际上传数据。包含上传下载字节一致、同名文件、分页、重启持久化、无效文档和超限拒绝、保存失败回滚、文件不存在等场景。前端组件测试覆盖拖拽、单文件限制、大小/类型检查和上传中禁用。

人工检查：上传自制 Word → 抽屉自动打开 → 下载打开 → 刷新页面 → 列表仍在；按 Esc 关闭抽屉后焦点回到右上角按钮。本阶段以桌面浏览器为目标。

## 代码阅读路径

1. `frontend/src/components/Composer.vue`：文字草稿与文件选择、拖拽。
2. `frontend/src/api.ts`：FormData、HTTP 请求与错误处理。
3. `backend/app/files.py`：上传/列表/下载路由。
4. `backend/app/storage.py`：DOCX 校验和本地存储位置。
5. `backend/app/database.py`：参数化 SQL、事务和连接关闭。
6. `frontend/src/App.vue`、`FileDrawer.vue`：请求状态、文件列表与抽屉交互。

学习路线见 [docs/roadmap.md](docs/roadmap.md)，本阶段笔记见 [文件上传与抽屉](docs/learning/02-file-upload.md)。

## 版本记录

- `v0.1.0`：初始化与健康检查。
- `v0.2.0`：输入框、DOCX 文件上传、SQLite 元信息、文件抽屉与原文件下载。
- `v0.4.0`：项目式多文件会话、文档问答与正文/表格增删改、新建文档与全文自动补改、联网搜索与网页阅读、Docker 部署与一键同步脚本。

真实 API Key、`.env`、数据、依赖和虚拟环境不提交 Git。原有 `py/` 练习目录保留在本地，不属于应用代码。

## Docker 与 ECS 部署

新增 Docker 多阶段构建、Nginx 反向代理和 Compose 配置。默认只绑定 `127.0.0.1:8080`，ECS 个人测试通过 SSH 隧道访问；数据库和文件挂载在容器外，避免重建丢失。

完整的目录权限、首次启动、验证、关闭和备份步骤见 [Docker 部署说明](docs/deployment.md)。本地 Docker 开发启动方式和原来的 Vite/FastAPI 开发方式可以分别使用，端口互不相同。


## 文档处理与项目会话

可以不上传文件直接聊天，首次发送创建项目并保存会话。上传文件会创建“新项目”，之后追加文件仍属于当前项目。首条提示词去除首尾空白后取前 20 个字符作为标题，超出加省略号；手动重命名后不会被后续消息覆盖。

同一项目支持多份 DOCX，提供综合问答和多文件增删改。普通聊天、总结、问答与澄清正常显示文字；实际文档修改才显示结果文件和逐项改动。每份源文件独立记录最新修改结果，原件不覆盖。多文件修改先全部生成、校验，再一次提交；任一失败或取消，整轮不发布修改。

“项目管理”的“继续聊天”恢复消息和附件选择；“预览源文件”打开表格列表，每行可以预览或下载原件。修改结果在对应消息中单独预览、下载。输入框附件 × 只取消本轮选择，保留草稿、已上传文件和对话；“新建对话”才清空当前界面。

版本恢复已停用（旧恢复接口返回 410），历史生成文件仍可访问。界面不再展示“原文依据”“阅读范围”栏目；读取限制影响回答时通过普通回复说明。

首次配置时将根目录 `.env.example` 复制为 `.env` 并填写 `DEEPSEEK_API_KEY`；已有 `.env` 请补充字段，不覆盖配置。后端读取密钥，前端无需配置。Docker 部署需重新构建前后端。服务重启终止未完成任务，不自动重跑；仍为单用户、单进程、一个活动任务。

后端启动自动幂等迁移旧项目、消息和文件关系。备份应包含整个数据目录。项目删除同时删除全部源文件、生成文件、任务、消息及摘要；运行任务期间禁止删除或追加文件。`.project-trash` 用于项目删除恢复，`publication-*.json` 用于中断后的文件发布清理，不应手动修改。

接口、预算、迁移和验收步骤见 [项目与文档处理助手说明](docs/document-assistant.md)。


## 联网搜索与网页阅读

输入框“联网”默认开启：系统按问题自动搜索或读取公开网页，普通闲聊无需搜索。支持北京当天天气、近期资讯以及文章链接总结；关闭后不调用搜索或读取服务。查询过程可停止，回答附可点击来源并与项目一起保存。

后端默认使用 DeepSeek Search / Fetch API 及现有 API Key；每轮最多 3 次搜索、5 次网页读取。配置、费用说明、接口字段及真实服务验收脚本见 [联网聊天说明](docs/web-search.md)。

## 一键同步并部署

本地已配置到服务器的 SSH 密钥或别名后，在项目根目录执行：

```bash
./scripts/deploy.sh --dry-run  # 只读预检与同步预览
./scripts/deploy.sh            # 同步、构建、备份、更新及健康检查
```

默认目标为 `root@60.205.124.226:/srv/office-agent/app`。可用 `--host` 指定 SSH 别名、`--remote-dir` 指定项目目录、`--backup-dir` 指定备份目录。同步包含本地未提交的源码，不上传本地 `.env`、数据库或上传文件。发布会短暂停机；当前有文档任务运行或数据目录权限不足时会停止。

依赖与首次 SSH 配置、同步范围、失败恢复和验证边界见 [部署脚本使用说明](docs/deployment.md#一键同步并部署脚本)。脚本实现和本地测试不等于已经发布到线上。

## 聊天中新建文档

无需上传空白附件即可请求生成 Word 文档；上传文件后，明确修改才修改，另建文档则创建独立文件。支持参考附件、预览、下载、删除及后续修改，说明与联调脚本见 [文档创建说明](docs/document-creation.md)。

## 全文修改自动补改

文档修改会检查临时生成的实际结果，发现遗漏时最多自动补改两轮，完整通过后再保存。补改或停止不会发布部分结果。流程、预算与真实服务验证见 [自动补改说明](docs/edit-repair.md)。

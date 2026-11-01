# Office Agent

通过实际项目学习 Agent 开发：Vue 3 + TypeScript 提供交互，Python + FastAPI 提供文档与模型工具。

## 当前版本：v0.2.0 · 文件上传与抽屉

- 中间输入框支持多行草稿和自动增高，附件按钮或拖拽区域可以上传 Word。
- 每次上传一个 `.docx`，最大 **10 MiB**；检查 ZIP 结构、正文内容类型和宏标记。
- 上传成功自动打开右侧抽屉，展示名称、大小、时间，支持下载原文件与分页加载。
- 文件和记录持久保存，刷新或重启后仍可使用，同名文件互不覆盖。
- 支持文件预览、删除及 DeepSeek 单段修改；发送后展示执行步骤，可中途停止，完成后下载修改版。

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
| `GET /api/health` | 进程健康检查，版本 0.2.0 |

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

真实 API Key、`.env`、数据、依赖和虚拟环境不提交 Git。原有 `py/` 练习目录保留在本地，不属于应用代码。

## Docker 与 ECS 部署

新增 Docker 多阶段构建、Nginx 反向代理和 Compose 配置。默认只绑定 `127.0.0.1:8080`，ECS 个人测试通过 SSH 隧道访问；数据库和文件挂载在容器外，避免重建丢失。

完整的目录权限、首次启动、验证、关闭和备份步骤见 [Docker 部署说明](docs/deployment.md)。本地 Docker 开发启动方式和原来的 Vite/FastAPI 开发方式可以分别使用，端口互不相同。


## 文档预览与 DeepSeek 修改

上传文件后自动选中，或在文件管理点击“修改文件”；文件和修改要求缺一不可。发送后直接执行单段修改，不需要确认预览。处理时展示真实步骤和停止按钮，完成后展示修改摘要、耗时、下载和查看入口，并自动选中新版本供下一轮修改。

前端“查看文件”提供 Word 页面预览，没有手动替换段落的编辑界面。后端仍保留 `GET /api/files/{id}/content` 与 `POST /api/files/{id}/edits`，供确定性读取和修改使用。

新增 `POST /api/tasks`、`GET /api/tasks/{id}`、`POST /api/tasks/{id}/cancel`。新增 `GET /api/files/{id}/history` 查询同一文件版本链的修改历史。无历史文件保持首页；首次任务创建成功或打开有历史的文件后进入连续对话，输入框固定底部。任务记录持久化到 SQLite；刷新页面恢复当前选择、历史和活动任务。服务重启会终止未完成任务，不自动重跑。当前只支持单进程和一个活动任务。

首次配置时将根目录 `.env.example` 复制为 `.env`，填写新建的 `DEEPSEEK_API_KEY`。已有 `.env` 则补充字段，不覆盖部署配置。重新安装后端 requirements 并重启；前端无需配置密钥。Docker 部署须重新构建前后端。真实 Key 和 `.env` 不提交 Git。

只修改一个正文标题或普通段落，生成新 DOCX 并记录 `source_file_id`，原文件不覆盖。支持段落边界的成对空书签，编辑时保留书签并清理目标段落的旧拼写检查标记。不支持表格、图片/超链接/域、修订、批注范围及涉及正文范围的书签；拒绝修改时说明具体结构。新文字继承首个文字片段的格式。模型调用最长 45 秒，失败说明原因并终止。

详见 [DeepSeek 配置、任务接口与验证](docs/learning/04-deepseek-tasks.md)。

文件标签 × 仅清除当前选择和草稿并返回首页，不删除文件或历史。执行期间禁止清除、切换及删除文件，后端同步校验。文件管理中的删除会删除整个项目及其所有版本和历史；旧数据中单独缺失的版本仍保留历史记录，但下载入口禁用。历史按稳定 ID 关联。

### 文件项目与版本
每次上传创建独立项目（同名文件不会合并）。右侧文件管理按项目计数和分页，只展示源文件名称与上传时间；“查看文件”和“下载源文件”始终对应原始上传文件。“修改文件”恢复该项目对话，自动选用最近成功且仍可用的版本。每轮修改版保留在对话结果中，可查看、下载及展开修改内容；结果卡片不再展示段落替换摘要和修改说明两行。

启动后端时会自动迁移已有历史，无需重新生成修改版。源文件已删除的历史仍显示项目入口，并标注“源文件不可用”；可继续修改现存版本，但不能预览或下载缺失的源文件。

项目接口：`GET /api/projects?limit=20&offset=0`、`DELETE /api/projects/{id}`。删除项目须确认，将删除源文件、全部修改版与对话历史，执行期间禁止删除。文件先暂存到数据目录的 `.project-trash`，数据库事务失败则恢复；提交成功后清理。后端重启会根据项目记录自动恢复或清理残留暂存，请勿手动删除该目录。原有文件预览、下载和历史接口继续用于具体版本。

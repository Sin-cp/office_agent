# Office Agent

通过实际项目学习 Agent 开发：Vue 3 + TypeScript 提供交互，Python + FastAPI 提供文档与模型工具。

## 当前版本：v0.2.0 · 文件上传与抽屉

- 中间输入框支持多行草稿和自动增高，附件按钮或拖拽区域可以上传 Word。
- 每次上传一个 `.docx`，最大 **10 MiB**；检查 ZIP 结构、正文内容类型和宏标记。
- 上传成功自动打开右侧抽屉，展示名称、大小、时间，支持下载原文件与分页加载。
- 文件和记录持久保存，刷新或重启后仍可使用，同名文件互不覆盖。
- 文字只作为当前页面草稿，刷新后清空；发送按钮暂不可用。本版本没有 AI 调用、文档修改、删除或独立文件管理页。

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

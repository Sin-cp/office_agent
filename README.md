# Office Agent

通过实际项目学习 Agent 开发：使用 Vue 3 + TypeScript、Python + FastAPI，逐步实现通过对话编辑 Word 文档。

## 当前版本：v0.1.0

本版本只完成项目初始化：Vue 页面、FastAPI 服务、前后端健康检查。尚未实现文件上传、聊天、DeepSeek 调用或文档编辑，不需要 API Key。

## 环境

- Node.js 22（本地验证：22.8.0），npm 10。
- Python 3.15（本地验证：3.15.1）。使用独立虚拟环境。
- 前端依赖由 `package-lock.json` 锁定，后端依赖由 `requirements.txt` 锁定。

## 本地启动

从 GitHub 下载项目：

```bash
git clone https://github.com/Sin-cp/office_agent.git
cd office_agent
```

如果已在当前项目目录，无需再次克隆。分别打开两个终端。

### 终端一：后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Windows PowerShell 将激活命令替换为 `.venv\Scripts\Activate.ps1`，创建环境时使用 `python -m venv .venv`。

- API 文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/api/health

### 终端二：前端

```bash
cd frontend
npm ci
npm run dev
```

打开 http://127.0.0.1:5173 ，看到“FastAPI 已连接，前后端运行正常”即表示联调成功。

前端使用相对地址 `/api/health`；Vite 将 `/api` 转发到 `127.0.0.1:8000`，因此本地开发无需配置跨域。生产部署时需要 Nginx 提供对应的反向代理，Vite 开发服务器不用于公网部署。

## 验证

```bash
cd frontend
npm run build
```

该命令先执行 Vue/TypeScript 类型检查，再生成 `dist/`。可运行 `npm run preview` 并访问 http://127.0.0.1:4173 预览构建结果，后端仍需启动。

后端启动后执行：

```bash
curl --fail http://127.0.0.1:8000/api/health
```

预期返回：

```json
{"status":"ok","service":"office-agent","version":"0.1.0"}
```

停止后端后点击“重新检查”，页面应显示连接失败提示；恢复后端后重新检查，应恢复连接成功。

## 目录

```text
frontend/src/          Vue 页面和样式
frontend/vite.config.ts 开发代理和构建配置
backend/app/main.py    FastAPI 应用与健康检查
backend/requirements.txt 后端依赖锁定
docs/roadmap.md        学习路线
docs/learning/         阶段学习笔记
```

## 常见问题

- 页面提示无法连接：先检查后端终端是否正常启动，以及 8000 端口是否正确。
- 端口被占用：停止占用端口的服务，再重新启动；前端配置为端口冲突时直接报错，避免访问错误地址。
- `No module named fastapi`：确认激活的是 `backend/.venv`，并安装了 requirements 中的依赖。

## 提交约定

每个小功能单独提交，每个验证完成的阶段打标签。`v0.1.0` 专门标记初始化成果，后续版本按实际完成情况递增。

不要提交真实 API Key、`.env`、上传文件、生成文件或数据库。当前目录原有的 `py/` 是本地练习文件，已忽略，不属于这个仓库的项目代码。

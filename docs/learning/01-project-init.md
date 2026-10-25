# 第一阶段：初始化项目

## 本次完成

Vue 3 + TypeScript 页面可以通过 Vite 代理访问 FastAPI 的 `/api/health`。页面展示加载、成功、失败三种状态，并支持手动重试。

## 阅读代码的顺序

1. `frontend/src/main.ts`：Vue 从哪里启动，如何挂载页面。
2. `frontend/src/App.vue`：组件状态、生命周期和 fetch 请求。
3. `frontend/vite.config.ts`：请求如何从 5173 端口转发到 8000。
4. `backend/app/main.py`：FastAPI 路由、类型标注和 Pydantic 响应模型。

## 需要理解

- `.venv` 相当于 Python 项目的独立依赖环境；不要复用其他练习项目的环境。
- FastAPI 声明 API，Uvicorn 启动 HTTP 服务。
- Pydantic 定义并校验响应结构，FastAPI 据此生成 `/docs` 文档。
- TypeScript 类型不自动校验网络数据，因此前端还检查健康响应中的 status。
- Vite 代理让浏览器请求同源 API，服务端再转发给后端。
- 健康检查只证明本服务可响应，不表示未来的模型或存储服务也可用。

## 练习

1. 在浏览器 Network 面板中观察 `/api/health` 的请求和响应。
2. 停止后端，点击重新检查，观察失败状态；再恢复后端。
3. 在 FastAPI `/docs` 页面直接执行健康检查接口。
4. 执行 `npm run build`，区分开发服务器和构建产物。

## 下一步

实现文件上传接口，先完成不依赖 AI 的文件上传和下载。

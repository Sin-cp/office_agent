# 文件上传与抽屉：从前端走向 Python 后端

## 这一阶段的数据流

选择/拖入文件 → Vue 校验 → FormData → FastAPI UploadFile → 分块写入临时文件 → 校验 DOCX → 保存文件 → SQLite 写入记录 → 返回文件 ID → 抽屉刷新列表。

下载走另一个流程：文件 ID → 查询数据库 → 获取服务端存储键 → FileResponse 返回原始字节。

## 前端：三个状态分开管理

- 草稿：Composer 内部 ref，仅在内存里，不发送给服务器。
- 上传状态：App 管理 uploading、message、error，防止重复提交。
- 文件列表：来自 GET /api/files，数据库是事实来源；刷新页面重新获取。

文件不是 JSON。FormData 的 file 字段携带 File 对象，fetch 自动生成 multipart Content-Type 和 boundary，不要手动只设置一个 multipart/form-data 请求头。

选择器和拖拽最终都调用同一个 select 方法，所以单文件、格式、空文件和大小检查可以复用。前端校验方便提示，后端仍需校验，因为客户端检查可以被绕过。

App 使用 generation 序号忽略过时的列表响应，防止上传后新列表被旧请求结果覆盖。抽屉用 Teleport 显示在 body，打开时锁定背景滚动并约束焦点，关闭后恢复到入口按钮。

## 后端：接口与业务分开

- main.py：创建应用，lifespan 在服务启动时初始化目录和数据库。
- files.py：定义 HTTP 参数、响应模型与状态码，协调存储和数据库。
- storage.py：定位数据目录、校验 ZIP 和 DOCX 内容。
- database.py：封装 SQLite，不让路由到处拼 SQL。

这里的 def 路由由 FastAPI 在线程池运行，文件和 SQLite 操作是同步的，不需要为学习目的给每个函数都加 async。

UploadFile 提供临时文件对象。分块读取每次只处理一部分数据并累计大小，避免在业务代码中一次读入整个上传文件。multipart 解析在路由执行前进行，未来公网部署仍要加请求体限制。

## 文件内容与元信息

Word 的实际字节放在 uploads/<uuid>.docx；SQLite 只记录 id、name、size、storage_key、created_at。UUID 解决同名覆盖问题，也让浏览器不需要知道真实磁盘路径。

pathlib.Path 用于组合路径，例如 root / 'uploads' / key。与字符串拼接相比，它清楚表达文件路径的含义。

数据库操作使用占位符：

```python
db.execute('SELECT * FROM files WHERE id = ?', (file_id,))
```

不要用 f-string 把用户输入拼进 SQL。with db 成功时提交、失败时回滚；连接仍需要显式 close，项目用 contextmanager 统一处理。

文件系统和数据库不是同一个事务，所以代码要补偿：文件写成功但数据库失败时，移除本次文件；任何校验失败都清理临时文件。进程被强制终止或机器断电属于后续生产化需要处理的孤立文件恢复场景。

## DOCX 为什么要检查内容

DOCX 实际上是一个 ZIP 包。只改扩展名并不会让普通文本变成 Word。本项目检查关键条目、正文内容类型、主文档关系、XML 结构、CRC 和宏标记，并限制条目数量和解压后总大小，不解压到磁盘。

这属于格式校验，不等于完整的 Office 兼容性校验或恶意软件扫描。本阶段只原样存储和下载，不执行文档内容。

## 自己动手练习

1. 在 Network 中观察 POST 的 Form Data，以及 GET 列表中的 id。
2. 查看 uploads 中的 UUID 文件名，再查询 SQLite 对应的原始名称。
3. 上传两个同名文件，比较它们的 ID 和内容。
4. 刷新页面，观察草稿消失但文件记录保留。
5. 用伪装成 .docx 的文本文件测试错误提示。
6. 阅读 tests/test_files.py，看测试如何用临时目录隔离真实数据。

## 验收记录

后端接口测试覆盖持久化、字节一致、分页、同名文件、非法格式、宏、超限、空文件、数据库失败回滚和文件丢失。前端组件测试覆盖拖拽和多文件拒绝。浏览器验收使用独立端口 15173/18000 和 /tmp 数据，真实用户数据不参与测试。

下一步才是读取 Word 段落、标题和表格，让确定性的 Python 工具能修改内容，再逐渐接入模型与 Agent。

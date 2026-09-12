# 本地找房 Agent 后端

## 会话暂停、恢复与持久化

正式 FastAPI 路径使用 DeepAgents + LangGraph checkpoint，不再把完整历史手工重发给模型。
默认 `memory` 模式可以立即运行；MongoDB 服务和 Python 驱动未安装时，不要切换到 `mongodb`。

配置位于 `backend/.env`，模板见 `backend/.env.example`。只追加或修改以下字段，保留原有模型和高德配置：

```dotenv
CHECKPOINT_BACKEND=memory
MONGODB_URI=mongodb://127.0.0.1:27017
MONGODB_DATABASE=rental_agent
```

- `memory`：支持同进程内暂停/回复、刷新前端、历史读取；后端退出、reload 都会丢失会话。
- `mongodb`：使用官方 `MongoDBSaver`，同时保存应用会话日志和幂等回执。
  连接失败或缺少 Python 驱动时明确报错，不静默降级到内存。
- 环境变量优先于 `.env`。不要把 `MONGODB_URI` 放进任何 `VITE_*` 配置。
- 本轮没有安装、启动 MongoDB，也没有执行真实 MongoDB 集成测试。

MongoDB 的安装和切换按本地环境说明执行；内部路线文档不随公开仓库发布。

## 由用户手动启动

在项目根目录：

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8090
```

只运行一个 worker、一个后端实例。不要加 `--workers 2`，也不要启动第二个后端共同操作同一数据库。
开发时可以加 `--reload`，但内存模式每次 reload 都会丢状态；验证 MongoDB 重启恢复时不加 reload。
前端继续在 `frontend` 下运行 `npm run dev -- --host 127.0.0.1`。

`GET /health` 仅为惰性存活检查，不创建模型客户端、不连接数据库。其 `storage` 表示配置，
不代表数据库已经就绪。`GET /api/v1/sessions` 可以验证会话存储是否可用，但不会调用模型。

## 已接入的业务暂停

`request_rental_preferences` 是固定业务工具，只接受预算、户型、通勤三种缺失字段。
它在 `interrupt()` 前没有浏览器或其他外部副作用：

1. Agent 判断缺少条件时调用工具。
2. SSE 返回白名单 `interrupt`，并以 `done.status=interrupted` 结束当前 HTTP 连接。
3. 用户在原聊天框自然语言回复；也可以说“不限”、改变需求或取消找房。
4. 前端使用相同会话的 `/resume/stream`，后端校验 interrupt ID 后构造 `Command(resume=...)`。
5. 原图从暂停处继续，不重新执行已完成并 checkpoint 的前置节点。

地点消歧仍可直接聊天；地图候选仅快速填入草稿，不决定 resume 路由，不强制点击确认。
离线夹具和采集实验不属于正式 API 运行路径，也不要求配置 checkpoint。

## 接口

路径前缀统一为 `/api/v1`：

- `POST /sessions/{session_id}/chat/stream`：普通新消息；原有 `search_context` 保持兼容。
- `POST /sessions/{session_id}/resume/stream`：`message`、必填 `client_request_id`、
  `interrupt_id`，可选 `search_context`。
- `POST /sessions/{session_id}/recover/stream`：仅 `client_request_id`，继续该会话尚未完成的请求。
  不追加新的用户消息。仅用户主动点击恢复时调用。
- `GET /sessions?limit=100&offset=0`：按更新时间倒序，limit 最大 100。
- `GET /sessions/{session_id}`：显示用的用户/助手文本、状态、待回复项、最后一次安全地点事件、
  `latest_request_id`、`recovery_request_id` 和存储模式。
- `PATCH /sessions/{session_id}`：`{"title":"新标题"}`，最长 32 字符。

聊天框回复至多 4000 字符。客户端不能提交任意 `Command`、checkpoint ID、状态更新或工具参数。
请求 ID 只能使用字母、数字、点、下划线和连字符，最长 128 字符。
普通聊天仍兼容省略请求 ID，但前端始终发送；省略时不能由客户端对那次首次提交直接做幂等重试。

新增 SSE `interrupt` 形状：

```json
{
  "interrupt_id": "opaque-id",
  "type": "rental_preferences",
  "missing_fields": ["budget", "commute"],
  "message": "请补充预算、通勤方式与可接受时间；直接在聊天框回复即可，也可以说不限、修改需求或取消找房。"
}
```

`done.status` 可以是 `completed`、`interrupted`、`error`；读取会话时还可能看到
`running` 和 `recoverable`。`turn_status` 仅是运行时内部事件，不发布到 SSE。

## 持久化与恢复边界

- MongoDB 集合：`agent_checkpoints`、`agent_checkpoint_writes`、`agent_sessions`。
- 数据库保存图状态、内部工具消息和应用日志，属于本地私有数据；不能直接开放数据库端口、
  导出后上传开源仓库，或把完整 checkpoint 转发给浏览器。
- 应用会话文档先记录请求预约，再以 `rental_request` 标记驱动图执行。
  图使用 `durability="sync"`；若图已完成但应用回执尚未写完，读取/恢复时按 checkpoint 对账，
  不重跑已完成的图。收到最终 SSE 前已经保存应用回执。
- 完成/暂停回执持久化；相同 session + request ID + 内容可重放。内容冲突、跨会话暂停、
  过期暂停均拒绝。重放历史请求不会把旧暂停/旧地点重新覆盖到新轮次。
- 历史 API 返回显示投影，不直接暴露内部工具参数、原始工具消息、Mongo URI、内部文件路径或序列化状态。
  地点字段做类型/长度白名单；回执只保留该轮最后一次地点投影。
- 每个会话最多 100 个提交轮次，且达到约 6 MB 应用日志后不接受新轮次，提示新建会话。
  单轮最终显示文本超过 16000 字符会带说明截断。暂未提供历史删除、checkpoint TTL 或自动压缩。
- SSE 断线/停止生成后保留可恢复的中间 checkpoint，不再承诺 M5 的“取消后完全丢弃本轮”。
  当前不支持 `Last-Event-ID` token 级续传，也不会后台自动继续；需读取状态并主动恢复。
- 恢复不是所有副作用的 exactly-once 保证：未完成或未 checkpoint 的节点可能再次进入；
  同步工具的底层 I/O 不一定能被立即停止。暂停前不要放不可重复操作。
- 已有平台人工验证的进程内许可、可见浏览器和 Storage State 没有迁移进 MongoDB。
  本轮不承诺跨重启恢复验证码窗口；不新增验证绕过能力。
- 没有迁移 SearchRun 产物、房源结构化结果卡片、通勤缓存；这些不属于本轮 M6。

## 离线验收

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m unittest discover -s tests -v

Set-Location frontend
npm test
$env:VITE_AMAP_JSAPI_KEY='placeholder-build-key'
$env:VITE_AMAP_SECURITY_CODE='placeholder-build-code'
npm run build
```

前端构建占位变量仅应在专用测试终端中设置；之后关闭终端，避免把占位值带入真实地图开发。
测试使用内存 saver、真实 LangGraph/DeepAgents 和固定假模型；默认跳过唯一真实 MongoDB 测试。
`memory` 运行时重建测试不等于 MongoDB 进程重启测试；后者按安装指南手动完成。

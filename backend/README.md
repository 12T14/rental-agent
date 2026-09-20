# 本地找房 Agent 后端

## 会话暂停、恢复与持久化

正式 FastAPI 路径使用 DeepAgents + LangGraph checkpoint，不再把完整历史手工重发给模型。
默认 `memory` 模式可以立即运行；需要跨进程或重启保留会话时，再切换到可访问的 MongoDB。

配置位于 `backend/.env`，模板见 `backend/.env.example`。只追加或修改以下字段，保留原有模型和高德配置：

```dotenv
CHECKPOINT_BACKEND=memory
MONGODB_URI=mongodb://127.0.0.1:27017
MONGODB_DATABASE=rental_agent
```

快速模式只安装 `backend/requirements.txt` 即可。切换 MongoDB 前，先在项目根目录执行：

```powershell
.\setup.bat -WithMongo
```

它会额外安装 `backend/requirements-mongodb.txt` 中的 Python 适配包；MongoDB 服务本身仍需由用户单独启动，项目不会自动安装或启动数据库。

高德地点解析默认使用 HTTPS 直连，不读取系统 `HTTP(S)_PROXY`；这是为了避免本机全局代理接管
请求后导致 Python TLS 握手异常。若所在网络必须通过系统代理，可在私有 `backend/.env` 中设置
`AMAP_USE_ENV_PROXY=true`。这项设置不关闭证书校验。

- `memory`：支持同进程内暂停/回复、刷新前端、历史读取；后端退出、reload 都会丢失会话。
- `mongodb`：使用官方 `MongoDBSaver`，同时保存应用会话日志和幂等回执。
  连接失败或缺少 Python 驱动时明确报错，不静默降级到内存。
- 环境变量优先于 `.env`。不要把 `MONGODB_URI` 放进任何 `VITE_*` 配置。
- 本项目可以连接本机 MongoDB，也可以连接虚拟机中的 MongoDB。虚拟机方案不会让 MongoDB
  常驻本机，适合只在需要持久化会话时启动虚拟机和数据库服务。

## 虚拟机 MongoDB

先确认虚拟机已启动、MongoDB 正在监听 `27017`，并且虚拟机网络允许宿主机访问。然后只在本机
私有文件 `backend/.env` 中切换配置；不要把真实 URI、用户名或密码写入模板、日志或 Git：

```dotenv
CHECKPOINT_BACKEND=mongodb
MONGODB_URI=mongodb://<username>:<password>@<vm-ip>:27017/?authSource=admin
MONGODB_DATABASE=rental_agent
```

当前运行时会先执行 MongoDB `ping`，成功后才创建 checkpoint saver；连接不通会直接报告存储不可用。
应用使用以下三个集合：`agent_checkpoints`、`agent_checkpoint_writes` 和 `agent_sessions`。
停止使用时关闭后端和虚拟机即可释放资源；下次启动后端会按相同 URI 恢复会话。

如果暂时不需要持久化，改回 `CHECKPOINT_BACKEND=memory` 即可，无需启动 MongoDB。内部路线文档
不随公开仓库发布。

## 由用户手动启动

在项目根目录（第一次使用时先创建项目虚拟环境）：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8090
```

只运行一个 worker、一个后端实例。不要加 `--workers 2`，也不要启动第二个后端共同操作同一数据库。
开发时可以加 `--reload`，但内存模式每次 reload 都会丢状态；验证 MongoDB 重启恢复时不加 reload。
前端继续在 `frontend` 下运行 `npm run dev -- --host 127.0.0.1`。

Windows 用户可以在项目根目录先运行 `.\install.bat`（空机入口）或 `.\setup.bat`（已有 Python/Node.js），再运行 `.\start.bat`，详见根目录 [安装说明](../README.md)。新配置默认使用内存存储，已有配置会保持原样。`.\setup.bat -SkipPlaywright` 可跳过 Chromium 下载，但真实平台采集和人工验证工具在没有浏览器运行时的情况下不可用。离线房源数据源不替代聊天模型：实际 Agent 对话仍需配置模型 API Key。

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

地点消歧可以直接在聊天中回复候选序号或名称；后端只会从当前会话保存的候选中绑定目标地点。
地图候选的点击操作只是把候选填入下一条消息草稿，发送后才提交给 Agent，不会由浏览器直接修改 Agent 状态，
也不决定 resume 路由。
正式 API 默认使用离线夹具模式，便于本地回归；将 `RENTAL_DEMO_MODE=live` 后才会访问真实公开平台。
采集实验的完整审计产物仍只保留在本地，不要求也不会通过 API 暴露给浏览器。

## 接口

路径前缀统一为 `/api/v1`：

- `POST /sessions/{session_id}/chat/stream`：普通新消息；原有 `search_context` 保持兼容。
- `POST /sessions/{session_id}/resume/stream`：`message`、必填 `client_request_id`、
  `interrupt_id`，可选 `search_context`。
- `POST /sessions/{session_id}/recover/stream`：仅 `client_request_id`，继续该会话尚未完成的请求。
  不追加新的用户消息。仅用户主动点击恢复时调用。
- `GET /sessions?limit=100&offset=0`：按更新时间倒序，limit 最大 100。
- `GET /sessions/{session_id}`：显示用的用户/助手文本、状态、待回复项、最后一次安全地点事件、
  当前安全房源候选、平台状态、搜索摘要、`latest_request_id`、`recovery_request_id`、
  `title_source` 和存储模式。首轮标题先使用安全兜底值，模型标题在后台补写，不阻塞聊天回执。
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

房源搜索工具完成后，SSE 还会发送以下结构化事件；前端不需要从助手文字中解析房源：

- `platform_status`：`{"platforms":[{"key":"58","name":"58同城","status":"ok|partial|blocked|failed","label":"…","count":0}],"status":"completed|partial|empty|failed","offline":true}`。
- `listings`：搜索阶段的候选快照，包含 `id`、平台、标题、小区/地址、租金、整租/合租类型、户型、面积、标签、公开链接和位置核验状态。
- `listing_details`：详情阶段的 URL、访问状态和详情页明确写出的租赁事实；不包含原始 HTML、证据全文或产物路径。某平台首次拦截后的剩余条目使用 `skipped_after_block`，表示为保护访问节奏而未再请求，并不表示这些详情自身已触发验证码。
- `listing_update`：把详情事实合并回候选后的最新快照。`offline: true` 明确表示离线夹具，不代表实时房源。
- `listing_enrichment`：目标地点、已解析的房源坐标、直线距离、有限的通勤结果、硬条件过滤状态和排序理由；
  没有可用目标点时整体状态为 `pending`，已定位房源的通勤状态为 `target_pending`，不会把城市关键词当成“附近”。

所有字符串、数量和链接都会经过长度/类型限制；链接只允许 58 同城、安居客和房天下的公开域名，
列表页会标记为 `source_list`，不会被当成单条详情页。

三个平台列表统一使用 Playwright；房天下从官方城市入口发现行政区筛选，逐卡片读取
真实详情链接，安居客缺少可选字段时保留候选。采集工具诊断区分 `blocked`（含 HTTP 200
脚本验证码跳转）、`empty`（明确无结果）、`parse_error`（解析失败）与区域/城市校验失败；
不能把解析失败或拦截解释成没有房源。区域或城市线索不等于已核验“附近”。

`human_verify_rental_platform` 三平台共用一个可见浏览器验证实现，但授权、冷却和
Storage State 按平台隔离，一次 58 验证不能保证安居客/房天下通过。搜索验证打开刚被拦的
原始区域/列表页；详情验证只打开授权的单条原始 URL。恢复对应列表/详情内容后才保存状态，
成功后最多重试一次；不通过验证码绕过、自动解题或代理轮换访问平台。

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
  单轮最终显示文本超过 16000 字符会带说明截断。历史删除会同时清理应用会话和对应 checkpoint；当前仍未提供
  checkpoint TTL 或自动压缩。
- SSE 断线/停止生成后保留可恢复的中间 checkpoint，不再承诺 M5 的“取消后完全丢弃本轮”。
  当前不支持 `Last-Event-ID` token 级续传，也不会后台自动继续；需读取状态并主动恢复。
- 恢复不是所有副作用的 exactly-once 保证：未完成或未 checkpoint 的节点可能再次进入；
  同步工具的底层 I/O 不一定能被立即停止。暂停前不要放不可重复操作。
- 已有平台人工验证的进程内许可、可见浏览器和 Storage State 没有迁移进 MongoDB。
  列表与详情验证使用独立的一次性短时许可；详情验证只重试首个被拦 URL 一次。本轮不承诺
  跨重启恢复验证码窗口；不新增验证绕过能力。
- 房源结构化结果卡片、平台状态和地图增强结果会随应用会话日志保存并由历史 API 恢复；完整 SearchRun/详情审计产物仍只保存在本地，
  不通过浏览器 API 暴露，也不迁移到公开仓库。通勤路线只对有限候选请求，地图缓存为进程内 TTL 缓存，不作为独立持久化数据迁移。

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
测试使用内存 saver、真实 LangGraph/DeepAgents 和固定假模型；真实 MongoDB 测试默认跳过，
只有明确设置 `RUN_MONGODB_TEST=1` 才会运行。`memory` 运行时重建测试不等于 MongoDB 进程重启测试；
后者需要启动虚拟机 MongoDB 后按项目测试说明手动完成。

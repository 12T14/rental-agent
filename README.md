# 通勤找房 Agent

这是一个面向本地单用户的通勤找房工具原型。用户可以在聊天框中输入公司、学校、地标、预算、户型和通勤要求；Agent 负责解析目标地点、整理公开房源候选，并保留来源链接、抓取时间和数据质量说明。

## 当前能力

- Vue 3 + Vite 前端：聊天、会话历史、地点候选和高德地图展示。
- FastAPI 后端：SSE 流式对话、会话历史、暂停/恢复和可切换 checkpoint。
- DeepAgents/LangGraph：受限业务工具编排，不提供任意 Shell、任意文件读写或任意代码执行。
- 地图服务：后端通过高德 Web Service 做地点解析；前端可选使用高德 JSAPI 展示地图。
- 房源实验：58 同城、安居客、房天下公开列表页的离线夹具和低频采集适配器。
- 默认离线模式：没有模型、高德或 MongoDB 配置时，离线测试和界面仍可运行。

## 目录

```text
backend/                 FastAPI 与 Agent 运行时
frontend/                Vue 3 + Vite 前端
```

采集实验、内部交接文档和调研资料只保留在本地，不属于公开发布内容；对应目录会被 Git 忽略。

## 本地配置

复制模板后，在本机私有文件中填写自己的配置；真实密钥不要提交到 Git：

```powershell
Copy-Item backend\.env.example backend\.env
Copy-Item frontend\.env.example frontend\.env.local
```

后端模型和高德 Web Service 配置写入 `backend/.env`，前端 JSAPI 配置写入 `frontend/.env.local`。模板只提供变量名和安全默认值，具体说明见 [后端说明](backend/README.md)。

## 手动启动

先安装依赖，再分别启动后端和前端。以下命令应在项目根目录执行；只启动一个后端进程：

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8090
```

另开一个终端：

```powershell
Set-Location frontend
npm install
npm run dev -- --host 127.0.0.1
```

MongoDB 不是默认必需项。`CHECKPOINT_BACKEND=memory` 适合快速体验；安装并启动本地 MongoDB 后，再按指南切换到 `mongodb`。

## 离线验收

这些命令不会启动服务，也不会访问真实房源平台或高德：

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m unittest discover -s tests -v

Set-Location ..\frontend
npm test
$env:VITE_AMAP_JSAPI_KEY = 'placeholder-build-key'
$env:VITE_AMAP_SECURITY_CODE = 'placeholder-build-code'
npm run build
```

联网采集必须由使用者明确选择并自行确认平台条款。遇到登录、验证码或访问拦截时程序应停止该来源，不进行验证码绕过、代理轮换、浏览器指纹伪装或私有接口逆向。

## 数据与隐私边界

- `.env`、`.env.local`、浏览器 Storage State、MongoDB 数据、日志和抓取产物默认被忽略，不应上传。
- 地图密钥只在对应的后端或前端配置中使用；不把密钥、Cookie 或原始供应商响应发送给浏览器聊天记录。
- 公开列表候选不等于当前可租；详情、房东身份、联系方式和费用必须由用户在原平台自行核验。
- `research/` 是本地调研资料目录，含外部资料副本，已从发布内容中排除。

## 许可证

本项目采用根目录 [LICENSE](LICENSE) 中的“非商业使用许可（NC-1.0）”：允许学习、研究、个人使用、修改和非商业再分发，禁止商业使用。它不是 OSI 认可的标准开源许可证。第三方依赖和数据源按各自条款执行。

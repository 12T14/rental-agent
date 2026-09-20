# 通勤找房 Agent

这是一个面向本地单用户的通勤找房工具原型。用户可以在聊天框中输入公司、学校、地标、预算、户型和通勤要求；Agent 负责解析目标地点、整理公开房源候选，并保留来源链接、抓取时间和数据质量说明。

## 当前能力

- Vue 3 + Vite 前端：聊天、会话历史、地点候选和高德地图展示。
- FastAPI 后端：SSE 流式对话、会话历史、暂停/恢复和可切换 checkpoint。
- 房源结果事件：搜索候选、平台状态和详情页明确条件会以安全的结构化事件进入前端，并随会话恢复。
- DeepAgents/LangGraph：受限业务工具编排，不提供任意 Shell、任意文件读写或任意代码执行。
- 地图服务：后端通过高德 Web Service 做地点解析；前端可选使用高德 JSAPI 展示地图。
- 房源实验：58 同城、安居客、房天下公开列表页的离线夹具和低频采集适配器。
- 默认离线模式：没有模型、高德或 MongoDB 配置时，离线测试和界面仍可运行。

## 目录

```text
backend/                 FastAPI 与 Agent 运行时
frontend/                Vue 3 + Vite 前端
scripts/                 一键启动的服务入口
install.bat / install.ps1  Windows 空机安装入口（可通过 winget 安装基础环境）
setup.bat / setup.ps1    Windows 已有基础环境时的项目安装
start.bat / start.ps1    Windows 一键启动
```

采集实验、内部交接文档和调研资料只保留在本地，不属于公开发布内容；对应目录会被 Git 忽略。

## 本地配置

一键安装会自动创建缺失的私有配置。手动安装时可以使用下面的命令，它们不会覆盖已有文件；真实密钥不要提交到 Git：

```powershell
if (-not (Test-Path backend\.env)) { Copy-Item backend\.env.example backend\.env }
if (-not (Test-Path frontend\.env.local)) { Copy-Item frontend\.env.example frontend\.env.local }
```

后端模型和高德 Web Service 配置写入 `backend/.env`，前端 JSAPI 配置写入 `frontend/.env.local`。模板只提供变量名和安全默认值，具体说明见 [后端说明](backend/README.md)。

## Windows 一键安装与启动

项目提供适用于 Windows 10/11、Windows PowerShell 5.1 或 PowerShell 7 的脚本。

如果用户电脑基本没有开发环境，先在项目根目录运行或双击：

```powershell
.\install.bat
```

它会检查 Python 和 Node.js；如果系统提供 `winget`，会尝试自动安装缺少的基础环境，然后继续执行项目安装。Windows 的“应用安装程序”未提供 `winget` 时，需要先手动安装下面两个基础环境，或安装应用安装程序后重新运行 `install.bat`：

- Python 3.11 或更高版本（推荐 Python 3.12，勾选 Add Python to PATH）
- Node.js（推荐 22/24 LTS，自带 npm；当前脚本最低要求 18）

如果 Python 和 Node.js 已经安装，也可以直接执行较快的项目安装入口：

```powershell
.\setup.bat
```

安装脚本会创建根目录 `.venv`，安装后端基础依赖、前端依赖和 Playwright Chromium，并在配置文件不存在时创建：
`backend/.env` 与 `frontend/.env.local`。已有私有配置不会被覆盖，现有存储和数据源模式也不会被自动修改。
首次安装需要联网下载 Python/Node 依赖和浏览器运行时；不需要管理员权限、Docker 或数据库。

```powershell
.\start.bat
```

安装完成后，在下载的项目文件夹中双击 `start.bat` 即可。启动脚本会分别打开后端和前端窗口，等服务就绪后打开 `http://127.0.0.1:5173/`。
若端口 8090 或 5173 已被占用，脚本会明确报错，不会结束其他进程或启动第二个后端。停止服务时关闭对应的后端、前端窗口即可。
若不想自动打开浏览器，使用 `.\start.bat -NoBrowser`。

`.bat` 入口只对当前脚本临时使用执行策略，不修改系统 PowerShell 策略。偏好直接使用 PowerShell 或安装了多个 Python 的用户可以运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -Python "C:\path\to\python.exe"
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -NoBrowser
```

### MongoDB（可选）

快速模式默认使用 `CHECKPOINT_BACKEND=memory`，不需要安装、启动或部署 MongoDB；后端重启后会丢失会话，适合首次体验。

需要跨重启保存会话时，先准备可访问的 MongoDB（本机或虚拟机均可），再执行：

```powershell
.\setup.bat -WithMongo
```

这只安装 MongoDB 的 Python 适配依赖，不会替你启动 MongoDB。然后在私有的 `backend/.env` 中填写：
`CHECKPOINT_BACKEND=mongodb`、`MONGODB_URI` 和 `MONGODB_DATABASE`。详细配置见 [后端说明](backend/README.md)。

项目不需要 Redis、MySQL 或其他常驻中间件。Playwright Chromium 是采集使用的浏览器，由安装脚本准备，并不是一个需要另外常驻部署的服务。

### API 与数据源配置

- `backend/.env` 的 `MAIN_MODEL_API_KEY`：AI 聊天必需，包括使用离线房源夹具的聊天。无密钥可以启动界面和运行离线测试，但不能调用真实 Agent。
- `backend/.env` 的 `AMAP_WEB_SERVICE_KEY`：真实地点解析、房源地址核验和通勤查询需要的高德 Web Service 密钥。
- `frontend/.env.local` 的 `VITE_AMAP_JSAPI_KEY`、`VITE_AMAP_SECURITY_CODE`：可选的前端地图配置；缺失时仍能聊天，但地图不可用。不要把后端密钥填入前端文件。
- 新配置的 `RENTAL_DEMO_MODE=offline` 只控制房源数据源。确认平台条款后改为 `live` 才会访问真实平台；它不代表免模型 API 的本地 AI。

这些密钥由使用者申请并填写；脚本不会获取、上传或打印密钥。安装脚本重复运行会保留私有配置。仅做离线测试、不需要真实平台采集时，可以使用 `.\setup.bat -SkipPlaywright` 跳过浏览器下载。

## 手动启动

先安装依赖，再分别启动后端和前端。以下命令应在项目根目录执行；只启动一个后端进程：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8090
```

另开一个终端：

```powershell
Set-Location frontend
npm install
npm run dev -- --host 127.0.0.1
```

MongoDB 不是默认必需项。`CHECKPOINT_BACKEND=memory` 适合快速体验；需要持久化会话时，可以启动本地或虚拟机 MongoDB，再按 [后端说明](backend/README.md) 切换到 `mongodb`。

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

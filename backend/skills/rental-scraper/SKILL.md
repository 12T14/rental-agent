---
name: rental-scraper
description: >
  租房房源采集技能包。从 58同城、安居客、房天下等平台读取公开列表页。
  当需要搜索指定城市/区域的房源时加载此技能。
  支持按城市、区域、关键词搜索，返回结构化房源数据（含链接）。
---

# 租房爬虫技能（操作手册）

## 适用场景
- 用户指定城市+区域搜索房源
- 用户指定关键词（如"目标地点附近"）搜索
- 多平台交叉搜索，对比房源
- 收集房源数据用于后续分析评分

## 可用脚本

| 脚本 | 用途 | 数据源 |
|------|------|--------|
| `scrape_58.py` | 58同城租房 | `zf.58.com` SEO 公开列表页 |
| `scrape_anjuke.py` | 安居客租房 | `zu.anjuke.com` 公开列表页 |
| `scrape_fang.py` | 房天下租房 | `zu.fang.com`，直接 HTTP |
| `scrape_all.py` | 统一入口 | 自动调用多平台，合并去重 |
| `probe_platforms.py` | 渠道探测 | 检查各平台公开入口是否可访问，不解析、不绕过验证 |

## 使用流程

### 第 1 步：理解需求
从任务描述中提取：【目标城市】、【目标区域】、【关键词】、【预算范围】

### 第 2 步：调用爬虫

```bash
# 58同城 — 访问一页公开列表，限速并保存快照
python .\scrape_58.py --city cz --area wujin --keyword "目标地点" --output ..\..\artifacts\rentals_58_cz.json

# 首次人工验证：本地有图形桌面时运行一次
python .\scrape_58.py --city cz --area wujin --keyword "目标地点" --login --session ..\..\artifacts\58_session.json

# 复用已保存会话；需要时输出结构化状态
python .\scrape_58.py --city cz --area wujin --keyword "目标地点" --session ..\..\artifacts\58_session.json --status --output ..\..\artifacts\rentals_58_cz_status.json

# 离线解析已有快照，不访问 58
python .\scrape_58.py --city cz --area wujin --keyword "目标地点" --snapshot ..\..\artifacts\58_cz_html.html

# 房天下 — 备用渠道（列表候选，无单条详情链接）
python .\scrape_fang.py --city gz --area tianhe

# 安居客 — 公开列表页；关键词在标题和地址中筛选
python .\scrape_anjuke.py --city cz --area wujin --keyword "目标地点" --output ..\..\artifacts\rentals_anjuke_cz.json

# 统一搜索 — 一键全平台（推荐：--status 看各平台状态，--session 复用 58 会话）
python .\scrape_all.py --city cz --keyword "目标地点" --status --session ..\..\artifacts\58_session.json

# 低频探测其他平台入口（不会登录、不会绕过验证）
python .\probe_platforms.py --city cz --area wujin --output ..\..\artifacts\platform_probe_cz.json
```

### 第 3 步：读取结果
脚本输出 JSON 到 stdout，或保存到 `--output` 指定路径。
未指定输出路径时，默认保存到项目的 `backend/artifacts/`；该目录用于本地产物，不应提交到 Git。

### 第 4 步：整理回复
从结果中提取关键信息，按价格排序，展示给用户。

## 城市代码

| 代码 | 城市 | 代码 | 城市 |
|------|------|------|------|
| bj | 北京 | sh | 上海 |
| gz | 广州 | sz | 深圳 |
| cz | 示例市 | nj | 南京 |
| hz | 杭州 | cd | 成都 |
| wh | 武汉 | su | 苏州 |

## 返回格式

每条房源：
```json
{
  "title": "大学城名仕佳园新出精装2室",
  "price": 2200,
  "monthly_rent_cny": 2200,
  "room": "2室1厅",
  "area": "80.00㎡",
  "area_sqm": 80.0,
  "tags": "邻地铁 配套齐全 精装修",
  "url": "https://cz.58.com/zufang/4714551069680654x.shtml",
  "detail_url": "https://cz.58.com/zufang/4714551069680654x.shtml",
  "platform": "58同城",
  "city": "cz",
  "region": "wujin",
  "source_page": "https://cz.zf.58.com/wujin/",
  "data_quality": "current_public_list_page_candidate",
  "detail_verification": "not_attempted"
}
```

房天下结果使用相同的 `price` / `monthly_rent_cny` / `area_sqm` 字段，但 `detail_url` 为空，`url` 和 `source_page` 指向列表页；不要把列表页链接当作单条房源详情链接。

## 依赖

| 依赖 | 用途 | 安装 |
|------|------|------|
| `requests` | HTTP 请求 | 沙箱已预装 |
| `beautifulsoup4` | HTML 解析 | 沙箱已预装 |
| `playwright` | 58同城浏览器渲染（必须） | `pip install playwright` |

> **重要**: 58同城 `zf.58.com` 子域名需要浏览器渲染（Playwright），普通 HTTP 请求通常会被反爬拦截。
> 首次使用需安装 Playwright: `pip install playwright`
> 沙箱启动时需确保 Chrome 可用: `playwright install chromium` 或使用系统 Chrome。

## 注意事项

1. 58同城只读取公开列表页；遇到验证码、登录或访问拦截就停止，不做绕过
2. 默认每次请求后等待 8 秒；不要并发请求或连续翻页
3. 成功时保存 HTML 快照，便于审计和离线解析；快照不能冒充实时结果
4. 结果包含详情 URL，但详情页是否仍在架需要用户在原平台核验
5. 房天下默认使用 `requests` 直接请求；通过人工验证保存会话后可用 Playwright 会话复用
6. 价格过滤：100-50000 元/月之间

## 会话和降级

`--login` 会打开有头浏览器，用户在 58 页面完成一次验证后脚本自动检测页面恢复并保存 Playwright storage state，不需要复制 Cookie 或回终端按 Enter。会话文件包含敏感登录状态，只能放在本机私有目录，不要提交到 Git 或发送给其他人。

正常采集通过 `--session` 显式加载该文件；脚本不会打印或解析 Cookie 内容。会话过期或仍被拦截时，`--status` 会返回 `blocked`，上层 Agent 应停止重试并切换房天下/安居客等来源，或提示用户重新运行 `--login`。

租房 Agent 还提供统一的 `human_verify_rental_platform` 工具：仅当本轮搜索明确报告
某个平台列表页 `blocked` 时调用，并把 `platform` 设为 `58同城`、`安居客` 或 `房天下`。
工具打开该平台的可见浏览器，等待用户完成官方验证后保存独立的 Storage State，会话分别
位于 `backend/runtime/sessions/58|anjuke|fang/`；成功后上层只重试原搜索一次。如果列表已
成功而详情页被单独限流，不应重复弹窗，应停止详情访问并保留 `blocked` 状态。

## 结果可信度

- `current_public_list_page_candidate`：抓取时公开列表页显示的候选，不代表现在仍可租。
- `detail_verification: not_attempted`：未访问详情页，联系方式、房东身份、押付条件和实际可租状态都未确认。
- 用户点击 `detail_url` 回到原平台核验后，Agent 才能把该条标记为“用户已核验”；不能自动声称“已确认可租”。

## 渠道状态

- 已接入：58同城、安居客、房天下。
- 暂不接入自动采集：贝壳/链家（入口可能跳转或网络受限）、我爱我家（公开入口跳登录）、自如（返回前端空壳）。这些平台可以通过 `probe_platforms.py` 重新探测，但不应把探测成功误认为已拿到房源数据。
- 稳定扩大覆盖的优先路径是获得平台/经纪机构授权数据，或接入长租公寓的公开库存接口；不使用验证码绕过、代理轮换或私有接口逆向。

"""基于本地 rental-scraper 技能的控制台租房 Agent。

--live 才会访问真实网络；--offline 必须显式指定，仅用于可重复的夹具测试。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .batch_detail_tool import batch_fetch_listing_details, fetch_detail_batch
from .candidate_search import search_rental_candidates
from .config import SKILL_PATH, build_rental_agent
from .human_verification_tool import human_verify_rental_platform
from .location_tool import resolve_target_place
from .playwright_browser_tool import playwright_browser

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

def run_smoke() -> int:
    """在不调用模型和网络的情况下走通完整数据链路。"""
    _set_mode(False)
    candidates = json.loads(search_rental_candidates.invoke({
        "city": "cz", "keyword": "示例园区", "area": "wujin", "max_results": 10,
    }))
    urls = candidates["detail_urls"]
    result = fetch_detail_batch(urls, live=False, max_urls=10)
    print(json.dumps({
        "mode": "offline_fixture",
        "warning": "仅用于回归测试，不代表实时房源，也不会访问网络或调用大模型。",
        "candidate_count": candidates["listing_count"],
        "detail_url_count": len(urls),
        "detail_status": result["status"],
        "detail_counts": result["counts"],
        "artifact_saved": result["artifact_saved"],
        "facts_preview": [
            {"url": item.get("url"), "status": item.get("status"), "facts": item.get("facts", {})}
            for item in result["results"]
        ],
    }, ensure_ascii=False, indent=2))
    return 0


def build_agent(*, checkpointer=None, state_schema=None, extra_tools=()):
    return build_rental_agent(
        [*agent_tools(), *extra_tools], checkpointer=checkpointer, state_schema=state_schema
    )


def agent_tools():
    """按稳定顺序返回向租房 Agent 暴露的固定工具。"""
    return [
        resolve_target_place,
        search_rental_candidates,
        batch_fetch_listing_details,
        human_verify_rental_platform,
        playwright_browser,
    ]


def _set_mode(live: bool) -> None:
    os.environ["RENTAL_DEMO_MODE"] = "live" if live else "offline"
    # 地图访问也必须显式选择：离线模式绝不访问高德，
    # 即使 backend/.env 中存在密钥。
    os.environ["MAP_PROVIDER"] = "amap" if live else "fake"


def _message_text(message) -> str:
    content = getattr(message, "content", message)
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2)


def _invoke(agent, messages):
    try:
        return agent.invoke({"messages": messages})
    except Exception as exc:
        raise RuntimeError(f"模型调用失败（请检查 API Key、Base URL 和网络）: {exc}") from exc


def run_chat(prompt: str, live: bool) -> int:
    _set_mode(live)
    agent = build_agent()
    print("[模式] 真实联网采集" if live else "[模式] 离线夹具回归", file=sys.stderr)
    print("[Agent] 正在搜索并整理房源，请等待平台状态和结果...", file=sys.stderr)
    try:
        result = _invoke(agent, [{"role": "user", "content": prompt}])
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    final_message = result["messages"][-1]
    print(_message_text(final_message))
    return 0


def run_interactive(live: bool) -> int:
    """保持一个 Agent 会话，以便用户逐步完善搜索条件。"""
    _set_mode(live)
    agent = build_agent()
    messages = []
    print("[模式] 真实联网采集" if live else "[模式] 离线夹具回归")
    print("租房 Agent 已就绪。输入 exit/quit 结束。每次搜索尽量包含城市、区域和小区或地标。")
    while True:
        try:
            prompt = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not prompt:
            continue
        if prompt.lower() in {"exit", "quit", ":q"}:
            break
        messages.append({"role": "user", "content": prompt})
        print("[Agent] 正在调用房源 skill 并整理结果...", file=sys.stderr)
        try:
            result = _invoke(agent, messages)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            continue
        final_message = result["messages"][-1]
        # 保留会话中的工具调用消息，让后续问题可以引用上次搜索，
        # 不必重复抓取。
        messages = result["messages"]
        print(f"\nAgent> {_message_text(final_message)}")
    return 0


def run_build_only() -> int:
    agent = build_agent()
    print(json.dumps({
        "status": "ok",
        "agent_name": "rental-research-agent",
        "skill_source": str(SKILL_PATH),
        "tools": [getattr(item, "name", str(item)) for item in agent_tools()],
        "message": "租房 Agent 图已构建；未调用模型、未访问网络。",
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="租房 Agent 搜索控制台")
    parser.add_argument("--smoke", action="store_true", help="离线验证工具链，不调用模型")
    parser.add_argument("--build-only", action="store_true", help="只构建租房 Agent 图，不调用模型")
    parser.add_argument("--chat", metavar="PROMPT", help="启动 DeepAgent 并发送一条消息")
    parser.add_argument("--interactive", action="store_true", help="启动持续控制台对话")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="使用真实平台公开页面（会访问网络）")
    mode.add_argument("--offline", action="store_true", help="使用离线夹具，仅用于回归测试")
    args = parser.parse_args()
    if args.smoke:
        return run_smoke()
    if args.build_only:
        return run_build_only()
    if args.chat and (args.live or args.offline):
        return run_chat(args.chat, args.live)
    if args.interactive and (args.live or args.offline):
        return run_interactive(args.live)
    parser.error("请指定 --smoke，或使用 --live/--offline 配合 --chat/--interactive")
    return 2


if __name__ == "__main__":
    sys.exit(main())

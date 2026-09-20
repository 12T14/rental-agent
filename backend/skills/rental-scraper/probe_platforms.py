#!/usr/bin/env python3
"""探测公开租房入口，不登录也不绕过访问控制。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup
from platform_pages import is_verification_page
from scrape_58 import _make_url as url_58
from scrape_anjuke import _make_url as url_anjuke
from scrape_fang import _make_url as url_fang

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def _pages(city: str, area: str, keyword: str) -> dict[str, str]:
    return {
        "58同城": url_58(city, area, keyword),
        "安居客": url_anjuke(city, area, keyword),
        "房天下": url_fang(city, area, keyword),
        "贝壳": f"https://{city}.ke.com/zufang/{area.strip('/')}/",
        "链家": f"https://{city}.lianjia.com/zufang/{area.strip('/')}/",
        "我爱我家": f"https://{city}.5i5j.com/zufang/{area.strip('/')}/",
        "自如": "https://www.ziroom.com/z/nl/z2.html",
    }


def _classify(response: requests.Response, body_text: str) -> str:
    prefix = body_text[:5000]
    if response.status_code in (401, 403, 429) or is_verification_page(response.text, response.url):
        return "login_or_verification"
    if response.status_code >= 400:
        return "http_error"
    if any(token in response.url for token in ("login", "verify", "antibot")):
        return "login_or_verification"
    if any(token in prefix for token in ("请输入验证码", "人机验证", "访问过于频繁", "安全验证")):
        return "login_or_verification"
    if len(body_text) < 300:
        return "empty_or_frontend_shell"
    return "page_reachable_needs_parser"


def probe(city: str, area: str = "", keyword: str = "") -> dict:
    results = {}
    for platform, url in _pages(city, area, keyword).items():
        try:
            response = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
            response.encoding = response.apparent_encoding or "utf-8"
            soup = BeautifulSoup(response.text, "lxml")
            text = soup.get_text(" ", strip=True)
            title = soup.title.get_text(" ", strip=True) if soup.title else ""
            results[platform] = {
                "status": _classify(response, text),
                "status_code": response.status_code,
                "url": url,
                "final_url": response.url,
                "title": title[:120],
                "content_chars": len(response.text),
            }
        except requests.RequestException as exc:
            results[platform] = {"status": "network_error", "url": url, "message": str(exc)}
    return {
        "schema_version": "1.0",
        "retrieved_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "query": {"city": city, "area": area, "keyword": keyword},
        "platforms": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="探测租房平台公开入口可用性")
    parser.add_argument("--city", default="cz")
    parser.add_argument("--area", default="wujin")
    parser.add_argument("--keyword", default="")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    result = probe(args.city, args.area, args.keyword)
    if args.output:
        from pathlib import Path
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

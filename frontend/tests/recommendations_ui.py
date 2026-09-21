"""Offline browser smoke test against a running Vite dev server.

Run: .venv/Scripts/python.exe frontend/tests/recommendations_ui.py
Uses mocked API + map SDK, never real listings, credentials or model requests.
"""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright, expect


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "backend" / "artifacts" / "ui-recommendations"
ORIGIN = "http://127.0.0.1:5191"

MAP_STUB = """
class Pixel { constructor(x,y) {this.x=x;this.y=y} getX(){return this.x} getY(){return this.y} }
class Marker {
  constructor(opts){this.opts=opts; this.el=opts.content}
}
class Map {
  constructor(host){this.host=host;this.events={};this.scale=1;
    host.style.background='#e6efe9';host.dataset.testMap='offline';
    const note=document.createElement('small');note.textContent='离线界面测试底图（非真实地理分布）';
    Object.assign(note.style,{position:'absolute',top:'6px',left:'8px',fontSize:'9px'});
    host.append(note);window.__testMap=this;}
  lngLatToContainer([x,y]) {return new Pixel(30+(x-119)*10000*this.scale,60+(y-31)*10000*this.scale)}
  add(markers){for(const m of markers){if(!m.el)continue; const p=this.lngLatToContainer(m.opts.position);
    Object.assign(m.el.style,{position:'absolute',left:p.x+'px',top:p.y+'px'});
    this.host.append(m.el);}}
  remove(markers){for(const m of markers)m.el?.remove();}
  setFitView(){}
  setZoomAndCenter(){}
  on(name,fn){this.events[name]=fn}
  off(name){delete this.events[name]}
  resize(){this.events.resize?.()}
  destroy(){this.host.innerHTML=''}
}
const api={Map,Marker,Pixel,getConfig:()=>({})};
export default {load:async()=>api};
"""


def snapshot():
    listings = []
    for index in range(1, 31):
        # #8 and #25 intentionally share the same point. Five have no position.
        position_index = 8 if index == 25 else index
        col, row = (position_index - 1) % 5, (position_index - 1) // 5
        item = {
            "id": f"l{index}", "displayNumber": index, "platform": "58同城", "platformKey": "58",
            "title": f"测试房源 {index} · 一室一厅", "community": f"测试小区 {index}",
            "rent": 1000 + index * 10, "room": "1室1厅", "area": 35,
            "detailUrl": f"https://nj.58.com/zufang/{index}.shtml",
            "filterStatus": "excluded" if index == 30 else "passed",
            "detailStatus": "ok" if index == 8 else "pending",
            "recommendation": {"reason": "预算内，适合比较", "caveat": "水电仍待核验"} if index == 8 else None,
        }
        if index <= 25:
            item.update(lng=119 + col * .0047, lat=31 + row * .0047, geocodeStatus="ok")
        listings.append(item)
    return {
        "session_id": "ui-test", "title": "全部候选与 Agent 推荐", "status": "completed",
        "messages": [{"id": "a", "role": "assistant", "text":
                      "共找到 30 套候选，地图已定位 25 套。\n推荐房源 #8：预算内，详情已读取。\n"
                      "待核验房源 #25：费用信息仍待核验。\n其他候选保留在地图和右侧列表。"}],
        "listings": listings, "search": {"status": "completed"}, "platforms": [],
        "storage": {"mode": "memory"}, "pending": None, "location": None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", help="Use an installed browser, e.g. chrome or msedge")
    args = parser.parse_args()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    data = snapshot()
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel=args.channel)
        try:
            context = browser.new_context(viewport={"width": 1440, "height": 1080})

            def route_request(route):
                url = route.request.url
                if "@amap_amap-jsapi-loader" in url:
                    route.fulfill(status=200, content_type="application/javascript", body=MAP_STUB)
                elif url.startswith(ORIGIN + "/api/"):
                    result = {"sessions": [{"session_id": "ui-test", "title": data["title"]}], "storage": data["storage"]} if "?" in url else data
                    route.fulfill(status=200, content_type="application/json", body=json.dumps(result))
                elif url.startswith(ORIGIN + "/src/components/MapPanel.vue") and "type=" not in url:
                    response = route.fetch()
                    # Enable the SDK test double even on a clean machine without keys.
                    body = response.text().replace('const mapConfigured = Boolean(jsapiKey && securityCode)', 'const mapConfigured = true')
                    route.fulfill(response=response, body=body)
                elif url.startswith(ORIGIN):
                    route.continue_()
                else:
                    route.abort()

            context.route("**/*", route_request)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(ORIGIN)
            expect(page.locator(".context-listing-list .map-list-row")).to_have_count(20)
            expect(page.locator(".candidate-pool-note")).to_contain_text("全部候选 30 套")
            expect(page.locator(".candidate-pool-note")).to_contain_text("初筛通过 29 套")
            expect(page.locator(".candidate-pool-note")).to_contain_text("Agent 精选 1 套")
            expect(page.locator(".candidate-pool-note")).to_contain_text("待核验 28 套")
            expect(page.locator(".map-summary")).to_contain_text("25 套已定位")
            expect(page.locator(".map-summary")).to_contain_text("5 套位置待核验")
            expect(page.locator(".amap-listing-marker.is-cluster")).to_have_text("2套")
            # #25 must already be on the map even though list page one ends at #20.
            page.locator(".amap-listing-marker.is-cluster").click()
            expect(page.locator(".map-cluster-list > button")).to_have_count(2)
            page.locator(".map-cluster-list > button").filter(has_text="#25").click()
            expect(page.locator(".drawer-kicker")).to_have_text("房源 #25")
            expect(page.locator(".drawer-panel")).to_contain_text("详情待核验")
            expect(page.locator(".recommendation-summary")).to_have_count(0)
            page.locator(".close-button").click()
            page.get_by_role("button", name="关闭聚合房源").click()
            page.screenshot(path=str(OUTPUT / "desktop.png"), full_page=True)
            before = page.locator(".amap-listing-marker").count()
            page.locator(".context-listing-list .listing-load-more").click()
            expect(page.locator(".context-listing-list .map-list-row")).to_have_count(30)
            assert page.locator(".amap-listing-marker").count() == before
            expect(page.locator(".context-listing-list .map-list-row.excluded")).to_have_count(1)
            page.reload()
            expect(page.locator(".map-summary")).to_contain_text("25 套已定位")
            expect(page.locator(".amap-listing-marker.is-cluster")).to_have_text("2套")
            # Zoom changes never lose identical-coordinate listings.
            page.evaluate("window.__testMap.scale=2; window.__testMap.events.zoomend()")
            expect(page.locator(".amap-listing-marker.is-cluster")).to_have_text("2套")
            page.set_viewport_size({"width": 390, "height": 844})
            page.locator(".context-column").scroll_into_view_if_needed()
            page.screenshot(path=str(OUTPUT / "mobile.png"), full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "mobile horizontal overflow"
            assert not errors, errors
            print(json.dumps({"status": "passed", "candidates": 30, "located": 25,
                              "recommendations": 1, "errors": errors, "screenshots": str(OUTPUT)}))
        finally:
            browser.close()


if __name__ == "__main__":
    main()

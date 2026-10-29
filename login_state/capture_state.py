"""从已登录的持久化 profile 导出登录态（storage_state），供抓取层复用。

用法：
    py -3.10 login_state/capture_state.py --profile <profile目录> --url https://wwlhc.com/ --out login_state/wwlhc.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--check", default="退出", help="页面上用于判断已登录的文本")
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(args.profile, headless=True)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(args.url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(2000)
        text = page.inner_text("body")
        state = context.storage_state()
        context.close()

    cookies = [c for c in state.get("cookies", []) if "wwlhc" in c.get("domain", "")]
    print("页面标题:", page.title() if False else "(已关闭)")
    print("登录标志命中:", args.check in text, "| cookie 数:", len(state.get("cookies", [])),
          "| wwlhc cookie:", len(cookies))
    out.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写入", out, out.stat().st_size, "字节")
    return 0 if args.check in text and cookies else 2


if __name__ == "__main__":
    raise SystemExit(main())

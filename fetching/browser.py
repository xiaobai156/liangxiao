from __future__ import annotations

import re
import time

import requests


def render_browser_text(url: str, timeout: int = 25) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise requests.RequestException(f"浏览器渲染不可用：{exc}") from exc
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent="Mozilla/5.0")
                page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                try:
                    page.wait_for_load_state("networkidle", timeout=min(timeout * 1000, 5000))
                except Exception:
                    pass
                deadline = time.monotonic() + min(timeout, 5)
                previous = ""
                content = page.content()
                while time.monotonic() < deadline:
                    has_target_marker = bool(
                        re.search(r"\d{3}\s*期", content)
                        and any(marker in content for marker in ("二肖", "两肖", "②肖", "2肖", "２肖"))
                    )
                    if has_target_marker and content == previous:
                        break
                    previous = content
                    page.wait_for_timeout(250)
                    content = page.content()
                return content
            finally:
                browser.close()
    except Exception as exc:
        raise requests.RequestException(f"浏览器渲染失败：{exc}") from exc

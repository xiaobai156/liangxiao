"""打开 杀两肖-V2 自己的持久化浏览器 profile，做一次性人工登录。

用途：洪福惠阳两肖所在论坛（wwlhc.com）正文需登录才可见。本脚本给
杀两肖 建一套**独立会话**（profile 目录 `杀两肖-V2/browser-profile`），
与 灵蛇九肖 的 browser-profile 互不影响，两边可同时运行。

用法：
    py -3.10 login_state/login_profile.py
    py -3.10 login_state/login_profile.py --url https://wwlhc.com/
    py -3.10 login_state/login_profile.py --check-only   # 只检查现有会话是否还有效

登录完成后回到命令行按 Enter，脚本会校验并报告会话状态。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "browser-profile"
DEFAULT_URL = "https://wwlhc.com/"
LOGIN_MARKERS = ("退出",)


def check_session(page) -> bool:
    content = page.content()
    return any(marker in content for marker in LOGIN_MARKERS)


def main() -> int:
    parser = argparse.ArgumentParser(description="杀两肖独立登录态")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--profile", default=str(PROFILE))
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    profile = Path(args.profile)
    profile.mkdir(parents=True, exist_ok=True)
    print(f"profile 目录：{profile}")

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile), headless=args.check_only)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)
            logged_in = check_session(page)
            if args.check_only:
                print("会话状态：", "已登录" if logged_in else "未登录或已失效")
                return 0 if logged_in else 3
            if logged_in:
                print("当前已是登录状态；如需换号请先在页面里退出再登录。")
            else:
                print("请在弹出的浏览器里完成登录（账号密码 / 扫码均可）。")
            input("登录完成后回到这里按 Enter 保存会话...")
            page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)
            if check_session(page):
                print("登录成功，会话已写入该 profile。")
                return 0
            print("仍未检测到登录状态，请重跑本脚本再试。")
            return 3
        finally:
            context.close()


if __name__ == "__main__":
    sys.exit(main())

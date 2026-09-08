from __future__ import annotations

from collections.abc import Callable
import queue
import threading
import time

import requests

try:
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover - exercised through the runtime error path
    sync_playwright: Callable[[], object] | None = None


RenderCommand = tuple[str, str, int, queue.Queue[tuple[bool, object]] | None]


def _render_page(browser_context: object, url: str, timeout: int) -> str:
    page = browser_context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        try:
            page.wait_for_load_state("networkidle", timeout=min(timeout * 1000, 5000))
        except Exception:
            pass
        deadline = time.monotonic() + min(timeout, 5)
        previous = ""
        content = page.content()
        while time.monotonic() < deadline:
            if content == previous:
                break
            previous = content
            page.wait_for_timeout(250)
            content = page.content()
        return content
    finally:
        page.close()


def _render_error(exc: BaseException) -> requests.RequestException:
    if isinstance(exc, requests.RequestException):
        return exc
    return requests.RequestException(f"浏览器渲染失败：{exc}")


class PlaywrightRenderer:
    """A Playwright sync runtime confined to one dedicated thread."""

    def __init__(self) -> None:
        self._commands: queue.Queue[RenderCommand] = queue.Queue()
        self._state_lock = threading.Lock()
        self._closed = False
        self._failure: requests.RequestException | None = None
        self._thread = threading.Thread(
            target=self._run,
            name="playwright-renderer",
            daemon=True,
        )
        self._thread.start()

    def __call__(self, url: str, timeout: int) -> str:
        result: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)
        with self._state_lock:
            if self._closed:
                raise requests.RequestException("浏览器渲染器已关闭")
            if self._failure is not None:
                raise self._failure
            if not self._thread.is_alive():
                raise requests.RequestException("浏览器渲染器线程已退出")
            self._commands.put(("render", url, timeout, result))
        ok, value = result.get()
        if not ok:
            raise value
        return str(value)

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            if self._thread.is_alive():
                self._commands.put(("close", "", 0, None))
        if threading.current_thread() is not self._thread:
            self._thread.join()

    def _run(self) -> None:
        playwright: object | None = None
        browser: object | None = None
        browser_context: object | None = None
        try:
            while True:
                command, url, timeout, result = self._commands.get()
                if command == "close":
                    break
                if browser_context is None:
                    try:
                        if sync_playwright is None:
                            raise requests.RequestException("浏览器渲染不可用：Playwright未安装")
                        playwright = sync_playwright().start()
                        browser = playwright.chromium.launch(headless=True)
                        browser_context = browser.new_context(user_agent="Mozilla/5.0")
                    except Exception as exc:
                        error = _render_error(exc)
                        with self._state_lock:
                            self._failure = error
                        if result is not None:
                            result.put((False, error))
                        while True:
                            try:
                                pending_command, _pending_url, _pending_timeout, pending_result = (
                                    self._commands.get_nowait()
                                )
                            except queue.Empty:
                                break
                            if pending_command == "render" and pending_result is not None:
                                pending_result.put((False, error))
                        break
                try:
                    rendered = _render_page(browser_context, url, timeout)
                except Exception as exc:
                    error = _render_error(exc)
                    if result is not None:
                        result.put((False, error))
                    continue
                if result is not None:
                    result.put((True, rendered))
        finally:
            if browser_context is not None:
                try:
                    browser_context.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            if playwright is not None:
                try:
                    playwright.stop()
                except Exception:
                    pass


def render_browser_text(url: str, timeout: int = 25) -> str:
    renderer = PlaywrightRenderer()
    try:
        return renderer(url, timeout)
    finally:
        renderer.close()

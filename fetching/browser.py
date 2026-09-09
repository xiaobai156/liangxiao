from __future__ import annotations

from collections.abc import Callable
import queue
import threading
import time

import requests

from fetching.urls import FetchedText, SourceBoundaryError, validate_redirect

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - the optional renderer is not installed
    sync_playwright: Callable[[], object] | None = None


RENDER_WAIT_GRACE_SECONDS = 5.0
SHUTDOWN_TIMEOUT_SECONDS = 3.0
RenderCommand = tuple[str, str, int, queue.Queue[tuple[bool, object]] | None, tuple[str, ...]]


def _render_page(browser_context: object, url: str, timeout: int,
                 allowed_origins: tuple[str, ...] = ()) -> str:
    page = browser_context.new_page()
    boundary_errors: list[SourceBoundaryError] = []
    visited: list[str] = [url]

    def guard_navigation(route, request):
        if request.is_navigation_request() and request.frame == page.main_frame:
            try:
                validate_redirect(url, request.url, allowed_origins)
            except SourceBoundaryError as exc:
                boundary_errors.append(exc)
                route.abort()
                return
            if visited[-1] != request.url:
                visited.append(request.url)
        route.continue_()

    try:
        page.route("**/*", guard_navigation)
        response = page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
        if response is not None and response.status >= 400:
            raise requests.RequestException(f"浏览器HTTP {response.status}: {url}")
        validate_redirect(url, page.url, allowed_origins)
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
        if boundary_errors:
            raise boundary_errors[0]
        validate_redirect(url, page.url, allowed_origins)
        return FetchedText(content, url, page.url, tuple(visited))
    finally:
        page.close()


def _render_error(exc: BaseException) -> requests.RequestException:
    if isinstance(exc, requests.RequestException):
        return exc
    return requests.RequestException(f"浏览器渲染失败：{exc}")


def _deliver(result, ok: bool, value: object) -> None:
    if result is not None:
        try:
            result.put_nowait((ok, value))
        except queue.Full:
            pass


class PlaywrightRenderer:
    """Confine the sync runtime to one worker, with bounded caller/close waits."""

    def __init__(self) -> None:
        self._commands: queue.Queue[RenderCommand] = queue.Queue()
        self._state_lock = threading.Lock()
        self._closed = False
        self._failure: requests.RequestException | None = None
        self._thread = threading.Thread(target=self._run, name="playwright-renderer", daemon=True)
        self._thread.start()

    def __call__(self, url: str, timeout: int, *, allowed_origins: tuple[str, ...] = ()) -> str:
        result: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)
        with self._state_lock:
            if self._closed:
                raise requests.RequestException("浏览器渲染器已关闭")
            if self._failure is not None:
                raise self._failure
            if not self._thread.is_alive():
                raise requests.RequestException("浏览器渲染器线程已退出")
            self._commands.put(("render", url, timeout, result, allowed_origins))
        try:
            ok, value = result.get(timeout=max(0, timeout) + RENDER_WAIT_GRACE_SECONDS)
        except queue.Empty as exc:
            error = requests.Timeout(f"浏览器排队或渲染超时：{url}")
            with self._state_lock:
                self._failure = error
                self._fail_pending(error)
                self._commands.put(("close", "", 0, None, ()))
            raise error from exc
        if not ok:
            raise value
        return value  # Preserve FetchedText.final_url rather than converting to str.

    def _fail_pending(self, error: requests.RequestException) -> None:
        while True:
            try:
                command, _url, _timeout, result, _origins = self._commands.get_nowait()
            except queue.Empty:
                return
            if command == "render":
                _deliver(result, False, error)

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            if self._thread.is_alive():
                self._commands.put(("close", "", 0, None, ()))
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=SHUTDOWN_TIMEOUT_SECONDS)

    def _run(self) -> None:
        playwright = browser = browser_context = None
        active_result = None
        try:
            while True:
                command, url, timeout, active_result, origins = self._commands.get()
                if command == "close":
                    break
                if browser_context is None:
                    if sync_playwright is None:
                        raise requests.RequestException("浏览器渲染不可用：Playwright未安装")
                    playwright = sync_playwright().start()
                    browser = playwright.chromium.launch(headless=True)
                    browser_context = browser.new_context(user_agent="Mozilla/5.0")
                try:
                    rendered = (_render_page(browser_context, url, timeout, origins)
                                if origins else _render_page(browser_context, url, timeout))
                except Exception as exc:
                    _deliver(active_result, False, _render_error(exc))
                else:
                    _deliver(active_result, True, rendered)
                active_result = None
        except BaseException as exc:
            error = _render_error(exc)
            with self._state_lock:
                self._failure = error
                _deliver(active_result, False, error)
                self._fail_pending(error)
        finally:
            with self._state_lock:
                self._fail_pending(self._failure or requests.RequestException("浏览器渲染器已退出"))
            for resource, method in ((browser_context, "close"), (browser, "close"), (playwright, "stop")):
                if resource is not None:
                    try:
                        getattr(resource, method)()
                    except Exception:
                        pass


def render_browser_text(url: str, timeout: int = 25) -> str:
    renderer = PlaywrightRenderer()
    try:
        return renderer(url, timeout)
    finally:
        renderer.close()

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable
from threading import Lock, current_thread, local, main_thread

import requests
import urllib3


MAX_FETCH_ATTEMPTS = 2
MAX_INCOMPLETE_READ_ATTEMPTS = 3
CURL_RECEIVE_RETRY_CODES = {18, 56}
HTTP_SESSION = requests.Session()
THREAD_LOCAL = local()
TextFetcher = Callable[[str, int], str]
Renderer = Callable[[str, int], str]


def _decode_best_text(content: bytes, encodings: tuple[str | None, ...]) -> str:
    best_text = ""
    best_score = -10**9
    seen_encodings: set[str] = set()
    for encoding in encodings:
        if not encoding or encoding in seen_encodings:
            continue
        seen_encodings.add(encoding)
        try:
            text = content.decode(encoding, errors="replace")
        except LookupError:
            continue
        score = len(re.findall(r"[\u4e00-\u9fff]", text)) + text.count("期") * 3 - text.count("\ufffd") * 20
        if score > best_score:
            best_text = text
            best_score = score
    return best_text


def http_get(url: str, timeout: int = 25) -> requests.Response:
    if current_thread() is main_thread():
        session = HTTP_SESSION
    else:
        session = getattr(THREAD_LOCAL, "session", None)
        if session is None:
            session = requests.Session()
            THREAD_LOCAL.session = session
    return session.get(url, timeout=timeout, verify=False, headers={"User-Agent": "Mozilla/5.0"})


def classified_request_error(exc: requests.RequestException, url: str) -> requests.RequestException:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(exc, requests.exceptions.HTTPError) and status is not None:
        error = requests.exceptions.HTTPError(f"HTTP {status}: {url}")
        error.response = response
        return error
    if isinstance(exc, requests.exceptions.SSLError):
        return requests.exceptions.SSLError(f"SSL 抓取失败：{url}：{exc}")
    if isinstance(exc, requests.exceptions.Timeout):
        return requests.exceptions.Timeout(f"超时抓取失败：{url}：{exc}")
    return exc


def should_retry_request(exc: requests.RequestException) -> bool:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return not (isinstance(exc, requests.exceptions.HTTPError) and status in {400, 401, 403, 404, 410})


def decode_response_content(response: requests.Response) -> str:
    apparent_encoding = getattr(response, "apparent_encoding", None)
    # These sites serve ASCII Base64 JavaScript without a charset.  Charset
    # detectors can misclassify the many "+...-" sequences as UTF-7 and turn
    # a valid script into Chinese-looking mojibake.  UTF-7 is never an
    # authorized site encoding here, so do not let that heuristic outrank the
    # real UTF-8/Chinese encodings.
    if str(apparent_encoding or "").lower().replace("-", "") == "utf7":
        apparent_encoding = None
    return _decode_best_text(
        response.content,
        (response.encoding, apparent_encoding, "utf-8", "gb18030", "gbk"),
    )


def fetch_curl_text(url: str, timeout: int = 25) -> str:
    completed: subprocess.CompletedProcess[bytes] | None = None
    for attempt in range(2):
        try:
            completed = subprocess.run(
                [
                    "curl.exe",
                    "-k",
                    "-L",
                    "--fail",
                    "--http1.1",
                    "--compressed",
                    "--max-time",
                    str(timeout),
                    "-A",
                    "Mozilla/5.0",
                    url,
                ],
                capture_output=True,
                check=True,
                timeout=timeout + 5,
            )
            break
        except subprocess.CalledProcessError as exc:
            if attempt == 0 and exc.returncode in CURL_RECEIVE_RETRY_CODES:
                time.sleep(0.25)
                continue
            raise requests.RequestException(f"curl SSL兼容抓取失败：{exc}") from exc
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            raise requests.RequestException(f"curl SSL兼容抓取失败：{exc}") from exc
    if completed is None:
        raise requests.RequestException(f"curl SSL兼容抓取失败：{url}")
    return _decode_best_text(completed.stdout, ("utf-8", "gb18030", "gbk"))


def fetch_text(url: str, timeout: int = 25) -> str:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    last_error: requests.RequestException | None = None
    response: requests.Response | None = None
    had_ssl_error = False
    had_incomplete_read = False
    for attempt in range(MAX_INCOMPLETE_READ_ATTEMPTS):
        try:
            response = http_get(url, timeout)
            response.raise_for_status()
            break
        except requests.RequestException as exc:
            had_ssl_error = had_ssl_error or isinstance(exc, requests.exceptions.SSLError)
            had_incomplete_read = had_incomplete_read or isinstance(
                exc, requests.exceptions.ChunkedEncodingError
            )
            last_error = classified_request_error(exc, url)
            if not should_retry_request(exc):
                break
            retry_limit = (
                MAX_INCOMPLETE_READ_ATTEMPTS
                if isinstance(exc, requests.exceptions.ChunkedEncodingError)
                else MAX_FETCH_ATTEMPTS
            )
            if attempt + 1 < retry_limit:
                time.sleep(0.25 * (attempt + 1))
            else:
                break
    if response is None or getattr(response, "status_code", 500) >= 400:
        if had_ssl_error or had_incomplete_read:
            return fetch_curl_text(url, timeout)
        if last_error is None:
            raise requests.RequestException(f"抓取失败：{url}")
        raise last_error
    return decode_response_content(response)


def is_transient_soft_payload(source: str) -> bool:
    lowered = source.strip().lower()
    return not lowered or any(
        marker in lowered
        for marker in ("just a moment", "cf-chl", "checking your browser", "captcha challenge")
    )


class FetchContext:
    def __init__(
        self,
        *,
        text_fetcher: TextFetcher | None = None,
        renderer: Renderer | None = None,
    ) -> None:
        self._text_fetcher = text_fetcher or fetch_text
        self._renderer = renderer
        self._texts: dict[str, str] = {}
        self._errors: dict[str, requests.RequestException] = {}
        self._url_locks: dict[str, Lock] = {}
        self._rendered_texts: dict[str, str] = {}
        self._render_locks: dict[str, Lock] = {}
        self._closed = False
        self._lock = Lock()

    def get_text(self, url: str, timeout: int) -> str:
        with self._lock:
            if url in self._texts:
                return self._texts[url]
            if url in self._errors:
                raise self._errors[url]
            url_lock = self._url_locks.setdefault(url, Lock())
        with url_lock:
            with self._lock:
                if url in self._texts:
                    return self._texts[url]
                if url in self._errors:
                    raise self._errors[url]
            try:
                text = self._text_fetcher(url, timeout)
            except requests.RequestException as exc:
                if not should_retry_request(exc):
                    with self._lock:
                        self._errors[url] = exc
                raise
            if not is_transient_soft_payload(text):
                with self._lock:
                    self._texts[url] = text
            return text

    def get_rendered(self, url: str, timeout: int) -> str:
        with self._lock:
            if url in self._rendered_texts:
                return self._rendered_texts[url]
            if self._closed:
                raise requests.RequestException("抓取上下文已关闭")
            if self._renderer is None:
                from fetching.browser import PlaywrightRenderer

                self._renderer = PlaywrightRenderer()
            render_lock = self._render_locks.setdefault(url, Lock())
            renderer = self._renderer
        with render_lock:
            with self._lock:
                if url in self._rendered_texts:
                    return self._rendered_texts[url]
                if self._closed:
                    raise requests.RequestException("抓取上下文已关闭")
            source = renderer(url, timeout)
            if not is_transient_soft_payload(source):
                with self._lock:
                    self._rendered_texts[url] = source
            return source

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        renderer = self._renderer
        if renderer is None:
            return
        close = getattr(renderer, "close", None)
        if callable(close):
            close()

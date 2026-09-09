from __future__ import annotations

import re
import shutil
from urllib.parse import urljoin
import subprocess
import time
from collections.abc import Callable
from threading import Lock, current_thread, local, main_thread

import requests
from fetching.urls import FetchedText, SourceBoundaryError, validate_redirect


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


def http_get(
    url: str, timeout: int = 25, *, allowed_origins: tuple[str, ...] = ()
) -> requests.Response:
    if current_thread() is main_thread():
        session = HTTP_SESSION
    else:
        session = getattr(THREAD_LOCAL, "session", None)
        if session is None:
            session = requests.Session()
            THREAD_LOCAL.session = session
    current = url
    visited: list[str] = []
    for _ in range(4):
        validate_redirect(url, current, allowed_origins)
        if current in visited:
            raise SourceBoundaryError("来源重定向出现循环")
        visited.append(current)
        response = session.get(current, timeout=timeout, verify=True,
                               allow_redirects=False, headers={"User-Agent": "Mozilla/5.0"})
        final = getattr(response, "url", None) or current
        validate_redirect(url, final, allowed_origins)
        if getattr(response, "status_code", 200) not in {301, 302, 303, 307, 308}:
            response.audit_redirect_chain = tuple(visited)
            return response
        location = response.headers.get("Location", "")
        response.close()
        if not location:
            raise SourceBoundaryError("重定向缺少Location")
        current = urljoin(current, location)
    raise SourceBoundaryError("来源重定向超过3次")


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
    if isinstance(exc, (SourceBoundaryError, requests.exceptions.SSLError)):
        return False
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


CURL_META_MARKER = b"\n__LIANGXIAO_HTTP_META__\n"


def fetch_curl_text(
    url: str, timeout: int = 25, *, insecure: bool = False,
    allowed_origins: tuple[str, ...] = (),
) -> str:
    """Legacy transport is explicit; never follow an unchecked redirect."""
    executable = shutil.which("curl.exe") or shutil.which("curl") or "curl.exe"
    current = url
    visited: list[str] = []
    for _ in range(4):
        validate_redirect(url, current, allowed_origins)
        if current in visited:
            raise SourceBoundaryError("来源重定向出现循环")
        visited.append(current)
        for attempt in range(2):
            try:
                command = [executable, "--silent", "--show-error", "--http1.1", "--compressed",
                           "--proto", "=http,https", "--max-time", str(timeout),
                           "-A", "Mozilla/5.0", "--write-out",
                           CURL_META_MARKER.decode() + "%{http_code}\n%{url_effective}\n%{redirect_url}"]
                if insecure:
                    command.append("-k")
                command.append(current)
                completed = subprocess.run(command, capture_output=True, check=True, timeout=timeout + 5)
                break
            except subprocess.CalledProcessError as exc:
                if attempt == 0 and exc.returncode in CURL_RECEIVE_RETRY_CODES:
                    time.sleep(0.25)
                    continue
                raise requests.RequestException(f"curl SSL兼容抓取失败：{exc}") from exc
            except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
                raise requests.RequestException(f"curl SSL兼容抓取失败：{exc}") from exc
        try:
            body, meta = completed.stdout.rsplit(CURL_META_MARKER, 1)
            raw_status, raw_final, raw_redirect = meta.decode("utf-8").split("\n", 2)
            status = int(raw_status)
            validate_redirect(url, raw_final, allowed_origins)
        except (ValueError, UnicodeError) as exc:
            raise SourceBoundaryError("curl缺少可信HTTP状态或最终URL") from exc
        if status in {301, 302, 303, 307, 308}:
            if not raw_redirect:
                raise SourceBoundaryError("curl重定向缺少Location")
            current = urljoin(raw_final, raw_redirect)
            continue
        if not 200 <= status < 300:
            response = requests.Response()
            response.status_code = status
            response.url = raw_final
            raise requests.HTTPError(f"HTTP {status}: {raw_final}", response=response)
        return FetchedText(_decode_best_text(body, ("utf-8", "gb18030", "gbk")),
                           url, raw_final, tuple(visited))
    raise SourceBoundaryError("来源重定向超过3次")


def fetch_text(url: str, timeout: int = 25, *, allowed_origins: tuple[str, ...] = ()) -> str:
    last_error: requests.RequestException | None = None
    for attempt in range(MAX_INCOMPLETE_READ_ATTEMPTS):
        try:
            response = (http_get(url, timeout, allowed_origins=allowed_origins)
                        if allowed_origins else http_get(url, timeout))
            response.raise_for_status()
            final = getattr(response, "url", None) or url
            validate_redirect(url, final, allowed_origins)
            return FetchedText(decode_response_content(response), url, final,
                               getattr(response, "audit_redirect_chain", (url,)))
        except requests.RequestException as exc:
            last_error = classified_request_error(exc, url)
            if not should_retry_request(exc):
                break
            retry_limit = (MAX_INCOMPLETE_READ_ATTEMPTS
                           if isinstance(exc, requests.exceptions.ChunkedEncodingError)
                           else MAX_FETCH_ATTEMPTS)
            if attempt + 1 >= retry_limit:
                break
            time.sleep(0.25 * (attempt + 1))
    assert last_error is not None
    # SSL and incomplete reads must not silently disable TLS validation.
    raise last_error


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
        self._texts: dict[tuple[str, tuple[str, ...], bool], str] = {}
        self._errors: dict[tuple[str, tuple[str, ...], bool], requests.RequestException] = {}
        self._url_locks: dict[tuple[str, tuple[str, ...], bool], Lock] = {}
        self._rendered_texts: dict[tuple[str, tuple[str, ...]], str] = {}
        self._render_locks: dict[tuple[str, tuple[str, ...]], Lock] = {}
        self._closed = False
        self._lock = Lock()

    def get_text(self, url: str, timeout: int, *, allowed_origins: tuple[str, ...] = (),
                 legacy_insecure: bool = False) -> str:
        cache_key = (url, allowed_origins, legacy_insecure)
        with self._lock:
            if self._closed:
                raise requests.RequestException("抓取上下文已关闭")
            if cache_key in self._texts:
                return self._texts[cache_key]
            if cache_key in self._errors:
                raise self._errors[cache_key]
            url_lock = self._url_locks.setdefault(cache_key, Lock())
        with url_lock:
            with self._lock:
                if cache_key in self._texts:
                    return self._texts[cache_key]
                if cache_key in self._errors:
                    raise self._errors[cache_key]
            try:
                if self._text_fetcher is fetch_text and legacy_insecure:
                    text = fetch_curl_text(url, timeout, insecure=True, allowed_origins=allowed_origins)
                elif self._text_fetcher is fetch_text and allowed_origins:
                    text = self._text_fetcher(url, timeout, allowed_origins=allowed_origins)
                else:
                    text = self._text_fetcher(url, timeout)
                validate_redirect(url, getattr(text, "final_url", url), allowed_origins)
            except requests.RequestException as exc:
                if not should_retry_request(exc):
                    with self._lock:
                        self._errors[cache_key] = exc
                raise
            if not is_transient_soft_payload(text):
                with self._lock:
                    self._texts[cache_key] = text
            return text

    def get_rendered(self, url: str, timeout: int, *, allowed_origins: tuple[str, ...] = ()) -> str:
        cache_key = (url, allowed_origins)
        with self._lock:
            if self._closed:
                raise requests.RequestException("抓取上下文已关闭")
            if cache_key in self._rendered_texts:
                return self._rendered_texts[cache_key]
            if self._renderer is None:
                from fetching.browser import PlaywrightRenderer

                self._renderer = PlaywrightRenderer()
            render_lock = self._render_locks.setdefault(cache_key, Lock())
            renderer = self._renderer
        with render_lock:
            with self._lock:
                if cache_key in self._rendered_texts:
                    return self._rendered_texts[cache_key]
                if self._closed:
                    raise requests.RequestException("抓取上下文已关闭")
            from fetching.browser import PlaywrightRenderer
            if isinstance(renderer, PlaywrightRenderer) and allowed_origins:
                source = renderer(url, timeout, allowed_origins=allowed_origins)
            else:
                source = renderer(url, timeout)
            validate_redirect(url, getattr(source, "final_url", url), allowed_origins)
            if not is_transient_soft_payload(source):
                with self._lock:
                    self._rendered_texts[cache_key] = source
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


def get_site_text(context: FetchContext, url: str, timeout: int, site) -> str:
    if site.payload == "curl_tls10_page_and_scripts":
        return context.get_text(url, timeout, allowed_origins=site.allowed_redirect_origins,
                                legacy_insecure=True)
    if site.allowed_redirect_origins:
        return context.get_text(url, timeout, allowed_origins=site.allowed_redirect_origins)
    return context.get_text(url, timeout)


def get_site_rendered(context: FetchContext, url: str, timeout: int, site) -> str:
    if site.allowed_redirect_origins:
        return context.get_rendered(url, timeout, allowed_origins=site.allowed_redirect_origins)
    return context.get_rendered(url, timeout)

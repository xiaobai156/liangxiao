from __future__ import annotations

import base64
from collections.abc import Callable
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from domain.errors import ErrorCategory, ScrapeFailure
from domain.models import DOCUMENT_BOUNDARY, DocumentBundle, HistoryResult, PayloadDocument, Record, Result, Site
import fetching.client as fetching_client
import fetching.page as fetching_page
from fetching.client import FetchContext
from fetching.dynamic_article import (
    admin_article_api_url,
    admin_article_id,
    decode_admin_article_record,
    page_has_record_boundary,
)
from fetching.page import (
    collect_page_and_scripts,
    decode_strdecode_blocks,
    fetch_payload,
)
from services import multi_period, recent_history, single_period
from validation.boundaries import (
    document_is_linked,
    expected_record_id,
    linked_document_is_authorized,
    validate_bundle_boundaries,
    validate_document_relationships,
)


class StubRegistry:
    def __init__(self, records_by_source: dict[str, list[Record]]) -> None:
        self.records_by_source = records_by_source
        self.calls: list[tuple[str, int | None]] = []

    def parse(self, source: str | PayloadDocument, site: Site) -> list[Record]:
        text = source.source if isinstance(source, PayloadDocument) else source
        return list(self.records_by_source.get(text, ()))


def make_record(period: int, zodiac: str = "狗蛇", position: int = 0) -> Record:
    return Record(period, zodiac, "开", f"{period:03d}期 {zodiac}", position=position)


def make_article_site(*, pick: str = "top", payload: str = "admin_article_api") -> Site:
    return Site(
        "测试站",
        pick,
        "https://example.test/article/admin/article-1?url=test",
        parser="stub",
        payload=payload,
    )


def article_context(
    rendered: str,
    *,
    raw: str = '<a href="/article/admin/article-1">页面壳</a>',
    renderer: Callable[[str, int], str] | None = None,
) -> tuple[FetchContext, list[str]]:
    api_url = admin_article_api_url(make_article_site().url)
    calls: list[str] = []

    def fetcher(url: str, _timeout: int) -> str:
        calls.append(url)
        if url == api_url:
            response = SimpleNamespace(status_code=404)
            error = requests.HTTPError("not found", response=response)
            raise error
        return raw

    return FetchContext(
        text_fetcher=fetcher,
        renderer=renderer or (lambda _url, _timeout: rendered),
    ), calls


def test_source_has_target_requires_a_parsed_period_or_any_candidate() -> None:
    site = Site("测试", "top", "https://example.test/page", parser="stub")
    record = make_record(209)
    registry = StubRegistry({"target": [record]})

    assert single_period.source_has_target(registry, "target", site, 209)
    assert not single_period.source_has_target(registry, "target", site, 208)
    assert single_period.source_has_target(registry, "target", site, None)
    assert not single_period.source_has_target(registry, "empty", site, None)


@pytest.mark.parametrize(
    ("error", "expected"),
    (
        (
            requests.HTTPError("bad", response=SimpleNamespace(status_code=502)),
            "HTTP 502: https://example.test/page",
        ),
        (
            requests.exceptions.SSLError("certificate"),
            "SSL 抓取失败：https://example.test/page：certificate",
        ),
        (
            requests.exceptions.Timeout("slow"),
            "超时抓取失败：https://example.test/page：slow",
        ),
    ),
)
def test_request_error_classification_preserves_business_category(
    error: requests.RequestException,
    expected: str,
) -> None:
    classified = fetching_client.classified_request_error(error, "https://example.test/page")

    assert str(classified) == expected


def test_http_error_retry_policy_distinguishes_terminal_and_retryable_statuses() -> None:
    for status in (400, 401, 403, 404, 410):
        error = requests.HTTPError(response=SimpleNamespace(status_code=status))
        assert not fetching_client.should_retry_request(error)
    assert fetching_client.should_retry_request(
        requests.HTTPError(response=SimpleNamespace(status_code=503))
    )
    assert fetching_client.should_retry_request(requests.RequestException("temporary"))


def test_http_get_uses_separate_main_and_worker_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int, bool, dict[str, str]]] = []

        def get(self, url: str, *, timeout: int, verify: bool, headers: dict[str, str]) -> object:
            self.calls.append((url, timeout, verify, headers))
            return self

    main_session = FakeSession()
    worker_session = FakeSession()
    monkeypatch.setattr(fetching_client, "HTTP_SESSION", main_session)
    monkeypatch.setattr(fetching_client.requests, "Session", lambda: worker_session)

    assert fetching_client.http_get("https://example.test/main", 3) is main_session
    worker_result: list[object] = []

    def fetch_from_worker() -> None:
        worker_result.append(fetching_client.http_get("https://example.test/worker", 4))

    thread = threading.Thread(target=fetch_from_worker)
    thread.start()
    thread.join(2)

    assert not thread.is_alive()
    assert worker_result == [worker_session]
    assert main_session.calls[0][1:3] == (3, False)
    assert worker_session.calls[0][1:3] == (4, False)


def test_decode_best_text_skips_unknown_encoding_hint() -> None:
    assert fetching_client._decode_best_text(b"209\xe6\x9c\x9f", ("unknown-codec", "utf-8")) == "209期"


def test_fetch_curl_retries_receive_error_without_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    def run(*args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(args)
        if len(calls) == 1:
            raise subprocess.CalledProcessError(56, args[0])
        return subprocess.CompletedProcess(args[0], 0, stdout=b"209\xe6\x9c\x9f")

    monkeypatch.setattr(fetching_client.subprocess, "run", run)
    monkeypatch.setattr(fetching_client.time, "sleep", lambda _seconds: None)

    assert fetching_client.fetch_curl_text("https://example.test/page", 3) == "209期"
    assert len(calls) == 2


@pytest.mark.parametrize(
    "failure",
    (
        subprocess.CalledProcessError(1, ["curl.exe"]),
        subprocess.TimeoutExpired("curl.exe", 3),
        FileNotFoundError("curl.exe"),
    ),
)
def test_fetch_curl_failures_are_classified_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    monkeypatch.setattr(fetching_client.subprocess, "run", Mock(side_effect=failure))

    with pytest.raises(requests.RequestException, match="curl SSL兼容抓取失败"):
        fetching_client.fetch_curl_text("https://example.test/page", 3)


def test_fetch_text_retries_then_decodes_success(monkeypatch: pytest.MonkeyPatch) -> None:
    response = SimpleNamespace(
        status_code=200,
        content="209期 狗蛇".encode(),
        encoding="utf-8",
        apparent_encoding="utf-8",
        raise_for_status=lambda: None,
    )
    errors = [requests.exceptions.Timeout("temporary")]
    calls = 0

    def http_get(_url: str, _timeout: int) -> object:
        nonlocal calls
        calls += 1
        if errors:
            raise errors.pop()
        return response

    monkeypatch.setattr(fetching_client, "http_get", http_get)
    monkeypatch.setattr(fetching_client.time, "sleep", lambda _seconds: None)

    assert fetching_client.fetch_text("https://example.test/page", 3) == "209期 狗蛇"
    assert calls == 2


def test_fetch_text_terminal_http_error_is_cached_by_context(monkeypatch: pytest.MonkeyPatch) -> None:
    response = SimpleNamespace(status_code=404)
    error = requests.HTTPError("missing", response=response)
    calls = 0

    def fetcher(_url: str, _timeout: int) -> str:
        nonlocal calls
        calls += 1
        raise error

    context = FetchContext(text_fetcher=fetcher)
    with pytest.raises(requests.HTTPError):
        context.get_text("https://example.test/page", 3)
    with pytest.raises(requests.HTTPError):
        context.get_text("https://example.test/page", 3)

    assert calls == 1


def test_fetch_context_caches_text_and_keeps_soft_payload_retryable() -> None:
    calls: list[str] = []

    def fetcher(url: str, _timeout: int) -> str:
        calls.append(url)
        return "" if url.endswith("/soft") else "stable"

    context = FetchContext(text_fetcher=fetcher)
    assert context.get_text("https://example.test/stable", 3) == "stable"
    assert context.get_text("https://example.test/stable", 3) == "stable"
    assert context.get_text("https://example.test/soft", 3) == ""
    assert context.get_text("https://example.test/soft", 3) == ""

    assert calls == [
        "https://example.test/stable",
        "https://example.test/soft",
        "https://example.test/soft",
    ]


def test_fetch_context_caches_rendered_text_and_rejects_new_work_after_close() -> None:
    calls: list[str] = []

    def renderer(url: str, _timeout: int) -> str:
        calls.append(url)
        return "rendered"

    context = FetchContext(renderer=renderer)
    assert context.get_rendered("https://example.test/page", 3) == "rendered"
    assert context.get_rendered("https://example.test/page", 3) == "rendered"
    context.close()

    with pytest.raises(requests.RequestException, match="上下文已关闭"):
        context.get_rendered("https://example.test/other", 3)
    assert calls == ["https://example.test/page"]


@pytest.mark.parametrize(
    ("pick", "records"),
    (
        ("top", [make_record(210), make_record(211), make_record(212), make_record(209)]),
        ("bottom", [make_record(209), make_record(210), make_record(211), make_record(212)]),
    ),
)
def test_http_period_outside_direction_window_reaches_renderer(
    pick: str,
    records: list[Record],
) -> None:
    site = make_article_site(pick=pick, payload="browser_rendered_page")
    raw = f"raw-{pick}-out-of-window"
    rendered = f"rendered-{pick}-target"
    registry = StubRegistry({raw: records, rendered: [make_record(209)]})
    rendered_calls: list[str] = []
    context = FetchContext(
        text_fetcher=lambda _url, _timeout: raw,
        renderer=lambda url, _timeout: rendered_calls.append(url) or rendered,
    )

    bundle = single_period.fetch_payload_for_period(site, 209, 3, context, registry)

    assert rendered_calls == [site.url]
    assert bundle.documents[0].label == "浏览器渲染页面"


def test_http_same_period_conflict_reaches_renderer() -> None:
    site = make_article_site(payload="browser_rendered_page")
    raw = "raw-conflict"
    rendered = "rendered-conflict-target"
    registry = StubRegistry(
        {
            raw: [make_record(209, position=0), make_record(209, position=1)],
            rendered: [make_record(209)],
        }
    )
    rendered_calls: list[str] = []
    context = FetchContext(
        text_fetcher=lambda _url, _timeout: raw,
        renderer=lambda url, _timeout: rendered_calls.append(url) or rendered,
    )

    single_period.fetch_payload_for_period(site, 209, 3, context, registry)

    assert rendered_calls == [site.url]


def test_http_correctly_selected_period_does_not_start_renderer() -> None:
    site = make_article_site(payload="browser_rendered_page")
    raw = "raw-valid"
    registry = StubRegistry({raw: [make_record(209)]})

    def renderer(_url: str, _timeout: int) -> str:
        raise AssertionError("renderer should not run for a complete HTTP document")

    context = FetchContext(text_fetcher=lambda _url, _timeout: raw, renderer=renderer)

    bundle = single_period.fetch_payload_for_period(site, 209, 3, context, registry)

    assert bundle.documents[0].label == "原始页面"


@pytest.mark.parametrize("service", [multi_period, recent_history])
def test_incomplete_http_page_reaches_renderer_in_multi_and_history(
    monkeypatch: pytest.MonkeyPatch,
    service: object,
) -> None:
    site = make_article_site()
    rendered = '<a href="/article/admin/article-1">209期 绝杀二肖【狗蛇】</a>'
    record = make_record(209)
    registry = StubRegistry({rendered: [record]})
    rendered_calls: list[str] = []

    def renderer(url: str, _timeout: int) -> str:
        rendered_calls.append(url)
        return rendered

    context, calls = article_context(rendered, renderer=renderer)
    monkeypatch.setattr(service, "parse_bundle_for_period", lambda *_args: ([record], record))
    if service is recent_history:
        monkeypatch.setattr(
            service,
            "history_result_from_records",
            lambda site, _records, _period: HistoryResult(site, (record,)),
        )

    if service is multi_period:
        results = service.scrape_site_multi_results(site, [209], 3, context, registry)
        assert results[0].ok
    else:
        result = service.scrape_site_history(site, 209, 3, context, registry)
        assert result.ok

    assert rendered_calls == [site.url]
    assert calls == [admin_article_api_url(site.url), site.url]


def test_single_period_uses_the_same_target_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    site = make_article_site(payload="browser_rendered_page")
    raw = "HTTP 200 page shell"
    rendered = "209期 绝杀二肖【狗蛇】"
    rendered_calls: list[str] = []

    def renderer(url: str, _timeout: int) -> str:
        rendered_calls.append(url)
        return rendered

    context = FetchContext(text_fetcher=lambda _url, _timeout: raw, renderer=renderer)
    record = make_record(209)
    registry = StubRegistry({rendered: [record]})
    monkeypatch.setattr(single_period, "parse_bundle_for_period", lambda *_args: ([record], record))

    result = single_period.scrape_site(site, 209, 3, context, registry)

    assert result.ok
    assert rendered_calls == [site.url]


def test_incomplete_document_bundle_is_rejected_by_boundary_validation() -> None:
    site = make_article_site()
    bundle = DocumentBundle(
        (PayloadDocument("文章", site.url, "209期 狗蛇", record_id="article-1"),),
        scan_complete=False,
    )

    with pytest.raises(ScrapeFailure) as exc_info:
        validate_bundle_boundaries(bundle, site)

    assert exc_info.value.category is ErrorCategory.STRUCTURE_CHANGED
    assert str(exc_info.value) == "页面结构已变化：文档扫描未完成"


def test_boundary_identity_covers_user_topic_generic_and_missing_routes() -> None:
    assert expected_record_id(
        Site("用户", "top", "https://example.test/#/users/7", payload="tuku_user_forums")
    ) == "user:7"
    assert expected_record_id(
        Site("列表", "top", "https://example.test/list", payload="topic_list_detail")
    ) == ""
    assert expected_record_id(
        Site("主题", "top", "https://example.test/topic/7.html", payload="page")
    ) == "topic:7"
    assert expected_record_id(
        Site("后台", "top", "https://example.test/not-an-article", payload="admin_article_api")
    ) == ""


@pytest.mark.parametrize(
    ("document", "message"),
    (
        (PayloadDocument("缺ID", "https://example.test/article/admin/article-1", "x"), "记录边界缺失"),
        (
            PayloadDocument(
                "错ID",
                "https://example.test/article/admin/article-1",
                "x",
                record_id="other",
            ),
            "文章ID边界冲突",
        ),
    ),
)
def test_boundary_validation_rejects_missing_and_mismatched_article_ids(
    document: PayloadDocument,
    message: str,
) -> None:
    site = make_article_site()

    with pytest.raises(ValueError, match=message):
        validate_bundle_boundaries(DocumentBundle((document,)), site)


def test_boundary_validation_rejects_mismatched_user_id() -> None:
    site = Site("用户", "top", "https://example.test/#/users/7", payload="tuku_user_forums")
    document = PayloadDocument("用户接口", "https://example.test/api", "x", record_id="user:8")

    with pytest.raises(ValueError, match="用户ID边界冲突"):
        validate_bundle_boundaries(DocumentBundle((document,)), site)


def test_document_relationship_validation_rejects_missing_parent_link_and_ids() -> None:
    anchor = PayloadDocument("页面", "https://example.test/page", '<a href="/body">body</a>')
    missing_link = PayloadDocument(
        "脚本",
        "https://example.test/body",
        "body",
        parent_url=anchor.url,
    )
    with pytest.raises(ValueError, match="缺少链接引用"):
        validate_document_relationships(DocumentBundle((anchor, missing_link)))

    missing_parent = PayloadDocument(
        "脚本",
        "https://example.test/body",
        "body",
        parent_url="https://example.test/missing",
        link_reference="/body",
    )
    with pytest.raises(ValueError, match="父文档"):
        validate_document_relationships(DocumentBundle((anchor, missing_parent)))

    invalid_link = PayloadDocument(
        "脚本",
        "https://example.test/body",
        "body",
        parent_url=anchor.url,
        link_reference="/not-body",
    )
    invalid_anchor = PayloadDocument("无引用页面", anchor.url, "no linked body")
    with pytest.raises(ValueError, match="父文档未引用"):
        validate_document_relationships(DocumentBundle((invalid_anchor, invalid_link)))

    conflicting_ids = PayloadDocument(
        "脚本",
        "https://example.test/body",
        "body",
        record_id="other",
        parent_url=anchor.url,
        link_reference="/body",
    )
    anchor_with_id = PayloadDocument(anchor.label, anchor.url, anchor.source, record_id="target")
    with pytest.raises(ValueError, match="记录ID冲突"):
        validate_document_relationships(DocumentBundle((anchor_with_id, conflicting_ids)))


def test_document_link_and_whitelist_validation_cover_authorized_and_rejected_sources() -> None:
    body_url = "https://cdn.test/data.js"
    anchor = PayloadDocument(
        "页面",
        "https://example.test/page",
        f'<script src="{body_url}"></script>',
    )
    body = PayloadDocument(
        "脚本",
        body_url,
        "body",
        parent_url=anchor.url,
        link_reference=body_url,
    )
    site = Site(
        "测试",
        "top",
        anchor.url,
        linked_document_pattern=r"https://cdn\.test/",
    )

    assert document_is_linked(anchor, body)
    assert linked_document_is_authorized(site, anchor, body)
    assert not linked_document_is_authorized(
        Site("测试", "top", anchor.url, linked_document_pattern=r"https://other\.test/"),
        anchor,
        body,
    )
    assert not linked_document_is_authorized(site, anchor, PayloadDocument("脚本", body_url, "body"))

    relative_body = PayloadDocument("相对", "https://example.test/relative", "body")
    relative_anchor = PayloadDocument("页面", anchor.url, '<a href="/relative">relative</a>')
    assert document_is_linked(relative_anchor, relative_body)
    assert not document_is_linked(relative_anchor, relative_anchor)
    assert not document_is_linked(relative_anchor, PayloadDocument("空URL", "", "body"))
    assert not document_is_linked(
        relative_anchor,
        PayloadDocument("跨域", "https://other.test/relative", "body"),
    )


def source_context(
    sources: dict[str, str],
    failures: set[str] | None = None,
) -> tuple[FetchContext, list[str]]:
    failures = failures or set()
    calls: list[str] = []

    def fetcher(url: str, _timeout: int) -> str:
        calls.append(url)
        if url in failures:
            raise requests.RequestException(f"failed: {url}")
        return sources[url]

    return FetchContext(text_fetcher=fetcher, renderer=lambda *_args: ""), calls


def test_collect_page_and_scripts_skips_irrelevant_cross_origin_references() -> None:
    site = Site("测试", "top", "https://example.test/page", parser="stub", payload="page_and_scripts")
    source = (
        '<script src="/same.js"></script><script src="https://other.test/evil.js"></script>'
        '<iframe src="/same-frame"></iframe><iframe src="https://other.test/evil-frame"></iframe>'
    )
    context, calls = source_context(
        {
            "https://example.test/same.js": "same script",
            "https://example.test/same-frame": "same frame",
        }
    )

    bundle = collect_page_and_scripts(site, source, 3, context)

    assert "https://other.test/evil.js" not in calls
    assert "https://other.test/evil-frame" not in calls
    assert calls == ["https://example.test/same.js", "https://example.test/same-frame"]
    assert [document.url for document in bundle.documents] == [
        site.url,
        "https://example.test/same.js",
        "https://example.test/same-frame",
    ]


def test_page_helpers_preserve_document_order_and_reject_empty_duplicates() -> None:
    documents = fetching_page.make_document_bundle(
        [
            PayloadDocument("空", "https://example.test/empty", ""),
            PayloadDocument("第一", "https://example.test/a", "same"),
            PayloadDocument("重复", "https://example.test/a", "same"),
            PayloadDocument("第二", "https://example.test/b", "other"),
        ]
    )

    assert [document.label for document in documents.documents] == ["第一", "第二"]
    assert fetching_page.canonical_detail_url("https://example.test/a.aspx?x=1#top") == (
        "https://example.test/a.aspx?x=1"
    )
    assert fetching_page.canonical_detail_url("https://example.test/a?x=1#top") == (
        "https://example.test/a"
    )
    assert fetching_page.visible_text("<style>x</style><p> 测试&nbsp;文本 </p>") == "测试 文本"


def test_listing_helpers_ignore_invalid_pagination_and_empty_links() -> None:
    listing = (
        '<a href="#">下一页</a>'
        '<a href="https://other.test/list?page=2">下一页</a>'
        '<a href="?page=2#top">下一页</a>'
        '<a href="/topic/209.html">209期</a>'
        '<a href="#">空链接</a>'
    )

    assert fetching_page.next_topic_listing_url(listing, "https://example.test/list") == (
        "https://example.test/list?page=2"
    )
    assert fetching_page.topic_listing_links(listing, "https://example.test/list") == [
        ("https://other.test/list", "下一页"),
        ("https://example.test/list", "下一页"),
        ("https://example.test/topic/209.html", "209期"),
    ]


def test_source_boundary_ids_cover_admin_user_topic_and_generic_modes() -> None:
    assert fetching_page.source_boundary_id(make_article_site()) == "article-1"
    assert fetching_page.source_boundary_id(
        Site("用户", "top", "https://example.test/#/users/7", payload="tuku_user_forums")
    ) == "user:7"
    assert fetching_page.source_boundary_id(
        Site("列表", "top", "https://example.test/list", payload="topic_list_detail")
    ) == ""
    assert fetching_page.source_boundary_id(
        Site("普通", "top", "https://example.test/topic/9.html", payload="page")
    ) == "topic:9"


def test_topic_detail_scripts_keep_only_allowed_referenced_sources() -> None:
    detail_url = "https://example.test/topic/9.html"
    script_url = "https://example.test/upload/script/9.js"
    source = '<script src="https://other.test/upload/script/evil.js"></script>' \
        '<script src="/upload/script/9.js"></script>'
    encoded = base64.b64encode("解码脚本".encode()).decode()
    context, calls = source_context(
        {
            detail_url: source,
            script_url: f'strdecode("{encoded}")',
        }
    )

    documents = fetching_page.fetch_topic_detail_documents(
        detail_url,
        3,
        context,
        Site("测试", "top", "https://example.test/page", payload="page"),
    )

    assert calls == [detail_url, script_url]
    assert [document.source for document in documents] == [source, "解码脚本"]


def test_collect_nested_document_fetches_its_iframe_and_script_in_source_order() -> None:
    site = Site("测试", "top", "https://example.test/page", parser="stub", payload="page_and_scripts")
    source = '<script src="/index.js"></script>'
    nested_url = "https://example.test/topic/nested.html"
    sources = {
        "https://example.test/index.js": "/topic/nested.html",
        nested_url: '<iframe src="/nested-frame"></iframe><script src="/nested.js"></script>',
        "https://example.test/nested-frame": "frame",
        "https://example.test/nested.js": "nested script",
    }
    context, calls = source_context(sources)

    bundle = fetching_page.collect_page_and_scripts(site, source, 3, context)

    assert calls == [
        "https://example.test/index.js",
        nested_url,
        "https://example.test/nested-frame",
        "https://example.test/nested.js",
    ]
    assert {document.label for document in bundle.documents} >= {"嵌套页面", "嵌套脚本", "iframe"}


@pytest.mark.parametrize("empty", (False, True))
def test_collect_nested_reference_failure_marks_scan_incomplete(empty: bool) -> None:
    site = Site("测试", "top", "https://example.test/page", parser="stub", payload="page_and_scripts")
    nested_url = "https://example.test/topic/failure.html"
    sources = {"https://example.test/index.js": "/topic/failure.html"}
    failures = {nested_url} if not empty else set()
    if empty:
        sources[nested_url] = ""
    context, calls = source_context(sources, failures)

    bundle = fetching_page.collect_page_and_scripts(
        site,
        '<script src="/index.js"></script>',
        3,
        context,
    )

    assert calls == ["https://example.test/index.js", nested_url]
    assert not bundle.scan_complete


def test_collect_document_capacity_only_fails_when_an_extra_reference_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = Site("测试", "top", "https://example.test/page", parser="stub", payload="page_and_scripts")
    monkeypatch.setattr(fetching_page, "MAX_PAYLOAD_DOCUMENTS", 1)

    complete_context, _complete_calls = source_context({})
    complete = fetching_page.collect_page_and_scripts(site, "plain page", 3, complete_context)
    incomplete_context, incomplete_calls = source_context(
        {"https://example.test/extra.js": "extra"}
    )
    incomplete = fetching_page.collect_page_and_scripts(
        site,
        '<script src="/extra.js"></script>',
        3,
        incomplete_context,
    )

    assert complete.scan_complete
    assert not incomplete.scan_complete
    assert incomplete_calls == []


def test_fetch_payload_browser_uses_complete_http_document_without_renderer() -> None:
    site = make_article_site(payload="browser_rendered_page")
    context = FetchContext(
        text_fetcher=lambda _url, _timeout: "complete",
        renderer=lambda *_args: (_ for _ in ()).throw(AssertionError("must not render")),
    )

    bundle = fetch_payload(site, 209, 3, context, lambda *_args: True)

    assert bundle.documents[0].label == "原始页面"


def test_fetch_payload_browser_reports_raw_and_render_failures() -> None:
    site = make_article_site(payload="browser_rendered_page")

    def failed_text(_url: str, _timeout: int) -> str:
        raise requests.RequestException("HTTP failed")

    def failed_render(_url: str, _timeout: int) -> str:
        raise requests.RequestException("browser failed")

    context = FetchContext(text_fetcher=failed_text, renderer=failed_render)
    with pytest.raises(requests.RequestException, match="原始页面与浏览器均抓取失败"):
        fetch_payload(site, 209, 3, context, lambda *_args: False)


def test_fetch_payload_browser_rejects_rendered_content_without_target() -> None:
    site = make_article_site(payload="browser_rendered_page")
    context = FetchContext(
        text_fetcher=lambda _url, _timeout: "shell",
        renderer=lambda _url, _timeout: "still shell",
    )

    with pytest.raises(ValueError, match="浏览器渲染后仍未找到"):
        fetch_payload(site, 209, 3, context, lambda *_args: False)


def test_fetch_payload_user_forums_and_curl_modes_stay_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    user_site = Site(
        "用户",
        "top",
        "https://example.test/#/users/7",
        payload="tuku_user_forums",
    )
    user_api = "https://example.test/api/v1/users/7/forums"
    user_context, user_calls = source_context(
        {user_api: '[{"user_id":"7","body":"209期 狗蛇"}]'}
    )
    user_bundle = fetch_payload(user_site, 209, 3, user_context, lambda *_args: False)

    curl_site = Site("页面", "top", "https://example.test/page", payload="curl_tls10_page_and_scripts")
    monkeypatch.setattr(fetching_page, "fetch_curl_text", lambda _url, _timeout: "curl source")
    curl_context, _curl_calls = source_context({})
    curl_bundle = fetch_payload(curl_site, 209, 3, curl_context, lambda *_args: False)

    assert user_calls == [user_api]
    assert user_bundle.documents[0].record_id == "user:7"
    assert curl_bundle.documents[0].source == "curl source"


def test_fetch_payload_rejects_service_only_and_unknown_payloads() -> None:
    context = FetchContext(text_fetcher=lambda _url, _timeout: "source")
    topic_site = Site("列表", "top", "https://example.test/list", payload="topic_list_detail")
    unknown_site = Site("未知", "top", "https://example.test/page", payload="unknown")

    with pytest.raises(ValueError, match="必须由服务层获取"):
        fetch_payload(topic_site, 209, 3, context, lambda *_args: False)
    with pytest.raises(ValueError, match="未知 payload"):
        fetch_payload(unknown_site, 209, 3, context, lambda *_args: False)


def test_scripts_payload_failure_and_decode_capacity_are_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = Site("脚本", "top", "https://example.test/page", payload="scripts")
    failed_context, _failed_calls = source_context(
        {site.url: '<script src="/bad.js"></script>'},
        {"https://example.test/bad.js"},
    )
    failed = fetch_payload(site, 209, 3, failed_context, lambda *_args: False)

    encoded = base64.b64encode(b"decoded").decode()
    monkeypatch.setattr(fetching_page, "MAX_PAYLOAD_DOCUMENTS", 2)
    capped_context, _capped_calls = source_context(
        {
            site.url: '<script src="/good.js"></script>',
            "https://example.test/good.js": f'strdecode("{encoded}")',
        }
    )
    capped = fetch_payload(site, 209, 3, capped_context, lambda *_args: False)

    assert not failed.scan_complete
    assert not capped.scan_complete


def test_explicit_api_url_remains_allowed_when_cross_origin() -> None:
    api_url = "https://api.test/landing-data"
    site = Site(
        "测试",
        "top",
        "https://example.test/page",
        parser="stub",
        payload="page_and_scripts",
        api_url=api_url,
    )
    source = f'<script src="{api_url}"></script>'
    context, calls = source_context({api_url: "configured API"})

    bundle = collect_page_and_scripts(site, source, 3, context)

    assert calls == [api_url]
    assert api_url in [document.url for document in bundle.documents]


def test_configured_linked_document_pattern_allows_cross_origin_reference() -> None:
    site = Site(
        "测试",
        "top",
        "https://example.test/page",
        parser="stub",
        payload="page_and_scripts",
        linked_document_pattern=r"https://cdn\.test/allowed/",
    )
    linked_url = "https://cdn.test/allowed/script.js"
    source = f'<script src="{linked_url}"></script>'
    context, calls = source_context({linked_url: "configured linked script"})

    bundle = collect_page_and_scripts(site, source, 3, context)

    assert calls == [linked_url]
    assert linked_url in [document.url for document in bundle.documents]


def test_scripts_payload_returns_bounded_same_origin_raw_and_decoded_documents() -> None:
    site = Site(
        "测试",
        "top",
        "https://example.test/page",
        parser="stub",
        payload="scripts",
        keywords=("不再由抓取层判定",),
    )
    encoded = base64.b64encode("209期 狗蛇".encode()).decode()
    source = '<script src="/same.js"></script><script src="https://other.test/noise.js"></script>'
    context, calls = source_context(
        {
            "https://example.test/page": source,
            "https://example.test/same.js": f'strdecode("{encoded}")',
        }
    )

    bundle = fetch_payload(site, 209, 3, context, lambda *_args: False)

    assert calls == ["https://example.test/page", "https://example.test/same.js"]
    assert [document.label for document in bundle.documents] == ["原始页面", "脚本", "脚本解码"]
    assert bundle.documents[-1].source == "209期 狗蛇"


def test_same_origin_reference_failure_marks_bundle_incomplete() -> None:
    site = Site("测试", "top", "https://example.test/page", parser="stub", payload="page_and_scripts")
    source = '<script src="/same.js"></script><iframe src="https://other.test/ignored"></iframe>'
    context, calls = source_context({}, {"https://example.test/same.js"})

    bundle = collect_page_and_scripts(site, source, 3, context)

    assert calls == ["https://example.test/same.js"]
    assert not bundle.scan_complete


def test_iframe_depth_cap_marks_bundle_incomplete() -> None:
    site = Site("测试", "top", "https://example.test/page", parser="stub", payload="page_and_scripts")
    source = '<iframe src="/frame-1"></iframe>'
    context, calls = source_context(
        {
            "https://example.test/frame-1": '<iframe src="/frame-2"></iframe>',
            "https://example.test/frame-2": '<iframe src="/frame-3"></iframe>',
            "https://example.test/frame-3": '<iframe src="/frame-4"></iframe>',
            "https://example.test/frame-4": "too deep",
        }
    )

    bundle = collect_page_and_scripts(site, source, 3, context)

    assert calls == [
        "https://example.test/frame-1",
        "https://example.test/frame-2",
        "https://example.test/frame-3",
    ]
    assert not bundle.scan_complete


def test_leaf_iframe_at_max_depth_does_not_mark_bundle_incomplete() -> None:
    site = Site("测试", "top", "https://example.test/page", parser="stub", payload="page_and_scripts")
    source = '<iframe src="/frame-1"></iframe>'
    context, calls = source_context(
        {
            "https://example.test/frame-1": '<iframe src="/frame-2"></iframe>',
            "https://example.test/frame-2": '<iframe src="/frame-3"></iframe>',
            "https://example.test/frame-3": "leaf",
        }
    )

    bundle = collect_page_and_scripts(site, source, 3, context)

    assert calls == [
        "https://example.test/frame-1",
        "https://example.test/frame-2",
        "https://example.test/frame-3",
    ]
    assert bundle.scan_complete


def test_nested_target_probe_prioritizes_later_valid_link_over_noisy_links() -> None:
    site = Site("测试", "top", "https://example.test/page", parser="stub", payload="page_and_scripts")
    links = "".join(
        f'<a href="/topic/noise-{index}">{"noise" * 1000}</a>' for index in range(12)
    )
    links += '<a href="/topic/valid">valid</a>'
    source = '<script src="/index.js"></script>'
    context, calls = source_context(
        {
            "https://example.test/index.js": links,
            "https://example.test/topic/valid": "valid document",
            **{f"https://example.test/topic/noise-{index}": "noise" for index in range(12)},
        }
    )

    bundle = collect_page_and_scripts(
        site,
        source,
        3,
        context,
        lambda snippet, _site, _period: "valid" in snippet,
    )

    assert calls[0] == "https://example.test/index.js"
    assert calls[1] == "https://example.test/topic/valid"
    assert calls == ["https://example.test/index.js", "https://example.test/topic/valid"]
    assert "https://example.test/topic/valid" in [document.url for document in bundle.documents]
    assert bundle.scan_complete


def test_topic_top_orchestration_scans_complete_list_before_window() -> None:
    site = Site("烟雨", "top", "https://example.test/list", parser="stub", payload="topic_list_detail")
    sources = {
        site.url: (
            '<a href="/topic/209.html">209期 烟雨 绝杀二肖</a>'
            '<a href="/topic/208.html">208期 烟雨 绝杀二肖</a>'
            '<a href="/topic/207.html">207期 烟雨 绝杀二肖</a>'
            '<a href="/list?page=2">下一页</a>'
        ),
        "https://example.test/list?page=2": '<p>无更多目标</p>',
        "https://example.test/topic/209.html": '<script src="/upload/script/209.js"></script>',
        "https://example.test/upload/script/209.js": "烟雨 209期 绝杀二肖【狗蛇】",
    }
    context, calls = source_context(sources)

    bundle = single_period.fetch_topic_list_detail_for_period(site, 209, 3, context)

    assert "https://example.test/list?page=2" in calls
    assert "狗蛇" in bundle.combined_source


def test_topic_bottom_orchestration_paginates_to_last_window() -> None:
    site = Site("烟雨", "bottom", "https://example.test/list", parser="stub", payload="topic_list_detail")
    sources = {
        site.url: (
            '<a href="/topic/215.html">215期 烟雨 绝杀二肖</a>'
            '<a href="/topic/214.html">214期 烟雨 绝杀二肖</a>'
            '<a href="/topic/213.html">213期 烟雨 绝杀二肖</a>'
            '<a href="/list?page=2">下一页</a>'
        ),
        "https://example.test/list?page=2": (
            '<a href="/topic/212.html">212期 烟雨 绝杀二肖</a>'
            '<a href="/topic/211.html">211期 烟雨 绝杀二肖</a>'
            '<a href="/topic/210.html">210期 烟雨 绝杀二肖</a>'
        ),
        "https://example.test/topic/211.html": '<script src="/upload/script/211.js"></script>',
        "https://example.test/upload/script/211.js": "烟雨 211期 绝杀二肖【狗蛇】",
    }
    context, calls = source_context(sources)

    bundle = single_period.fetch_topic_list_detail_for_period(site, 211, 3, context)

    assert "https://example.test/list?page=2" in calls
    assert "狗蛇" in bundle.combined_source


def test_topic_script_match_keeps_detail_parent_for_full_bundle_validation() -> None:
    site = Site(
        "烟雨",
        "top",
        "https://example.test/list",
        parser="stub",
        payload="topic_list_detail",
    )
    detail_url = "https://example.test/topic/209.html"
    script_url = "https://example.test/upload/script/209.js"
    sources = {
        site.url: '<a href="/topic/209.html">209期 烟雨 绝杀二肖</a>',
        detail_url: '<script src="/upload/script/209.js"></script>',
        script_url: "烟雨 209期 绝杀二肖【狗蛇】",
    }
    context, _calls = source_context(sources)
    bundle = single_period.fetch_topic_list_detail_for_period(site, 209, 3, context)
    registry = StubRegistry({sources[script_url]: [make_record(209)]})

    records, selected = single_period.parse_bundle_for_period(bundle, site, 209, registry)

    assert selected.period == 209
    assert records == [make_record(209)]
    assert [document.label for document in bundle.documents] == ["详情页", "详情脚本"]
    script_document = bundle.documents[1]
    assert script_document.parent_url == detail_url
    assert script_document.link_reference == "/upload/script/209.js"


def test_topic_later_page_duplicate_period_is_fetched_and_conflicts() -> None:
    site = Site("烟雨", "top", "https://example.test/list", parser="stub", payload="topic_list_detail")
    first_detail = "https://example.test/topic/209.html"
    second_detail = "https://example.test/topic/209-alt.html"
    first_script = "https://example.test/upload/script/209.js"
    second_script = "https://example.test/upload/script/209-alt.js"
    sources = {
        site.url: (
            '<a href="/topic/209.html">209期 烟雨 绝杀二肖</a>'
            '<a href="/topic/208.html">208期 烟雨 绝杀二肖</a>'
            '<a href="/topic/207.html">207期 烟雨 绝杀二肖</a>'
            '<a href="/list?page=2">下一页</a>'
        ),
        "https://example.test/list?page=2": (
            '<a href="/topic/209-alt.html">209期 烟雨 绝杀二肖</a>'
        ),
        first_detail: '<script src="/upload/script/209.js"></script>',
        first_script: "烟雨 209期 绝杀二肖【狗蛇】",
        second_detail: '<script src="/upload/script/209-alt.js"></script>',
        second_script: "烟雨 209期 绝杀二肖【鼠虎】",
    }
    context, calls = source_context(sources)
    bundle = single_period.fetch_topic_list_detail_for_period(site, 209, 3, context)
    registry = StubRegistry(
        {
            sources[first_script]: [make_record(209, "狗蛇")],
            sources[second_script]: [make_record(209, "鼠虎")],
        }
    )

    assert second_detail in calls
    assert second_script in calls
    with pytest.raises(ValueError, match="冲突"):
        single_period.parse_bundle_for_period(bundle, site, 209, registry)


def test_topic_list_errors_keep_period_and_direction_boundaries() -> None:
    site = Site("烟雨", "top", "https://example.test/list", parser="stub", payload="topic_list_detail")
    context, _calls = source_context({site.url: '<a href="/topic/209.html">209期 烟雨 绝杀二肖</a>'})

    with pytest.raises(ValueError, match="必须指定期数"):
        single_period.fetch_topic_list_detail_for_period(site, None, 3, context)

    detail_site = Site("烟雨", "top", site.url, parser="stub", payload="topic_list_detail")
    detail_context, _detail_calls = source_context(
        {
            site.url: '<a href="/topic/209.html">209期 烟雨 绝杀二肖</a>',
            "https://example.test/topic/209.html": "209期 无关正文",
        }
    )
    with pytest.raises(ValueError, match="详情页脚本内未找到"):
        single_period.fetch_topic_list_detail_for_period(detail_site, 209, 3, detail_context)

    outside_context, _outside_calls = source_context(
        {
            site.url: (
                '<a href="/topic/209.html">209期 烟雨 绝杀二肖</a>'
                '<a href="/topic/208.html">208期 烟雨 绝杀二肖</a>'
                '<a href="/topic/207.html">207期 烟雨 绝杀二肖</a>'
                '<a href="/topic/206.html">206期 烟雨 绝杀二肖</a>'
            )
        }
    )
    with pytest.raises(ValueError, match="候选内未找到 206 期"):
        single_period.fetch_topic_list_detail_for_period(site, 206, 3, outside_context)


def test_topic_list_pagination_cap_is_a_hard_failure() -> None:
    site = Site("烟雨", "top", "https://example.test/list", parser="stub", payload="topic_list_detail")
    calls: list[str] = []

    def fetcher(url: str, _timeout: int) -> str:
        calls.append(url)
        page = int(url.rsplit("=", 1)[1]) if "=" in url else 0
        return f'<a href="/list?page={page + 1}">下一页</a>'

    context = FetchContext(text_fetcher=fetcher)

    with pytest.raises(ValueError, match="超过扫描上限"):
        single_period.fetch_topic_list_detail_for_period(site, 209, 3, context)
    assert len(calls) == 32


def test_topic_title_matching_is_regex_configured_and_safe_on_bad_regex() -> None:
    regex_site = Site("烟雨", "top", "https://example.test/list", title=r"精品")
    invalid_site = Site("烟雨", "top", "https://example.test/list", title="[")
    default_site = Site("烟雨", "top", "https://example.test/list")

    assert single_period._topic_list_item_matches("精品", regex_site)
    assert not single_period._topic_list_item_matches("精品", invalid_site)
    assert single_period._topic_list_item_matches("烟雨 绝杀两肖", default_site)
    assert not single_period._topic_list_item_matches("烟雨 普通内容", default_site)


def test_first_successful_document_keeps_source_order_over_larger_later_document() -> None:
    site = Site("测试", "top", "https://example.test/page", parser="stub")
    first_source = "first source"
    second_source = "second source"
    first_records = [make_record(209, "狗蛇")]
    second_records = [
        make_record(209, "狗蛇"),
        make_record(208, "鼠虎"),
        make_record(207, "牛马"),
    ]
    bundle = DocumentBundle(
        (
            PayloadDocument("第一文档", site.url, first_source),
            PayloadDocument("第二文档", "https://example.test/second", second_source),
        )
    )
    registry = StubRegistry({first_source: first_records, second_source: second_records})

    records, selected = single_period.parse_bundle_for_period(bundle, site, 209, registry)

    assert records == first_records
    assert selected.period == first_records[0].period
    assert selected.zodiac == first_records[0].zodiac


def test_parse_bundle_errors_do_not_synthesize_a_record() -> None:
    site = Site("测试", "top", "https://example.test/page", parser="stub")

    with pytest.raises(ValueError, match="未抓到有效候选"):
        single_period.parse_bundle_for_period(DocumentBundle(()), site, 209, StubRegistry({}))

    empty_bundle = DocumentBundle(
        (
            PayloadDocument("一", site.url, "one"),
            PayloadDocument("二", "https://example.test/two", "two"),
        )
    )
    with pytest.raises(ValueError, match="多文档内未能独立验证"):
        single_period.parse_bundle_for_period(empty_bundle, site, 209, StubRegistry({}))

    wrong_period = DocumentBundle((PayloadDocument("一", site.url, "wrong"),))
    with pytest.raises(ValueError, match="候选内未找到 209 期"):
        single_period.parse_bundle_for_period(
            wrong_period,
            site,
            209,
            StubRegistry({"wrong": [make_record(208)]}),
        )

    invalid_record = DocumentBundle((PayloadDocument("一", site.url, "invalid"),))
    with pytest.raises(ValueError, match="生肖唯一性"):
        single_period.parse_bundle_for_period(
            invalid_record,
            site,
            209,
            StubRegistry({"invalid": [make_record(209, "狗狗")]}),
        )


def test_linked_document_authorization_can_supply_the_only_success() -> None:
    body_url = "https://cdn.test/allowed/data.js"
    site = Site(
        "测试",
        "top",
        "https://example.test/page",
        parser="stub",
        linked_document_pattern=r"https://cdn\.test/allowed/",
    )
    anchor_source = f'<h1>测试</h1><script src="{body_url}"></script>'
    body_source = "body data"
    bundle = DocumentBundle(
        (
            PayloadDocument("页面", site.url, anchor_source),
            PayloadDocument(
                "白名单脚本",
                body_url,
                body_source,
                parent_url=site.url,
                link_reference=body_url,
            ),
        )
    )
    combined = anchor_source + DOCUMENT_BOUNDARY + body_source
    registry = StubRegistry({combined: [make_record(209)]})

    records, selected = single_period.parse_bundle_for_period(bundle, site, 209, registry)

    assert records == [make_record(209)]
    assert selected.period == 209


def test_linked_pair_result_conflicts_with_independent_document() -> None:
    body_url = "https://cdn.test/allowed/data.js"
    site = Site(
        "测试",
        "top",
        "https://example.test/page",
        parser="stub",
        linked_document_pattern=r"https://cdn\.test/allowed/",
    )
    anchor_source = f'<h1>测试</h1><script src="{body_url}"></script>'
    body_source = "body data"
    bundle = DocumentBundle(
        (
            PayloadDocument("页面", site.url, anchor_source),
            PayloadDocument(
                "白名单脚本",
                body_url,
                body_source,
                parent_url=site.url,
                link_reference=body_url,
            ),
        )
    )
    combined = anchor_source + DOCUMENT_BOUNDARY + body_source
    registry = StubRegistry(
        {
            anchor_source: [make_record(209, "狗蛇")],
            combined: [make_record(209, "鼠虎")],
        }
    )

    with pytest.raises(ValueError, match="独立文档与标题正文结果不同"):
        single_period.parse_bundle_for_period(bundle, site, 209, registry)


def test_linked_same_url_record_ids_conflict_before_pairing() -> None:
    site = Site(
        "测试",
        "top",
        "https://example.test/page",
        parser="stub",
        linked_document_pattern=r"https://cdn\.test/allowed/",
    )
    bundle = DocumentBundle(
        (
            PayloadDocument("一", site.url, "one", record_id="id-1"),
            PayloadDocument("二", site.url, "two", record_id="id-2"),
        )
    )

    with pytest.raises(ValueError, match="同URL文档记录ID不同"):
        single_period.parse_bundle_for_period(bundle, site, 209, StubRegistry({}))


def test_single_service_reports_fetch_errors_as_failed_result() -> None:
    site = Site("测试", "top", "https://example.test/page", parser="stub")
    context = FetchContext(
        text_fetcher=lambda _url, _timeout: (_ for _ in ()).throw(
            requests.RequestException("network")
        )
    )

    result = single_period.scrape_site(site, 209, 3, context, StubRegistry({}))

    assert not result.ok
    assert "HTTP请求失败" in result.error


def test_admin_base64_requires_valid_utf8_and_allows_harmless_whitespace() -> None:
    title = base64.b64encode("标题".encode()).decode()
    html = base64.b64encode("209期 狗蛇".encode()).decode()
    spaced_title = title[:3] + "\n " + title[3:]

    assert decode_admin_article_record({"title": spaced_title, "html": html}) == "标题\n209期 狗蛇"

    with pytest.raises(ValueError):
        decode_admin_article_record({"title": "%%%not-base64%%%", "html": html})
    with pytest.raises(ValueError):
        decode_admin_article_record({"title": base64.b64encode(b"\xff").decode(), "html": html})


def test_malformed_strdecode_blocks_are_skipped_without_permissive_decoding() -> None:
    encoded = base64.b64encode("209期 狗蛇".encode()).decode()
    spaced = encoded[:4] + "\n " + encoded[4:]
    source = f'strdecode("{spaced}") strdecode("%%%bad%%") strdecode("") __PAGE_DATA__="%%%bad%%"'

    decoded = decode_strdecode_blocks(source)

    assert decoded == "209期 狗蛇"


def test_lottery_article_identity_and_page_boundary_are_supported() -> None:
    url = "https://example.test/article/lottery/lottery-1?url=test"
    site = Site("测试站", "top", url, parser="stub", payload="admin_article_api")

    assert admin_article_id(url) == "lottery-1"
    assert admin_article_api_url(url) == "https://example.test/api/proxy/admin-articles/lottery-1"
    assert expected_record_id(site) == "lottery-1"
    assert page_has_record_boundary(
        '<a href="/article/lottery/lottery-1">文章</a>',
        "lottery-1",
    )
    assert not page_has_record_boundary(
        '<a href="/article/lottery/other">文章</a>',
        "lottery-1",
    )


def test_history_rejects_same_period_same_value_at_different_positions() -> None:
    site = Site("测试", "top", "https://example.test/page", parser="stub")
    records = [make_record(209, position=0), make_record(209, position=1)]
    records.extend(make_record(period, position=period) for period in range(208, 199, -1))

    result = recent_history.history_result_from_records(site, records, 209)

    assert not result.ok
    assert "209期" in result.error


def test_history_may_dedupe_exact_same_position_duplicates() -> None:
    site = Site("测试", "top", "https://example.test/page", parser="stub")
    records = [make_record(209, position=0), make_record(209, position=0)]
    records.extend(make_record(period, position=period) for period in range(208, 199, -1))

    result = recent_history.history_result_from_records(site, records, 209)

    assert result.ok
    assert len(result.records) == 10


@pytest.mark.parametrize(
    ("module", "entry", "result_factory"),
    [
        (single_period, "scrape_sites", lambda site, record: Result(site, record)),
        (multi_period, "scrape_sites_for_periods", lambda site, record: [Result(site, record)]),
        (recent_history, "scrape_history_sites", lambda site, record: HistoryResult(site, (record,))),
    ],
)
def test_services_close_only_contexts_they_create(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    entry: str,
    result_factory: Callable[[Site, Record], object],
) -> None:
    site = Site("测试", "top", "https://example.test/page", parser="stub")
    record = make_record(209)
    context = Mock()
    monkeypatch.setattr(module, "FetchContext", lambda: context)

    if module is single_period:
        monkeypatch.setattr(module, "scrape_site", lambda *_args: result_factory(site, record))
        result = module.scrape_sites([site], 209, 3, 1, registry=object(), progress=None)
    elif module is multi_period:
        monkeypatch.setattr(
            module,
            "scrape_site_multi_results",
            lambda *_args: result_factory(site, record),
        )
        result = module.scrape_sites_for_periods([site], [209], 3, 1, registry=object(), progress=None)
    else:
        monkeypatch.setattr(module, "scrape_site_history", lambda *_args: result_factory(site, record))
        result = module.scrape_history_sites([site], 209, 1, 1, registry=object(), progress=None)

    assert result
    context.close.assert_called_once_with()


def test_multi_period_turns_period_scoped_fetch_failure_into_each_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = make_article_site(payload="browser_rendered_page")

    def fail_fetch(*_args: object) -> DocumentBundle:
        raise requests.RequestException("period fetch failed")

    monkeypatch.setattr(multi_period, "fetch_payload_for_period", fail_fetch)

    results = multi_period.scrape_site_multi_results(
        site,
        [209, 208],
        3,
        FetchContext(renderer=lambda *_args: ""),
        StubRegistry({}),
    )

    assert len(results) == 2
    assert all(not result.ok and "HTTP请求失败" in result.error for result in results)


def test_multi_period_turns_shared_fetch_failure_into_each_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = Site("测试", "top", "https://example.test/page", parser="stub", payload="page")

    def fail_fetch(*_args: object) -> DocumentBundle:
        raise ValueError("shared fetch failed")

    monkeypatch.setattr(multi_period, "fetch_payload_for_period", fail_fetch)

    results = multi_period.scrape_site_multi_results(
        site,
        [209, 208],
        3,
        FetchContext(renderer=lambda *_args: ""),
        StubRegistry({}),
    )

    assert len(results) == 2
    assert all(not result.ok and "字段校验未通过" in result.error for result in results)


def test_multi_period_outer_worker_exception_becomes_ordered_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = Site("测试", "top", "https://example.test/page", parser="stub")

    def fail_worker(*_args: object) -> list[Result]:
        raise RuntimeError("worker failed")

    monkeypatch.setattr(multi_period, "scrape_site_multi_results", fail_worker)
    results = multi_period.scrape_sites_for_periods(
        [site],
        [209, 208],
        3,
        1,
        context=FetchContext(renderer=lambda *_args: ""),
        registry=StubRegistry({}),
        progress=None,
    )

    assert [result.site for result in results[209]] == [site]
    assert all("字段校验未通过" in result.error for result in results[209])
    assert all("字段校验未通过" in result.error for result in results[208])


def test_multi_period_empty_site_list_does_not_create_context() -> None:
    assert multi_period.scrape_sites_for_periods([], [209, 208], 3, 1) == {
        209: [],
        208: [],
    }


class FakePage:
    def __init__(self, content: str) -> None:
        self.content_value = content
        self.closed = 0

    def goto(self, _url: str, *, wait_until: str, timeout: int) -> None:
        assert wait_until == "domcontentloaded"
        assert timeout > 0

    def wait_for_load_state(self, *, timeout: int) -> None:
        assert timeout > 0

    def content(self) -> str:
        return self.content_value

    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    def close(self) -> None:
        self.closed += 1


class FakeBrowserContext:
    def __init__(self) -> None:
        self.pages: list[FakePage] = []
        self.closed = 0

    def new_page(self) -> FakePage:
        page = FakePage(f'<p>{len(self.pages) + 1}</p>')
        self.pages.append(page)
        return page

    def close(self) -> None:
        self.closed += 1


class FakeBrowser:
    def __init__(self) -> None:
        self.contexts: list[FakeBrowserContext] = []
        self.closed = 0

    def new_context(self, *, user_agent: str) -> FakeBrowserContext:
        assert user_agent
        context = FakeBrowserContext()
        self.contexts.append(context)
        return context

    def close(self) -> None:
        self.closed += 1


class FakePlaywright:
    def __init__(self) -> None:
        self.browser = FakeBrowser()
        self.stopped = 0
        self.chromium = self

    def launch(self, *, headless: bool) -> FakeBrowser:
        assert headless
        return self.browser

    def stop(self) -> None:
        self.stopped += 1


def test_default_renderer_reuses_runtime_and_closes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakePlaywright()
    monkeypatch.setattr("fetching.browser.sync_playwright", lambda: SimpleNamespace(start=lambda: runtime))
    context = FetchContext()

    assert context.get_rendered("https://example.test/one", 3) == "<p>1</p>"
    assert context.get_rendered("https://example.test/two", 3) == "<p>2</p>"
    context.close()
    context.close()

    assert len(runtime.browser.contexts) == 1
    assert len(runtime.browser.contexts[0].pages) == 2
    assert runtime.browser.closed == 1
    assert runtime.browser.contexts[0].closed == 1
    assert runtime.stopped == 1


def test_renderer_initialization_failure_fails_all_queued_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    initialization_started = threading.Event()
    release_initialization = threading.Event()

    class FailingPlaywright:
        def start(self) -> object:
            initialization_started.set()
            release_initialization.wait(2)
            raise requests.RequestException("startup failed")

    monkeypatch.setattr("fetching.browser.sync_playwright", lambda: FailingPlaywright())
    context = FetchContext()
    errors: list[BaseException] = []

    def render(url: str) -> None:
        try:
            context.get_rendered(url, 3)
        except Exception as exc:
            errors.append(exc)

    first = threading.Thread(target=render, args=("https://example.test/one",), daemon=True)
    first.start()
    assert initialization_started.wait(1)
    second = threading.Thread(target=render, args=("https://example.test/two",), daemon=True)
    second.start()

    renderer = context._renderer
    assert renderer is not None
    for _ in range(100):
        if renderer._commands.qsize() >= 1:
            break
        threading.Event().wait(0.01)
    else:
        pytest.fail("second render command was not queued")

    release_initialization.set()
    first.join(2)
    second.join(2)
    context.close()

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(errors) == 2
    assert all(isinstance(error, requests.RequestException) for error in errors)
    assert all(str(error) == "startup failed" for error in errors)


def test_page_render_failure_does_not_poison_reused_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = FakePlaywright()
    monkeypatch.setattr("fetching.browser.sync_playwright", lambda: SimpleNamespace(start=lambda: runtime))
    render_page = Mock(side_effect=[requests.RequestException("bad page"), "second"])
    monkeypatch.setattr("fetching.browser._render_page", render_page)
    context = FetchContext()

    with pytest.raises(requests.RequestException, match="bad page"):
        context.get_rendered("https://example.test/bad", 3)
    assert context.get_rendered("https://example.test/good", 3) == "second"
    context.close()

    assert render_page.call_count == 2
    assert len(runtime.browser.contexts) == 1
    assert runtime.browser.closed == 1
    assert runtime.browser.contexts[0].closed == 1
    assert runtime.stopped == 1


def test_injected_renderer_is_not_closed_without_close_method() -> None:
    calls: list[tuple[str, int]] = []

    def renderer(url: str, timeout: int) -> str:
        calls.append((url, timeout))
        return "rendered"

    context = FetchContext(renderer=renderer)

    assert context.get_rendered("https://example.test/page", 3) == "rendered"
    context.close()

    assert calls == [("https://example.test/page", 3)]


def test_injected_renderer_with_close_method_is_closed_once() -> None:
    class CloseableRenderer:
        def __init__(self) -> None:
            self.closed = 0

        def __call__(self, _url: str, _timeout: int) -> str:
            return "rendered"

        def close(self) -> None:
            self.closed += 1

    renderer = CloseableRenderer()
    context = FetchContext(renderer=renderer)

    context.get_rendered("https://example.test/page", 3)
    context.close()
    context.close()

    assert renderer.closed == 1

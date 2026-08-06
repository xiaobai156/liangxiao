from __future__ import annotations

import base64
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from domain.models import Site
from fetching.browser import render_browser_text
from fetching.client import FetchContext, fetch_curl_text, fetch_text
from fetching.dynamic_article import (
    admin_article_api_url,
    admin_article_id,
    decode_admin_article_api_response,
    decode_admin_article_record,
    fetch_admin_article_payload,
    landing_page_data_api_url,
)
from fetching.page import (
    decode_strdecode_blocks,
    detail_record_identity,
    fetch_payload,
    fetch_topic_list_detail_payload,
    make_document_bundle,
    source_boundary_id,
    visible_text,
)
from domain.models import PayloadDocument
from fetching.user_forum import filter_user_forums, user_forums_api_url


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200, encoding: str = "utf-8") -> None:
        self.content = body
        self.status_code = status
        self.encoding: str | None = encoding
        self.apparent_encoding = encoding

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError(f"HTTP {self.status_code}", response=response)


def encoded(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def admin_record(record_id: str, title: str, body: str) -> dict[str, str]:
    return {"id": record_id, "title": encoded(title), "html": encoded(body)}


def test_fetch_text_retries_transient_error_and_decodes_best_text(monkeypatch: pytest.MonkeyPatch) -> None:
    getter = Mock(side_effect=[requests.ConnectionError("temporary"), FakeResponse("209期 狗蛇".encode("utf-8"))])
    monkeypatch.setattr("fetching.client.http_get", getter)
    monkeypatch.setattr("fetching.client.time.sleep", lambda _seconds: None)

    assert fetch_text("https://example.test", 3) == "209期 狗蛇"
    assert getter.call_count == 2


def test_fetch_text_rejects_false_utf7_detection_for_base64_javascript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b'utf8to16(strdecode("PHA+PHN0cm9uZz4="));'
    response = FakeResponse(body)
    response.encoding = None
    response.apparent_encoding = "utf-7"
    monkeypatch.setattr("fetching.client.http_get", Mock(return_value=response))

    assert fetch_text("https://example.test/data.js", 3) == body.decode("utf-8")


def test_fetch_text_does_not_retry_404(monkeypatch: pytest.MonkeyPatch) -> None:
    getter = Mock(return_value=FakeResponse(b"missing", status=404))
    monkeypatch.setattr("fetching.client.http_get", getter)

    with pytest.raises(requests.HTTPError, match="HTTP 404"):
        fetch_text("https://example.test/missing", 3)
    assert getter.call_count == 1


def test_fetch_text_uses_curl_only_after_ssl_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    getter = Mock(side_effect=requests.exceptions.SSLError("bad tls"))
    fallback = Mock(return_value="209期 狗蛇")
    monkeypatch.setattr("fetching.client.http_get", getter)
    monkeypatch.setattr("fetching.client.fetch_curl_text", fallback)
    monkeypatch.setattr("fetching.client.time.sleep", lambda _seconds: None)

    assert fetch_text("https://example.test", 3) == "209期 狗蛇"
    assert getter.call_count == 2
    fallback.assert_called_once_with("https://example.test", 3)


def test_fetch_context_reuses_success_but_not_soft_challenge() -> None:
    values = iter(["正文", "Just a moment", "真实正文"])
    fetcher = Mock(side_effect=lambda _url, _timeout: next(values))
    context = FetchContext(text_fetcher=fetcher, renderer=lambda _url, _timeout: "rendered")

    assert context.get_text("https://example.test/a", 3) == "正文"
    assert context.get_text("https://example.test/a", 3) == "正文"
    assert context.get_text("https://example.test/b", 3) == "Just a moment"
    assert context.get_text("https://example.test/b", 3) == "真实正文"
    assert fetcher.call_count == 3


def test_incomplete_response_gets_one_extra_fast_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    response = Mock()
    response.raise_for_status.return_value = None
    response.content = "正文".encode("utf-8")
    response.encoding = "utf-8"
    response.apparent_encoding = "utf-8"
    response.status_code = 200
    fetcher = Mock(
        side_effect=[
            requests.exceptions.ChunkedEncodingError("incomplete-1"),
            requests.exceptions.ChunkedEncodingError("incomplete-2"),
            response,
        ]
    )
    monkeypatch.setattr("fetching.client.http_get", fetcher)
    monkeypatch.setattr("fetching.client.time.sleep", lambda _seconds: None)

    assert fetch_text("https://example.test/data", 3) == "正文"
    assert fetcher.call_count == 3


def test_repeated_incomplete_response_falls_back_to_curl(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = Mock(side_effect=requests.exceptions.ChunkedEncodingError("incomplete"))
    fallback = Mock(return_value="完整正文")
    monkeypatch.setattr("fetching.client.http_get", fetcher)
    monkeypatch.setattr("fetching.client.fetch_curl_text", fallback)
    monkeypatch.setattr("fetching.client.time.sleep", lambda _seconds: None)

    assert fetch_text("https://example.test/data", 3) == "完整正文"
    assert fetcher.call_count == 3
    fallback.assert_called_once_with("https://example.test/data", 3)


def test_timeout_still_stops_after_two_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = Mock(side_effect=requests.exceptions.Timeout("slow"))
    monkeypatch.setattr("fetching.client.http_get", fetcher)
    monkeypatch.setattr("fetching.client.time.sleep", lambda _seconds: None)

    with pytest.raises(requests.exceptions.Timeout):
        fetch_text("https://example.test/data", 3)
    assert fetcher.call_count == 2


def test_detail_record_identity_covers_supported_routes() -> None:
    assert detail_record_identity("https://a.test/topic/123.html") == "topic:123"
    assert detail_record_identity("https://a.test/view.php?id=282") == "view:282"
    assert detail_record_identity("https://a.test/read.php?tid=639") == "read:639"
    assert detail_record_identity("https://a.test/bbs/topic.php?id=912") == "topic.php:912"
    assert detail_record_identity("https://a.test/art_zhuanqu/217.html") == "art_zhuanqu:217"


def test_decode_blocks_and_admin_api_lock_to_unique_article_id() -> None:
    page = "document.write(strdecode('%s'))" % encoded("209期 狗蛇")
    assert decode_strdecode_blocks(page) == "209期 狗蛇"

    payload = json.dumps(
        {"items": [admin_record("wanted", "目标标题", "209期狗蛇"), admin_record("decoy", "诱饵", "209期龙虎")]},
        ensure_ascii=False,
    )
    bundle = decode_admin_article_api_response(payload, "wanted", "https://example.test/api/wanted")
    assert len(bundle.documents) == 1
    assert bundle.documents[0].record_id == "wanted"
    assert bundle.documents[0].url == "https://example.test/api/wanted"
    assert bundle.documents[0].record_path == "root.items[0]"
    assert bundle.documents[0].record_count == 2
    assert "狗蛇" in bundle.combined_source
    assert "龙虎" not in bundle.combined_source


def test_admin_api_rejects_duplicate_same_id() -> None:
    payload = json.dumps({"a": admin_record("same", "A", "209期狗蛇"), "b": admin_record("same", "B", "209期龙虎")})
    with pytest.raises(ValueError, match="多个同ID文章"):
        decode_admin_article_api_response(payload, "same")


def test_admin_404_uses_page_then_render_with_same_strict_probe() -> None:
    site = Site(
        "动态站",
        "bottom",
        "https://example.test/article/manager/wanted?url=x",
        parser="dynamic_site",
        payload="admin_article_api",
        api_url="https://example.test/api/wanted",
    )
    not_found = requests.HTTPError("404")
    response = requests.Response()
    response.status_code = 404
    not_found.response = response
    fetcher = Mock(side_effect=[not_found, "<html>空壳</html>"])
    renderer = Mock(return_value="/article/manager/wanted 209期 动态站 稳杀二肖【狗蛇】")
    probe = Mock(side_effect=lambda source, _site, period: str(period) in source and "狗蛇" in source)
    context = FetchContext(text_fetcher=fetcher, renderer=renderer)

    bundle = fetch_admin_article_payload(site, 209, 3, context, probe)
    assert bundle.documents[0].label == "浏览器渲染后台文章"
    assert bundle.documents[0].record_id == "wanted"
    renderer.assert_called_once()
    assert probe.call_count == 1


def test_admin_non_404_does_not_render() -> None:
    site = Site(
        "动态站",
        "bottom",
        "https://example.test/article/admin/wanted?url=x",
        parser="dynamic_site",
        payload="admin_article_api",
        api_url="https://example.test/api/wanted",
    )
    renderer = Mock(return_value="不应调用")
    context = FetchContext(text_fetcher=Mock(side_effect=requests.Timeout("slow")), renderer=renderer)
    with pytest.raises(requests.Timeout):
        fetch_admin_article_payload(site, 209, 3, context, lambda *_args: True)
    renderer.assert_not_called()


def test_user_forum_filter_requires_exact_user_boundary() -> None:
    url = "https://forum.test/#/users/200204"
    assert user_forums_api_url(url) == "https://forum.test/api/v1/users/200204/forums"
    records, count = filter_user_forums(json.dumps([{"user_id": 200204, "body": "209期狗蛇"}]), "200204")
    assert count == 1
    assert records[0]["body"] == "209期狗蛇"
    with pytest.raises(ValueError, match="用户ID边界冲突"):
        filter_user_forums(json.dumps([{"user_id": 999, "body": "诱饵"}]), "200204")


def test_page_and_scripts_collects_each_url_once_and_preserves_identity() -> None:
    site = Site(
        "目录",
        "top",
        "https://example.test/topic/123.html",
        parser="site_parser",
        payload="page_and_scripts",
    )
    sources = {
        site.url: '<script src="/a.js"></script><iframe src="/frame.html"></iframe>',
        "https://example.test/a.js": '<iframe src="/frame.html"></iframe> 209期 绝杀二肖',
        "https://example.test/frame.html": "209期 绝杀二肖【狗蛇】",
    }
    fetcher = Mock(side_effect=lambda url, _timeout: sources[url])
    context = FetchContext(text_fetcher=fetcher, renderer=lambda *_args: "")

    bundle = fetch_payload(site, 209, 3, context, lambda *_args: True)
    labels = [document.label for document in bundle.documents]
    assert labels == ["原始页面", "脚本", "iframe"]
    assert all(document.record_id == "topic:123" for document in bundle.documents)
    assert fetcher.call_count == 3


def test_browser_payload_requires_injected_probe_after_render() -> None:
    site = Site("渲染站", "top", "https://example.test/read.php?tid=1", parser="rendered", payload="browser_rendered_page")
    context = FetchContext(
        text_fetcher=lambda _url, _timeout: "空壳",
        renderer=lambda _url, _timeout: "209期 渲染站 绝杀二肖【狗蛇】",
    )
    probe = lambda source, _site, period: str(period) in source and "狗蛇" in source
    bundle = fetch_payload(site, 209, 3, context, probe)
    assert bundle.documents[0].label == "浏览器渲染页面"
    assert bundle.documents[0].record_id == "read:1"


def test_admin_id_is_extracted_from_both_dynamic_routes() -> None:
    assert admin_article_id("https://a.test/article/admin/abc?url=x") == "abc"
    assert admin_article_id("https://a.test/article/manager/xyz?url=x") == "xyz"


def test_fetching_layer_has_no_upward_imports() -> None:
    root = Path(__file__).resolve().parents[1] / "fetching"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    for forbidden in ("from parsers", "from validation", "from cache", "from services", "from output"):
        assert forbidden not in source


def test_curl_transport_decodes_content_and_classifies_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    completed = SimpleNamespace(stdout="209期 狗蛇".encode("utf-8"))
    monkeypatch.setattr("fetching.client.subprocess.run", Mock(return_value=completed))
    assert fetch_curl_text("https://example.test", 3) == "209期 狗蛇"

    monkeypatch.setattr(
        "fetching.client.subprocess.run",
        Mock(side_effect=subprocess.CalledProcessError(35, ["curl.exe"])),
    )
    with pytest.raises(requests.RequestException, match="curl SSL兼容抓取失败"):
        fetch_curl_text("https://example.test", 3)


def test_curl_transport_retries_receive_failure_once(monkeypatch: pytest.MonkeyPatch) -> None:
    completed = SimpleNamespace(stdout="209期 狗蛇".encode("utf-8"))
    runner = Mock(
        side_effect=[
            subprocess.CalledProcessError(56, ["curl.exe"]),
            completed,
        ]
    )
    monkeypatch.setattr("fetching.client.subprocess.run", runner)
    monkeypatch.setattr("fetching.client.time.sleep", lambda _seconds: None)

    assert fetch_curl_text("https://example.test", 3) == "209期 狗蛇"
    assert runner.call_count == 2


def test_context_caches_terminal_http_error_and_rendered_page() -> None:
    response = requests.Response()
    response.status_code = 404
    error = requests.HTTPError("missing", response=response)
    fetcher = Mock(side_effect=error)
    renderer = Mock(return_value="rendered")
    context = FetchContext(text_fetcher=fetcher, renderer=renderer)
    for _ in range(2):
        with pytest.raises(requests.HTTPError):
            context.get_text("https://example.test/missing", 3)
    assert fetcher.call_count == 1
    assert context.get_rendered("https://example.test/page", 3) == "rendered"
    assert context.get_rendered("https://example.test/page", 3) == "rendered"
    assert renderer.call_count == 1


def test_document_bundle_helpers_dedupe_and_strip_markup() -> None:
    documents = [
        PayloadDocument("空", "", ""),
        PayloadDocument("A", "https://a", "正文"),
        PayloadDocument("B", "https://b", "正文"),
        PayloadDocument("A重复", "https://a", "正文"),
    ]
    bundle = make_document_bundle(documents)
    assert [document.label for document in bundle.documents] == ["A", "B"]
    assert visible_text("<style>x</style><script>y</script><p>A&amp;B</p>") == "A&B"
    assert decode_strdecode_blocks("strdecode('not-base64')") == ""


def test_more_record_identities_and_source_boundaries() -> None:
    assert detail_record_identity("https://a.test/Article.Aspx?ListId=247&id=53502") == "article:247:53502"
    assert detail_record_identity("https://a.test/gsb.aspx?id=3238") == "gsb.aspx:3238"
    assert detail_record_identity("https://a.test/bbs/8026") == "bbs:8026"
    assert detail_record_identity("https://a.test/index.html") == ""
    assert source_boundary_id(Site("A", "top", "https://a.test/article/admin/x", payload="admin_article_api")) == "x"
    assert source_boundary_id(Site("A", "top", "https://a.test/#/users/12", payload="tuku_user_forums")) == "user:12"
    assert source_boundary_id(Site("A", "top", "https://a.test/list", payload="topic_list_detail")) == ""


def test_topic_list_detail_uses_direction_window_and_dedupes_url() -> None:
    site = Site("烟雨", "top", "https://example.test/list", parser="list", payload="topic_list_detail")
    listing = (
        '<a href="/topic/209.html?mobile=1">209期 烟雨 绝杀二肖</a>'
        '<a href="/topic/209.html?desktop=1">209期 烟雨 绝杀二肖</a>'
        '<a href="/topic/208.html">208期 烟雨 绝杀二肖</a>'
        '<a href="/topic/207.html">207期 烟雨 绝杀二肖</a>'
    )
    detail = '<script src="/upload/script/209.js"></script><script src="/other.js"></script>'
    script = "document.write(strdecode('%s'))" % encoded("烟雨 209期 绝杀二肖【狗蛇】")
    sources = {
        site.url: listing,
        "https://example.test/topic/209.html": detail,
        "https://example.test/upload/script/209.js": script,
    }
    context = FetchContext(text_fetcher=lambda url, _timeout: sources[url], renderer=lambda *_args: "")
    bundle = fetch_topic_list_detail_payload(site, 209, 3, context)
    assert len(bundle.documents) == 2
    assert {document.label for document in bundle.documents} == {"详情页", "详情脚本"}
    assert next(document for document in bundle.documents if document.label == "详情脚本").record_id == "topic:209"
    assert "狗蛇" in bundle.combined_source

    with pytest.raises(ValueError, match="列表详情站必须指定期数"):
        fetch_topic_list_detail_payload(site, None, 3, context)
    with pytest.raises(ValueError, match="top 列表候选内未找到 206 期"):
        fetch_topic_list_detail_payload(site, 206, 3, context)


def test_topic_list_detail_rejects_detail_without_target_script() -> None:
    site = Site("烟雨", "top", "https://example.test/list", parser="list", payload="topic_list_detail")
    sources = {
        site.url: '<a href="/topic/209.html">209期 烟雨 绝杀二肖</a>',
        "https://example.test/topic/209.html": '<script src="/upload/script/209.js"></script>',
        "https://example.test/upload/script/209.js": "其它栏目",
    }
    context = FetchContext(text_fetcher=lambda url, _timeout: sources[url], renderer=lambda *_args: "")
    with pytest.raises(ValueError, match="详情页脚本内未找到"):
        fetch_topic_list_detail_payload(site, 209, 3, context)


def test_nested_page_scripts_and_identity_guard() -> None:
    site = Site("目录", "top", "https://example.test/topic/123.html", parser="site", payload="page_and_scripts")
    main = '<script src="/upload/script/a.js"></script>'
    image_url = "https://img.example.test/upload/epy/img/12345"
    script = f"目录 209期 绝杀二肖 /htm/data.html /topic/999.html {image_url}"
    nested = '<script src="/nested.js"></script><iframe src="/inner.html"></iframe>'
    sources = {
        site.url: main,
        "https://example.test/upload/script/a.js": script,
        "https://example.test/htm/data.html": nested,
        "https://example.test/nested.js": "嵌套脚本正文",
        "https://example.test/inner.html": "iframe正文",
        image_url: "图片二进制误抓",
    }
    fetcher = Mock(side_effect=lambda url, _timeout: sources[url])
    context = FetchContext(text_fetcher=fetcher, renderer=lambda *_args: "")
    bundle = fetch_payload(site, 209, 3, context, lambda *_args: True)
    labels = [document.label for document in bundle.documents]
    assert "嵌套页面" in labels
    assert "嵌套脚本" in labels
    assert "iframe" in labels
    assert "https://example.test/topic/999.html" not in [document.url for document in bundle.documents]
    assert image_url not in [document.url for document in bundle.documents]


def test_all_payload_strategies_and_unknown_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    page_site = Site("A", "top", "https://example.test/page", parser="p", payload="page")
    context = FetchContext(text_fetcher=lambda _url, _timeout: "page-body", renderer=lambda *_args: "")
    assert fetch_payload(page_site, 209, 3, context, lambda *_args: True).documents[0].label == "原始页面"

    user_site = Site("U", "top", "https://forum.test/#/users/12", parser="u", payload="tuku_user_forums")
    user_context = FetchContext(
        text_fetcher=lambda _url, _timeout: json.dumps([{"user_id": 12, "body": "209期"}]),
        renderer=lambda *_args: "",
    )
    user_bundle = fetch_payload(user_site, 209, 3, user_context, lambda *_args: True)
    assert user_bundle.documents[0].record_id == "user:12"

    scripts_site = Site(
        "S",
        "top",
        "https://example.test/scripts",
        parser="s",
        payload="scripts",
        keywords=("S", "二肖"),
    )
    script_sources = {
        scripts_site.url: '<script src="/good.js"></script><script src="/bad.js"></script>',
        "https://example.test/good.js": "S 二肖",
        "https://example.test/bad.js": "其它",
    }
    script_context = FetchContext(text_fetcher=lambda url, _timeout: script_sources[url], renderer=lambda *_args: "")
    assert len(fetch_payload(scripts_site, 209, 3, script_context, lambda *_args: True).documents) == 2

    missing_site = Site("M", "top", scripts_site.url, parser="m", payload="scripts", keywords=("missing",))
    with pytest.raises(ValueError, match="未找到M目标脚本"):
        fetch_payload(missing_site, 209, 3, script_context, lambda *_args: True)

    curl_site = Site("C", "top", "https://example.test/curl", parser="c", payload="curl_tls10_page_and_scripts")
    monkeypatch.setattr("fetching.page.fetch_curl_text", lambda _url, _timeout: "curl-body")
    assert fetch_payload(curl_site, 209, 3, context, lambda *_args: True).documents[0].source == "curl-body"

    unknown = Site("X", "top", "https://example.test/x", parser="x", payload="unknown")
    with pytest.raises(ValueError, match="未知 payload"):
        fetch_payload(unknown, 209, 3, context, lambda *_args: True)


def test_distant_period_text_does_not_fill_document_limit_before_later_target_script() -> None:
    site = Site(
        "目标站",
        "top",
        "https://example.test/index",
        parser="site_scoped_two_zodiac",
        payload="page_and_scripts",
    )
    script_urls = [f"https://example.test/s{index}.js" for index in range(9)]
    root = "".join(f'<script src="{url}"></script>' for url in script_urls)
    noisy = " ".join(
        f"210期绝杀二肖{'x' * 210}https://example.test/topic/{index}.html"
        for index in range(12)
    )
    sources = {site.url: root, **{url: noisy for url in script_urls[:-1]}}
    sources[script_urls[-1]] = "目标站 210期绝杀二肖【狗蛇】开:11"
    for index in range(12):
        sources[f"https://example.test/topic/{index}.html"] = "诱饵"
    context = FetchContext(text_fetcher=lambda url, _timeout: sources[url], renderer=lambda *_args: "")

    bundle = fetch_payload(site, 210, 3, context, lambda *_args: True)

    assert any(document.url == script_urls[-1] and "狗蛇" in document.source for document in bundle.documents)


def test_browser_payload_raw_success_and_render_rejection() -> None:
    site = Site("R", "top", "https://example.test/read.php?tid=2", parser="r", payload="browser_rendered_page")
    raw_context = FetchContext(text_fetcher=lambda *_args: "209期狗蛇", renderer=lambda *_args: "unused")
    assert fetch_payload(site, 209, 3, raw_context, lambda source, *_args: "狗蛇" in source).documents[0].label == "原始页面"

    rejected = FetchContext(text_fetcher=lambda *_args: "empty", renderer=lambda *_args: "still empty")
    with pytest.raises(ValueError, match="浏览器渲染后仍未找到"):
        fetch_payload(site, 209, 3, rejected, lambda source, *_args: "狗蛇" in source)


def test_dynamic_helpers_and_field_failures() -> None:
    assert admin_article_api_url("https://a.test/article/admin/abc") == "https://a.test/api/proxy/admin-articles/abc"
    assert landing_page_data_api_url("https://a.test/article/manager/abc?url=lh") == "https://a.test/api/proxy/landing-page-data?url=lh"
    with pytest.raises(ValueError, match="无法识别后台文章 ID"):
        admin_article_id("https://a.test/index")
    with pytest.raises(ValueError, match="无法识别落地页"):
        landing_page_data_api_url("https://a.test/article/admin/abc")
    assert decode_admin_article_api_response(json.dumps({"items": []}), "missing").documents == ()
    with pytest.raises(ValueError, match="缺少html字段"):
        decode_admin_article_record({"id": "x", "title": encoded("title")})
    with pytest.raises(ValueError, match="html字段为空"):
        decode_admin_article_record({"id": "x", "title": encoded("title"), "html": encoded(" ")})


def test_admin_raw_page_success_and_empty_api_stays_failed() -> None:
    site = Site(
        "动态站",
        "bottom",
        "https://example.test/article/admin/wanted?url=x",
        parser="dynamic",
        payload="admin_article_api",
        api_url="https://example.test/api/wanted",
    )
    not_found = requests.HTTPError("404")
    response = requests.Response()
    response.status_code = 404
    not_found.response = response
    raw = "/article/admin/wanted 209期 动态站 二肖 狗蛇"
    context = FetchContext(text_fetcher=Mock(side_effect=[not_found, raw]), renderer=Mock(return_value="unused"))
    bundle = fetch_admin_article_payload(site, 209, 3, context, lambda source, *_args: "狗蛇" in source)
    assert bundle.documents[0].label == "后台文章页面"

    empty_context = FetchContext(text_fetcher=lambda *_args: json.dumps({"items": []}), renderer=Mock())
    with pytest.raises(ValueError, match="接口返回内容内未找到"):
        fetch_admin_article_payload(site, 209, 3, empty_context, lambda *_args: True)


def test_render_browser_text_with_fake_playwright(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePage:
        def __init__(self) -> None:
            self.waits = 0

        def goto(self, *_args, **_kwargs) -> None:
            return None

        def wait_for_load_state(self, *_args, **_kwargs) -> None:
            raise RuntimeError("networkidle unavailable")

        def content(self) -> str:
            return "<p>209期 绝杀二肖【狗蛇】</p>"

        def wait_for_timeout(self, _milliseconds: int) -> None:
            self.waits += 1

    page = FakePage()
    browser = SimpleNamespace(new_page=lambda **_kwargs: page, close=Mock())
    chromium = SimpleNamespace(launch=lambda **_kwargs: browser)
    playwright = SimpleNamespace(chromium=chromium)

    class Manager:
        def __enter__(self):
            return playwright

        def __exit__(self, *_args):
            return False

    module = ModuleType("playwright.sync_api")
    module.sync_playwright = lambda: Manager()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    assert "狗蛇" in render_browser_text("https://example.test", 1)
    browser.close.assert_called_once()

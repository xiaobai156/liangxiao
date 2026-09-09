from __future__ import annotations

import requests

from domain.models import PayloadDocument, Site
from fetching.client import FetchContext
from fetching.page import collect_page_and_scripts
from parsers.registry import ParserRegistry
from services.single_period import parse_bundle_for_period
from validation.boundaries import linked_document_is_authorized


def _context(sources: dict[str, str]) -> tuple[FetchContext, list[str]]:
    calls: list[str] = []

    def fetcher(url: str, _timeout: int) -> str:
        calls.append(url)
        return sources[url]

    return FetchContext(text_fetcher=fetcher, renderer=lambda *_args: ""), calls


def test_configured_cross_origin_upload_script_is_collected() -> None:
    site = Site(
        "测试站",
        "top",
        "https://example.test/topic/1.html",
        payload="page_and_scripts",
        linked_document_pattern=r"^https://cdn\.test/upload/script/08/",
    )
    script_url = "https://cdn.test/upload/script/08/target.js"
    context, calls = _context({script_url: "测试站 236期 绝杀二肖【龙虎】"})

    bundle = collect_page_and_scripts(
        site,
        f'<script src="{script_url}"></script>',
        3,
        context,
        lambda source, _site, _period: "236期" in source,
    )

    assert calls == [script_url]
    assert script_url in [document.url for document in bundle.documents]


def test_direct_cross_origin_data_script_can_form_an_authorized_pair_without_global_whitelist() -> None:
    parent_url = "https://example.test/page"
    body_url = "https://cdn.test/upload/script/08/body.js"
    site = Site("测试站", "top", parent_url, payload="page_and_scripts")
    anchor = PayloadDocument("页面", parent_url, f'<script src="{body_url}"></script>')
    body = PayloadDocument(
        "脚本",
        body_url,
        "236期 龙虎",
        parent_url=parent_url,
        link_reference=body_url,
    )

    assert linked_document_is_authorized(site, anchor, body)


def test_cross_origin_non_data_script_still_needs_explicit_authorization() -> None:
    parent_url = "https://example.test/page"
    body_url = "https://cdn.test/assets/body.js"
    site = Site("测试站", "top", parent_url, payload="page_and_scripts")
    anchor = PayloadDocument("页面", parent_url, f'<script src="{body_url}"></script>')
    body = PayloadDocument(
        "脚本",
        body_url,
        "236期 龙虎",
        parent_url=parent_url,
        link_reference=body_url,
    )

    assert not linked_document_is_authorized(site, anchor, body)


def test_irrelevant_nested_links_do_not_make_scan_incomplete() -> None:
    site = Site(
        "测试站",
        "top",
        "https://example.test/page",
        payload="page_and_scripts",
    )
    index_url = "https://example.test/index.js"
    links = "".join(f'/topic/noise-{index}.html ' for index in range(13))
    context, calls = _context(
        {
            index_url: links,
            **{
                f"https://example.test/topic/noise-{index}.html": "noise"
                for index in range(13)
            },
        }
    )

    bundle = collect_page_and_scripts(
        site,
        '<script src="/index.js"></script>',
        3,
        context,
        lambda _source, _site, _period: False,
    )

    assert calls == [index_url]
    assert bundle.scan_complete


def test_missing_auxiliary_script_is_a_complete_absence() -> None:
    site = Site("测试站", "top", "https://example.test/page", payload="page_and_scripts")
    missing_url = "https://example.test/missing.js"

    def fetcher(url: str, _timeout: int) -> str:
        response = requests.Response()
        response.status_code = 404
        error = requests.HTTPError(f"HTTP 404: {url}", response=response)
        raise error

    context = FetchContext(text_fetcher=fetcher, renderer=lambda *_args: "")
    bundle = collect_page_and_scripts(
        site,
        f'<script src="{missing_url}"></script>',
        3,
        context,
    )

    assert bundle.scan_complete


def test_empty_ordinary_auxiliary_script_does_not_invalidate_page() -> None:
    site = Site("测试站", "top", "https://example.test/page", payload="page_and_scripts")
    empty_url = "https://example.test/empty.js"
    context, calls = _context({empty_url: ""})

    bundle = collect_page_and_scripts(
        site,
        '<p>236期 绝杀两肖【猪羊】</p><script src="/empty.js"></script>',
        3,
        context,
    )

    assert calls == [empty_url]
    assert bundle.scan_complete


def test_empty_explicit_data_script_keeps_scan_incomplete() -> None:
    site = Site("测试站", "top", "https://example.test/page", payload="page_and_scripts")
    data_url = "https://example.test/upload/script/08/data.js"
    context, calls = _context({data_url: ""})

    bundle = collect_page_and_scripts(
        site,
        f'<script src="{data_url}"></script>',
        3,
        context,
    )

    assert calls == [data_url]
    assert not bundle.scan_complete


def test_missing_explicit_data_script_keeps_scan_incomplete() -> None:
    site = Site("测试站", "top", "https://example.test/page", payload="page_and_scripts")
    data_url = "https://example.test/upload/script/08/data.js"

    def fetcher(url: str, _timeout: int) -> str:
        response = requests.Response()
        response.status_code = 404
        raise requests.HTTPError(f"HTTP 404: {url}", response=response)

    context = FetchContext(text_fetcher=fetcher, renderer=lambda *_args: "")
    bundle = collect_page_and_scripts(
        site,
        f'<script src="{data_url}"></script>',
        3,
        context,
    )

    assert not bundle.scan_complete


def test_missing_selected_nested_document_keeps_scan_incomplete() -> None:
    site = Site("测试站", "top", "https://example.test/page", payload="page_and_scripts")
    index_url = "https://example.test/index.js"
    nested_url = "https://example.test/topic/missing.html"

    def fetcher(url: str, _timeout: int) -> str:
        if url == index_url:
            return nested_url
        response = requests.Response()
        response.status_code = 404
        raise requests.HTTPError(f"HTTP 404: {url}", response=response)

    context = FetchContext(text_fetcher=fetcher, renderer=lambda *_args: "")
    bundle = collect_page_and_scripts(
        site,
        '<script src="/index.js"></script>',
        3,
        context,
        lambda source, _site, _period: nested_url in source,
    )

    assert not bundle.scan_complete


def test_unresolved_script_template_is_not_requested() -> None:
    site = Site("测试站", "top", "https://example.test/page", payload="page_and_scripts")
    context, calls = _context({})

    bundle = collect_page_and_scripts(
        site,
        '<script src="/upload/script/08/${data[id]}.js"></script>',
        3,
        context,
    )

    assert calls == []
    assert bundle.scan_complete


def test_configured_scripts_with_the_same_parent_can_be_paired() -> None:
    parent_url = "https://example.test/page"
    anchor_url = "https://cdn.test/upload/script/08/anchor.js"
    body_url = "https://cdn.test/upload/script/08/body.js"
    site = Site(
        "测试站",
        "top",
        parent_url,
        payload="page_and_scripts",
        linked_document_pattern=r"https://cdn\.test/upload/script/08/",
    )
    anchor = PayloadDocument(
        "标题脚本",
        anchor_url,
        "测试站标题",
        parent_url=parent_url,
        link_reference=anchor_url,
    )
    body = PayloadDocument(
        "正文脚本",
        body_url,
        "236期 龙虎",
        parent_url=parent_url,
        link_reference=body_url,
    )

    assert linked_document_is_authorized(site, anchor, body)


def test_configured_title_link_is_followed_without_an_inline_record() -> None:
    site = Site(
        "武林高手",
        "top",
        "https://example.test/page",
        payload="page_and_scripts",
        title=r"武林高手\s*\d{3}\s*期\s*《\s*绝杀两肖\s*》",
        linked_document_pattern=r"https://cdn\.test/upload/script/08/",
    )
    index_url = "https://example.test/index.js"
    body_url = "https://cdn.test/upload/script/08/body.js"
    context, calls = _context(
        {
            index_url: f"武林高手 237期 《绝杀两肖》 {body_url}",
            body_url: "236期 绝杀两肖【蛇龙】 开00准",
        }
    )

    bundle = collect_page_and_scripts(
        site,
        '<script src="/index.js"></script>',
        3,
        context,
        lambda _source, _site, _period: False,
    )

    assert calls == [index_url, body_url]
    assert body_url in [document.url for document in bundle.documents]


def test_configured_title_is_bound_to_the_immediately_following_link() -> None:
    site = Site(
        "武林高手",
        "top",
        "https://example.test/page",
        payload="page_and_scripts",
        title=r"武林高手\s*\d{3}\s*期\s*《\s*绝杀两肖\s*》",
        linked_document_pattern=r"https://cdn\.test/upload/script/08/",
    )
    index_url = "https://example.test/index.js"
    wrong_url = "https://cdn.test/upload/script/08/wrong.js"
    body_url = "https://cdn.test/upload/script/08/body.js"
    context, calls = _context(
        {
            index_url: (
                f"武林高手 236期 《三肖发财》 {wrong_url} "
                f"武林高手 236期 《绝杀两肖》 {body_url}"
            ),
            body_url: "236期 绝杀两肖【蛇龙】 开00准",
        }
    )

    bundle = collect_page_and_scripts(
        site,
        '<script src="/index.js"></script>',
        3,
        context,
        lambda _source, _site, _period: False,
    )

    assert calls == [index_url, body_url]
    assert wrong_url not in [document.url for document in bundle.documents]


def test_wulin_configured_title_link_selects_the_requested_top_period() -> None:
    site = Site(
        "武林高手",
        "top",
        "https://example.test/page",
        parser="wulin_gaoshou_linked_top",
        payload="page_and_scripts",
        title=r"武林高手\s*\d{3}\s*期\s*《\s*绝杀两肖\s*》",
        linked_document_pattern=r"https://cdn\.test/upload/script/08/",
    )
    index_url = "https://example.test/index.js"
    wrong_url = "https://cdn.test/upload/script/08/wrong.js"
    body_url = "https://cdn.test/upload/script/08/body.js"
    context, calls = _context(
        {
            index_url: (
                f"武林高手 237期 《三肖发财》 {wrong_url} "
                f"武林高手 237期 《绝杀两肖》 {body_url}"
            ),
            body_url: (
                "237期 绝杀两肖【牛猪】开00准 "
                "236期 绝杀两肖【蛇龙】开00准 "
                "235期 绝杀两肖【鸡马】开00准"
            ),
        }
    )

    bundle = collect_page_and_scripts(
        site,
        '<script src="/index.js"></script>',
        3,
        context,
        lambda _source, _site, _period: False,
    )
    _records, selected = parse_bundle_for_period(
        bundle,
        site,
        236,
        ParserRegistry.bind_sites([site]),
    )

    assert calls == [index_url, body_url]
    assert selected.period == 236
    assert selected.zodiac == "蛇龙"
    assert selected.position >= 0

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from domain.models import Site
from fetching.client import FetchContext, decode_response_content
from fetching.dynamic_article import find_unique_admin_article_record
from fetching.user_forum import filter_user_forums
from services.single_period import fetch_topic_list_detail_for_period


def test_response_decoding_skips_duplicate_encoding_hints() -> None:
    class RecordingContent:
        def __init__(self) -> None:
            self.encodings: list[str] = []

        def decode(self, encoding: str, errors: str = "strict") -> str:
            self.encodings.append(encoding)
            return "209期 狗蛇"

    content = RecordingContent()
    response = SimpleNamespace(
        content=content,
        encoding="utf-8",
        apparent_encoding="utf-8",
    )

    assert decode_response_content(response) == "209期 狗蛇"
    assert content.encodings == ["utf-8", "gb18030", "gbk"]


def test_top_topic_list_scans_complete_list_before_direction_window() -> None:
    site = Site("烟雨", "top", "https://example.test/list", parser="list", payload="topic_list_detail")
    sources = {
        site.url: (
            '<a href="/topic/209.html">209期 烟雨 绝杀二肖</a>'
            '<a href="/topic/208.html">208期 烟雨 绝杀二肖</a>'
            '<a href="/topic/207.html">207期 烟雨 绝杀二肖</a>'
            '<a href="/list?page=2">下一页</a>'
        ),
        "https://example.test/list?page=2": "<p>无更多目标</p>",
        "https://example.test/topic/209.html": '<script src="/upload/script/209.js"></script>',
        "https://example.test/upload/script/209.js": "烟雨 209期 绝杀二肖【狗蛇】",
    }
    fetcher = Mock(side_effect=lambda url, _timeout: sources[url])
    context = FetchContext(text_fetcher=fetcher, renderer=lambda *_args: "")

    bundle = fetch_topic_list_detail_for_period(site, 209, 3, context)

    assert "https://example.test/list?page=2" in [call.args[0] for call in fetcher.call_args_list]
    assert "狗蛇" in bundle.combined_source


def test_bottom_topic_list_fetches_next_page_for_last_three_periods() -> None:
    site = Site("烟雨", "bottom", "https://example.test/list", parser="list", payload="topic_list_detail")
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
    fetcher = Mock(side_effect=lambda url, _timeout: sources[url])
    context = FetchContext(text_fetcher=fetcher, renderer=lambda *_args: "")

    bundle = fetch_topic_list_detail_for_period(site, 211, 3, context)

    called_urls = [call.args[0] for call in fetcher.call_args_list]
    assert "https://example.test/list?page=2" in called_urls
    assert "狗蛇" in bundle.combined_source


def test_duplicate_admin_article_id_still_raises_conflict() -> None:
    source = json.dumps(
        {
            "items": [
                {"id": "same", "title": "标题一", "html": "正文一"},
                {"id": "same", "title": "标题二", "html": "正文二"},
            ]
        },
        ensure_ascii=False,
    )

    with pytest.raises(ValueError, match="多个同ID文章"):
        find_unique_admin_article_record(source, "same")


def test_user_forum_filter_reuses_decoded_list_after_validation(monkeypatch) -> None:
    decoded = [{"user_id": 200204, "body": "209期狗蛇"}]
    monkeypatch.setattr("fetching.user_forum.json.loads", lambda _source: decoded)

    records, count = filter_user_forums(json.dumps(decoded), "200204")

    assert records is decoded
    assert count == 1

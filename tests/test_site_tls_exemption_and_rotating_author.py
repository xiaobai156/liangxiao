"""284 期两处修复的回归测试。

1) 站点级 TLS 豁免：`allow_invalid_certificate` 只作用于显式声明的站点
   （朱雀 站方证书过期），不改变其它站点的严格校验，也不共享渲染上下文。
2) 花颜月貌 后台文章槽位署名轮换：同一 article id 上『花颜月貌』→『红云红河』
   （284 期起）仍能取值；未核实的署名按解析失败暴露，且不放宽其它站点。
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import fetching.browser as browser
from config.loader import ConfigError, site_from_mapping
from domain.models import Site
from fetching.client import FetchContext, get_site_rendered, get_site_text
from fetching.urls import FetchedText
from parsers.dynamic import (
    HUAYAN_YUEMAO_ROTATING_AUTHORS,
    parse_huayan_yuemao_admin_article_records,
    parse_yeyeshengcai_admin_article_records,
)

ROOT = Path(__file__).resolve().parents[1]


def _entries() -> list[dict[str, object]]:
    return json.loads((ROOT / "config" / "sites.json").read_text(encoding="utf-8-sig"))


def _entry(name: str) -> dict[str, object]:
    return next(item for item in _entries() if item["name"] == name)


def _site(name: str) -> Site:
    return site_from_mapping(_entry(name))


def test_configured_zhuque_site_is_the_only_tls_exemption() -> None:
    assert _site("朱雀").allow_invalid_certificate is True
    assert _site("花颜月貌").allow_invalid_certificate is False
    assert [
        item["name"]
        for item in _entries()
        if item.get("allow_invalid_certificate") is True
    ] == ["朱雀"]


@pytest.mark.parametrize("value", ["true", 1, None, []])
def test_tls_exemption_flag_must_be_a_boolean(value: object) -> None:
    with pytest.raises(ConfigError, match="allow_invalid_certificate"):
        site_from_mapping({**_entry("朱雀"), "allow_invalid_certificate": value})


def test_tls_exemption_text_transport_is_scoped_to_its_site(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://a.test/page"
    strict = Mock(return_value=FetchedText("strict body", url, url))
    legacy = Mock(return_value=FetchedText("legacy body", url, url))
    monkeypatch.setattr("fetching.client.fetch_text", strict)
    monkeypatch.setattr("fetching.client.fetch_curl_text", legacy)

    exempt = Site("朱雀", "top", url, payload="browser_rendered_page",
                  allow_invalid_certificate=True)
    plain = Site("严格站", "top", url, payload="browser_rendered_page")

    assert get_site_text(FetchContext(), url, 2, exempt) == "legacy body"
    assert get_site_text(FetchContext(), url, 2, plain) == "strict body"
    legacy.assert_called_once_with(url, 2, insecure=True, allowed_origins=())
    strict.assert_called_once_with(url, 2)


class _FakePage:
    def __init__(self, html: str) -> None:
        self._html = html
        self.url = "about:blank"
        self.main_frame = object()
        self.closed = 0

    def route(self, *_args: object) -> None:
        return None

    def goto(self, url: str, *, wait_until: str, timeout: int) -> None:
        self.url = url
        return None

    def wait_for_load_state(self, *_args: object, **_kwargs: object) -> None:
        return None

    def wait_for_timeout(self, _milliseconds: int) -> None:
        return None

    def content(self) -> str:
        return self._html

    def close(self) -> None:
        self.closed += 1


class _FakeBrowserContext:
    def __init__(self, html: str, options: dict[str, object]) -> None:
        self._html = html
        self.options = options
        self.pages: list[_FakePage] = []
        self.closed = 0

    def new_page(self) -> _FakePage:
        page = _FakePage(self._html)
        self.pages.append(page)
        return page

    def close(self) -> None:
        self.closed += 1


class _FakeBrowser:
    def __init__(self, html: str) -> None:
        self._html = html
        self.contexts: list[_FakeBrowserContext] = []

    def new_context(self, *, user_agent: str, **options: object) -> _FakeBrowserContext:
        assert user_agent
        context = _FakeBrowserContext(self._html, options)
        self.contexts.append(context)
        return context

    def close(self) -> None:
        return None


class _FakePlaywright:
    def __init__(self, html: str) -> None:
        self.browser = _FakeBrowser(html)
        self.chromium = self

    def launch(self, *, headless: bool) -> _FakeBrowser:
        assert headless
        return self.browser

    def stop(self) -> None:
        return None


def test_tls_exempt_render_uses_its_own_insecure_context(monkeypatch: pytest.MonkeyPatch) -> None:
    html = "<p>朱雀 284期→禁用二肖→【鼠牛】开:00</p>"
    runtime = _FakePlaywright(html)
    monkeypatch.setattr(browser, "sync_playwright", lambda: SimpleNamespace(start=lambda: runtime))
    context = FetchContext()
    plain = Site("严格站", "top", "https://a.test/page", payload="browser_rendered_page")
    exempt = Site("朱雀", "top", "https://a.test/page", payload="browser_rendered_page",
                  allow_invalid_certificate=True)

    assert get_site_rendered(context, "https://a.test/page", 3, plain) == html
    assert get_site_rendered(context, "https://a.test/page", 3, exempt) == html
    assert get_site_rendered(context, "https://a.test/page", 3, exempt) == html
    context.close()

    assert [item.options for item in runtime.browser.contexts] == [
        {},
        {"ignore_https_errors": True},
    ]
    assert [item.closed for item in runtime.browser.contexts] == [1, 1]
    assert [len(item.pages) for item in runtime.browser.contexts] == [1, 1]


ROTATED_BODY = (
    "283期:『花颜月貌』😜 绝杀②肖 😜【牛.虎】开: 牛08准\n"
    "284期:『红云红河』😜 绝杀②肖 😜【猴.蛇】开: 00\n"
    "285期:『张三』😜 绝杀②肖 😜【猪.鼠】开: 00\n"
)


def test_huayan_yuemao_accepts_the_rotated_author_only() -> None:
    site = _site("花颜月貌")

    records = parse_huayan_yuemao_admin_article_records(ROTATED_BODY, site)

    assert [(item.period, item.zodiac, item.open_result) for item in records] == [
        (283, "牛虎", "牛08"),
        (284, "猴蛇", "00"),
    ]
    assert HUAYAN_YUEMAO_ROTATING_AUTHORS == ("花颜月貌", "红云红河")
    assert site.name in HUAYAN_YUEMAO_ROTATING_AUTHORS


def test_huayan_yuemao_parser_guard_rejects_other_sites_and_payloads() -> None:
    other = _site("夜夜笙财")

    assert parse_huayan_yuemao_admin_article_records(ROTATED_BODY, other) == []
    assert parse_huayan_yuemao_admin_article_records(
        ROTATED_BODY, replace(_site("花颜月貌"), payload="page")) == []


def test_rotating_author_allowlist_does_not_widen_other_article_sites() -> None:
    other = _site("夜夜笙财")

    assert parse_yeyeshengcai_admin_article_records(
        "284期:『红云红河』📸 绝杀二肖 📸【猴.蛇】开: 00\n", other) == []
    assert parse_yeyeshengcai_admin_article_records(
        "284期:『夜夜笙财』📸 绝杀二肖 📸【猴.蛇】开: 00\n", other
    )[0].zodiac == "猴蛇"

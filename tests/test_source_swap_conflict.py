"""目标期冲突不得触发「换源」（浏览器渲染页 / 文章渲染页顶替原始页面）。

原来 source_has_target 把所有 ValueError 都吞成 False，于是「两个不同结果」的
冲突会被当成「这份来源没有目标」，随后换一份只剩一种值的页面，冲突被抹平。
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from domain.models import Record, Site
from fetching.client import FetchContext
from services import single_period


class StubRegistry:
    def __init__(self, records_by_source: dict[str, list[Record]]) -> None:
        self.records_by_source = records_by_source

    def parse(self, source, site: Site) -> list[Record]:
        text = source.source if hasattr(source, "source") else source
        return list(self.records_by_source.get(text, ()))


def make_record(period: int, zodiac: str, position: int = 0) -> Record:
    return Record(period, zodiac, "开", f"{period:03d}期 {zodiac}", position=position)


def make_site(payload: str = "browser_rendered_page") -> Site:
    return Site("测试站", "top", "https://example.test/topic/1.html", parser="stub", payload=payload)


def make_context(raw: str, rendered: str, calls: list[str]) -> FetchContext:
    def fetcher(_url: str, _timeout: int) -> str:
        return raw

    def renderer(url: str, _timeout: int) -> str:
        calls.append(url)
        return rendered

    return FetchContext(text_fetcher=fetcher, renderer=renderer)


def test_source_has_target_keeps_a_conflicting_raw_source() -> None:
    """冲突时探针返回「有目标」，原始页面不被替换，冲突留给后续校验报出。"""
    site = make_site(payload="page")
    source = "原始页面里有两条 209 期不同结果"
    registry = StubRegistry(
        {
            source: [
                make_record(209, "狗蛇"),
                make_record(209, "鼠牛", position=40),
            ]
        }
    )

    assert single_period.source_has_target(registry, source, site, 209)


def test_same_value_two_positions_does_not_get_laundered_by_the_renderer() -> None:
    """同值两位置也禁止换源：渲染页给的值会被无条件采信，等于把值换掉。"""
    site = make_site()
    raw = "原始页面同一行出现两次"
    rendered = "渲染页给出另一个值"
    rendered_calls: list[str] = []
    context = make_context(raw, rendered, rendered_calls)
    registry = StubRegistry(
        {
            raw: [make_record(209, "狗蛇"), make_record(209, "狗蛇", position=40)],
            rendered: [make_record(209, "鼠牛")],
        }
    )

    result = single_period.scrape_site(site, 209, 3, context, registry)

    assert not result.ok
    assert "数据存在冲突" in (result.error or "")
    assert rendered_calls == []


def test_conflicting_raw_page_is_not_replaced_by_the_rendered_page() -> None:
    site = make_site()
    raw = "原始页面含两条不同结果"
    rendered = "渲染页只剩一种值"
    rendered_calls: list[str] = []
    context = make_context(raw, rendered, rendered_calls)
    registry = StubRegistry(
        {
            raw: [make_record(209, "狗蛇"), make_record(209, "鼠牛", position=40)],
            rendered: [make_record(209, "狗蛇")],
        }
    )

    result = single_period.scrape_site(site, 209, 3, context, registry)

    assert not result.ok
    assert "数据存在冲突" in (result.error or "")
    assert rendered_calls == []


def test_rendered_page_is_still_used_when_the_raw_page_has_no_target(monkeypatch) -> None:
    site = make_site()
    raw = "空壳页面"
    rendered = "209期 绝杀二肖【狗蛇】"
    rendered_calls: list[str] = []
    context = make_context(raw, rendered, rendered_calls)
    record = make_record(209, "狗蛇")
    registry = StubRegistry({rendered: [record]})
    monkeypatch.setattr(
        single_period, "parse_bundle_for_period", lambda *_args: ([record], record)
    )

    result = single_period.scrape_site(site, 209, 3, context, registry)

    assert result.ok
    assert rendered_calls == [site.url]


class RaisingRegistry:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def parse(self, source, site: Site) -> list[Record]:
        raise self.exc


def test_period_free_scoring_still_treats_a_parse_conflict_as_no_target() -> None:
    """period=None 是嵌套链接评分：parse 自己抛冲突也必须保持 False，否则会多跟进一份文档。"""
    from domain.errors import CandidateConflict

    site = make_site(payload="page")
    registry = RaisingRegistry(CandidateConflict("用户论坛同一快照结果不同", []))

    assert not single_period.source_has_target(registry, "任意来源", site, None)

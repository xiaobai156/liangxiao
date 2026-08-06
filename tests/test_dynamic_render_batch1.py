from __future__ import annotations

from pathlib import Path

import pytest

from config.loader import load_sites
from domain.models import DocumentBundle, PayloadDocument
from fetching.client import FetchContext
from fetching.page import fetch_payload
from parsers.registry import ENGINE_REGISTRY, ParserRegistry
from services.single_period import parse_bundle_for_period
from validation.boundaries import expected_record_id


ROOT = Path(__file__).resolve().parents[1]
BATCH_NAMES = (
    "专心致志",
    "心里有底",
    "人不聊生",
    "一丘之貉",
    "科方东文",
    "狙击杀手",
    "积少成多第二",
    "超级稳定",
    "白蛇",
    "反反复复",
    "来情去意",
    "文房四宝",
    "七嘴八舌",
    "恍然自失",
    "一言一句",
    "博闻茄子",
    "流合",
    "宿柳眠花",
    "不由自主",
    "俯拾地芥",
    "不伏烧埋",
    "莲叶满池",
    "徒手敬月",
    "自然和谐",
)


def configured_batch_sites() -> dict[str, object]:
    sites = load_sites(ROOT / "config" / "sites.json", allowed_parsers=ENGINE_REGISTRY)
    return {site.name: site for site in sites if site.name in BATCH_NAMES}


def _anchor(site) -> str:
    return {
        "科方东文": "网红帖 216期: 【绝杀二肖】 作者:科方东文",
        "积少成多第二": "积少成多 发表于",
        "白蛇": "白蛇传 【绝杀二肖】 →",
        "反反复复": "反反复复 发表于",
    }.get(site.name, site.name)


def _record_line(site, period: int, zodiac: str = "鼠虎") -> str:
    if site.parser == "liuhe_tail":
        return f"{period}期:【绝杀两肖】【{zodiac}】开:00准"
    return f"{period}期:绝杀二肖【{zodiac}】开:00准"


def _source(site, periods: tuple[int, ...] | None = None, *, conflict: bool = False) -> str:
    if periods is None:
        periods = (216, 215, 214) if site.pick == "top" else (214, 215, 216)
    lines = [_anchor(site)]
    lines.extend(_record_line(site, period) for period in periods)
    if conflict:
        # Keep both 216 candidates inside the configured three-record window.
        if site.pick == "top":
            lines = [_anchor(site), _record_line(site, 216), _record_line(site, 216, "蛇狗"), _record_line(site, 215)]
        else:
            lines.extend((_record_line(site, 216, "蛇狗"),))
    if site.name == "反反复复" or site.parser == "liuhe_tail":
        lines.append("上一篇：下一篇")
    return "\n".join(lines)


def _document(site, source: str, *, url: str | None = None) -> PayloadDocument:
    return PayloadDocument(
        "测试文档",
        url or site.url,
        source,
        record_id=expected_record_id(site),
    )


@pytest.mark.parametrize("name", BATCH_NAMES)
def test_batch1_target_adjacent_and_nonexistent_periods(name: str) -> None:
    site = configured_batch_sites()[name]
    registry = ParserRegistry.bind_sites([site])
    bundle = DocumentBundle((_document(site, _source(site)),))

    _records, selected = parse_bundle_for_period(bundle, site, 216, registry)
    assert (selected.period, selected.zodiac) == (216, "鼠虎")
    _records, adjacent = parse_bundle_for_period(bundle, site, 215, registry)
    assert (adjacent.period, adjacent.zodiac) == (215, "鼠虎")
    with pytest.raises(ValueError, match="217"):
        parse_bundle_for_period(bundle, site, 217, registry)


@pytest.mark.parametrize("name", BATCH_NAMES)
def test_batch1_wrong_anchor_is_not_accepted(name: str) -> None:
    site = configured_batch_sites()[name]
    registry = ParserRegistry.bind_sites([site])
    wrong = _document(site, "错误栏目\n" + "\n".join(_record_line(site, period) for period in (216, 215, 214)))

    if site.parser == "liuhe_tail":
        # This dedicated parser is keyed by the exact 绝杀两肖 block; it has
        # no site-name heading in the live script, so reject a wrong semantic
        # block instead of requiring a generic heading that would break it.
        wrong = _document(site, "错误栏目\n216期:【其他资料】【鼠虎】开:00准")
        assert registry.parse(wrong, site) == []
    else:
        assert registry.parse(wrong, site) == []


@pytest.mark.parametrize("name", BATCH_NAMES)
def test_batch1_direction_window_and_same_period_conflict(name: str) -> None:
    site = configured_batch_sites()[name]
    registry = ParserRegistry.bind_sites([site])
    order = (216, 215, 214, 213) if site.pick == "top" else (213, 214, 215, 216)
    overflow = DocumentBundle((_document(site, _source(site, order)),))
    with pytest.raises(ValueError, match="213"):
        parse_bundle_for_period(overflow, site, 213, registry)

    conflict = DocumentBundle((_document(site, _source(site, conflict=True)),))
    with pytest.raises(ValueError, match="数据存在冲突"):
        parse_bundle_for_period(conflict, site, 216, registry)


@pytest.mark.parametrize("name", tuple(item for item in BATCH_NAMES if item != "流合"))
def test_batch1_cross_document_cannot_borrow_anchor(name: str) -> None:
    site = configured_batch_sites()[name]
    registry = ParserRegistry.bind_sites([site])
    anchor = _document(site, _anchor(site))
    body_source = "\n".join(_record_line(site, period) for period in (214, 215, 216))
    body = _document(site, body_source, url="https://other.test/body.js")

    with pytest.raises(ValueError, match="多文档内未能独立验证"):
        parse_bundle_for_period(DocumentBundle((anchor, body)), site, 216, registry)


@pytest.mark.parametrize("name", ("专心致志", "心里有底", "一言一句"))
def test_browser_sites_use_same_url_rendered_target(name: str) -> None:
    site = configured_batch_sites()[name]
    assert site.payload == "browser_rendered_page"
    registry = ParserRegistry.bind_sites([site])
    raw = "<html><body>动态空壳</body></html>"
    rendered = _source(site)
    context = FetchContext(
        text_fetcher=lambda _url, _timeout: raw,
        renderer=lambda _url, _timeout: rendered,
    )

    def probe(source, target, issue):
        records = registry.parse(source, target)
        return bool(records) if issue is None else any(record.period == issue for record in records)

    bundle = fetch_payload(site, 216, 3, context, probe)
    assert bundle.documents[0].label == "浏览器渲染页面"
    assert bundle.documents[0].url == site.url
    assert bundle.documents[0].record_id == expected_record_id(site)
    _records, selected = parse_bundle_for_period(bundle, site, 216, registry)
    assert (selected.period, selected.zodiac) == (216, "鼠虎")

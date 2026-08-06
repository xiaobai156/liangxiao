from __future__ import annotations

from pathlib import Path

import pytest

from config.loader import load_sites
from domain.models import DocumentBundle, PayloadDocument, Site
from fetching.client import FetchContext
from fetching.page import fetch_payload
from parsers.registry import ENGINE_REGISTRY, ParserRegistry
from services.single_period import parse_bundle_for_period
from validation.boundaries import expected_record_id


ROOT = Path(__file__).resolve().parents[1]

BATCH_NAMES = (
    "雷锋二",
    "一言中的",
    "酒有别肠",
    "口有同嗜",
    "金科玉律",
    "一箭双雕",
    "绘声绘色",
    "相见恨晚",
    "澳门-白虎",
    "最美记忆",
    "万里长城",
    "老大主",
    "捕风捉影",
    "碎银几两",
    "无忧无虑",
    "白手起家",
    "手起刀落",
    "武林高手",
    "广西仔",
    "广东",
    "王者九点杀二肖图",
    "没忍住啊",
)

EXPECTED_PICKS = {
    "雷锋二": "top",
    "一言中的": "top",
    "酒有别肠": "top",
    "口有同嗜": "bottom",
    "金科玉律": "top",
    "一箭双雕": "top",
    "绘声绘色": "top",
    "相见恨晚": "top",
    "澳门-白虎": "top",
    "最美记忆": "top",
    "万里长城": "top",
    "老大主": "bottom",
    "捕风捉影": "top",
    "碎银几两": "top",
    "无忧无虑": "top",
    "白手起家": "bottom",
    "手起刀落": "top",
    "武林高手": "top",
    "广西仔": "top",
    "广东": "top",
    "王者九点杀二肖图": "top",
    "没忍住啊": "top",
}


def configured_batch_sites() -> dict[str, Site]:
    sites = load_sites(ROOT / "config" / "sites.json", allowed_parsers=ENGINE_REGISTRY)
    return {site.name: site for site in sites if site.name in BATCH_NAMES}


def _document(site, source: str, *, url: str | None = None) -> PayloadDocument:
    return PayloadDocument(
        "测试文档",
        url or site.url,
        source,
        record_id=expected_record_id(site),
    )


def _rows(periods: tuple[int, ...], zodiac: str = "鼠虎") -> str:
    return "\n".join(f"{period:03d}期:绝杀二肖【{zodiac}】开:00准" for period in periods)


def _named_source(name: str, periods: tuple[int, ...]) -> str:
    rows = _rows(periods)
    if name == "雷锋二":
        return f"杀料榜 216期【稳杀二肖】\n{rows}"
    if name == "一言中的":
        return f"作者：一言中的\n{rows}"
    if name == "酒有别肠":
        return f"作者：酒有别肠\n{rows}"
    if name == "口有同嗜":
        return f"口有同嗜发表于\n{rows}\n上一篇："
    if name == "金科玉律":
        return f"作者：金科玉律\n{rows}"
    if name == "一箭双雕":
        return "绝杀区 216期【绝杀二肖】\n" + "\n".join(
            f"{period:03d}期【绝杀二肖】~鼠虎~开00" for period in periods
        )
    if name == "相见恨晚":
        return "相见恨晚\n" + "\n".join(
            f"{period:03d}期《绝杀2肖》☆鼠虎☆开00" for period in periods
        )
    if name == "没忍住啊":
        return f"高手贴 216期：[绝杀二肖]━ 118060d.com\n{rows}"
    return f"{name}\n{rows}"


def _special_source(name: str, periods: tuple[int, ...]) -> str:
    if name == "老大主":
        ten = "牛马羊鸡狗猪兔龙蛇猴"
        return "【老大主十肖】\n" + "\n".join(
            f"{period:03d}期【{ten}】" for period in periods
        ) + "\n【老二主四头】"
    if name == "捕风捉影":
        return (
            "216期：【绝杀二肖】独家提供 作者：捕风捉影\n"
            + "\n".join(
                f"{period:03d}期：绝杀二肖【鼠虎】开:00准" for period in periods
            )
        )
    if name == "碎银几两":
        ten = "牛马羊鸡狗猪兔龙蛇猴"
        return (
            "216期：碎银几两【必中十肖】作者：碎银几两\n"
            + "\n".join(
                f"{period:03d}期【必中十肖】【{ten}】开:00准" for period in periods
            )
        )
    if name == "无忧无虑":
        return (
            "216期【绝杀二肖】作者：无忧无虑\n"
            + "\n".join(
                f"{period:03d}期：➹绝杀二肖➹_【鼠虎】_开:00准" for period in periods
            )
        )
    if name == "手起刀落":
        return "手起刀落【绝杀二肖】\n" + "\n".join(
            f"{period:03d}期绝杀两肖【鼠虎】开:00准" for period in periods
        )
    if name == "王者九点杀二肖图":
        return (
            "《王者九点网 ㊣ 杀禁统计》\n"
            + "\n".join(
                f"{period:03d}期杀肖统计\n杀二肖图：【鼠虎】 特开 00" for period in periods
            )
        )
    raise AssertionError(name)


def _whitehand_source() -> str:
    # The dedicated tail parser requires a complete 365-period cycle.  End at
    # 216 so the configured bottom window is exactly 214/215/216.
    periods = list(range(217, 366)) + list(range(1, 217))
    rows = "\n".join(f"{period:03d}期:杀二肖【鼠虎】开:00" for period in periods)
    return f"216期:白手起家【稳杀二肖】\n{rows}\n上一篇：下一篇"


def _linked_bundle(site, anchor: str, body: str) -> DocumentBundle:
    body_url = "https://xia06.cosds.ahsccn.com/upload/script/08/target.js"
    page = _document(site, f'{anchor}<script src="{body_url}"></script>')
    script = PayloadDocument(
        "脚本解码",
        body_url,
        body,
        record_id=expected_record_id(site),
        parent_url=site.url,
        link_reference=body_url,
    )
    return DocumentBundle((page, script))


def _bundle_for(site, periods: tuple[int, ...]) -> DocumentBundle:
    if site.name == "白手起家":
        return DocumentBundle((_document(site, _whitehand_source()),))
    if site.name == "武林高手":
        return _linked_bundle(
            site,
            f"武林高手 {periods[0]:03d}期《绝杀两肖》",
            "\n".join(f"{period:03d}期:绝杀两肖【鼠虎】开:00准" for period in periods),
        )
    if site.name == "广西仔":
        return _linked_bundle(
            site,
            "澳门广西仔",
            f"{periods[0]:03d}期:本站推荐【绝杀二肖】\n"
            + "\n".join(
                f"{period:03d}期:绝杀二肖-(鼠虎)-开0000准" for period in periods
            ),
        )
    if site.name == "广东":
        return _linked_bundle(
            site,
            "澳门广东八二站",
            f"{periods[0]:03d}期:【绝杀二肖】\n"
            + "\n".join(
                f"{period:03d}期（必杀二肖）【鼠虎】开:00准" for period in periods
            ),
        )
    if site.name in {"老大主", "捕风捉影", "碎银几两", "无忧无虑", "手起刀落", "王者九点杀二肖图"}:
        return DocumentBundle((_document(site, _special_source(site.name, periods)),))
    if site.name == "澳门-白虎":
        # 216 exists in a different column, but not in this site's target
        # 『绝杀二肖』 block.  The top window must therefore reject it.
        source = (
            "澳门-白虎『绝杀二肖』\n"
            + _rows(periods)
            + "\n澳门-白虎『六肖12码』\n216期【六肖12码】开:00"
        )
        return DocumentBundle((_document(site, source),))
    source = _named_source(site.name, periods)
    return DocumentBundle((_document(site, source),))


def test_batch3_configuration_scope_and_meiren_anchor() -> None:
    sites = configured_batch_sites()
    assert set(sites) == set(BATCH_NAMES)
    assert {name: sites[name].pick for name in BATCH_NAMES} == EXPECTED_PICKS
    assert sites["没忍住啊"].title.endswith(r"118060[a-z]\.com")
    assert sites["老大主"].parser == "laodazhu_ten_zodiac_complement"
    assert sites["碎银几两"].parser == "suiyin_jiliang_ten_zodiac_complement"


@pytest.mark.parametrize(
    "name",
    tuple(
        name
        for name in BATCH_NAMES
        if name not in {"澳门-白虎", "老大主", "捕风捉影", "碎银几两", "无忧无虑", "白手起家", "手起刀落", "武林高手", "广西仔", "广东", "王者九点杀二肖图"}
    ),
)
def test_batch3_named_sites_target_adjacent_and_nonexistent(name: str) -> None:
    site = configured_batch_sites()[name]
    registry = ParserRegistry.bind_sites([site])
    periods = (214, 215, 216) if site.pick == "bottom" else (216, 215, 214)
    bundle = _bundle_for(site, periods)
    _records, selected = parse_bundle_for_period(bundle, site, 216, registry)
    assert (selected.period, selected.zodiac) == (216, "鼠虎")
    _records, adjacent = parse_bundle_for_period(bundle, site, 215, registry)
    assert (adjacent.period, adjacent.zodiac) == (215, "鼠虎")
    with pytest.raises(ValueError, match="217"):
        parse_bundle_for_period(bundle, site, 217, registry)


@pytest.mark.parametrize(
    "name",
    ("老大主", "捕风捉影", "碎银几两", "无忧无虑", "白手起家", "手起刀落", "武林高手", "广西仔", "广东", "王者九点杀二肖图"),
)
def test_batch3_specialized_sites_period_boundaries(name: str) -> None:
    site = configured_batch_sites()[name]
    registry = ParserRegistry.bind_sites([site])
    periods = (214, 215, 216) if site.pick == "bottom" else (216, 215, 214)
    bundle = _bundle_for(site, periods)
    _records, selected = parse_bundle_for_period(bundle, site, 216, registry)
    assert selected.period == 216
    assert len(selected.zodiac) == 2
    _records, adjacent = parse_bundle_for_period(bundle, site, 215, registry)
    assert adjacent.period == 215
    with pytest.raises(ValueError, match="217"):
        parse_bundle_for_period(bundle, site, 217, registry)

    # A fourth row outside the configured three-record window must not make
    # the target eligible.  White-hand's dedicated parser requires a full
    # 365-period cycle, so its fixed source already exercises this boundary.
    if name != "白手起家":
        overflow_order = (215, 214, 213, 216) if site.pick == "top" else (216, 215, 214, 213)
        overflow = _bundle_for(site, overflow_order)
        with pytest.raises(ValueError, match="216"):
            parse_bundle_for_period(overflow, site, 216, registry)
    else:
        with pytest.raises(ValueError, match="213"):
            parse_bundle_for_period(bundle, site, 213, registry)


def test_batch3_dynamic_script_same_url_and_target_probe() -> None:
    site = configured_batch_sites()["没忍住啊"]
    registry = ParserRegistry.bind_sites([site])
    script_url = "https://xia01.cosds.ahsccn.com/upload/script/08/target.js"
    page = f'<html><body><script src="{script_url}"></script></body></html>'
    script = _named_source(site.name, (216, 215, 214))
    context = FetchContext(
        text_fetcher=lambda url, _timeout: page if url == site.url else script,
        renderer=lambda _url, _timeout: "<html>empty browser shell</html>",
    )
    bundle = fetch_payload(site, 216, 3, context, lambda source, target, issue: bool(registry.parse(source, target)))
    assert any(document.url == script_url for document in bundle.documents)
    _records, selected = parse_bundle_for_period(bundle, site, 216, registry)
    assert selected.zodiac == "鼠虎"


def test_batch3_wrong_anchor_field_direction_conflict_and_cross_document() -> None:
    site = configured_batch_sites()["没忍住啊"]
    registry = ParserRegistry.bind_sites([site])

    old_anchor = _document(site, _named_source(site.name, (216, 215, 214)).replace("118060", "118082"))
    assert registry.parse(old_anchor, site) == []
    wrong_field = _document(site, _named_source(site.name, (216, 215, 214)).replace("绝杀二肖", "绝杀三肖"))
    assert registry.parse(wrong_field, site) == []

    overflow = _document(site, _named_source(site.name, (215, 214, 213, 216)))
    with pytest.raises(ValueError, match="top 候选内未找到 216"):
        parse_bundle_for_period(DocumentBundle((overflow,)), site, 216, registry)

    conflict_source = (
        "高手贴 216期：[绝杀二肖]━ 118060d.com\n"
        "216期：绝杀二肖【鼠虎】开00准\n"
        "216期：绝杀二肖【蛇狗】开00准\n"
        "215期：绝杀二肖【鼠虎】开00准"
    )
    with pytest.raises(ValueError, match="数据存在冲突"):
        parse_bundle_for_period(DocumentBundle((_document(site, conflict_source),)), site, 216, registry)

    page = _document(site, '<script src="https://other.test/body.js"></script>')
    body = _document(site, _rows((216, 215, 214)), url="https://other.test/body.js")
    with pytest.raises(ValueError, match="多文档内未能独立验证"):
        parse_bundle_for_period(DocumentBundle((page, body)), site, 216, registry)


def test_batch3_macau_direction_overflow_stays_failed() -> None:
    sites = configured_batch_sites()
    macau = sites["澳门-白虎"]
    macau_registry = ParserRegistry.bind_sites([macau])
    overflow = _bundle_for(macau, (215, 214, 213))
    with pytest.raises(ValueError, match="top 候选内未找到 216"):
        parse_bundle_for_period(overflow, macau, 216, macau_registry)

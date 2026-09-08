from __future__ import annotations

from pathlib import Path

import pytest

from config.loader import load_sites
from domain.models import DocumentBundle, PayloadDocument
from parsers.registry import ENGINE_REGISTRY, ParserRegistry
from services.single_period import parse_bundle_for_period


ROOT = Path(__file__).resolve().parents[2]
SITES = {
    site.name: site
    for site in load_sites(ROOT / "config" / "sites.json", allowed_parsers=ENGINE_REGISTRY)
    if site.name in {"枪打天下", "雁落沙滩"}
}


def registry_for(name: str) -> ParserRegistry:
    return ParserRegistry.bind_sites([SITES[name]])


def gun_source(*rows: str, anchor: str = "枪打天下（绝杀二肖）") -> str:
    body = "".join(f"<div>{row}</div>" for row in rows)
    return f"<section><h2>{anchor}</h2>{body}<div>上一篇：</div></section>"


def goose_source(*rows: str, anchor: str = "作者：雁落沙滩") -> str:
    body = "".join(f"<div>{row}</div>" for row in rows)
    return f"<article><h2>{anchor}</h2>{body}<div>上一篇：</div></article>"


def bundle(name: str, source: str, label: str = "脚本解码") -> DocumentBundle:
    site = SITES[name]
    record_id = "topic:216108" if name == "雁落沙滩" else ""
    return DocumentBundle((PayloadDocument(label, site.url, source, record_id=record_id),))


@pytest.mark.parametrize(
    ("name", "source", "zodiac"),
    [
        (
            "枪打天下",
            gun_source(
                "229期稳杀二肖（狗虎）开00",
                "230期稳杀二肖（蛇龙）开00",
                "231期稳杀二肖（猪兔）开00",
                "232期稳杀二肖（龙马）开00",
            ),
            "龙马",
        ),
        (
            "雁落沙滩",
            goose_source(
                "232期绝杀二肖【蛇猪】开00",
                "231期绝杀二肖【龙猴】开00",
                "230期绝杀二肖【猪虎】开00",
                "229期绝杀二肖【鼠牛】开00",
            ),
            "蛇猪",
        ),
    ],
)
def test_232_uses_configured_direction_window(name: str, source: str, zodiac: str) -> None:
    site = SITES[name]
    _records, selected = parse_bundle_for_period(bundle(name, source), site, 232, registry_for(name))
    assert selected.zodiac == zodiac
    assert selected.position >= 0


@pytest.mark.parametrize(
    ("name", "source"),
    [
        (
            "枪打天下",
            gun_source(
                "229期稳杀二肖（狗虎）开00",
                "230期稳杀二肖（蛇龙）开00",
                "231期稳杀二肖（猪兔）开00",
                "232期稳杀二肖（龙马）开00",
            ),
        ),
        (
            "雁落沙滩",
            goose_source(
                "232期绝杀二肖【蛇猪】开00",
                "231期绝杀二肖【龙猴】开00",
                "230期绝杀二肖【猪虎】开00",
                "229期绝杀二肖【鼠牛】开00",
            ),
        ),
    ],
)
def test_233_is_rejected_as_unpublished(name: str, source: str) -> None:
    with pytest.raises(ValueError, match=r"候选内未找到 233 期"):
        parse_bundle_for_period(bundle(name, source), SITES[name], 233, registry_for(name))


def test_bottom_position_excludes_same_period_outside_window() -> None:
    source = gun_source(
        "232期稳杀二肖（牛羊）开00",
        "230期稳杀二肖（蛇龙）开00",
        "231期稳杀二肖（猪兔）开00",
        "232期稳杀二肖（龙马）开00",
    )
    _records, selected = parse_bundle_for_period(
        bundle("枪打天下", source), SITES["枪打天下"], 232, registry_for("枪打天下")
    )
    assert selected.zodiac == "龙马"


def test_top_position_excludes_same_period_outside_window() -> None:
    source = goose_source(
        "232期绝杀二肖【蛇猪】开00",
        "231期绝杀二肖【龙猴】开00",
        "230期绝杀二肖【猪虎】开00",
        "232期绝杀二肖【牛羊】开00",
    )
    _records, selected = parse_bundle_for_period(
        bundle("雁落沙滩", source), SITES["雁落沙滩"], 232, registry_for("雁落沙滩")
    )
    assert selected.zodiac == "蛇猪"


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("枪打天下", gun_source("232期稳杀三肖（龙马）开00")),
        ("雁落沙滩", goose_source("232期绝杀三肖【蛇猪】开00")),
    ],
)
def test_wrong_two_zodiac_field_is_rejected(name: str, source: str) -> None:
    with pytest.raises(ValueError, match="未抓到有效候选"):
        parse_bundle_for_period(bundle(name, source), SITES[name], 232, registry_for(name))


@pytest.mark.parametrize(
    ("name", "source"),
    [
        ("枪打天下", gun_source("232期稳杀二肖（龙马）开00", anchor="别的栏目")),
        ("雁落沙滩", goose_source("232期绝杀二肖【蛇猪】开00", anchor="作者：别人")),
    ],
)
def test_wrong_anchor_is_rejected(name: str, source: str) -> None:
    with pytest.raises(ValueError, match="未抓到有效候选"):
        parse_bundle_for_period(bundle(name, source), SITES[name], 232, registry_for(name))


def test_same_window_conflict_is_rejected() -> None:
    source = gun_source(
        "231期稳杀二肖（猪兔）开00",
        "232期稳杀二肖（龙马）开00",
        "232期稳杀二肖（牛羊）开00",
    )
    with pytest.raises(ValueError, match="数据存在冲突"):
        parse_bundle_for_period(bundle("枪打天下", source), SITES["枪打天下"], 232, registry_for("枪打天下"))


def test_cross_document_conflict_is_rejected() -> None:
    site = SITES["雁落沙滩"]
    docs = (
        PayloadDocument(
            "当前脚本解码",
            site.url,
            goose_source("232期绝杀二肖【蛇猪】开00", "231期绝杀二肖【龙猴】开00"),
            record_id="topic:216108",
        ),
        PayloadDocument(
            "冲突脚本解码",
            site.url,
            goose_source("232期绝杀二肖【牛羊】开00", "231期绝杀二肖【龙猴】开00"),
            record_id="topic:216108",
        ),
    )
    with pytest.raises(ValueError, match="多文档近3条结果不同"):
        parse_bundle_for_period(DocumentBundle(docs), site, 232, registry_for("雁落沙滩"))


def test_stale_document_cannot_replace_current_232() -> None:
    site = SITES["雁落沙滩"]
    docs = (
        PayloadDocument(
            "当前脚本解码",
            site.url,
            goose_source(
                "232期绝杀二肖【蛇猪】开00",
                "231期绝杀二肖【龙猴】开00",
                "230期绝杀二肖【猪虎】开00",
            ),
            record_id="topic:216108",
        ),
        PayloadDocument(
            "旧脚本解码",
            site.url,
            goose_source(
                "231期绝杀二肖【龙猴】开00",
                "230期绝杀二肖【猪虎】开00",
                "229期绝杀二肖【鼠牛】开00",
            ),
            record_id="topic:216108",
        ),
    )
    _records, selected = parse_bundle_for_period(
        DocumentBundle(docs), site, 232, registry_for("雁落沙滩")
    )
    assert selected.zodiac == "蛇猪"

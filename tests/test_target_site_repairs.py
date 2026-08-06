from __future__ import annotations

from pathlib import Path

import pytest

from config.loader import load_sites
from domain.models import DOCUMENT_BOUNDARY, DocumentBundle, PayloadDocument, Site
from parsers.forum import (
    parse_guangdong_linked_top_records,
    parse_guangxizai_linked_top_records,
    parse_jiulong_forum_top_records,
    parse_liuhe_toutiao_linked_top_records,
    parse_shouqi_daoluo_top_records,
    parse_taxue_wuhen_top_records,
    parse_wulin_gaoshou_linked_top_records,
)
from parsers.registry import ENGINE_REGISTRY, ParserRegistry
from services.single_period import parse_bundle_for_period
from validation.boundaries import expected_record_id
from validation.direction import direction_window, select_record


ROOT = Path(__file__).resolve().parents[1]
TARGET_NAMES = (
    "六合头条",
    "九龙论坛",
    "手起刀落",
    "武林高手",
    "广西仔",
    "踏雪无痕",
    "广东",
)


def configured_targets() -> dict[str, object]:
    sites = load_sites(ROOT / "config" / "sites.json", allowed_parsers=ENGINE_REGISTRY)
    return {site.name: site for site in sites if site.name in TARGET_NAMES}


def linked_bundle(site, body_url: str, body_source: str, anchor: str) -> DocumentBundle:
    record_id = expected_record_id(site)
    page = PayloadDocument(
        "页面",
        site.url,
        f'{anchor}<script src="{body_url}"></script>',
        record_id=record_id,
    )
    body = PayloadDocument(
        "脚本解码",
        body_url,
        body_source,
        record_id=record_id,
        parent_url=site.url,
        link_reference=body_url,
    )
    return DocumentBundle((page, body))


def test_repaired_sites_have_explicit_source_and_direction_contracts() -> None:
    sites = configured_targets()

    assert {name: sites[name].pick for name in TARGET_NAMES} == {
        "六合头条": "top",
        "九龙论坛": "top",
        "手起刀落": "top",
        "武林高手": "top",
        "广西仔": "top",
        "踏雪无痕": "top",
        "广东": "top",
    }
    assert sites["踏雪无痕"].payload == "browser_rendered_page"
    assert sites["踏雪无痕"].parser == "taxue_wuhen_top"
    assert all(
        sites[name].linked_document_pattern
        for name in ("六合头条", "广西仔", "广东", "武林高手")
    )


@pytest.mark.parametrize(
    ("name", "body_source", "anchor", "expected"),
    (
        (
            "六合头条",
            "精品帖 213期【绝杀两肖】绝对好料 "
            "213期绝杀两肖【虎牛】开:00准 "
            "212期绝杀两肖【鼠马】开:牛06准 "
            "211期绝杀两肖【猪狗】开:马01准",
            "澳门六合头条",
            "虎牛",
        ),
        (
            "广西仔",
            "213期:本站推荐【绝杀二肖】 877750a.com "
            "213期:绝杀二肖-(鸡羊)-开0000准 "
            "212期:绝杀二肖-(猪羊)-开牛06准 "
            "211期:绝杀二肖-(猴鼠)-开马01准",
            "澳门广西仔",
            "鸡羊",
        ),
        (
            "广东",
            "213期:【绝杀二肖】 94245a.com "
            "213期（必杀二肖）【虎牛】开00准 "
            "212期（必杀二肖）【狗鸡】开牛06准 "
            "211期（必杀二肖）【马羊】开马01错",
            "澳门广东八二站",
            "虎牛",
        ),
        (
            "武林高手",
            "213期:绝杀两肖【龙蛇】开:00准 "
            "212期:绝杀两肖【兔龙】开:牛06准 "
            "211期:绝杀两肖【马羊】开:马01错",
            "武林高手 213期《绝杀两肖》",
            "龙蛇",
        ),
    ),
)
def test_linked_target_parsers_use_only_authorized_body(name, body_source, anchor, expected) -> None:
    site = configured_targets()[name]
    body_url = "https://xia06.cosds.ahsccn.com/upload/script/08/target.js"
    bundle = linked_bundle(site, body_url, body_source, anchor)
    registry = ParserRegistry.bind_sites([site])

    records, selected = parse_bundle_for_period(bundle, site, 213, registry)

    assert [(record.period, record.zodiac) for record in direction_window(records, site)] == [
        (213, expected),
        (212, records[1].zodiac),
        (211, records[2].zodiac),
    ]
    assert selected.zodiac == expected


def test_linked_target_parser_rejects_body_without_page_link() -> None:
    site = configured_targets()["六合头条"]
    record_id = expected_record_id(site)
    body = PayloadDocument(
        "未授权脚本",
        "https://xia06.cosds.ahsccn.com/upload/script/08/target.js",
        "精品帖 213期【绝杀两肖】绝对好料 213期绝杀两肖【虎牛】开:00准",
        record_id=record_id,
    )
    page = PayloadDocument("页面", site.url, "澳门六合头条", record_id=record_id)
    registry = ParserRegistry.bind_sites([site])

    with pytest.raises(ValueError, match="多文档内未能独立验证"):
        parse_bundle_for_period(DocumentBundle((page, body)), site, 213, registry)


def test_jiulong_and_shouqi_ignore_click_ad_and_adjacent_column() -> None:
    sites = configured_targets()
    registry = ParserRegistry.bind_sites([sites["九龙论坛"], sites["手起刀落"]])
    cases = {
        "九龙论坛": (
            "九龙论坛『绝杀二肖』 点击投注港澳六合彩 "
            "213期:〖杀二肖→牛鼠〗开:00准 "
            "212期:〖杀二肖→猪鼠〗开:牛06准 "
            "九龙论坛『五肖十码』 213期:〖二肖〗鸡猪",
            "牛鼠",
        ),
        "手起刀落": (
            "手起刀落【绝杀二肖】 点击投注港澳六合彩 "
            "213期绝杀两肖【虎牛】开:00准 "
            "212期绝杀两肖【鼠马】开:牛06准 "
            "过分耀眼【必杀2尾】 213期必杀2尾【4尾5尾】开:00准",
            "虎牛",
        ),
    }
    for name, (source, expected) in cases.items():
        site = sites[name]
        records = registry.parse(source, site)
        assert [(record.period, record.zodiac) for record in records] == [
            (213, expected),
            (212, records[1].zodiac),
        ]
        assert select_record(records, 213, site).zodiac == expected


def test_taxue_top_uses_exact_section_and_rejects_kill_two_zodiac_tail() -> None:
    site = configured_targets()["踏雪无痕"]
    registry = ParserRegistry.bind_sites([site])
    source = (
        "澳门踏雪无痕『绝杀二肖』 "
        "213期:绝杀二肖【鸡龙】开:0000准 "
        "212期:绝杀二肖【狗猴】开:牛06准 "
        "210期:绝杀二肖【鸡羊】开:马49准 点击投注 "
        "澳门踏雪无痕『组合20码』 "
        "213期杀2肖2尾【猴猪+01尾】√ 212期杀2肖2尾【鼠牛+23尾】错"
    )

    records = registry.parse(source, site)
    selected = parse_bundle_for_period(
        DocumentBundle((PayloadDocument("浏览器渲染页面", site.url, source),)),
        site,
        213,
        registry,
    )[1]

    assert [(record.period, record.zodiac) for record in direction_window(records, site)] == [
        (213, "鸡龙"),
        (212, "狗猴"),
        (210, "鸡羊"),
    ]
    assert selected.zodiac == "鸡龙"
    with pytest.raises(ValueError, match="top 候选内未找到 211 期"):
        select_record(records, 211, site)


@pytest.mark.parametrize(
    "parser",
    (
        parse_liuhe_toutiao_linked_top_records,
        parse_jiulong_forum_top_records,
        parse_shouqi_daoluo_top_records,
        parse_wulin_gaoshou_linked_top_records,
        parse_guangxizai_linked_top_records,
        parse_guangdong_linked_top_records,
    ),
)
def test_repaired_parsers_reject_wrong_site_identity(parser) -> None:
    wrong_site = Site("其他站点", "bottom", "https://example.test/")

    assert parser("213期绝杀二肖【虎牛】开:00准", wrong_site) == []


def test_linked_parsers_reject_wrong_anchor_and_missing_target_heading() -> None:
    site = configured_targets()["广西仔"]
    body = "213期:绝杀二肖-(鸡羊)-开0000准 212期:绝杀二肖-(猪羊)-开牛06准"

    assert parse_guangxizai_linked_top_records(
        "不是广西仔" + DOCUMENT_BOUNDARY + body,
        site,
    ) == []
    assert parse_guangxizai_linked_top_records(
        "澳门广西仔" + DOCUMENT_BOUNDARY + "没有本站推荐栏目",
        site,
    ) == []


def test_specialized_parsers_reject_incomplete_or_mismatched_sections() -> None:
    sites = configured_targets()
    taxue = sites["踏雪无痕"]
    assert parse_taxue_wuhen_top_records(
        "澳门踏雪无痕『绝杀二肖』 213期:绝杀二肖【鸡龙】开:00准 "
        "212期:绝杀二肖【狗猴】开:牛06准 点击投注",
        taxue,
    ) == []
    assert parse_taxue_wuhen_top_records(
        "澳门踏雪无痕『绝杀二肖』 澳门踏雪无痕『绝杀二肖』 "
        "213期:绝杀二肖【鸡龙】开:00准 212期:绝杀二肖【狗猴】开:牛06准 "
        "210期:绝杀二肖【鸡羊】开:马49准 点击投注",
        taxue,
    ) == []

    wulin = sites["武林高手"]
    assert parse_wulin_gaoshou_linked_top_records(
        "武林高手 214期《绝杀两肖》" + DOCUMENT_BOUNDARY
        + "213期:绝杀两肖【龙蛇】开:00准",
        wulin,
    ) == []

    jiulong = sites["九龙论坛"]
    records = parse_jiulong_forum_top_records(
        "九龙论坛『绝杀二肖』 213期:〖杀二肖→牛鼠〗开:00准",
        jiulong,
    )
    assert [(record.period, record.zodiac) for record in records] == [(213, "牛鼠")]

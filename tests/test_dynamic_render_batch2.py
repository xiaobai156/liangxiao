from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from config.loader import load_sites
from domain.models import DocumentBundle, PayloadDocument, Site
from fetching.client import FetchContext
from fetching.page import fetch_payload
from parsers.registry import ENGINE_REGISTRY, ParserRegistry
from services.single_period import parse_bundle_for_period
from validation.boundaries import expected_record_id
from validation.direction import select_record


ROOT = Path(__file__).resolve().parents[1]
BATCH_NAMES = (
    "纷纷扬扬",
    "唯利是趋",
    "白头偕老",
    "春华秋实",
    "头破血流",
    "欧阳澜纯",
    "白里透红",
    "老金绝杀",
    "振聋发聩",
    "奴颜媚骨",
    "惘然若失",
    "与虎谋皮",
    "同心叶力",
    "自我作古",
    "一点红",
    "王者九点",
    "王者九点禁二肖图",
    "玄机网禁两肖",
    "澳门杀两肖图",
    "澳门图库禁肖",
    "朱雀",
    "哼哼唧唧",
    "众多非一",
)


def configured_batch_sites() -> dict[str, Site]:
    sites = load_sites(ROOT / "config" / "sites.json", allowed_parsers=ENGINE_REGISTRY)
    selected = {site.name: site for site in sites if site.name in BATCH_NAMES}
    assert tuple(name for name in BATCH_NAMES if name in selected) == BATCH_NAMES
    return selected


def bundle_for(site, source: str) -> DocumentBundle:
    return DocumentBundle(
        (
            PayloadDocument(
                "浏览器渲染页面",
                site.url,
                source,
                record_id=expected_record_id(site),
            ),
        )
    )


def scoped_rows(site_name: str = "纷纷扬扬") -> str:
    return (
        f"<h1>{site_name}</h1>"
        "<p>216期【绝杀二肖】【牛马】开0000准</p>"
        "<p>215期【绝杀二肖】【龙虎】开蛇14准</p>"
        "<p>214期【绝杀二肖】【猴狗】开兔04准</p>"
        "<h2>其他栏目</h2><p>216期【绝杀二肖】【鸡猪】开0000准</p>"
    )


def test_batch2_keeps_explicit_page_and_scripts_scope() -> None:
    sites = configured_batch_sites()

    assert all(
        sites[name].payload == "page_and_scripts"
        for name in BATCH_NAMES
        if name != "朱雀"
    )
    assert sites["朱雀"].payload == "browser_rendered_page"
    assert sites["纷纷扬扬"].parser == "site_scoped_two_zodiac"
    assert sites["王者九点禁二肖图"].parser == "wangzhejiudian_forbidden_chart"
    assert sites["玄机网禁两肖"].parser == "shuqhbq_xuanji_forbidden"
    assert sites["澳门杀两肖图"].parser == "shuqhbq_macau_kill_chart"
    assert sites["澳门图库禁肖"].parser == "shuqhbq_macau_gallery_forbidden"
    assert sites["朱雀"].parser == "zhuque_forbidden_material"


def test_browser_rendered_source_is_one_same_url_record_and_target_block() -> None:
    configured = configured_batch_sites()["纷纷扬扬"]
    site = replace(configured, payload="browser_rendered_page")
    rendered = scoped_rows()
    context = FetchContext(
        text_fetcher=lambda _url, _timeout: "<html><body>动态空壳</body></html>",
        renderer=lambda _url, _timeout: rendered,
    )
    registry = ParserRegistry.bind_sites([site])

    bundle = fetch_payload(
        site,
        216,
        2,
        context,
        lambda source, target, period: any(
            record.period == period for record in registry.parse(source, target)
        ),
    )
    assert len(bundle.documents) == 1
    assert bundle.documents[0].label == "浏览器渲染页面"
    assert bundle.documents[0].url == site.url
    assert bundle.documents[0].record_id == expected_record_id(site)

    _records, selected = parse_bundle_for_period(bundle, site, 216, registry)
    assert selected.zodiac == "牛马"


def test_target_period_adjacent_period_and_out_of_window_are_distinct() -> None:
    site = configured_batch_sites()["纷纷扬扬"]
    registry = ParserRegistry.bind_sites([site])
    records = registry.parse(scoped_rows(), site)

    assert select_record(records, 216, site).zodiac == "牛马"
    assert select_record(records, 215, site).zodiac == "龙虎"
    with pytest.raises(ValueError, match="top 候选内未找到 217 期"):
        select_record(records, 217, site)
    with pytest.raises(ValueError, match="top 候选内未找到 213 期"):
        select_record(records, 213, site)


def test_wrong_site_anchor_and_cross_document_borrowing_fail() -> None:
    site = configured_batch_sites()["纷纷扬扬"]
    registry = ParserRegistry.bind_sites([site])

    assert registry.parse(scoped_rows("其他栏目"), site) == []

    anchor = PayloadDocument(
        "页面锚点",
        site.url,
        "<h1>纷纷扬扬</h1><a href='/target.js'>正文</a>",
        record_id=expected_record_id(site),
    )
    body = PayloadDocument(
        "正文脚本",
        "https://example.test/target.js",
        "216期【绝杀二肖】【牛马】开0000准",
        record_id=expected_record_id(site),
    )
    with pytest.raises(ValueError, match="多文档内未能独立验证"):
        parse_bundle_for_period(DocumentBundle((anchor, body)), site, 216, registry)


def test_same_period_conflict_is_not_resolved_by_direction_or_source_order() -> None:
    site = configured_batch_sites()["纷纷扬扬"]
    source = (
        "<h1>纷纷扬扬</h1>"
        "<p>216期【绝杀二肖】【牛马】开0000准</p>"
        "<p>216期【绝杀二肖】【猴蛇】开0000准</p>"
        "<p>215期【绝杀二肖】【龙虎】开蛇14准</p>"
        "<p>214期【绝杀二肖】【猴狗】开兔04准</p>"
    )
    registry = ParserRegistry.bind_sites([site])

    with pytest.raises(ValueError, match="数据存在冲突"):
        parse_bundle_for_period(bundle_for(site, source), site, 216, registry)


@pytest.mark.parametrize(
    ("site_name", "heading", "target_field", "wrong_field", "target", "wrong"),
    (
        (
            "玄机网禁两肖",
            "禁两肖",
            "玄机网禁两肖",
            "澳门杀两肖图",
            "蛇虎",
            "牛羊",
        ),
        (
            "澳门杀两肖图",
            "禁两肖",
            "澳门杀两肖图",
            "玄机网禁两肖",
            "牛羊",
            "蛇虎",
        ),
        (
            "澳门图库禁肖",
            "禁两肖",
            "澳门图库禁肖",
            "澳门杀两肖图",
            "猪狗",
            "牛羊",
        ),
    ),
)
def test_shuqhbq_chart_parsers_lock_exact_column_name(
    site_name: str,
    heading: str,
    target_field: str,
    wrong_field: str,
    target: str,
    wrong: str,
) -> None:
    site = configured_batch_sites()[site_name]
    source = (
        f"<h2>{heading}</h2>"
        f"<p>216期{target_field}【{target}】特开0000准</p>"
        f"<p>215期{target_field}【龙虎】特开蛇14准</p>"
        f"<p>214期{target_field}【猴狗】特开兔04准</p>"
        f"<p>216期{wrong_field}【{wrong}】特开0000准</p>"
    )
    registry = ParserRegistry.bind_sites([site])
    records = registry.parse(source, site)

    assert [(record.period, record.zodiac) for record in records] == [
        (216, target),
        (215, "龙虎"),
        (214, "猴狗"),
    ]
    assert all(target_field in record.raw for record in records)

    wrong_only = f"<h2>{heading}</h2><p>216期{wrong_field}【{wrong}】特开0000准</p>"
    assert registry.parse(wrong_only, site) == []


def test_wangzhe_chart_and_kill_sections_are_not_interchangeable() -> None:
    configured = configured_batch_sites()
    forbidden = configured["王者九点禁二肖图"]
    registry = ParserRegistry.bind_sites([forbidden])
    source = (
        "《王者九点网㊣杀禁统计》\n"
        "216期杀肖统计 禁二肖图：【兔蛇】 特开0000\n"
        "215期杀肖统计 禁二肖图：【牛鸡】 特开蛇14\n"
        "214期杀肖统计 禁二肖图：【猴狗】 特开兔04\n"
        "216期杀肖统计 杀二肖图：【牛马】 特开0000\n"
    )
    records = registry.parse(source, forbidden)
    assert [(record.period, record.zodiac) for record in records] == [
        (216, "兔蛇"),
        (215, "牛鸡"),
        (214, "猴狗"),
    ]
    assert all("禁二肖图" in record.raw for record in records)

    kill = configured["王者九点"]
    kill_registry = ParserRegistry.bind_sites([kill])
    kill_source = (
        "《王者九点网㊣杀二肖》\n"
        "214期杀二肖【猴狗】开兔04准\n"
        "215期杀二肖【牛鸡】开蛇14准\n"
        "216期杀二肖【牛兔】开0000准\n"
    )
    _records, selected = parse_bundle_for_period(
        bundle_for(kill, kill_source), kill, 216, kill_registry
    )
    assert selected.zodiac == "牛兔"


def test_zhuque_target_period_is_rejected_when_only_earlier_top_window_exists() -> None:
    site = configured_batch_sites()["朱雀"]
    source = (
        "177792a.com『禁用资料』"
        "215期→禁用二肖→【马鼠】开蛇14准"
        "214期→禁用二肖→【猪鼠】开兔04准"
        "213期→禁用二肖→【马猪】开猴35准"
    )
    registry = ParserRegistry.bind_sites([site])
    records = registry.parse(source, site)

    assert [record.period for record in records] == [215, 214, 213]
    with pytest.raises(ValueError, match="top 候选内未找到 216 期"):
        parse_bundle_for_period(bundle_for(site, source), site, 216, registry)


def test_zhuque_uses_same_url_browser_dom_when_root_page_is_only_a_shell() -> None:
    site = configured_batch_sites()["朱雀"]
    rendered = (
        "177792a.com『禁用资料』"
        "216期→禁用二肖→【兔猪】开000中"
        "215期→禁用二肖→【马鼠】开蛇14准"
        "214期→禁用二肖→【猪鼠】开兔04准"
        "禁两肖"
    )
    context = FetchContext(
        text_fetcher=lambda url, _timeout: (
            '<html><script src="/upload/script/stale.js"></script></html>'
            if url == site.url
            else ""
        ),
        renderer=lambda url, _timeout: rendered if url == site.url else "",
    )
    registry = ParserRegistry.bind_sites([site])

    bundle = fetch_payload(
        site,
        216,
        2,
        context,
        lambda source, target, period: any(
            record.period == period for record in registry.parse(source, target)
        ),
    )

    assert len(bundle.documents) == 1
    assert bundle.documents[0].label == "浏览器渲染页面"
    assert bundle.documents[0].url == site.url
    _records, selected = parse_bundle_for_period(bundle, site, 216, registry)
    assert selected.zodiac == "兔猪"
    _records, adjacent = parse_bundle_for_period(bundle, site, 215, registry)
    assert adjacent.zodiac == "马鼠"
    with pytest.raises(ValueError, match="top 候选内未找到 217 期"):
        parse_bundle_for_period(bundle, site, 217, registry)

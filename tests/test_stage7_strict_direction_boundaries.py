from __future__ import annotations

import re

import pytest

import adaptive_scrapling as adaptive
from domain.models import DocumentBundle, POSITION_KIND_VISIBLE_TEXT, PayloadDocument, Site
from parsers.registry import ParserRegistry
from services.single_period import parse_bundle_for_period


ZODIACS = "牛马羊鸡狗猪鼠虎兔龙蛇猴"


def scoped_site(*, pick: str = "bottom", linked_document_pattern: str = "") -> Site:
    return Site(
        "目标作者",
        pick,
        "https://example.test/topic/1",
        parser="site_scoped_two_zodiac",
        title="目标作者",
        payload="page_and_scripts",
        linked_document_pattern=linked_document_pattern,
    )


def named_site(*, pick: str = "bottom") -> Site:
    return Site(
        "目标作者",
        pick,
        "https://example.test/topic/1",
        parser="named_block",
        title="目标作者",
        record=(
            rf"(?P<period>\d{{3}})期\s*绝杀二肖【"
            rf"(?P<zodiac>[{ZODIACS}]{{2}})】开:(?P<open>\d+)"
        ),
        payload="page",
    )


def parse_single(source: str, site: Site, period: int = 210):
    registry = ParserRegistry.bind_sites([site])
    bundle = DocumentBundle((PayloadDocument("页面", site.url, source, record_id="topic:1"),))
    return parse_bundle_for_period(bundle, site, period, registry)


@pytest.mark.parametrize("site", [scoped_site(), named_site()])
def test_bottom_uses_true_tail_cycle_before_period_matching(site: Site) -> None:
    source = (
        "目标作者 "
        "210期绝杀二肖【狗蛇】开:01 209期绝杀二肖【牛马】开:02 208期绝杀二肖【鸡羊】开:03 "
        "210期绝杀二肖【龙虎】开:04 209期绝杀二肖【鼠兔】开:05 208期绝杀二肖【猪猴】开:06"
    )

    records, selected = parse_single(source, site)

    assert [record.zodiac for record in records[-3:]] == ["龙虎", "鼠兔", "猪猴"]
    assert selected.zodiac == "龙虎"
    assert selected.position == source.index("210期绝杀二肖【龙虎】")
    assert selected.position_kind == POSITION_KIND_VISIBLE_TEXT


def test_true_bottom_window_conflict_is_not_hidden_by_earlier_cycle() -> None:
    site = scoped_site()
    source = (
        "目标作者 "
        "210期绝杀二肖【狗蛇】开:01 209期绝杀二肖【牛马】开:02 208期绝杀二肖【鸡羊】开:03 "
        "210期绝杀二肖【龙虎】开:04 210期绝杀二肖【鼠兔】开:05 209期绝杀二肖【猪猴】开:06"
    )

    with pytest.raises(ValueError, match="数据存在冲突.*龙虎.*鼠兔"):
        parse_single(source, site)


def test_same_value_duplicate_merges_provenance_without_consuming_direction_window() -> None:
    site = scoped_site()
    source = (
        "目标作者 "
        "208期绝杀二肖【鸡羊】开:01 209期绝杀二肖【牛马】开:02 "
        "210期绝杀二肖【龙虎】开:03 210期绝杀二肖【龙虎】开:03"
    )

    _records, selected = parse_single(source, site)

    expected_positions = tuple(
        match.start()
        for match in re.finditer("210期绝杀二肖【龙虎】", source)
    )
    assert selected.zodiac == "龙虎"
    assert selected.source_positions == expected_positions


def test_top_keeps_first_cycle_symmetrically() -> None:
    site = scoped_site(pick="top")
    source = (
        "目标作者 "
        "210期绝杀二肖【狗蛇】开:01 209期绝杀二肖【牛马】开:02 208期绝杀二肖【鸡羊】开:03 "
        "210期绝杀二肖【龙虎】开:04 209期绝杀二肖【鼠兔】开:05 208期绝杀二肖【猪猴】开:06"
    )

    _records, selected = parse_single(source, site)

    assert selected.zodiac == "狗蛇"
    assert selected.position == source.index("210期绝杀二肖【狗蛇】")


@pytest.mark.parametrize(
    "source",
    [
        "<div>目标作者</div><p>本栏目尚未发布</p><div>其他栏目</div><p>211期绝杀二肖【狗蛇】开:01</p>",
        "<p>目标作者</p><p>本栏目尚未发布</p><p>其他栏目</p><p>211期绝杀二肖【狗蛇】开:01</p>",
        "<table><tr><th>目标作者</th></tr><tr><td>本栏目尚未发布</td></tr><tr><th>其他栏目</th></tr><tr><td>211期绝杀二肖【狗蛇】开:01</td></tr></table>",
        "目标作者\n本栏目尚未发布\n其他栏目\n211期绝杀二肖【狗蛇】开:01",
    ],
    ids=("div", "p", "table", "plain-text"),
)
def test_shared_parser_never_reads_adjacent_section_without_explicit_stop(source: str) -> None:
    site = scoped_site(pick="top")
    registry = ParserRegistry.bind_sites([site])

    assert registry.parse(source, site) == []


def test_semantic_adjacent_section_is_also_a_hard_boundary() -> None:
    site = scoped_site(pick="top")
    source = (
        "<div>目标作者</div><p>本栏目尚未发布</p>"
        "<div>其他栏目绝杀二肖</div><p>211期绝杀二肖【狗蛇】开:01</p>"
    )
    registry = ParserRegistry.bind_sites([site])

    assert registry.parse(source, site) == []


def test_same_line_duplicate_anchor_is_ambiguous() -> None:
    site = scoped_site(pick="top")
    source = "<div>目标作者 目标作者</div><p>210期绝杀二肖【狗蛇】开:01</p>"
    registry = ParserRegistry.bind_sites([site])

    with pytest.raises(ValueError, match="锚点不唯一"):
        registry.parse(source, site)


def test_multiple_structural_title_blocks_are_ambiguous() -> None:
    site = scoped_site(pick="top")
    source = (
        "<section><h2>目标作者</h2><p>210期绝杀二肖【狗蛇】开:01</p></section>"
        "<section><h2>目标作者</h2><p>210期绝杀二肖【龙虎】开:02</p></section>"
    )
    registry = ParserRegistry.bind_sites([site])

    with pytest.raises(ValueError, match="锚点不唯一"):
        registry.parse(source, site)


def linked_bundle(site: Site) -> DocumentBundle:
    return DocumentBundle(
        (
            PayloadDocument(
                "标题",
                site.url,
                '<h1>目标作者</h1><a href="/target.js">目标正文</a>',
                record_id="topic:1",
                record_path="root.anchor",
                record_count=2,
            ),
            PayloadDocument(
                "正文",
                "https://example.test/target.js",
                "210期绝杀二肖【狗蛇】开:01",
                record_id="topic:1",
                record_path="root.body",
                record_count=3,
                parent_url=site.url,
                link_reference="/target.js",
            ),
        )
    )


def test_linked_title_body_requires_explicit_site_authorization() -> None:
    site = scoped_site(pick="top")
    registry = ParserRegistry.bind_sites([site])

    with pytest.raises(ValueError, match="多文档内未能独立验证"):
        parse_bundle_for_period(linked_bundle(site), site, 210, registry)


def test_authorized_linked_title_body_keeps_both_sides_of_evidence() -> None:
    site = scoped_site(pick="top", linked_document_pattern=r"/target\.js$")
    registry = ParserRegistry.bind_sites([site])

    _records, selected = parse_bundle_for_period(linked_bundle(site), site, 210, registry)

    assert selected.zodiac == "狗蛇"
    assert selected.document_url == "https://example.test/target.js"
    assert selected.anchor_document_url == site.url
    assert selected.link_reference == "/target.js"
    assert selected.anchor_record_id == "topic:1"
    assert selected.anchor_record_path == "root.anchor"
    assert selected.anchor_record_count == 2
    assert selected.body_record_id == "topic:1"
    assert selected.body_record_path == "root.body"
    assert selected.body_record_count == 3


def test_parser_ambiguity_is_explicit_and_never_enters_adaptive_recovery() -> None:
    site = Site(
        "登堂入室",
        "bottom",
        "https://nlafoq9v.dh5565656.xyz/bbs/topic.php?id=912",
        parser="dengtang_rushi_bottom",
        payload="page",
    )
    rows = []
    for period in range(201, 211):
        rows.append(f"{period}期：《登堂入室》🥫绝杀二肖🥫【狗.蛇】开:01")
    rows.append("210期：《登堂入室》🥫绝杀二肖🥫【龙.虎】开:02")
    source = "澳彩总站【绝杀二肖】 " + " ".join(rows) + " 提示！"
    registry = ParserRegistry.bind_sites([site])

    with pytest.raises(ValueError, match="数据存在冲突") as captured:
        registry.parse(source, site)
    category = adaptive.classify_failure(captured.value)
    assert category == adaptive.FAILURE_DATA_CONFLICT
    assert not adaptive.should_attempt_recovery(site, category, str(captured.value))


def test_plain_empty_candidate_is_not_a_structural_recovery_signal() -> None:
    site = scoped_site()
    category = adaptive.classify_failure(ValueError("未抓到有效候选"))

    assert category == adaptive.FAILURE_TARGET_NOT_FOUND
    assert not adaptive.should_attempt_recovery(site, category, "未抓到有效候选")


def test_adaptive_recovery_rejects_unbound_legacy_document_triples() -> None:
    with pytest.raises(ValueError, match="必须使用DocumentBundle"):
        adaptive._as_document_bundle([("页面", "https://example.test/", "正文")], scoped_site())


def test_dynamic_record_keeps_api_provenance_after_hydration() -> None:
    site = Site(
        "六合中心",
        "top",
        "https://example.test/article/manager/target-id?url=x",
        parser="manager_article_two_zodiac",
        payload="admin_article_api",
    )
    document = PayloadDocument(
        "后台文章",
        "https://example.test/api/target-id",
        "210期：🚲《六合中心》🚲绝杀②肖【狗蛇】开:01",
        record_id="target-id",
        record_path="root.items[0]",
        record_count=2,
    )
    registry = ParserRegistry.bind_sites([site])

    _records, selected = parse_bundle_for_period(
        DocumentBundle((document,)),
        site,
        210,
        registry,
    )

    assert selected.document_url == document.url
    assert selected.record_path == "root.items[0]"
    assert selected.record_count == 2


def test_xiangfu_parser_keeps_all_records_instead_of_trimming_to_365() -> None:
    site = Site(
        "相辅而成",
        "bottom",
        "https://example.test/topic/472645.html",
        parser="xiangfu_ercheng_tail",
        payload="page",
    )
    periods = list(range(195, 366)) + list(range(1, 198))
    rows = [
        f"{period:03d}期：★绝杀二肖★【{'猴龙' if index == len(periods) - 1 else '牛马'}】开00准"
        for index, period in enumerate(periods)
    ]
    source = "197期:【绝杀二肖】相辅而成\n" + "\n".join(rows) + "\n上一篇：其它栏目"
    registry = ParserRegistry.bind_sites([site])

    records = registry.parse(source, site)

    assert len(records) == len(periods) == 368
    assert [record.period for record in records[:4]] == [195, 196, 197, 198]
    assert records[-1].position == source.index("197期：★绝杀二肖★【猴龙】")


def test_baishou_parser_keeps_all_records_instead_of_trimming_to_latest_cycle() -> None:
    site = Site(
        "白手起家",
        "bottom",
        "https://example.test/topic/547564.html",
        parser="baishou_qijia_tail",
        payload="page_and_scripts",
    )
    periods = list(range(119, 366)) + list(range(1, 196))
    rows = [
        f"{period:03d}期：杀二肖【{'马虎' if index == len(periods) - 1 else '牛马'}】开00赢"
        for index, period in enumerate(periods)
    ]
    source = "195期:白手起家【稳杀二肖】\n" + "\n".join(rows) + "\n上一篇：其它栏目"
    registry = ParserRegistry.bind_sites([site])

    records = registry.parse(source, site)

    assert len(records) == len(periods) == 442
    assert records[0].period == 119
    assert records[-1].position == source.index("195期：杀二肖【马虎】")


def test_taxue_parser_preserves_page_order_and_does_not_override_bottom() -> None:
    site = Site(
        "踏雪无痕",
        "bottom",
        "https://example.test/",
        parser="taxue_wuhen_bottom",
        payload="page",
    )
    source = (
        "澳门踏雪无痕『绝杀二肖』 "
        "199期:绝杀二肖【鸡牛】开0000准 "
        "198期:绝杀二肖【牛马】开0000准 "
        "197期:绝杀二肖【猴狗】开0000准 "
        "196期:绝杀二肖【虎羊】开0000准 点击投注"
    )
    registry = ParserRegistry.bind_sites([site])
    bundle = DocumentBundle((PayloadDocument("页面", site.url, source),))

    records = registry.parse(source, site)
    assert [record.period for record in records] == [199, 198, 197, 196]
    with pytest.raises(ValueError, match="bottom 候选内未找到 199 期"):
        parse_bundle_for_period(bundle, site, 199, registry)


def test_taxue_parser_merges_duplicate_value_source_positions() -> None:
    site = Site(
        "踏雪无痕",
        "bottom",
        "https://example.test/",
        parser="taxue_wuhen_bottom",
        payload="page",
    )
    source = (
        "澳门踏雪无痕『绝杀二肖』 "
        "210期:绝杀二肖【鸡牛】开0000准 "
        "210期:绝杀二肖【鸡牛】开0000准 "
        "209期:绝杀二肖【牛马】开0000准 "
        "208期:绝杀二肖【猴狗】开0000准 点击投注"
    )
    registry = ParserRegistry.bind_sites([site])

    records = registry.parse(source, site)

    expected = tuple(match.start() for match in re.finditer("210期:绝杀二肖【鸡牛】", source))
    selected = parse_bundle_for_period(
        DocumentBundle((PayloadDocument("页面", site.url, source),)),
        site,
        210,
        registry,
    )[1]
    assert [record.period for record in records] == [210, 209, 208]
    assert records[0].source_positions == expected
    assert selected.source_positions == expected


def test_dengtang_parser_merges_duplicate_value_source_positions() -> None:
    site = Site(
        "登堂入室",
        "bottom",
        "https://nlafoq9v.dh5565656.xyz/bbs/topic.php?id=912",
        parser="dengtang_rushi_bottom",
        payload="page",
    )
    rows = [
        f"{period}期：《登堂入室》🥫绝杀二肖🥫【狗.蛇】开:01"
        for period in range(201, 211)
    ]
    rows.append("210期：《登堂入室》🥫绝杀二肖🥫【狗.蛇】开:01")
    source = "澳彩总站【绝杀二肖】 " + " ".join(rows) + " 提示！"
    registry = ParserRegistry.bind_sites([site])

    records = registry.parse(source, site)

    expected = tuple(match.start() for match in re.finditer("210期：《登堂入室》", source))
    assert len(records) == 10
    assert records[-1].source_positions == expected

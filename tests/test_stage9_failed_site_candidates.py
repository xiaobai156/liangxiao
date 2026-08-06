from __future__ import annotations

from domain.models import DOCUMENT_BOUNDARY, DocumentBundle, PayloadDocument, Site
from services.failed_site_validation_candidates import (
    CANDIDATE_PARSERS,
    parse_guangdong_candidate,
    parse_guangxizai_candidate,
    parse_jiulong_forum_candidate,
    parse_liuhe_toutiao_candidate,
    parse_meirenzhu_a_candidate,
    parse_shouqi_daoluo_candidate,
    parse_wulin_gaoshou_candidate,
    parse_zhuangyuan_red_candidate,
    validate_candidate_bundle,
)


def test_zhuangyuan_aliases_form_one_anchor() -> None:
    site = Site("狀元紅", "top", "https://example.test/20.html")
    source = (
        "澳门状元红 狀元紅⊙『绝杀②肖』 "
        "212期:绝杀②肖❇〖牛猴〗开:00准 "
        "211期:绝杀②肖❇〖兔鼠〗开:01准 "
        "210期:绝杀②肖❇〖羊虎〗开:49准"
    )
    records = parse_zhuangyuan_red_candidate(source, site)
    assert [(record.period, record.zodiac) for record in records[:3]] == [
        (212, "牛猴"),
        (211, "兔鼠"),
        (210, "羊虎"),
    ]


def test_script_column_candidates_stop_at_their_own_semantic() -> None:
    cases = (
        (
            "六合头条",
            parse_liuhe_toutiao_candidate,
            "精品帖 212期【绝杀两肖】绝对好料 212期绝杀两肖【鼠马】开:00准 211期绝杀两肖【猪狗】开:01准 210期绝杀两肖【龙兔】开:49准",
        ),
        (
            "九龙论坛",
            parse_jiulong_forum_candidate,
            "九龙论坛『绝杀二肖』 点击投注 212期:〖杀二肖→猪鼠〗开:00准 211期:〖杀二肖→猴马〗开:01准",
        ),
        (
            "手起刀落",
            parse_shouqi_daoluo_candidate,
            "手起刀落【绝杀二肖】点击投注 212期绝杀两肖【鼠马】开:00准 211期绝杀两肖【猪狗】开:01准",
        ),
        (
            "广西仔",
            parse_guangxizai_candidate,
            "212期:本站推荐【绝杀二肖】212期:绝杀二肖-(猪羊)-开0000准 211期:绝杀二肖-(猴鼠)-开马01准",
        ),
        (
            "广东",
            parse_guangdong_candidate,
            "212期:【绝杀二肖】212期（必杀二肖）【狗鸡】开00准 211期（必杀二肖）【马羊】开马01准",
        ),
        (
            "没忍住啊",
            parse_meirenzhu_a_candidate,
            "高手贴 212期：[绝杀二肖]━ 118060g.com 212期：绝杀二肖【牛鼠】开0000准 211期：绝杀二肖【羊狗】开马01准",
        ),
    )
    for name, parser, source in cases:
        site = Site(name, "top", f"https://example.test/{name}")
        records = parser(source, site)
        assert records and records[0].period == 212


def test_wulin_candidate_requires_authorized_parent_boundary() -> None:
    site = Site("武林高手", "top", "https://example.test/")
    anchor = "武林高手 212期《绝杀两肖》 item-hidden https://example.test/target.js"
    body = "212期:绝杀两肖【兔龙】开:00准 211期:绝杀两肖【马羊】开:01准 210期:绝杀两肖【牛鼠】开:49准"
    assert parse_wulin_gaoshou_candidate(anchor + DOCUMENT_BOUNDARY + body, site)
    assert not parse_wulin_gaoshou_candidate(body, site)


def test_wrong_column_and_duplicate_zodiac_are_rejected() -> None:
    site = Site("六合头条", "top", "https://example.test/topic/1")
    wrong_column = "精品帖 212期【绝杀三肖】绝对好料 212期绝杀三肖【鼠马猴】开:00准"
    duplicate = "精品帖 212期【绝杀两肖】绝对好料 212期绝杀两肖【牛牛】开:00准"
    assert not parse_liuhe_toutiao_candidate(wrong_column, site)
    assert not parse_liuhe_toutiao_candidate(duplicate, site)


def test_authorized_same_period_conflict_is_not_selected() -> None:
    site = Site("九龙论坛", "top", "https://example.test/#am")
    page = PayloadDocument(
        "页面",
        site.url,
        '<script src="https://cdn.example.test/a.js"></script>'
        '<script src="https://cdn.example.test/b.js"></script>',
    )
    body_a = PayloadDocument(
        "脚本A",
        "https://cdn.example.test/a.js",
        "九龙论坛『绝杀二肖』 212期:〖杀二肖→猪鼠〗开:00准",
        parent_url=site.url,
        link_reference="https://cdn.example.test/a.js",
    )
    body_b = PayloadDocument(
        "脚本B",
        "https://cdn.example.test/b.js",
        "九龙论坛『绝杀二肖』 212期:〖杀二肖→龙虎〗开:00准",
        parent_url=site.url,
        link_reference="https://cdn.example.test/b.js",
    )
    observations, selected, error = validate_candidate_bundle(
        DocumentBundle((page, body_a, body_b)), site, 212
    )
    assert len(observations) == 2
    assert selected is None
    assert "同期冲突" in error


def test_unlinked_script_is_not_an_authorized_candidate() -> None:
    site = Site("九龙论坛", "top", "https://example.test/#am")
    page = PayloadDocument("页面", site.url, "页面")
    linked = PayloadDocument(
        "脚本",
        "https://cdn.example.test/target.js",
        "212期:〖杀二肖→猪鼠〗开:00准",
        parent_url=site.url,
        link_reference="https://cdn.example.test/target.js",
    )
    unrelated = PayloadDocument(
        "诱饵",
        "https://cdn.example.test/other.js",
        "212期:〖杀二肖→龙虎〗开:00准",
    )
    observations, selected, error = validate_candidate_bundle(
        DocumentBundle((page, linked, unrelated)), site, 212
    )
    assert not observations
    assert selected is None
    assert "未找到" in error


def test_candidate_registry_is_explicit() -> None:
    assert set(CANDIDATE_PARSERS) == {
        "狀元紅",
        "六合头条",
        "九龙论坛",
        "手起刀落",
        "武林高手",
        "广西仔",
        "广东",
        "没忍住啊",
    }

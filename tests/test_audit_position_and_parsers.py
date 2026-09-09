from __future__ import annotations

import json

import pytest

from config.loader import ConfigError, load_sites, site_from_mapping
from domain.errors import CandidateConflict
from domain.models import Record, Site
from parsers.chart import (
    parse_laodazhu_ten_zodiac_complement_records,
    parse_suiyin_jiliang_ten_zodiac_complement_records,
    parse_tuku_user_forums_shizhuang_cut_records,
    parse_wangzhejiudian_stat_chart_records,
)
from parsers.forum import (
    parse_baijie_shujinguang_top_records,
    parse_dengtang_rushi_bottom_records,
    parse_gaohuo_zhifei_topic_records,
    parse_taxue_wuhen_bottom_records,
    parse_yiben_wanli_sisha_bottom_records,
)
from parsers.new_sites_235 import parse_xiaosuan_bottom_records
from validation.conflicts import validate_document_windows
from validation.direction import select_record
from validation.records import record_signature


def site(pick: str = "top", name: str = "测试站") -> Site:
    return Site(name, pick, "https://example.test")


def record(period: int, zodiac: str, position: int) -> Record:
    return Record(period, zodiac, "", zodiac, position)


def test_record_signature_normalizes_value_and_keeps_original_position() -> None:
    assert record_signature(record(235, "虎-兔", 17)) == ("虎兔", 17)


def test_gaohuo_zhifei_accepts_current_missing_closing_parenthesis() -> None:
    source = (
        "绝杀料 240期: 【稳杀二肖】 作者:膏火之费 "
        "238期:☆ 稳杀二肖 ☆(狗龙)开:虎17准 "
        "239期:☆ 稳杀二肖 ☆(鸡猪)开:虎05准 "
        "240期:☆ 稳杀二肖 ☆(蛇龙开:？00准"
    )

    records = parse_gaohuo_zhifei_topic_records(source, site(pick="bottom", name="膏火之费"))

    assert [(item.period, item.zodiac) for item in records] == [
        (238, "狗龙"),
        (239, "鸡猪"),
        (240, "蛇龙"),
    ]
    assert select_record(records, 240, site(pick="bottom", name="膏火之费")).zodiac == "蛇龙"


def test_same_zodiac_at_different_positions_is_a_direction_conflict() -> None:
    records = [record(235, "虎兔", 10), record(235, "虎兔", 20)]

    with pytest.raises(ValueError, match="数据存在冲突"):
        select_record(records, 235, site())


def test_exact_duplicate_at_the_same_position_is_deduplicated() -> None:
    duplicate = record(235, "虎兔", 10)

    selected = select_record([duplicate, duplicate], 235, site())

    assert selected.position == 10
    assert selected.source_positions == (10,)


def test_document_conflicts_use_positions_locally_but_values_across_documents() -> None:
    with pytest.raises(ValueError, match="数据存在冲突"):
        validate_document_windows(
            [("正文", [record(235, "虎兔", 10), record(235, "虎兔", 20)])],
            site(),
            "测试",
        )

    validate_document_windows(
        [
            ("正文A", [record(235, "虎兔", 10)]),
            ("正文B", [record(235, "虎兔", 20)]),
        ],
        site(),
        "测试",
    )


def test_chart_parser_preserves_same_value_at_distinct_positions() -> None:
    source = (
        "《王者九点网㊣杀禁统计》"
        "235期杀肖统计 禁二肖图：【虎兔】特开 00 "
        "禁二肖图：【虎兔】"
    )

    records = parse_wangzhejiudian_stat_chart_records(source, "禁二肖图")

    assert [(item.period, item.zodiac) for item in records] == [(235, "虎兔"), (235, "虎兔")]
    assert records[0].position != records[1].position


_TEN_ZODIACS_WITHOUT_HU_TU = "牛马羊鸡狗猪鼠龙蛇猴"


@pytest.mark.parametrize(
    ("parser", "site_config", "source", "expected_zodiac"),
    [
        pytest.param(
            parse_yiben_wanli_sisha_bottom_records,
            site(pick="bottom", name="一本万利死杀"),
            "一本万利【绝杀二肖】"
            "233期绝杀二肖开:00准【牛马】"
            "235期绝杀二肖开:00准【虎兔】"
            "235期绝杀二肖开:00准【虎兔】",
            "虎兔",
            id="yiben-wanli-sisha-bottom",
        ),
        pytest.param(
            parse_baijie_shujinguang_top_records,
            site(pick="top", name="白姐輸盡光"),
            "澳门姜太公☛白姐輸盡光☚"
            "235期【白姐輸尽光】开:00 今期虎兔输尽光 "
            "235期【白姐輸尽光】开:00 今期虎兔输尽光 "
            "234期【白姐輸尽光】开:00 今期牛马输尽光 "
            "澳门姜太公☛其他",
            "虎兔",
            id="baijie-shujinguang-top",
        ),
        pytest.param(
            parse_laodazhu_ten_zodiac_complement_records,
            site(pick="top"),
            "【老大主十肖】"
            f"233期【{_TEN_ZODIACS_WITHOUT_HU_TU}】"
            f"235期【{_TEN_ZODIACS_WITHOUT_HU_TU}】"
            f"235期【{_TEN_ZODIACS_WITHOUT_HU_TU}】",
            "虎兔",
            id="laodazhu-ten-zodiac-complement",
        ),
        pytest.param(
            parse_suiyin_jiliang_ten_zodiac_complement_records,
            site(pick="top"),
            "235期：碎银几两【必中十肖】作者：碎银几两 "
            f"233期【必中十肖】【{_TEN_ZODIACS_WITHOUT_HU_TU}】开:00 "
            f"235期【必中十肖】【{_TEN_ZODIACS_WITHOUT_HU_TU}】开:00 "
            f"235期【必中十肖】【{_TEN_ZODIACS_WITHOUT_HU_TU}】开:00",
            "虎兔",
            id="suiyin-jiliang-ten-zodiac-complement",
        ),
        pytest.param(
            parse_tuku_user_forums_shizhuang_cut_records,
            site(pick="top", name="试装"),
            json.dumps(
                [
                    {
                        "topic": "砍②肖",
                        "content": "235砍-虎兔 235砍-虎兔 234砍-牛马",
                        "user": {"nickname": "试装"},
                    }
                ],
                ensure_ascii=False,
            ),
            "虎兔",
            id="tuku-shizhuang-cut",
        ),
    ],
)
def test_active_parser_position_merges_reach_position_aware_conflict(
    parser, site_config, source, expected_zodiac
):
    records = parser(source, site_config)
    assert len(records) == 3
    matches = [
        record
        for record in records
        if record.period == 235 and record.zodiac == expected_zodiac
    ]

    assert len(matches) == 2
    assert len({record.position for record in matches}) == 2
    with pytest.raises(ValueError, match="数据存在冲突"):
        select_record(records, 235, site_config)


def test_taxue_position_signature_path_keeps_raw_order() -> None:
    source = (
        "澳门踏雪无痕『绝杀二肖』"
        "235期：绝杀二肖【虎兔】开:00 "
        "234期：绝杀二肖【牛马】开:00 "
        "233期：绝杀二肖【羊鸡】开:00 "
        "点击投注"
    )

    records = parse_taxue_wuhen_bottom_records(
        source,
        site(pick="bottom", name="踏雪无痕"),
    )

    assert [(record.period, record.zodiac) for record in records] == [
        (235, "虎兔"),
        (234, "牛马"),
        (233, "羊鸡"),
    ]
    assert [record.position for record in records] == sorted(record.position for record in records)


def test_dengtang_rejects_same_value_at_distinct_raw_positions() -> None:
    source = (
        "澳彩总站【绝杀二肖】"
        "235期：《登堂入室》🥫绝杀二肖🥫【虎.兔】开:00 "
        "235期：《登堂入室》🥫绝杀二肖🥫【虎.兔】开:00 "
        "提示！"
    )
    site_config = Site(
        "登堂入室",
        "bottom",
        "https://example.test/topic.php?id=912",
    )

    with pytest.raises(CandidateConflict, match="重复候选"):
        parse_dengtang_rushi_bottom_records(source, site_config)


def test_xiaosuan_rejects_duplicate_target_records() -> None:
    site_config = Site(
        "小算算",
        "bottom",
        "https://zcphjs.ce83x-ms2rz-orwude.work:12277/#/users/214200",
        parser="xiaosuan_bottom_two_zodiac",
        payload="tuku_user_forums",
    )
    source = json.dumps(
        [
            {"topic": "杀肖", "content": "235期猴鸡", "user": {"nickname": "小算算"}},
            {"topic": "杀肖", "content": "235期虎兔", "user": {"nickname": "小算算"}},
        ],
        ensure_ascii=False,
    )

    assert parse_xiaosuan_bottom_records(source, site_config) == []


@pytest.mark.parametrize(
    "payload",
    (
        "page",
        "page_and_scripts",
        "browser_rendered_page",
        "topic_list_detail",
        "admin_article_api",
        "tuku_user_forums",
        "curl_tls10_page_and_scripts",
        "scripts",
    ),
)
def test_loader_accepts_each_fetch_payload_mode(payload: str) -> None:
    loaded = site_from_mapping(
        {
            "name": "模式站",
            "pick": "top",
            "url": "https://example.test/page",
            "payload": payload,
        }
    )

    assert loaded.payload == payload


def test_loader_rejects_unknown_payload() -> None:
    with pytest.raises(ConfigError, match="模式站.*payload"):
        site_from_mapping(
            {
                "name": "模式站",
                "pick": "top",
                "url": "https://example.test/page",
                "payload": "unknown_mode",
            }
        )


@pytest.mark.parametrize("url", ("http://[bad", "http://example.test:notaport"))
def test_loader_rejects_malformed_main_url_as_config_error(url: str) -> None:
    with pytest.raises(ConfigError, match="模式站.*URL"):
        site_from_mapping(
            {
                "name": "模式站",
                "pick": "top",
                "url": url,
                "payload": "page",
            }
        )


def test_loader_rejects_malformed_json(tmp_path) -> None:
    path = tmp_path / "sites.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(ConfigError, match="JSON格式错误"):
        load_sites(path)


def test_loader_rejects_invalid_utf8_as_config_error(tmp_path) -> None:
    path = tmp_path / "sites.json"
    path.write_bytes(b"\xff")

    with pytest.raises(ConfigError, match="JSON格式错误"):
        load_sites(path)


def test_loader_rejects_empty_site_list(tmp_path) -> None:
    path = tmp_path / "sites.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ConfigError, match="站点配置不能为空"):
        load_sites(path)


@pytest.mark.parametrize("field", ("title", "record", "stop", "linked_document_pattern"))
def test_loader_rejects_invalid_site_regex(field: str) -> None:
    with pytest.raises(ConfigError, match=rf"模式站.*{field}"):
        site_from_mapping(
            {
                "name": "模式站",
                "pick": "top",
                "url": "https://example.test/page",
                "payload": "page",
                field: "[",
            }
        )


@pytest.mark.parametrize(
    "api_url",
    ("ftp://api.example.test/data", "/relative", "http:///missing-host", "http://example.test:notaport"),
)
def test_loader_rejects_invalid_api_url(api_url: str) -> None:
    with pytest.raises(ConfigError, match="模式站.*api_url"):
        site_from_mapping(
            {
                "name": "模式站",
                "pick": "top",
                "url": "https://example.test/page",
                "payload": "page",
                "api_url": api_url,
            }
        )


def test_loader_preserves_same_url_sites_with_distinct_identities(tmp_path) -> None:
    path = tmp_path / "sites.json"
    path.write_text(
        json.dumps(
            [
                {"name": "顶部站", "pick": "top", "url": "https://same.example.test", "payload": "page", "parser": "site_scoped_two_zodiac", "title": "顶部站"},
                {"name": "底部站", "pick": "bottom", "url": "https://same.example.test", "payload": "page", "parser": "site_scoped_two_zodiac", "title": "底部站"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loaded = load_sites(path)

    assert [site.identity for site in loaded] == [
        ("顶部站", "https://same.example.test", "top"),
        ("底部站", "https://same.example.test", "bottom"),
    ]

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from domain.models import DOCUMENT_BOUNDARY, Site
from fetching.client import FetchContext
from parsers.new_sites_235 import (
    EXACT_TOPIC_SITES,
    parse_feilong_qishi_records,
    parse_new_topic_235_records,
    parse_tuoni_kulmonika_records,
    parse_xiaosuan_bottom_records,
    parse_zhougong_shensuan_records,
)
from parsers.registry import ENGINE_REGISTRY, ParserRegistry
from services.single_period import fetch_payload_for_period, parse_bundle_for_period
from validation.direction import select_record


def topic_site(name: str) -> Site:
    pick, url = EXACT_TOPIC_SITES[name]
    return Site(
        name=name,
        pick=pick,
        url=url,
        parser="new_topic_235_exact",
        title=name,
        payload="page_and_scripts",
    )


def topic_source(name: str, rows: tuple[tuple[int, str], ...]) -> str:
    return "".join(
        [f"<h2>{name}</h2>"]
        + [f"<div>{period:03d}期绝杀二肖【{zodiac}】开:00</div>" for period, zodiac in rows]
        + ["<div>上一篇：</div>"]
    )


@pytest.mark.parametrize("name", tuple(EXACT_TOPIC_SITES))
def test_exact_topic_parser_is_registered_and_identity_locked(name: str) -> None:
    site = topic_site(name)
    rows = (
        ((235, "龙狗"), (234, "鼠牛"), (233, "兔猴"), (232, "虎狗"))
        if site.pick == "top"
        else ((232, "虎狗"), (233, "兔猴"), (234, "鼠牛"), (235, "龙狗"))
    )
    source = topic_source(name, rows)

    assert ENGINE_REGISTRY[site.parser] is parse_new_topic_235_records
    records = ParserRegistry.bind_sites([site]).parse(source, site)
    assert select_record(records, 235, site).zodiac == "龙狗"
    assert parse_new_topic_235_records(source, replace(site, url=site.url + "?wrong=1")) == []


def test_topic_parser_rejects_cross_document_anchor_borrowing() -> None:
    site = topic_site("上官研之")
    source = topic_source("上官研之", ((235, "龙狗"),))

    assert parse_new_topic_235_records(source + DOCUMENT_BOUNDARY + source, site) == []


def test_zhougong_parser_selects_only_exact_two_zodiac_field() -> None:
    site = Site(
        "周公神算",
        "top",
        "https://xxn08n.w2jqr-rbl71-ngvkmq.work/",
        parser="zhougong_shensuan_two_zodiac",
        payload="page_and_scripts",
    )
    source = "".join(
        (
            "<p>235期绝杀半波【红双】开:00</p>",
            "<p>235期绝杀二肖【虎羊】开:0000准</p>",
            "<p>234期绝杀二肖【猪猴】开:龙39准</p>",
            "<p>233期绝杀二肖【龙鸡】开:鼠07准</p>",
        )
    )

    assert ENGINE_REGISTRY[site.parser] is parse_zhougong_shensuan_records
    records = ParserRegistry.bind_sites([site]).parse(source, site)
    assert [(record.period, record.zodiac) for record in records] == [
        (235, "虎羊"),
        (234, "猪猴"),
        (233, "龙鸡"),
    ]
    assert select_record(records, 235, site).zodiac == "虎羊"


def test_tuoni_uses_api_snapshot_order_for_top_window() -> None:
    site = Site(
        "托尼库尔莫妮卡",
        "top",
        "https://zcphjs.ce83x-ms2rz-orwude.work:12277/#/users/230245",
        parser="tuoni_kulmonika_snapshots",
        payload="tuku_user_forums",
    )
    source = json.dumps(
        [
            {"draw": 236, "topic": "杀：猴猪", "user": {"nickname": site.name}},
            {"draw": 235, "topic": "杀：兔龙", "user": {"nickname": site.name}},
            {"draw": 234, "topic": "杀：马鼠", "user": {"nickname": site.name}},
            {"draw": 233, "topic": "杀：牛猴", "user": {"nickname": site.name}},
        ],
        ensure_ascii=False,
    )

    assert ENGINE_REGISTRY[site.parser] is parse_tuoni_kulmonika_records
    records = ParserRegistry.bind_sites([site]).parse(source, site)
    assert [(record.period, record.zodiac) for record in records] == [
        (236, "猴猪"),
        (235, "兔龙"),
        (234, "马鼠"),
        (233, "牛猴"),
    ]
    assert select_record(records, 235, site).zodiac == "兔龙"


def test_xiaosuan_bottom_ignores_one_and_three_zodiac_rows() -> None:
    site = Site(
        "小算算",
        "bottom",
        "https://zcphjs.ce83x-ms2rz-orwude.work:12277/#/users/214200",
        parser="xiaosuan_bottom_two_zodiac",
        payload="tuku_user_forums",
    )
    source = json.dumps(
        [
            {
                "draw": 236,
                "topic": "杀肖",
                "content": (
                    "232期杀羊✔️<div>233期杀猪✔️</div>"
                    "<div>234期杀猪虎✔️</div><div>235期猴鸡✔️</div>"
                    "<div>236期杀马鸡</div><div>150期杀牛蛇龙✔️</div>"
                ),
                "user": {"nickname": site.name},
            }
        ],
        ensure_ascii=False,
    )

    assert ENGINE_REGISTRY[site.parser] is parse_xiaosuan_bottom_records
    records = ParserRegistry.bind_sites([site]).parse(source, site)
    assert [(record.period, record.zodiac) for record in records] == [
        (234, "猪虎"),
        (235, "猴鸡"),
        (236, "马鸡"),
    ]
    assert select_record(records, 235, site).zodiac == "猴鸡"


def xiaosuan_item(
    draw: int,
    content: str,
    *,
    item_id: int | None = None,
    nickname: str = "小算算",
    topic: str = "杀肖",
    status: str = "published",
) -> dict[str, object]:
    return {
        "id": item_id or draw,
        "draw": draw,
        "status": status,
        "topic": topic,
        "content": content,
        "user_id": 214200,
        "user": {"id": 214200, "nickname": nickname},
    }


def test_xiaosuan_service_isolates_the_exact_237_post_before_parsing() -> None:
    site = Site(
        "小算算",
        "bottom",
        "https://zcphjs.ce83x-ms2rz-orwude.work:12277/#/users/214200",
        parser="xiaosuan_bottom_two_zodiac",
        payload="tuku_user_forums",
    )
    api_source = json.dumps(
        [
            xiaosuan_item(
                237,
                "234期杀猪虎✔️ 235期猴鸡✔️ 236期杀马鸡✔️ 237期杀蛇鸡",
                item_id=15883799,
            ),
            xiaosuan_item(236, "234期杀猪虎✔️ 235期猴鸡✔️ 236期杀马鸡"),
        ],
        ensure_ascii=False,
    )
    context = FetchContext(text_fetcher=lambda _url, _timeout: api_source)
    registry = ParserRegistry.bind_sites([site])

    bundle = fetch_payload_for_period(site, 237, 3, context, registry)
    isolated = json.loads(bundle.documents[0].source)
    _records, selected = parse_bundle_for_period(bundle, site, 237, registry)

    assert [item["id"] for item in isolated] == [15883799]
    assert bundle.documents[0].record_id == "user:214200"
    assert bundle.documents[0].record_path == "root[id=15883799]"
    assert bundle.documents[0].record_count == 1
    assert selected.zodiac == "蛇鸡"


@pytest.mark.parametrize(
    ("items", "expected"),
    (
        (
            [xiaosuan_item(236, "236期杀马鸡 237期杀蛇鸡")],
            "内容尚未发布：用户接口内未找到237期帖子",
        ),
        (
            [
                xiaosuan_item(237, "237期杀蛇鸡", item_id=1),
                xiaosuan_item(237, "237期杀虎兔", item_id=2),
            ],
            "数据存在冲突：用户接口内237期出现2篇帖子",
        ),
        (
            [xiaosuan_item(237, "237期杀蛇鸡", nickname="错误作者")],
            "未找到指定目标：237期帖子作者、栏目或发布状态不匹配",
        ),
        (
            [xiaosuan_item(237, "237期杀蛇鸡", topic="八码")],
            "未找到指定目标：237期帖子作者、栏目或发布状态不匹配",
        ),
        (
            [xiaosuan_item(237, "237期杀蛇鸡", status="draft")],
            "未找到指定目标：237期帖子作者、栏目或发布状态不匹配",
        ),
    ),
)
def test_xiaosuan_service_rejects_missing_ambiguous_or_wrong_target_post(
    items: list[dict[str, object]],
    expected: str,
) -> None:
    site = Site(
        "小算算",
        "bottom",
        "https://zcphjs.ce83x-ms2rz-orwude.work:12277/#/users/214200",
        parser="xiaosuan_bottom_two_zodiac",
        payload="tuku_user_forums",
    )
    source = json.dumps(items, ensure_ascii=False)
    context = FetchContext(text_fetcher=lambda _url, _timeout: source)

    with pytest.raises(ValueError, match=expected):
        fetch_payload_for_period(site, 237, 3, context, ParserRegistry.bind_sites([site]))


def test_xiaosuan_service_requires_the_target_article_id() -> None:
    site = Site(
        "小算算",
        "bottom",
        "https://zcphjs.ce83x-ms2rz-orwude.work:12277/#/users/214200",
        parser="xiaosuan_bottom_two_zodiac",
        payload="tuku_user_forums",
    )
    target = xiaosuan_item(237, "237期杀蛇鸡")
    target["id"] = None
    source = json.dumps([target], ensure_ascii=False)
    context = FetchContext(text_fetcher=lambda _url, _timeout: source)

    with pytest.raises(ValueError, match="字段校验未通过：237期目标帖子缺少文章ID"):
        fetch_payload_for_period(site, 237, 3, context, ParserRegistry.bind_sites([site]))


def test_feilong_qishi_keeps_records_after_inline_ad_for_bottom_window() -> None:
    site = Site(
        "飞龙骑士",
        "bottom",
        "https://bbs02.836280.cyou/bbs/topic.php?id=18357",
        parser="feilong_qishi_bottom",
        payload="page_and_scripts",
    )
    source = "".join(
        (
            "<h2>236期：飞龙骑士【绝杀二肖】</h2>",
            "<p>227期:【飞龙骑士】绝杀二肖【狗.虎】开:兔16准</p>",
            "<p>本资料最早发表在836280.com欢迎转发+关注</p>",
            "<p>234期:【飞龙骑士】绝杀二肖【虎.羊】开:龙39准</p>",
            "<p>235期:【飞龙骑士】绝杀二肖【鸡.猪】开:猪32错</p>",
            "<p>236期:【飞龙骑士】绝杀二肖【龙.鼠】开:00准</p>",
            "<p>下一贴：其他资料</p>",
            "<p>235期:【其他】绝杀二肖【牛.马】开:00准</p>",
        )
    )

    assert ENGINE_REGISTRY[site.parser] is parse_feilong_qishi_records
    records = ParserRegistry.bind_sites([site]).parse(source, site)
    assert [(record.period, record.zodiac) for record in records] == [
        (227, "狗虎"),
        (234, "虎羊"),
        (235, "鸡猪"),
        (236, "龙鼠"),
    ]
    assert select_record(records, 235, site).zodiac == "鸡猪"
    assert parse_feilong_qishi_records(source, replace(site, pick="top")) == []


@pytest.mark.parametrize(
    ("parser", "site"),
    [
        (
            parse_zhougong_shensuan_records,
            Site("错误", "top", "https://xxn08n.w2jqr-rbl71-ngvkmq.work/", payload="page_and_scripts"),
        ),
        (
            parse_tuoni_kulmonika_records,
            Site("错误", "top", "https://zcphjs.ce83x-ms2rz-orwude.work:12277/#/users/230245", payload="tuku_user_forums"),
        ),
        (
            parse_xiaosuan_bottom_records,
            Site("错误", "bottom", "https://zcphjs.ce83x-ms2rz-orwude.work:12277/#/users/214200", payload="tuku_user_forums"),
        ),
    ],
)
def test_special_parsers_reject_wrong_identity(parser, site: Site) -> None:
    assert parser("[]", site) == []

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from diagnostics.xiaosuan_stage1.strategy import select_target_record
from domain.models import Site

SITE = Site(
    "小算算",
    "bottom",
    "https://zcphjs.ce83x-ms2rz-orwude.work:12277/#/users/214200",
    parser="xiaosuan_bottom_two_zodiac",
    payload="tuku_user_forums",
)


def item(
    draw: int,
    content: str,
    *,
    item_id: int | None = None,
    user_id: int = 214200,
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
        "user_id": user_id,
        "user": {"id": user_id, "nickname": nickname},
    }


def payload(*items: dict[str, object]) -> str:
    return json.dumps(items, ensure_ascii=False)


def test_exact_draw_post_supplies_237_bottom_record() -> None:
    source = payload(
        item(238, "236期马鸡✔️ 237期虎兔✔️ 238期牛羊"),
        item(
            237,
            "233期杀猪✔️ 234期杀猪虎✔️ 235期猴鸡✔️ 236期杀马鸡✔️ 237期杀蛇鸡",
            item_id=15883799,
        ),
        item(236, "234期杀猪虎✔️ 235期猴鸡✔️ 236期杀马鸡"),
    )

    selected = select_target_record(source, SITE, 237)

    assert selected.period == 237
    assert selected.zodiac == "蛇鸡"
    assert selected.position >= 0


def test_adjacent_and_missing_draws_are_independent() -> None:
    source = payload(
        item(236, "234期杀猪虎✔️ 235期猴鸡✔️ 236期杀马鸡"),
        item(235, "233期杀猪✔️ 234期杀猪虎✔️ 235期猴鸡"),
    )

    assert select_target_record(source, SITE, 235).zodiac == "猴鸡"
    with pytest.raises(ValueError, match="238期目标帖子数量不是1：0"):
        select_target_record(source, SITE, 238)


def test_target_cannot_borrow_the_period_from_another_draw_post() -> None:
    source = payload(item(238, "236期马鸡✔️ 237期蛇鸡✔️ 238期牛羊"))

    with pytest.raises(ValueError, match="237期目标帖子数量不是1：0"):
        select_target_record(source, SITE, 237)


def test_duplicate_target_draw_is_rejected() -> None:
    source = payload(
        item(237, "237期杀蛇鸡", item_id=1),
        item(237, "237期杀虎兔", item_id=2),
    )

    with pytest.raises(ValueError, match="237期目标帖子数量不是1：2"):
        select_target_record(source, SITE, 237)


@pytest.mark.parametrize(
    "changed",
    (
        {"nickname": "错误作者"},
        {"topic": "八码"},
        {"status": "draft"},
    ),
)
def test_wrong_author_column_or_status_is_rejected(changed: dict[str, str]) -> None:
    source = payload(item(237, "237期杀蛇鸡", **changed))

    with pytest.raises(ValueError, match="237期目标帖子数量不是1：0"):
        select_target_record(source, SITE, 237)


def test_wrong_user_and_direction_are_rejected() -> None:
    with pytest.raises(ValueError, match="用户ID边界冲突"):
        select_target_record(payload(item(237, "237期杀蛇鸡", user_id=999)), SITE, 237)
    with pytest.raises(ValueError, match="目标帖子内没有有效二肖候选"):
        select_target_record(
            payload(item(237, "237期杀蛇鸡")),
            replace(SITE, pick="top"),
            237,
        )

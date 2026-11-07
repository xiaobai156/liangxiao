"""site_scoped_two_zodiac 静默回退的取数边界。

回退只在锚点不唯一时接管，但它必须仍然由本站锚点开始、在本站区块边界结束；
否则同页里其他作者的同期行会被当成本站结果（抓错数据）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from domain.models import Site
from parsers.helpers import parse_site_scoped_two_zodiac_records
from validation.direction import select_record


LONG = "这里是无关的版面导航与说明文字，转载前请先阅读本页提示，谢谢大家的长期支持与配合"
LONG_NAME = "张三" + LONG
LONG_NAME2 = LONG + "张三的最新栏目已更新"
LONG_PLAIN = "绝杀专区" + LONG
LONG_PLAIN2 = LONG + "绝杀专区说明"


def _site(**overrides) -> Site:
    values = {
        "name": "张三",
        "pick": "top",
        "url": "http://example.test/topic/1.html",
        "parser": "site_scoped_two_zodiac",
        "title": "张三",
        "payload": "page_and_scripts",
    }
    values.update(overrides)
    return Site(**values)


def _rows(records) -> list[tuple[int, str]]:
    return [(record.period, record.zodiac) for record in records]


def test_fallback_ignores_rows_before_the_site_anchor() -> None:
    html = f"""<html><body>
<div>{LONG}</div>
<div>249期 绝杀二肖【龙蛇】 开:00</div>
<div>{LONG_NAME}</div>
<div>250期 绝杀二肖【鼠虎】 开:00</div>
<div>{LONG_NAME2}</div>
<div>251期 绝杀二肖【牛马】 开:00</div>
</body></html>"""
    site = _site()
    records = parse_site_scoped_two_zodiac_records(html, site)

    assert _rows(records) == [(250, "鼠虎"), (251, "牛马")]
    with pytest.raises(ValueError):
        select_record(records, 249, site)


def test_fallback_stops_at_the_visible_section_boundary() -> None:
    html = f"""<html><body>
<div>{LONG}</div>
<div>{LONG_NAME}</div>
<div>250期 绝杀二肖【鼠虎】 开:00</div>
<div>李四 发表于</div>
<div>249期 绝杀二肖【龙蛇】 开:00</div>
<div>{LONG_NAME}</div>
</body></html>"""
    site = _site()
    records = parse_site_scoped_two_zodiac_records(html, site)

    assert _rows(records) == [(250, "鼠虎")]
    with pytest.raises(ValueError):
        select_record(records, 249, site)


def test_fallback_never_borrows_without_a_site_anchor_on_the_page() -> None:
    html = f"""<html><body>
<div>{LONG}</div>
<div>{LONG_PLAIN}</div>
<div>249期 绝杀二肖【龙蛇】 开:00</div>
<div>{LONG_PLAIN2}</div>
</body></html>"""
    site = _site(title="绝杀专区")

    with pytest.raises(ValueError):
        parse_site_scoped_two_zodiac_records(html, site)


def test_strict_block_refuses_the_silent_fallback() -> None:
    html = f"""<html><body>
<div>{LONG}</div>
<div>249期 绝杀二肖【龙蛇】 开:00</div>
<div>{LONG_NAME}</div>
<div>250期 绝杀二肖【鼠虎】 开:00</div>
<div>{LONG_NAME2}</div>
<div>251期 绝杀二肖【牛马】 开:00</div>
</body></html>"""
    site = _site(strict_block=True)

    with pytest.raises(ValueError, match="专属锚点不唯一"):
        parse_site_scoped_two_zodiac_records(html, site)


def test_stop_that_only_exists_in_joined_text_kills_a_single_document() -> None:
    """`#`×512 是多文档拼接用的分隔线，单个文档里不存在。

    把它当 stop，逐文档解析时会报「停止边界缺失」，整套流程里任一文档抛错就整站失败。
    这条钉住那个坑：真实事故是 26 个站因此全挂。
    """
    document = "张三\n251期 绝杀二肖【牛马】 开:00\n"
    site = _site(stop="#{64,}", strict_block=True)

    with pytest.raises(ValueError, match="停止边界缺失"):
        parse_site_scoped_two_zodiac_records(document, site)


def test_no_site_uses_the_joined_document_boundary_as_a_stop() -> None:
    """配置层面：任何站点都不许把多文档分隔线当 stop。"""
    from config.loader import load_sites
    from parsers.registry import ENGINE_REGISTRY

    root = Path(__file__).resolve().parents[1]
    sites = load_sites(root / "config" / "sites.json", allowed_parsers=ENGINE_REGISTRY)
    offenders = [site.name for site in sites if re.search(r"#{64,}|\{64,\}", site.stop)]

    assert offenders == []


def test_strict_block_returns_empty_when_the_block_has_no_records() -> None:
    """锚点能唯一锁定、但区块里没有记录：这份文档就是没数据，不能整站判失败。"""
    html = f"""<html><body>
<div>张三</div>
<div>{LONG}</div>
<div>{LONG_NAME2}</div>
</body></html>"""
    site = _site(strict_block=True)

    assert parse_site_scoped_two_zodiac_records(html, site) == []


def test_structural_title_still_parses_without_the_fallback() -> None:
    html = f"""<html><body>
<div>{LONG}</div>
<div>张三</div>
<div>251期 绝杀二肖【牛马】 开:00</div>
<div>250期 绝杀二肖【鼠虎】 开:00</div>
<div>李四 发表于</div>
<div>249期 绝杀二肖【龙蛇】 开:00</div>
</body></html>"""
    site = _site(strict_block=True)
    records = parse_site_scoped_two_zodiac_records(html, site)

    assert _rows(records) == [(251, "牛马"), (250, "鼠虎")]


def test_fallback_stops_at_a_long_author_signature_line() -> None:
    signature = "李四 发表于 2026年11月06日 22:00:00 浏览 1234 次 回复 5 次 最后编辑于 22:30"
    html = f"""<html><body>
<div>{LONG}</div>
<div>{LONG_NAME}</div>
<div>251期 绝杀二肖【牛马】 开:00</div>
<div>{signature}</div>
<div>249期 绝杀二肖【龙蛇】 开:00</div>
<div>{LONG_NAME2}</div>
</body></html>"""
    site = _site()
    records = parse_site_scoped_two_zodiac_records(html, site)

    assert _rows(records) == [(251, "牛马")]
    with pytest.raises(ValueError):
        select_record(records, 249, site)


def test_fallback_skips_unpublished_placeholder_instead_of_cutting() -> None:
    html = f"""<html><body>
<div>{LONG}</div>
<div>{LONG_NAME}</div>
<div>251期 绝杀二肖【牛马】 开:00</div>
<div>暂无数据</div>
<div>250期 绝杀二肖【鼠虎】 开:00</div>
<div>{LONG_NAME}</div>
</body></html>"""
    site = _site()
    records = parse_site_scoped_two_zodiac_records(html, site)

    assert _rows(records) == [(251, "牛马"), (250, "鼠虎")]


def test_fallback_region_never_exceeds_the_scoring_window() -> None:
    filler = "无关的页面填充文字" * 8  # 80 字，超过短标签判据，不构成分节边界
    padding = "\n".join(f"<div>{filler}</div>" for _ in range(200))  # >12000 字
    html = f"""<html><body>
<div>{LONG_NAME}</div>
{padding}
<div>{LONG_NAME}</div>
{padding}
<div>249期 绝杀二肖【龙蛇】 开:00</div>
</body></html>"""
    site = _site()

    with pytest.raises(ValueError):
        parse_site_scoped_two_zodiac_records(html, site)


def test_strict_block_is_rejected_for_parsers_that_ignore_it(tmp_path) -> None:
    from config.loader import load_sites

    config = tmp_path / "sites.json"
    config.write_text(
        json.dumps(
            [
                {
                    "name": "澳门铁粉",
                    "pick": "top",
                    "url": "http://example.test/gsb/050.html",
                    "payload": "page",
                    "parser": "named_block",
                    "title": "作者\\s*[:：]\\s*澳门铁粉",
                    "record": "(?P<period>\\d{3})期[^\\n]{0,80}?[【〖]\\s*(?P<zodiac>[牛马羊鸡狗猪鼠虎兔龙蛇猴]{2})\\s*[】〗][^\\n]{0,40}?(?P<open>[^\\s准中错赢对]+)",
                    "strict_block": True,
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(Exception, match="strict_block"):
        load_sites(config)


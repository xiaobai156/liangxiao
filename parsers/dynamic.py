from __future__ import annotations

import json
import re

from domain.models import Record, Site
from parsers.helpers import (
    ZODIACS,
    ZODIAC_SET,
    clean_zodiac,
    detail_record_identity,
    html_to_text,
    records_from_pattern,
    ten_unique_zodiacs,
)

def parse_fugui_kede_admin_article_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*《\s*富贵可得\s*》\s*"
        rf"绝\s*杀\s*(?:二|两|2|２|②)\s*肖\s*"
        rf"[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]]\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
    )
    return records_from_pattern(text, pattern)


def parse_songjiu_yingxin_admin_article_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*《\s*送旧迎新\s*》\s*"
        rf"💎\s*绝\s*杀\s*(?:二|两|2|２|②)\s*肖\s*💎\s*"
        rf"[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]]\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
    )
    return records_from_pattern(text, pattern)


def parse_fixed_named_material_records(
    source: str,
    site: Site,
    marker: str,
    semantic: str,
) -> list[Record]:
    text = html_to_text(source)
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*"
        rf"[『〖【]\s*{re.escape(site.name)}\s*[』〗】]\s*"
        rf"{re.escape(marker)}\s*{re.escape(semantic)}\s*{re.escape(marker)}\s*"
        rf"[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]]\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
    )
    return records_from_pattern(text, pattern)


def parse_liuhe_daoren_admin_article_records(source: str, site: Site) -> list[Record]:
    return parse_fixed_named_material_records(source, site, "🚲", "绝杀二肖")


def parse_junlin_tianxia_manager_article_records(source: str, site: Site) -> list[Record]:
    if site.name != "君临天下":
        return []
    return parse_fixed_named_material_records(source, site, "⚽", "绝杀二肖")


def parse_liuhe_zhongxin_manager_article_records(source: str, site: Site) -> list[Record]:
    if site.name != "六合中心":
        return []
    return parse_fixed_named_material_records(source, site, "🚲", "绝杀②肖")


def parse_jinbao_mawang_manager_article_records(source: str, site: Site) -> list[Record]:
    if site.name != "金宝码王":
        return []
    return parse_fixed_named_material_records(source, site, "🧶", "绝杀二肖")


def parse_xuanji_tianshu_admin_article_records(source: str, site: Site) -> list[Record]:
    if site.name != "玄机天书":
        return []
    text = html_to_text(source)
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*🧤\s*"
        rf"[『〖【]\s*玄机天书\s*[』〗】]\s*🧤\s*绝杀二肖\s*🧤\s*"
        rf"[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]]\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
    )
    return records_from_pattern(text, pattern)


def parse_rushen_tantao_read_page_records(source: str, site: Site) -> list[Record]:
    if site.name != "如神探讨":
        return []
    return parse_fixed_named_material_records(source, site, "🚲", "绝杀②肖")


def parse_huayan_yuemao_admin_article_records(source: str, site: Site) -> list[Record]:
    return parse_fixed_named_material_records(source, site, "😜", "绝杀②肖")


def parse_yeyeshengcai_admin_article_records(source: str, site: Site) -> list[Record]:
    return parse_fixed_named_material_records(source, site, "📸", "绝杀二肖")


def parse_yuebaifengqing_admin_article_records(source: str, site: Site) -> list[Record]:
    return parse_fixed_named_material_records(source, site, "💎", "绝杀二肖")


def parse_yangchun_caihong_read_page_records(source: str, site: Site) -> list[Record]:
    return parse_fixed_named_material_records(source, site, "🎆", "绝杀②肖")


def parse_wusuoweiju_read_page_records(source: str, site: Site) -> list[Record]:
    return parse_fixed_named_material_records(source, site, "🍏", "绝杀二肖")


def parse_zhenlong_huoxian_admin_article_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*🥰\s*"
        rf"[『〖【]\s*真龙活现\s*[』〗】]\s*🥰\s*绝杀二肖\s*🥰\s*"
        rf"[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]]\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
    )
    return records_from_pattern(text, pattern)


def parse_manager_article_two_zodiac_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    period_gap = r"(?:(?!\d{3}\s*期).)"
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]{period_gap}{{0,45}}?[《『【〖]\s*{re.escape(site.name)}\s*[》』】〗]"
        rf"{period_gap}{{0,70}}?绝\s*杀\s*(?:二|两|2|２|②)\s*肖"
        rf"{period_gap}{{0,70}}?[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]]\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
    )
    return records_from_pattern(text, pattern)


def parse_meifeise_wu_manager_article_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    period_gap = r"(?:(?!\d{3}\s*期).)"
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]{period_gap}{{0,45}}?眉飞色舞"
        rf"{period_gap}{{0,70}}?绝\s*杀\s*(?:二|两|2|２|②)\s*肖"
        rf"{period_gap}{{0,70}}?[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]]\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
    )
    return records_from_pattern(text, pattern)


def parse_qinneng_buzhuo_read_page_records(source: str, site: Site) -> list[Record]:
    text = html_to_text(source)
    pattern = re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*《\s*勤能补拙\s*》\s*"
        rf"👘\s*绝\s*杀\s*(?:二|两|2|２|②)\s*肖\s*👘\s*"
        rf"[【〖\[]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]]\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错赢对↑√]+)"
    )
    return records_from_pattern(text, pattern)

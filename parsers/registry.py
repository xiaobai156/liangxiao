from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from types import MappingProxyType

from domain.errors import ConfigurationError, ErrorCategory, ScrapeFailure
from domain.models import POSITION_KIND_VISIBLE_TEXT, PayloadDocument, Record, Site
from parsers.chart import (
    parse_laodazhu_ten_zodiac_complement_records,
    parse_shuqhbq_macau_gallery_forbidden_records,
    parse_shuqhbq_macau_kill_chart_records,
    parse_shuqhbq_xuanji_forbidden_records,
    parse_suiyin_jiliang_ten_zodiac_complement_records,
    parse_tuku_user_forums_precise_two_zodiac_records,
    parse_tuku_user_forums_shizhuang_cut_records,
    parse_tuku_user_forums_short_kill_records,
    parse_tuku_user_forums_two_zodiac_records,
    parse_wangzhejiudian_forbidden_chart_records,
    parse_wangzhejiudian_kill_chart_records,
    parse_wangzhejiudian_records,
    parse_zhuque_forbidden_material_records,
)
from parsers.dynamic import (
    parse_fugui_kede_admin_article_records,
    parse_guanwang_touma_manager_article_records,
    parse_huayan_yuemao_admin_article_records,
    parse_jinbao_mawang_manager_article_records,
    parse_junlin_tianxia_manager_article_records,
    parse_liuhe_daoren_admin_article_records,
    parse_liuhe_zhongxin_manager_article_records,
    parse_manager_article_two_zodiac_records,
    parse_meifeise_wu_manager_article_records,
    parse_mengxiang_rensheng_manager_article_records,
    parse_qinneng_buzhuo_read_page_records,
    parse_rushen_tantao_read_page_records,
    parse_songjiu_yingxin_admin_article_records,
    parse_wusuoweiju_read_page_records,
    parse_xuanji_tianshu_admin_article_records,
    parse_yangchun_caihong_read_page_records,
    parse_yeyeshengcai_admin_article_records,
    parse_yuebaifengqing_admin_article_records,
    parse_zhenlong_huoxian_admin_article_records,
)
from parsers.forum import (
    parse_baijie_shujinguang_top_records,
    parse_baishou_qijia_tail_records,
    parse_bufeng_zhuoying_topic_records,
    parse_dengtang_rushi_bottom_records,
    parse_gaohuo_zhifei_topic_records,
    parse_guangdong_baerzhan_top_records,
    parse_guangdong_linked_top_records,
    parse_guangxizai_linked_top_records,
    parse_guangxizai_top_records,
    parse_jinduobao_second_tail_records,
    parse_jingzhongbaoguo_tail_records,
    parse_jiulong_forum_top_records,
    parse_liuhe_tail_records,
    parse_liuhe_toutiao_linked_top_records,
    parse_liuhe_toutiao_top_records,
    parse_liuhebadian_tail_records,
    parse_nuwabutiantail_records,
    parse_nuyanmeigu_tail_records,
    parse_shouqi_daoluo_top_records,
    parse_taxue_wuhen_bottom_records,
    parse_taxue_wuhen_top_records,
    parse_wenru_taishan_top_records,
    parse_wulin_gaoshou_linked_top_records,
    parse_wuyou_wulv_topic_records,
    parse_xiangfu_ercheng_tail_records,
    parse_yiben_wanli_sisha_bottom_records,
    parse_yichou_mozhan_top_records,
    parse_zhenlong_fankui_topic_records,
    parse_zhongduo_feiyi_top_records,
    parse_zhuangyuan_red_top_records,
    parse_ziranziran_top_records,
)
from parsers.helpers import (
    html_to_text,
    parse_generic_two_zodiac_records,
    parse_named_block_records,
    parse_site_scoped_two_zodiac_records,
)
from parsers.manager_234 import (
    parse_jinma_dushen_manager_article_records,
    parse_yanji_dushan_manager_article_records,
    parse_zhuangba_disheng_manager_article_records,
)
from parsers.new_sites_235 import (
    parse_feilong_qishi_records,
    parse_new_topic_235_records,
    parse_tuoni_kulmonika_records,
    parse_xiaosuan_bottom_records,
    parse_zhougong_shensuan_records,
)
from parsers.new_sites_241 import (
    parse_bubu_gaosheng_top_records,
    parse_jiujie_liangfeng_top_records,
    parse_lujiu_home_top_records,
    parse_lujiu_wensha_bottom_records,
    parse_tianlang_shaxing_bottom_records,
    parse_yueku_zhixiao_top_records,
)
from parsers.special import parse_yanyu_fusu_list_detail_records

ParserFunction = Callable[[str, Site], list[Record]]


ENGINE_REGISTRY: Mapping[str, ParserFunction] = MappingProxyType(
    {
        "named_block": parse_named_block_records,
        "generic_two_zodiac": parse_generic_two_zodiac_records,
        "site_scoped_two_zodiac": parse_site_scoped_two_zodiac_records,
        "feilong_qishi_bottom": parse_feilong_qishi_records,
        "ziranziran_top": parse_ziranziran_top_records,
        "jingzhongbaoguo_tail": parse_jingzhongbaoguo_tail_records,
        "nuwabutiantail": parse_nuwabutiantail_records,
        "jinduobao_second_tail": parse_jinduobao_second_tail_records,
        "liuhe_tail": parse_liuhe_tail_records,
        "nuyanmeigu_tail": parse_nuyanmeigu_tail_records,
        "liuhebadian_tail": parse_liuhebadian_tail_records,
        "wangzhejiudian_two_zodiac": parse_wangzhejiudian_records,
        "wangzhejiudian_forbidden_chart": parse_wangzhejiudian_forbidden_chart_records,
        "wangzhejiudian_kill_chart": parse_wangzhejiudian_kill_chart_records,
        "shuqhbq_xuanji_forbidden": parse_shuqhbq_xuanji_forbidden_records,
        "shuqhbq_macau_kill_chart": parse_shuqhbq_macau_kill_chart_records,
        "shuqhbq_macau_gallery_forbidden": parse_shuqhbq_macau_gallery_forbidden_records,
        "zhuque_forbidden_material": parse_zhuque_forbidden_material_records,
        "tuku_user_forums_two_zodiac": parse_tuku_user_forums_two_zodiac_records,
        "tuku_user_forums_short_kill": parse_tuku_user_forums_short_kill_records,
        "tuku_user_forums_precise_two_zodiac": parse_tuku_user_forums_precise_two_zodiac_records,
        "tuku_user_forums_shizhuang_cut": parse_tuku_user_forums_shizhuang_cut_records,
        "wuyou_wulv_topic": parse_wuyou_wulv_topic_records,
        "fugui_kede_admin_article": parse_fugui_kede_admin_article_records,
        "guanwang_touma_manager_article": parse_guanwang_touma_manager_article_records,
        "songjiu_yingxin_admin_article": parse_songjiu_yingxin_admin_article_records,
        "liuhe_daoren_admin_article": parse_liuhe_daoren_admin_article_records,
        "huayan_yuemao_admin_article": parse_huayan_yuemao_admin_article_records,
        "jinbao_mawang_manager_article": parse_jinbao_mawang_manager_article_records,
        "xuanji_tianshu_admin_article": parse_xuanji_tianshu_admin_article_records,
        "yeyeshengcai_admin_article": parse_yeyeshengcai_admin_article_records,
        "yuebaifengqing_admin_article": parse_yuebaifengqing_admin_article_records,
        "yangchun_caihong_read_page": parse_yangchun_caihong_read_page_records,
        "wusuoweiju_read_page": parse_wusuoweiju_read_page_records,
        "zhenlong_huoxian_admin_article": parse_zhenlong_huoxian_admin_article_records,
        "junlin_tianxia_manager_article": parse_junlin_tianxia_manager_article_records,
        "liuhe_zhongxin_manager_article": parse_liuhe_zhongxin_manager_article_records,
        "manager_article_two_zodiac": parse_manager_article_two_zodiac_records,
        "mengxiang_rensheng_manager_article": parse_mengxiang_rensheng_manager_article_records,
        "zhuangba_disheng_manager_article": parse_zhuangba_disheng_manager_article_records,
        "yanji_dushan_manager_article": parse_yanji_dushan_manager_article_records,
        "jinma_dushen_manager_article": parse_jinma_dushen_manager_article_records,
        "meifeise_wu_manager_article": parse_meifeise_wu_manager_article_records,
        "yanyu_fusu_list_detail": parse_yanyu_fusu_list_detail_records,
        "qinneng_buzhuo_read_page": parse_qinneng_buzhuo_read_page_records,
        "rushen_tantao_read_page": parse_rushen_tantao_read_page_records,
        "laodazhu_ten_zodiac_complement": parse_laodazhu_ten_zodiac_complement_records,
        "suiyin_jiliang_ten_zodiac_complement": parse_suiyin_jiliang_ten_zodiac_complement_records,
        "bufeng_zhuoying_topic": parse_bufeng_zhuoying_topic_records,
        "zhenlong_fankui_topic": parse_zhenlong_fankui_topic_records,
        "gaohuo_zhifei_topic": parse_gaohuo_zhifei_topic_records,
        "xiangfu_ercheng_tail": parse_xiangfu_ercheng_tail_records,
        "baishou_qijia_tail": parse_baishou_qijia_tail_records,
        "taxue_wuhen_bottom": parse_taxue_wuhen_bottom_records,
        "taxue_wuhen_top": parse_taxue_wuhen_top_records,
        "zhuangyuan_red_top": parse_zhuangyuan_red_top_records,
        "yiben_wanli_sisha_bottom": parse_yiben_wanli_sisha_bottom_records,
        "baijie_shujinguang_top": parse_baijie_shujinguang_top_records,
        "liuhe_toutiao_top": parse_liuhe_toutiao_top_records,
        "liuhe_toutiao_linked_top": parse_liuhe_toutiao_linked_top_records,
        "jiulong_forum_top": parse_jiulong_forum_top_records,
        "shouqi_daoluo_top": parse_shouqi_daoluo_top_records,
        "wulin_gaoshou_linked_top": parse_wulin_gaoshou_linked_top_records,
        "guangxizai_linked_top": parse_guangxizai_linked_top_records,
        "guangdong_linked_top": parse_guangdong_linked_top_records,
        "guangxizai_top": parse_guangxizai_top_records,
        "guangdong_baerzhan_top": parse_guangdong_baerzhan_top_records,
        "dengtang_rushi_bottom": parse_dengtang_rushi_bottom_records,
        "yichou_mozhan_top": parse_yichou_mozhan_top_records,
        "zhongduo_feiyi_top": parse_zhongduo_feiyi_top_records,
        "wenru_taishan_top": parse_wenru_taishan_top_records,
        "new_topic_235_exact": parse_new_topic_235_records,
        "zhougong_shensuan_two_zodiac": parse_zhougong_shensuan_records,
        "tuoni_kulmonika_snapshots": parse_tuoni_kulmonika_records,
        "xiaosuan_bottom_two_zodiac": parse_xiaosuan_bottom_records,
        "lujiu_wensha_bottom": parse_lujiu_wensha_bottom_records,
        "lujiu_home_top": parse_lujiu_home_top_records,
        "yueku_zhixiao_top": parse_yueku_zhixiao_top_records,
        "jiujie_liangfeng_top": parse_jiujie_liangfeng_top_records,
        "bubu_gaosheng_top": parse_bubu_gaosheng_top_records,
        "tianlang_shaxing_bottom": parse_tianlang_shaxing_bottom_records,
    }
)


def _mask_invalid_periods(source: str) -> str:
    def replace_invalid(match: re.Match[str]) -> str:
        token = match.group(0)
        return token if len(token) == 3 and 1 <= int(token) <= 365 else " " * len(token)

    return re.sub(r"(?<!\d)\d{3,}(?=\s*期)", replace_invalid, source)


def _flexible_raw_pattern(raw: str) -> re.Pattern[str] | None:
    tokens = [token for token in re.split(r"\s+", raw.strip()) if token]
    if not tokens:
        return None
    return re.compile(r"\s+".join(re.escape(token) for token in tokens))


def _record_offsets(text: str, record: Record) -> list[int]:
    pattern = _flexible_raw_pattern(record.raw)
    offsets = [match.start() for match in pattern.finditer(text)] if pattern is not None else []
    if offsets:
        return offsets
    zodiac = re.sub(r"[\s\-－.。·、,，]+", "", record.zodiac)
    if len(zodiac) != 2:
        return []
    fallback = re.compile(
        rf"(?<!\d){record.period}\s*期[\s\S]{{0,220}}?{re.escape(zodiac[0])}"
        rf"[\s\-－.。·、,，]{{0,12}}{re.escape(zodiac[1])}"
    )
    return [match.start() for match in fallback.finditer(text)]


def _hydrate_records(
    records: list[Record],
    source: str,
    site: Site,
    document: PayloadDocument | None,
) -> list[Record]:
    visible = html_to_text(source)
    used: set[int] = set()
    hydrated: list[Record] = []
    body_start = 0
    if document is not None and document.body_source_start > 0:
        body_text = html_to_text(source[document.body_source_start :])
        located = visible.rfind(body_text) if body_text else -1
        body_start = located if located >= 0 else 0
    for record in records:
        offsets = _record_offsets(visible, record)
        if not offsets:
            raise ScrapeFailure(
                ErrorCategory.FIELD_VALIDATION,
                f"{site.name}候选无法定位真实可见正文位置：{record.period}期 {record.zodiac}",
            )
        preferred = record.position if record.position in offsets and record.position not in used else None
        position = preferred if preferred is not None else next((value for value in offsets if value not in used), offsets[0])
        used.add(position)
        adjusted_position = position - body_start if position >= body_start else position
        source_positions = tuple(
            sorted(
                {
                    value - body_start if value >= body_start else value
                    for value in (record.source_positions or (position,))
                    if value >= 0
                }
            )
        )
        anchor_match = re.search(site.title, visible, flags=re.I) if site.title else None
        anchor_text = record.anchor_text or (anchor_match.group(0) if anchor_match else site.name)
        anchor_offset = record.anchor_offset if record.anchor_offset >= 0 else (anchor_match.start() if anchor_match else -1)
        block_start = record.block_start if record.block_start >= 0 else 0
        block_end = record.block_end if record.block_end >= 0 else len(visible)
        if body_start:
            block_start = max(0, block_start - body_start)
            block_end = max(block_start, block_end - body_start)
        block_id = record.block_id or hashlib.sha256(
            f"{site.name}|{block_start}|{block_end}|{anchor_text}".encode("utf-8")
        ).hexdigest()[:16]
        hydrated.append(
            replace(
                record,
                position=adjusted_position,
                record_id=record.record_id or (document.record_id if document is not None else ""),
                document_url=record.document_url or (document.url if document is not None else site.url),
                position_kind=POSITION_KIND_VISIBLE_TEXT,
                anchor_text=anchor_text,
                anchor_offset=anchor_offset,
                block_id=block_id,
                block_start=block_start,
                block_end=block_end,
                anchor_document_url=record.anchor_document_url
                or (document.parent_url if document is not None else ""),
                link_reference=record.link_reference
                or (document.link_reference if document is not None else ""),
                source_positions=source_positions or (adjusted_position,),
                record_path=record.record_path or (document.record_path if document is not None else ""),
                record_count=record.record_count or (document.record_count if document is not None else 0),
                anchor_record_id=record.anchor_record_id
                or (document.anchor_record_id if document is not None else ""),
                anchor_record_path=record.anchor_record_path
                or (document.anchor_record_path if document is not None else ""),
                anchor_record_count=record.anchor_record_count
                or (document.anchor_record_count if document is not None else 0),
                body_record_id=record.body_record_id
                or (document.body_record_id if document is not None else ""),
                body_record_path=record.body_record_path
                or (document.body_record_path if document is not None else ""),
                body_record_count=record.body_record_count
                or (document.body_record_count if document is not None else 0),
            )
        )
    hydrated.sort(key=lambda record: record.position)
    return hydrated


class ParserRegistry:
    def __init__(self, bindings: Mapping[tuple[str, str, str], str]) -> None:
        self.bindings = MappingProxyType(dict(bindings))

    @classmethod
    def bind_sites(cls, sites: Iterable[Site]) -> ParserRegistry:
        bindings: dict[tuple[str, str, str], str] = {}
        for site in sites:
            if site.parser not in ENGINE_REGISTRY:
                raise ConfigurationError(f"{site.name} 使用未知 parser：{site.parser}")
            if site.identity in bindings:
                raise ConfigurationError(f"站点身份重复：{site.identity}")
            bindings[site.identity] = site.parser
        return cls(bindings)

    def parse(self, source: str | PayloadDocument, site: Site) -> list[Record]:
        bound_parser = self.bindings.get(site.identity)
        if bound_parser is None:
            raise ConfigurationError(f"站点身份未绑定专属 parser：{site.identity}")
        if bound_parser != site.parser:
            raise ConfigurationError(
                f"站点 parser 与启动绑定不一致：{site.name} 绑定={bound_parser} 当前={site.parser}"
            )
        parser = ENGINE_REGISTRY[bound_parser]
        if isinstance(source, PayloadDocument):
            text = source.source
            record_id = source.record_id
            document_url = source.url
        else:
            text = source
            record_id = ""
            document_url = site.url
        masked = _mask_invalid_periods(text)
        records = parser(masked, site)
        document = source if isinstance(source, PayloadDocument) else None
        hydrated = _hydrate_records(records, masked, site, document)
        return [
            replace(
                record,
                record_id=record.record_id or record_id,
                document_url=record.document_url or document_url,
            )
            for record in hydrated
        ]

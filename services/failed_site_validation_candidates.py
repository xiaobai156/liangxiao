from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
import re

from domain.models import DOCUMENT_BOUNDARY, DocumentBundle, PayloadDocument, Record, Site
from parsers.helpers import ZODIACS, html_to_text, records_from_pattern
from validation.boundaries import document_is_linked
from validation.direction import select_record
from validation.records import record_value_signature, validate_selected_record


CandidateParser = Callable[[str, Site], list[Record]]

_PAIR = rf"[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}]"


def _section_records(
    source: str,
    heading: str,
    record: re.Pattern[str],
    *,
    stop: str = "",
) -> list[Record]:
    text = html_to_text(source)
    match = re.search(heading, text, flags=re.I)
    if not match:
        return []
    tail = text[match.end() :]
    if stop:
        boundary = re.search(stop, tail, flags=re.I)
        if boundary:
            tail = tail[: boundary.start()]
    return records_from_pattern(tail, record, base_offset=match.end())


def parse_zhuangyuan_red_candidate(source: str, site: Site) -> list[Record]:
    if site.name != "狀元紅" or site.pick != "top":
        return []
    return _section_records(
        source,
        r"澳门\s*状元红\s+狀元紅\s*⊙\s*[『〖【]\s*绝杀②肖\s*[』〗】]",
        re.compile(
            rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*绝杀②肖\s*❇\s*"
            rf"[〖【]\s*(?P<zodiac>{_PAIR})\s*[〗】]\s*"
            rf"开\s*[:：]?\s*(?P<open>[^\s准中错赢对]+)"
        ),
    )


def parse_liuhe_toutiao_candidate(source: str, site: Site) -> list[Record]:
    if site.name != "六合头条" or site.pick != "top":
        return []
    return _section_records(
        source,
        r"精品帖\s*\d{3}\s*期\s*[【〖]\s*绝杀两肖\s*[】〗]\s*绝对好料",
        re.compile(
            rf"(?P<period>\d{{3}})\s*期\s*绝杀两肖\s*[【〖]\s*"
            rf"(?P<zodiac>{_PAIR})\s*[】〗]\s*开\s*[:：]?\s*"
            rf"(?P<open>[^\s准中错赢对]+)"
        ),
    )


def parse_jiulong_forum_candidate(source: str, site: Site) -> list[Record]:
    if site.name != "九龙论坛" or site.pick != "top":
        return []
    return _section_records(
        source,
        r"九龙论坛\s*『\s*绝杀二肖\s*』",
        re.compile(
            rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*[〖【]\s*杀(?:二|两|2|２|②)\s*肖\s*[→>]\s*"
            rf"(?P<zodiac>{_PAIR})\s*[〗】]\s*开\s*[:：]?\s*"
            rf"(?P<open>[^\s准中错赢对]+)"
        ),
        stop=r"九龙论坛\s*『",
    )


def parse_shouqi_daoluo_candidate(source: str, site: Site) -> list[Record]:
    if site.name != "手起刀落" or site.pick != "top":
        return []
    return _section_records(
        source,
        r"手起刀落\s*[【〖『]\s*绝杀二肖\s*[】〗』]",
        re.compile(
            rf"(?P<period>\d{{3}})\s*期\s*绝杀两肖\s*[【〖]\s*"
            rf"(?P<zodiac>{_PAIR})\s*[】〗]\s*开\s*[:：]?\s*"
            rf"(?P<open>[^\s准中错赢对]+)"
        ),
    )


def parse_guangxizai_candidate(source: str, site: Site) -> list[Record]:
    if site.name != "广西仔" or site.pick != "top":
        return []
    return _section_records(
        source,
        r"\d{3}\s*期\s*[:：]\s*本站推荐\s*[【〖]\s*绝杀二肖\s*[】〗]",
        re.compile(
            rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*绝杀二肖\s*[-－]\s*[（(]\s*"
            rf"(?P<zodiac>{_PAIR})\s*[）)]\s*[-－]\s*开\s*[:：]?\s*"
            rf"(?P<open>[^\s准中错赢对]+)"
        ),
        stop=r"(?=\d{3}\s*期\s*[:：]\s*(?:本站推荐|精品推荐))",
    )


def parse_guangdong_candidate(source: str, site: Site) -> list[Record]:
    if site.name != "广东" or site.pick != "top":
        return []
    return _section_records(
        source,
        r"\d{3}\s*期\s*[:：]?\s*[【〖]\s*绝杀二肖\s*[】〗]",
        re.compile(
            rf"(?P<period>\d{{3}})\s*期\s*[（(]\s*必杀二肖\s*[）)]\s*"
            rf"[【〖]\s*(?P<zodiac>{_PAIR})\s*[】〗]\s*开\s*[:：]?\s*"
            rf"(?P<open>[^\s准中错赢对]+)"
        ),
    )


def parse_meirenzhu_a_candidate(source: str, site: Site) -> list[Record]:
    if site.name != "没忍住啊" or site.pick != "top":
        return []
    return _section_records(
        source,
        r"高手贴\s*\d{3}\s*期\s*[:：]?\s*[【\[]\s*绝杀二肖\s*[】\]]",
        re.compile(
            rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*绝杀二肖\s*"
            rf"[【〖]\s*(?P<zodiac>{_PAIR})\s*[】〗]\s*开\s*[:：]?\s*"
            rf"(?P<open>[^\s准中错赢对]+)"
        ),
    )


def parse_wulin_gaoshou_candidate(source: str, site: Site) -> list[Record]:
    if site.name != "武林高手" or site.pick != "top":
        return []
    parts = source.split(DOCUMENT_BOUNDARY)
    if len(parts) < 2:
        return []
    anchor = html_to_text(DOCUMENT_BOUNDARY.join(parts[:-1]))
    body = html_to_text(parts[-1])
    if not re.search(r"武林高手\s*\d{3}\s*期\s*《\s*绝杀两肖\s*》", anchor, flags=re.I):
        return []
    return records_from_pattern(
        body,
        re.compile(
            rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*绝杀两肖\s*[【〖]\s*"
            rf"(?P<zodiac>{_PAIR})\s*[】〗]\s*开\s*[:：]?\s*"
            rf"(?P<open>[^\s准中错赢对]+)"
        ),
    )


CANDIDATE_PARSERS: dict[str, CandidateParser] = {
    "狀元紅": parse_zhuangyuan_red_candidate,
    "六合头条": parse_liuhe_toutiao_candidate,
    "九龙论坛": parse_jiulong_forum_candidate,
    "手起刀落": parse_shouqi_daoluo_candidate,
    "武林高手": parse_wulin_gaoshou_candidate,
    "广西仔": parse_guangxizai_candidate,
    "广东": parse_guangdong_candidate,
    "没忍住啊": parse_meirenzhu_a_candidate,
}


@dataclass(frozen=True, slots=True)
class CandidateObservation:
    document: PayloadDocument
    records: tuple[Record, ...]
    selected: Record | None
    error: str = ""


def authorized_parent_pairs(bundle: DocumentBundle) -> Iterator[PayloadDocument]:
    """Yield only direct page/script pairs explicitly linked by the source page."""
    by_url: dict[str, list[PayloadDocument]] = {}
    for document in bundle.documents:
        if document.url:
            by_url.setdefault(document.url, []).append(document)
    for body in bundle.documents:
        if not body.parent_url:
            continue
        parents = by_url.get(body.parent_url, [])
        if not any(document_is_linked(parent, body) for parent in parents):
            continue
        anchor_source = DOCUMENT_BOUNDARY.join(parent.source for parent in parents)
        record_ids = {parent.record_id for parent in parents if parent.record_id}
        if body.record_id:
            record_ids.add(body.record_id)
        if len(record_ids) > 1:
            continue
        yield PayloadDocument(
            f"授权父文档+{body.label}",
            body.url,
            anchor_source + DOCUMENT_BOUNDARY + body.source,
            record_id=next(iter(record_ids), ""),
            parent_url=body.parent_url,
            link_reference=body.link_reference,
            body_source_start=len(anchor_source) + len(DOCUMENT_BOUNDARY),
        )


def validate_candidate_bundle(
    bundle: DocumentBundle,
    site: Site,
    period: int,
) -> tuple[tuple[CandidateObservation, ...], Record | None, str]:
    parser = CANDIDATE_PARSERS.get(site.name)
    if parser is None:
        return (), None, "没有该站点的隔离候选策略"

    documents: list[PayloadDocument] = []
    if site.name == "狀元紅":
        documents.extend(
            document
            for document in bundle.documents
            if document.label == "原始页面" and document.url == site.url
        )
    else:
        documents.extend(authorized_parent_pairs(bundle))

    observations: list[CandidateObservation] = []
    for document in documents:
        records = tuple(parser(document.source, site))
        if not records:
            continue
        try:
            selected = select_record(list(records), period, site)
        except ValueError as exc:
            observations.append(CandidateObservation(document, records, None, str(exc)))
            continue
        if not validate_selected_record(selected, period):
            observations.append(CandidateObservation(document, records, None, "统一字段校验失败"))
            continue
        observations.append(CandidateObservation(document, records, selected))

    successes = [item for item in observations if item.selected is not None]
    if not successes:
        details = "；".join(
            f"{item.document.url}：{item.error}"
            for item in observations
            if item.error
        )
        return tuple(observations), None, details or "未找到授权父子文档中的有效候选"
    signatures = {record_value_signature(item.selected) for item in successes if item.selected is not None}
    if len(signatures) > 1:
        details = "、".join(
            f"{item.selected.zodiac}@位置{item.selected.position}"
            for item in successes
            if item.selected is not None
        )
        return tuple(observations), None, f"授权文档同期冲突：{period}期 {details}"
    return tuple(observations), successes[0].selected, ""

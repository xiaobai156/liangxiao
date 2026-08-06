"""隔离的朱雀候选源验证策略。

这个模块只做诊断目录内的候选收集和裁决，不被生产 CLI 或服务层导入。
它把每个页面、脚本或浏览器 DOM 当作独立文档，且只接受明确的朱雀锚点
和“禁用二肖”字段；截图中的生肖值不参与任何逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import re

from domain.models import POSITION_KIND_VISIBLE_TEXT, Record
from parsers.helpers import html_to_text


ANCHOR_RE = re.compile(r"177792a\.com\s*『\s*禁\s*用\s*资\s*料\s*』")
STOP_RE = re.compile(r"(?:禁\s*两\s*肖|短期规律专区|长期规律专区|热门专区|精品专区)")
ZODIACS = "牛马羊鸡狗猪鼠虎兔龙蛇猴"
ZODIAC_SET = set(ZODIACS)
FIELD_RE = re.compile(
    r"(?P<period>\d{3})\s*期\s*[→>＞\-—–✈]*\s*禁\s*用\s*二\s*肖\s*"
    r"[→>＞\-—–✈]*\s*[【〖]\s*(?P<zodiac>[^】〗]{1,32}?)\s*[】〗]\s*"
    r"[开開]\s*(?P<open>[^\s准中错赢对]+)"
)


def normalize_zodiac(value: str) -> str:
    return re.sub(r"[\s\-－.。·、,，]+", "", value)


@dataclass(frozen=True, slots=True)
class SourceInput:
    label: str
    url: str
    source: str
    record_id: str = ""
    parent_url: str = ""
    link_reference: str = ""


@dataclass(frozen=True, slots=True)
class InvalidCandidate:
    period: int
    zodiac: str
    raw: str
    position: int
    reason: str


@dataclass(frozen=True, slots=True)
class DocumentEvidence:
    label: str
    url: str
    source_sha256: str
    source_chars: int
    visible_chars: int
    anchor_count: int
    anchor_text: str
    anchor_offset: int
    block_start: int
    block_end: int
    valid_candidates: tuple[Record, ...]
    invalid_candidates: tuple[InvalidCandidate, ...]
    top3: tuple[Record, ...]
    target_candidates: tuple[Record, ...]
    success: bool
    selected: Record | None
    reason: str
    record_id: str = ""
    parent_url: str = ""
    link_reference: str = ""


@dataclass(frozen=True, slots=True)
class SourceEvaluation:
    success: bool
    selected: Record | None
    documents: tuple[DocumentEvidence, ...]
    reason: str


def _make_record(
    match: re.Match[str],
    *,
    block_start: int,
    block_end: int,
    anchor_text: str,
    anchor_offset: int,
    source: SourceInput,
) -> Record:
    zodiac = normalize_zodiac(match.group("zodiac"))
    position = block_start + match.start()
    return Record(
        period=int(match.group("period")),
        zodiac=zodiac,
        open_result=match.group("open"),
        raw=match.group(0),
        position=position,
        record_id=source.record_id,
        document_url=source.url,
        position_kind=POSITION_KIND_VISIBLE_TEXT,
        anchor_text=anchor_text,
        anchor_offset=anchor_offset,
        block_id=f"visible:{block_start}-{block_end}",
        block_start=block_start,
        block_end=block_end,
        anchor_document_url=source.parent_url,
        link_reference=source.link_reference,
        source_positions=(position,),
    )


def _invalid_reason(period: int, zodiac: str) -> str | None:
    if not 1 <= period <= 365:
        return "期号不在项目合法范围1..365"
    normalized = normalize_zodiac(zodiac)
    if len(normalized) != 2:
        return "生肖数量不是恰好两个"
    if len(set(normalized)) != 2:
        return "重复生肖"
    if any(value not in ZODIAC_SET for value in normalized):
        return "含非法生肖"
    return None


def _dedupe_adjacent_equivalent(records: tuple[Record, ...]) -> tuple[Record, ...]:
    """合并相邻同一期同值展示，同时保留全部原始位置证据。"""
    merged: list[Record] = []
    for record in records:
        if (
            merged
            and merged[-1].period == record.period
            and normalize_zodiac(merged[-1].zodiac) == normalize_zodiac(record.zodiac)
        ):
            previous = merged[-1]
            positions = tuple(
                sorted(
                    set(previous.source_positions or (previous.position,))
                    | set(record.source_positions or (record.position,))
                )
            )
            merged[-1] = replace(previous, source_positions=positions)
        else:
            merged.append(record)
    return tuple(merged)


def _target_block_conflict(records: tuple[Record, ...]) -> str | None:
    by_period: dict[int, list[Record]] = {}
    for record in records:
        by_period.setdefault(record.period, []).append(record)
    for period, matches in by_period.items():
        values = {normalize_zodiac(record.zodiac) for record in matches}
        if len(values) > 1:
            details = "、".join(f"{record.zodiac}@位置{record.position}" for record in matches)
            return f"同文档同期冲突：目标区块内{period}期出现不同合法值：{details}"
    return None


def analyze_document(
    source: str,
    *,
    label: str,
    url: str,
    target_period: int,
    record_id: str = "",
    parent_url: str = "",
    link_reference: str = "",
) -> DocumentEvidence:
    source_input = SourceInput(label, url, source, record_id, parent_url, link_reference)
    visible = html_to_text(source)
    anchors = list(ANCHOR_RE.finditer(visible))
    anchor_text = anchors[0].group(0) if anchors else ""
    anchor_offset = anchors[0].start() if anchors else -1
    block_start = anchor_offset
    block_end = len(visible) if anchor_offset >= 0 else -1
    if anchors:
        tail_start = anchors[0].end()
        stop = STOP_RE.search(visible[tail_start:])
        if stop:
            block_end = tail_start + stop.start()

    valid: list[Record] = []
    invalid: list[InvalidCandidate] = []
    if anchor_offset >= 0 and block_end >= block_start:
        block_text = visible[block_start:block_end]
        for match in FIELD_RE.finditer(block_text):
            period = int(match.group("period"))
            raw_zodiac = match.group("zodiac")
            reason = _invalid_reason(period, raw_zodiac)
            position = block_start + match.start()
            if reason is not None:
                invalid.append(InvalidCandidate(period, raw_zodiac, match.group(0), position, reason))
                continue
            valid.append(
                _make_record(
                    match,
                    block_start=block_start,
                    block_end=block_end,
                    anchor_text=anchor_text,
                    anchor_offset=anchor_offset,
                    source=source_input,
                )
            )

    valid_candidates = tuple(valid)
    logical_candidates = _dedupe_adjacent_equivalent(valid_candidates)
    top3 = logical_candidates[:3]
    target_candidates = tuple(record for record in valid_candidates if record.period == target_period)
    conflict = _target_block_conflict(valid_candidates)
    selected: Record | None = None
    reasons: list[str] = []
    if len(anchors) != 1:
        reasons.append(f"目标锚点不唯一：匹配{len(anchors)}处")
    if not valid_candidates:
        reasons.append("同一文档内无合法禁用二肖候选")
    if conflict:
        reasons.append(conflict)
    matches_in_window = tuple(record for record in top3 if record.period == target_period)
    if not matches_in_window:
        window_text = "、".join(f"{r.period}期{r.zodiac}@位置{r.position}" for r in top3) or "无"
        reasons.append(f"方向窗口内未找到{target_period}期；top3：{window_text}")
    elif len(matches_in_window) != 1:
        reasons.append(f"方向窗口内{target_period}期出现{len(matches_in_window)}个候选")
    else:
        selected = matches_in_window[0]
    if not reasons and selected is not None:
        success = True
        reason = "同一文档、同一目标区块、禁用二肖字段、top3及唯一性均通过"
    else:
        success = False
        reason = "；".join(reasons)
        selected = None
    return DocumentEvidence(
        label=label,
        url=url,
        source_sha256=hashlib.sha256(source.encode("utf-8", errors="replace")).hexdigest(),
        source_chars=len(source),
        visible_chars=len(visible),
        anchor_count=len(anchors),
        anchor_text=anchor_text,
        anchor_offset=anchor_offset,
        block_start=block_start,
        block_end=block_end,
        valid_candidates=valid_candidates,
        invalid_candidates=tuple(invalid),
        top3=top3,
        target_candidates=target_candidates,
        success=success,
        selected=selected,
        reason=reason,
        record_id=record_id,
        parent_url=parent_url,
        link_reference=link_reference,
    )


def evaluate_sources(sources: list[SourceInput] | tuple[SourceInput, ...], *, target_period: int) -> SourceEvaluation:
    documents = tuple(
        analyze_document(
            source.source,
            label=source.label,
            url=source.url,
            target_period=target_period,
            record_id=source.record_id,
            parent_url=source.parent_url,
            link_reference=source.link_reference,
        )
        for source in sources
    )
    if any("同文档同期冲突" in document.reason for document in documents):
        return SourceEvaluation(False, None, documents, "存在同文档同期冲突，安全失败")

    authoritative = tuple(
        document
        for document in documents
        if document.anchor_count or document.valid_candidates or document.invalid_candidates
    )
    failed_authoritative = tuple(document for document in authoritative if not document.success)
    if failed_authoritative:
        details = "；".join(f"{document.label}：{document.reason}" for document in failed_authoritative)
        if any("方向窗口内未找到" in document.reason for document in failed_authoritative):
            return SourceEvaluation(False, None, documents, f"来源状态/方向窗口分歧：{details}")
        return SourceEvaluation(False, None, documents, f"来源独立证据状态不一致：{details}")

    successful = tuple(document for document in documents if document.success and document.selected is not None)
    if not successful:
        return SourceEvaluation(
            False,
            None,
            documents,
            "没有任何单一文档同时证明锚点、目标区块、禁用二肖字段和方向窗口；同一文档证据不足，禁止跨文档借锚点",
        )
    signatures = {document.selected.zodiac for document in successful if document.selected is not None}
    if len(signatures) > 1:
        details = "、".join(f"{document.label}:{document.selected.zodiac}" for document in successful if document.selected)
        return SourceEvaluation(False, None, documents, f"跨文档同期冲突：{target_period}期结果不同：{details}")
    return SourceEvaluation(
        True,
        successful[0].selected,
        documents,
        "所有成功来源的目标期结果一致，且每个来源均独立证明；保留来源证据",
    )

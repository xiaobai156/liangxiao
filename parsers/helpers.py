from __future__ import annotations

import html
from dataclasses import dataclass
import re

from domain.models import POSITION_KIND_VISIBLE_TEXT, Record, Site


ZODIACS = "牛马羊鸡狗猪鼠虎兔龙蛇猴"
ZODIAC_SET = set(ZODIACS)

TWO_ZODIAC_SEMANTIC_RE = re.compile(r"(?:绝\s*杀|必\s*杀|禁\s*杀|死\s*杀|稳\s*杀|温\s*杀|砍\s*杀|杀|禁|不要)")
BLOCK_TAG_RE = re.compile(
    r"</?(?:address|article|aside|blockquote|br|caption|dd|div|dl|dt|fieldset|figcaption|figure|footer|"
    r"form|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)\b[^>]*>",
    flags=re.I,
)
UNPUBLISHED_RE = re.compile(r"(?:尚未发布|暂无(?:资料|数据|内容)?|没有(?:资料|数据|内容)|无(?:资料|数据|内容))")
KNOWN_BOUNDARY_RE = re.compile(
    r"(?:上一篇|下一篇)\s*[：:]|Copyright|免责声明|返回首页|点击投注|六合优秀站点收录",
    flags=re.I,
)


@dataclass(frozen=True, slots=True)
class TargetBlock:
    text: str
    anchor_text: str
    anchor_offset: int
    block_start: int
    block_end: int

    @property
    def block_id(self) -> str:
        return f"visible:{self.block_start}-{self.block_end}"

GENERIC_TWO_ZODIAC_PATTERNS = (
    re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[：:]?\s*☆\s*(?:绝\s*杀|稳\s*杀|温\s*杀|杀)\s*(?:二|②|两|2|２)\s*肖\s*☆\s*"
        rf"\(\s*\(\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*\)\s*\)\s*"
        rf"[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对)）]+)"
    ),
    re.compile(
        rf"(?P<period>\d{{3}})\s*期[^期]{{0,30}}?(?:将\s*杀\s*两\s*肖|死\s*杀\s*二\s*肖)[^期]{{0,20}}?"
        rf"[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对]+)\s*[中准错赢对]?\s*"
        rf"[【〖\[（(《『「]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]）)》』」]"
    ),
    re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[：:]?\s*[➹★☆✺_\-\s]*[【〖\[（(《『「]?\s*"
        rf"(?:绝\s*杀|必\s*杀|禁\s*杀|死\s*杀|稳\s*杀|温\s*杀|杀|砍\s*杀)\s*(?:二|②|两|2|２|\(2\))\s*肖\s*(?:准)?\s*"
        rf"[】〗\]）)》』」]?[➹★☆✺_\-\s]*[【〖\[（(《『「◢〈☆>]+\s*"
        rf"(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*"
        rf"[】〗\]）)》』」◣〉☆<\s]+\s*(?:[➹★☆✺_\-－—|\s]*|\(）)?(?:←)?\s*(?:[开開]|\([开開])\s*[:：]?\s*(?P<open>[^\s准中错赢对)）]+)"
    ),
    re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[：:]?\s*(?:绝\s*杀|必\s*杀|禁\s*杀|死\s*杀|稳\s*杀|温\s*杀|杀|砍\s*杀)\s*"
        rf"\(?\s*(?:二|②|两|2|２)\s*\)?\s*肖\s*[【〖\[（(《『「◢〈☆>]+\s*"
        rf"(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*"
        rf"[】〗\]）)》』」◣〉☆<\s]+\s*[－\-—|]*\s*(?:[开開]|\([开開])\s*[:：]?\s*(?P<open>[^\s准中错赢对)）]+)"
    ),
    re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[：:]?\s*[【〖\[（(《『「]?\s*(?:杀|禁杀|绝杀)\s*(?:二|②|两|2|２)\s*肖\s*[→>]\s*"
        rf"(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[〗】\]\s]*"
        rf"[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对]+)"
    ),
    re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[：:]?\s*【?\s*(?:杀|禁杀|绝杀|不要)\s*(?:二|②|两|2|２)\s*肖(?:码)?\s*】?\s*"
        rf"[【〖\[（(《『「◢〈☆>]+\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*"
        rf"[】〗\]）)》』」◣〉☆<]+[^开開]{{0,30}}[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对]+)"
    ),
    re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[：:]?\s*★\s*绝杀\s*★\s*[【〖\[（(《『「◢〈☆>]+\s*"
        rf"(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]）)》』」◣〉☆<]+\s*"
        rf"[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢对]+)"
    ),
    re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*[➹★☆✺_\-\s]*"
        rf"(?:绝\s*杀|必\s*杀|禁\s*杀|死\s*杀|稳\s*杀|温\s*杀|杀|砍\s*杀)\s*(?:二|②|两|2|２)\s*肖"
        rf"[➹★☆✺_\-\s]*[【〖\[（(《『「]\s*"
        rf"(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]）)》』」]\s*"
        rf"(?:[➹★☆✺_\-\s]*|\(）)?(?:←)?\s*(?:[开開]|\([开開])\s*[:：]?\s*(?P<open>[^\s准中错赢)）]+)"
    ),
    re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*杀\s*[【〖\[（(《『「]\s*"
        rf"(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]）)》』」]\s*肖"
        rf"[^开]{{0,20}}开\s*[:：]?\s*(?P<open>[^\s准中错赢]+)"
    ),
    re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[：:]?\s*\(?\s*"
        rf"(?:绝\s*杀|必\s*杀|禁\s*杀|死\s*杀|稳\s*杀|温\s*杀|杀|砍\s*杀)\s*(?:二|②|两|2|２)\s*肖\s*\)?\s*"
        rf"✺\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*✺\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错赢]+)"
    ),
    re.compile(
        rf"(?P<period>\d{{3}})\s*期[^期]{{0,80}}?(?:绝杀|禁杀|死杀|稳杀|温杀|杀|砍杀)"
        rf"[^期]{{0,20}}?(?:二|②|两|2|２)\s*肖[^【〖\[（(《『「]{{0,20}}[【〖\[（(《『「]\s*"
        rf"(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]）)》』」]\s*"
        rf"开\s*[:：]?\s*(?P<open>[^\s准中错]+)"
    ),
    re.compile(
        rf"(?P<period>\d{{3}})\s*期[^期]{{0,80}}?(?:绝杀|禁杀|死杀|稳杀|温杀|杀|砍杀)\s*"
        rf"[【〖\[（(《『「]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*"
        rf"[】〗\]）)》』」]\s*肖?\s*开\s*[:：]?\s*(?P<open>[^\s准中错]+)"
    ),
    re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*[【〖\[（(《『「]?\s*"
        rf"(?:绝\s*杀|必\s*杀|禁\s*杀|死\s*杀|稳\s*杀|温\s*杀|杀|砍\s*杀)\s*(?:二|②|两|2|２)\s*肖\s*"
        rf"[】〗\]）)》』」]?\s*[【〖\[（(《『「]\s*"
        rf"(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*"
        rf"[】〗\]）)》』」]\s*[－\-—|]*\s*(?:←)?\s*(?:开|\(开)\s*[:：]?\s*(?P<open>[^\s准中错赢)）]+)"
    ),
    re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[:：]?\s*"
        rf"(?:绝\s*杀|必\s*杀|禁\s*杀|死\s*杀|稳\s*杀|温\s*杀|杀|砍\s*杀)\s*(?:二|②|两|2|２)\s*肖\s*"
        rf"[-－—]?\s*[（(]\s*(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[）)]\s*"
        rf"[-－—]?\s*(?:[开開]|\([开開])\s*[:：]?\s*(?P<open>[^\s准中错赢)）]+)"
    ),
    re.compile(
        rf"(?P<period>\d{{3}})\s*期\s*[【〖\[（(《『「]\s*"
        rf"(?P<zodiac>[{ZODIACS}]\s*[-－、,，.。· ]?\s*[{ZODIACS}])\s*[】〗\]）)》』」]\s*"
        rf"(?:←)?\s*[开開]\s*[:：]?\s*(?P<open>[^\s准中错赢✔]+)"
    ),
)


def _expand_written_markup(source: str) -> str:
    text = re.sub(r"document\.writeln?\([\s\S]{0,80}(?:strdecode|atob)\([\s\S]*?\)\s*\);?", " ", source, flags=re.S)
    text = re.sub(r"document\.writeln?\(\s*([\"'`])([\s\S]*?)\1\s*\);?", r"\2\n", text, flags=re.S)
    text = text.replace(r"\'", "'").replace(r"\"", '"')
    return text


def html_to_text(source: str) -> str:
    text = _expand_written_markup(source)
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def visible_source_text(source: str) -> str:
    source = _expand_written_markup(source)
    text = re.sub(r"<script[\s\S]*?</script>", " ", source, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = BLOCK_TAG_RE.sub("\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text.replace(r"\'", "'").replace(r'\"', '"'))
    lines = [re.sub(r"[\t\f\v ]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _line_spans(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    offset = 0
    for line in text.split("\n"):
        spans.append((offset, offset + len(line), line))
        offset += len(line) + 1
    return spans


def _line_index_at(spans: list[tuple[int, int, str]], offset: int) -> int:
    for index, (start, end, _line) in enumerate(spans):
        if start <= offset <= end:
            return index
    return max(0, len(spans) - 1)


def _contains_candidate_semantic(line: str) -> bool:
    return bool(re.search(r"\d{3}\s*期", line) or TWO_ZODIAC_SEMANTIC_RE.search(line))


def _is_short_section_label(line: str) -> bool:
    compact = re.sub(r"\s+", "", line)
    return (
        2 <= len(compact) <= 40
        and not re.search(r"\d{3}\s*期", line)
        and not re.search(r"[牛马羊鸡狗猪鼠虎兔龙蛇猴]{2}", compact)
    )


def select_target_block(source: str, site: Site) -> TargetBlock | None:
    structured_text = visible_source_text(source)
    if not structured_text or not site.title:
        return None
    matches = list(re.finditer(site.title, structured_text, flags=re.I))
    if not matches:
        return None
    if site.record and len(matches) > 1:
        # A named-block title is often repeated verbatim inside each historical
        # record.  Those occurrences are data rows, not independent block
        # anchors.  Prefer title matches outside an actual record span when at
        # least one such anchor exists; genuinely separate headers remain
        # ambiguous and still fail below.
        record_spans = [
            record_match.span()
            for record_match in re.finditer(site.record, structured_text, flags=re.I)
        ]
        outside_records = [
            title_match
            for title_match in matches
            if not any(
                start <= title_match.start() and title_match.end() <= end
                for start, end in record_spans
            )
        ]
        if outside_records:
            matches = outside_records
    spans = _line_spans(structured_text)
    structural_matches: list[re.Match[str]] = []
    for match in matches:
        line = spans[_line_index_at(spans, match.start())][2]
        if _is_short_section_label(line):
            structural_matches.append(match)
    structural_lines = {_line_index_at(spans, match.start()) for match in structural_matches}
    if len(structural_matches) > 1 or len(structural_lines) > 1:
        raise ValueError(f"专属锚点不唯一：{site.name}匹配到{len(structural_lines)}个目标块")

    if not structural_matches and len(matches) != 1:
        raise ValueError(f"专属锚点不唯一：{site.name}匹配到{len(matches)}个非结构标题")
    match = structural_matches[0] if structural_matches else matches[0]
    anchor_line = _line_index_at(spans, match.start())
    block_start = match.start()
    block_end = len(structured_text)
    tail_start = match.end()
    if site.stop:
        stop = re.search(site.stop, structured_text[tail_start:], flags=re.I)
        if not stop:
            raise ValueError(f"目标区块停止边界缺失：{site.name}")
        block_end = tail_start + stop.start()
    else:
        data_seen = False
        unpublished_seen = False
        for line_start, _line_end, line in spans[anchor_line + 1 :]:
            if re.fullmatch(r"#{64,}", line) and not site.linked_document_pattern:
                block_end = line_start
                break
            if KNOWN_BOUNDARY_RE.search(line):
                block_end = line_start
                break
            if UNPUBLISHED_RE.search(line):
                unpublished_seen = True
                continue
            if _is_short_section_label(line) and (data_seen or unpublished_seen):
                block_end = line_start
                break
            if _contains_candidate_semantic(line):
                data_seen = data_seen or bool(re.search(r"\d{3}\s*期", line))
                continue
    full_text = html_to_text(source)
    flat_anchor = html_to_text(match.group(0))
    if not flat_anchor:
        raise ValueError(f"目标区块无法唯一映射：{site.name}")

    # `visible_source_text()` deliberately preserves structural line breaks,
    # while the position contract is based on `html_to_text()`.  On real
    # article pages those two flattened representations can differ by hidden
    # markup before the target block, so an exact whole-block substring lookup
    # is too brittle.  The block anchor has already been proven unique above;
    # map that anchor into the canonical flat text and then derive the block
    # boundary from the configured stop (or the structured boundary delta).
    anchor_offsets = [
        anchor_match.start()
        for anchor_match in re.finditer(re.escape(flat_anchor), full_text)
    ]
    if not anchor_offsets:
        raise ValueError(f"目标区块无法唯一映射：{site.name}")
    expected_anchor = len(html_to_text(structured_text[: match.start()]))
    distances = [abs(offset - expected_anchor) for offset in anchor_offsets]
    best_distance = min(distances)
    best_offsets = [
        offset for offset, distance in zip(anchor_offsets, distances) if distance == best_distance
    ]
    if len(best_offsets) != 1 or best_distance > 128:
        raise ValueError(f"目标区块无法唯一映射：{site.name}")
    flat_anchor_offset = best_offsets[0]
    flat_start = flat_anchor_offset

    if site.stop:
        flat_tail_start = flat_anchor_offset + len(flat_anchor)
        flat_stop = re.search(site.stop, full_text[flat_tail_start:], flags=re.I)
        if not flat_stop:
            raise ValueError(f"目标区块停止边界缺失：{site.name}")
        flat_end = flat_tail_start + flat_stop.start()
    else:
        expected_end = len(html_to_text(structured_text[:block_end]))
        delta = flat_anchor_offset - expected_anchor
        flat_end = max(flat_start, min(len(full_text), expected_end + delta))

    block_text = full_text[flat_start:flat_end].strip()
    if not block_text:
        raise ValueError(f"目标区块无法唯一映射：{site.name}")
    # Stripping can move the start only by whitespace.  Preserve the canonical
    # visible-text offset by locating the stripped text at the mapped boundary.
    stripped_offset = full_text.find(block_text, flat_start, flat_end + 1)
    if stripped_offset < 0:
        raise ValueError(f"目标区块无法唯一映射：{site.name}")
    flat_start = stripped_offset
    flat_anchor_offset = full_text.find(flat_anchor, flat_start, flat_start + len(block_text))
    if flat_anchor_offset < 0:
        raise ValueError(f"目标区块无法唯一映射：{site.name}")
    return TargetBlock(
        block_text,
        flat_anchor,
        flat_anchor_offset,
        flat_start,
        flat_start + len(block_text),
    )


def target_block(source: str, site: Site) -> str:
    block = select_target_block(source, site)
    return block.text if block is not None else ""


def clean_zodiac(value: str) -> str:
    zodiac = re.sub(r"[\s\-－.。·、,，]+", "", value)
    if len(zodiac) == 2 and zodiac[0] == zodiac[1]:
        return ""
    return zodiac


def ten_unique_zodiacs(value: str) -> list[str]:
    items = [zodiac for zodiac in value if zodiac in ZODIAC_SET]
    if len(items) != 10 or len(set(items)) != 10:
        return []
    return items


def has_two_zodiac_semantic(raw: str) -> bool:
    return bool(TWO_ZODIAC_SEMANTIC_RE.search(raw))


def site_scoped_region(text: str, site: Site) -> tuple[str, int]:
    aliases = [site.name]
    alias_map = {
        "白蛇": ["白蛇传"],
        "蓝月亮": ["澳门蓝月亮", "藍月亮"],
        "码票": ["澳门马票"],
        "澳門男人味": ["澳门男人味"],
    }
    aliases.extend(alias_map.get(site.name, []))
    starts: list[int] = []
    target_pattern = re.compile(r"(?:绝\s*杀|必\s*杀|禁\s*杀|死\s*杀|稳\s*杀|温\s*杀|杀|砍\s*杀)\s*(?:二|②|两|2|２)\s*肖")
    for match in target_pattern.finditer(text):
        starts.append(max(0, match.start() - 500))
    for alias in aliases:
        for match in re.finditer(re.escape(alias), text):
            starts.append(match.start())
    if not starts:
        return text, 0

    best = text
    best_start = 0
    best_count = -1
    for region_start in dict.fromkeys(starts):
        chunk = text[region_start : region_start + 12000]
        record_count = sum(
            1 for pattern in GENERIC_TWO_ZODIAC_PATTERNS for _ in pattern.finditer(chunk)
        )
        count = record_count * 100 + len(re.findall(r"\d{3}\s*期", chunk))
        count += 20 if re.search(r"\d{3}\s*期[^期]{0,80}" + target_pattern.pattern, chunk) else 0
        count += 10 if any(alias in chunk[:1200] for alias in aliases) else 0
        count += 1000 if (
            record_count
            and any(alias in chunk[:300] for alias in aliases)
            and target_pattern.search(chunk[:1000])
        ) else 0
        if count > best_count:
            best_count = count
            best = chunk
            best_start = region_start
    return best, best_start


def site_scoped_text(text: str, site: Site) -> str:
    return site_scoped_region(text, site)[0]


def records_from_two_zodiac_text(
    text: str,
    *,
    base_offset: int = 0,
    anchor_text: str = "",
    anchor_offset: int = -1,
    block_start: int = -1,
    block_end: int = -1,
    block_id: str = "",
) -> list[Record]:
    matches: list[tuple[int, Record]] = []
    for pattern in GENERIC_TWO_ZODIAC_PATTERNS:
        for match in pattern.finditer(text):
            zodiac = clean_zodiac(match.group("zodiac"))
            if len(zodiac) != 2 or any(item not in ZODIAC_SET for item in zodiac):
                continue
            if "作者" in match.group(0):
                continue
            if not has_two_zodiac_semantic(match.group(0)):
                continue
            period = int(match.group("period"))
            matches.append(
                (
                    match.start(),
                    Record(
                        period,
                        zodiac,
                        match.group("open"),
                        match.group(0),
                        base_offset + match.start(),
                        position_kind=POSITION_KIND_VISIBLE_TEXT,
                        anchor_text=anchor_text,
                        anchor_offset=anchor_offset,
                        block_id=block_id,
                        block_start=block_start,
                        block_end=block_end,
                        source_positions=(base_offset + match.start(),),
                    ),
                )
            )
    unique_records: list[Record] = []
    seen: set[tuple[int, int, str]] = set()
    for match_start, record in sorted(matches, key=lambda item: item[0]):
        signature = (match_start, record.period, record.zodiac)
        if signature in seen:
            continue
        seen.add(signature)
        unique_records.append(record)
    return unique_records


def parse_generic_two_zodiac_records(source: str, site: Site) -> list[Record]:
    text = visible_source_text(source)
    records = records_from_two_zodiac_text(site_scoped_text(text, site))
    semantic_aliases = {
        "将杀两肖": "将杀两肖",
        "死杀二肖": "死杀二肖",
    }
    target_semantic = semantic_aliases.get(re.sub(r"\s+", "", site.name))
    if target_semantic:
        records = [
            record
            for record in records
            if target_semantic in re.sub(r"\s+", "", record.raw)
        ]
    return records


def _site_scoped_fallback_records(source: str, site: Site) -> list[Record]:
    visible = visible_source_text(source)
    scoped, scoped_start = site_scoped_region(visible, site)
    return records_from_two_zodiac_text(
        scoped,
        base_offset=scoped_start,
        anchor_text=site.name,
        anchor_offset=scoped_start,
        block_start=scoped_start,
        block_end=scoped_start + len(scoped),
        block_id=f"site-scoped:{scoped_start}-{scoped_start + len(scoped)}",
    )


def parse_site_scoped_two_zodiac_records(source: str, site: Site) -> list[Record]:
    if not site.title:
        return []
    try:
        selection = select_target_block(source, site)
    except ValueError as exc:
        message = str(exc)
        recoverable = (
            (message.startswith("专属锚点不唯一：") and "非结构标题" in message)
            or message.startswith("目标区块无法唯一映射：")
        )
        if not recoverable:
            raise
        records = _site_scoped_fallback_records(source, site)
        if not records:
            raise exc
        return records
    if selection is None or not selection.text:
        return []
    records = records_from_two_zodiac_text(
        selection.text,
        base_offset=selection.block_start,
        anchor_text=selection.anchor_text,
        anchor_offset=selection.anchor_offset,
        block_start=selection.block_start,
        block_end=selection.block_end,
        block_id=selection.block_id,
    )
    if records:
        return records

    # A short navigation/index label can be the only structural title even
    # when the real data header and historical rows contain the same title.
    # This is specific to site_scoped_two_zodiac; named_block remains strict.
    visible = visible_source_text(source)
    if len(list(re.finditer(site.title, visible, flags=re.I))) <= 1:
        return []
    return _site_scoped_fallback_records(source, site)


def records_from_pattern(text: str, pattern: re.Pattern[str], *, base_offset: int = 0) -> list[Record]:
    records: list[Record] = []
    seen: set[tuple[int, int, str]] = set()
    for match in pattern.finditer(text):
        period = int(match.group("period"))
        zodiac = clean_zodiac(match.group("zodiac"))
        signature = (match.start(), period, zodiac)
        if signature in seen or len(zodiac) != 2 or zodiac[0] == zodiac[1]:
            continue
        seen.add(signature)
        position = base_offset + match.start()
        records.append(
            Record(
                period,
                zodiac,
                match.groupdict().get("open", ""),
                match.group(0),
                position,
                position_kind=POSITION_KIND_VISIBLE_TEXT,
                block_start=base_offset,
                block_end=base_offset + len(text),
                block_id=f"visible:{base_offset}-{base_offset + len(text)}",
                source_positions=(position,),
            )
        )
    return records


def parse_named_block_records(source: str, site: Site) -> list[Record]:
    selection = select_target_block(source, site)
    if selection is None or not selection.text or not site.record:
        return []
    records: list[Record] = []
    seen: set[tuple[int, int, str]] = set()
    for match in re.finditer(site.record, selection.text):
        zodiac = clean_zodiac(match.group("zodiac"))
        if len(zodiac) != 2 or any(item not in ZODIAC_SET for item in zodiac):
            continue
        period = int(match.group("period"))
        key = (match.start(), period, zodiac)
        if key in seen:
            continue
        seen.add(key)
        position = selection.block_start + match.start()
        records.append(
            Record(
                period,
                zodiac,
                match.group("open"),
                match.group(0),
                position,
                position_kind=POSITION_KIND_VISIBLE_TEXT,
                anchor_text=selection.anchor_text,
                anchor_offset=selection.anchor_offset,
                block_id=selection.block_id,
                block_start=selection.block_start,
                block_end=selection.block_end,
                source_positions=(position,),
            )
        )
    return records

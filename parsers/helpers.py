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


def html_to_text(source: str) -> str:
    text = re.sub(r"document\.writeln?\([\s\S]{0,80}(?:strdecode|atob)\([\s\S]*?\)\s*\);?", " ", source, flags=re.S)
    text = re.sub(r"document\.writeln?\(\s*([\"'`])([\s\S]*?)\1\s*\);?", r"\2\n", text, flags=re.S)
    text = text.replace(r"\'", "'").replace(r"\"", '"')
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def visible_source_text(source: str) -> str:
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
    spans = _line_spans(structured_text)
    structural_matches: list[re.Match[str]] = []
    for match in matches:
        line = spans[_line_index_at(spans, match.start())][2]
        if _is_short_section_label(line):
            structural_matches.append(match)
    structural_lines = {_line_index_at(spans, match.start()) for match in structural_matches}
    if len(structural_matches) > 1 or len(structural_lines) > 1:
        raise ValueError(f"专属锚点不唯一：{site.name}匹配到{len(structural_lines)}个目标块")

    match = structural_matches[0] if structural_matches else matches[0]
    anchor_line = _line_index_at(spans, match.start())
    block_start = match.start()
    block_end = len(structured_text)
    tail_start = match.end()
    if site.stop:
        stop = re.search(site.stop, structured_text[tail_start:], flags=re.I)
        if stop:
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
    structured_block = structured_text[block_start:block_end].strip()
    block_text = html_to_text(structured_block)
    full_text = html_to_text(source)
    flat_start = full_text.find(block_text)
    if flat_start < 0:
        flat_anchor = html_to_text(match.group(0))
        flat_start = full_text.find(flat_anchor)
        if flat_start < 0:
            return None
        block_text = full_text[flat_start:]
    flat_anchor = html_to_text(match.group(0))
    anchor_in_block = block_text.find(flat_anchor)
    flat_anchor_offset = flat_start + max(0, anchor_in_block)
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
    zodiac = re.sub(r"[\s\-－.。·、]+", "", value)
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


def site_scoped_text(text: str, site: Site) -> str:
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
    best = text
    best_count = -1
    for alias in aliases:
        for match in re.finditer(re.escape(alias), text):
            starts.append(match.start())
    for start in starts:
        tail = text[start:]
        chunk = tail[:12000]
        record_count = sum(1 for pattern in GENERIC_TWO_ZODIAC_PATTERNS for _ in pattern.finditer(chunk))
        count = record_count * 100 + len(re.findall(r"\d{3}\s*期", chunk))
        count += 20 if re.search(r"\d{3}\s*期[^期]{0,80}" + target_pattern.pattern, chunk) else 0
        count += 10 if any(alias in chunk[:1200] for alias in aliases) else 0
        count += 1000 if record_count and any(alias in chunk[:300] for alias in aliases) and target_pattern.search(chunk[:1000]) else 0
        if count > best_count:
            best_count = count
            best = chunk
    return best


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


def parse_site_scoped_two_zodiac_records(source: str, site: Site) -> list[Record]:
    if not site.title:
        return []
    selection = select_target_block(source, site)
    if selection is None or not selection.text:
        return []
    return records_from_two_zodiac_text(
        selection.text,
        base_offset=selection.block_start,
        anchor_text=selection.anchor_text,
        anchor_offset=selection.anchor_offset,
        block_start=selection.block_start,
        block_end=selection.block_end,
        block_id=selection.block_id,
    )


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

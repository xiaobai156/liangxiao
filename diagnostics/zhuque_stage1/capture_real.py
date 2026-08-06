"""Run the isolated, real Zhuque stage-1 capture.

This script intentionally calls only the fetch layer, the browser renderer,
the bound parser, and the direction/conflict validators. It does not call the
single-period service, CLI, BAT files, cache writer, or TXT writer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import asdict
import json
from pathlib import Path
import re
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from domain.models import PayloadDocument, Record, Site  # noqa: E402
from fetching.browser import render_browser_text  # noqa: E402
from fetching.client import FetchContext  # noqa: E402
from fetching.page import fetch_payload  # noqa: E402
from parsers.registry import ParserRegistry  # noqa: E402
from validation.conflicts import validate_document_windows  # noqa: E402
from validation.direction import select_record  # noqa: E402

from strategy import DocumentEvidence, SourceEvaluation, SourceInput, evaluate_sources  # noqa: E402


ROOT = PROJECT_ROOT
OUT = Path(__file__).resolve().parent
RAW_DIR = OUT / "raw"
SITE_URL = "https://estsghu.t7gsm-xl1x8-uohlna.xyz:16677/"
TARGET_PERIOD = 216
TIMEOUT = 35


def build_site() -> Site:
    with (ROOT / "config" / "sites.json").open(encoding="utf-8") as handle:
        entries = json.load(handle)
    entry = next(item for item in entries if item.get("name") == "朱雀" and item.get("url") == SITE_URL)
    if entry.get("pick") not in {"top", "顶部", "上"}:
        raise ValueError(f"朱雀配置方向不是top兼容值：{entry.get('pick')!r}")
    if entry.get("payload") != "page_and_scripts":
        raise ValueError(f"朱雀配置来源策略变化：{entry.get('payload')!r}")
    return Site(
        name=entry["name"],
        pick="top",
        url=entry["url"],
        parser=entry["parser"],
        title=entry.get("title", ""),
        record=entry.get("record", ""),
        stop=entry.get("stop", ""),
        payload=entry["payload"],
        api_url=entry.get("api_url", ""),
        keywords=tuple(entry.get("keywords", ())),
        profile_id=entry.get("profile_id", ""),
        linked_document_pattern=entry.get("linked_document_pattern", ""),
    )


def record_dict(record: Record | None) -> dict[str, object] | None:
    if record is None:
        return None
    return {
        "period": record.period,
        "zodiac": record.zodiac,
        "open_result": record.open_result,
        "raw": record.raw,
        "position": record.position,
        "position_kind": record.position_kind,
        "record_id": record.record_id,
        "document_url": record.document_url,
        "anchor_text": record.anchor_text,
        "anchor_offset": record.anchor_offset,
        "block_id": record.block_id,
        "block_start": record.block_start,
        "block_end": record.block_end,
        "anchor_document_url": record.anchor_document_url,
        "link_reference": record.link_reference,
        "source_positions": list(record.source_positions),
    }


def evidence_dict(evidence: DocumentEvidence) -> dict[str, object]:
    return {
        "label": evidence.label,
        "url": evidence.url,
        "source_sha256": evidence.source_sha256,
        "source_chars": evidence.source_chars,
        "visible_chars": evidence.visible_chars,
        "anchor_count": evidence.anchor_count,
        "anchor_text": evidence.anchor_text,
        "anchor_offset": evidence.anchor_offset,
        "block_start": evidence.block_start,
        "block_end": evidence.block_end,
        "all_legal_candidates": [record_dict(record) for record in evidence.valid_candidates],
        "invalid_candidates": [asdict(item) for item in evidence.invalid_candidates],
        "top3": [record_dict(record) for record in evidence.top3],
        "target_candidates": [record_dict(record) for record in evidence.target_candidates],
        "success": evidence.success,
        "selected": record_dict(evidence.selected),
        "reason": evidence.reason,
        "record_id": evidence.record_id,
        "parent_url": evidence.parent_url,
        "link_reference": evidence.link_reference,
    }


def short_record(record: Record | None) -> str:
    if record is None:
        return "无"
    return f"{record.period}期{record.zodiac}@位置{record.position}"


def short_records(records: tuple[Record, ...] | list[Record]) -> str:
    return "、".join(short_record(record) for record in records) or "无"


def source_input_from_document(document: PayloadDocument) -> SourceInput:
    return SourceInput(
        label=document.label,
        url=document.url,
        source=document.source,
        record_id=document.record_id,
        parent_url=document.parent_url,
        link_reference=document.link_reference,
    )


def production_record_dicts(records: list[Record]) -> list[dict[str, object] | None]:
    return [record_dict(record) for record in records]


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def save_source(index: int, kind: str, source: str, digest: str) -> str:
    safe_kind = re.sub(r"[^A-Za-z0-9_-]+", "_", kind).strip("_") or "document"
    filename = f"{index:03d}_{safe_kind}_{digest[:12]}.txt"
    path = RAW_DIR / filename
    write_text(path, source)
    return path.relative_to(OUT).as_posix()


def markdown_document_row(document: dict[str, object]) -> str:
    top3 = document["top3"]
    top_text = "、".join(
        f"{item['period']}期{item['zodiac']}@{item['position']}" for item in top3  # type: ignore[index]
    ) or "无"
    target = document["selected"]
    target_text = "未通过"
    if target:
        target_text = f"{target['zodiac']}@{target['position']}"  # type: ignore[index]
    return (
        f"| {document['label']} | `{document['url']}` | {document['anchor_offset']} | "
        f"{document['block_start']}–{document['block_end']} | {top_text} | "
        f"{target_text} | {'通过' if document['success'] else '失败'} |"
    )


def run() -> int:
    site = build_site()
    registry = ParserRegistry.bind_sites([site])
    context = FetchContext()

    bundle = fetch_payload(
        site,
        TARGET_PERIOD,
        TIMEOUT,
        context,
        lambda _source, _target, _period: True,
    )
    page_sources = [source_input_from_document(document) for document in bundle.documents]
    page_evaluation: SourceEvaluation = evaluate_sources(page_sources, target_period=TARGET_PERIOD)

    browser_error = ""
    browser_source = ""
    try:
        browser_source = render_browser_text(SITE_URL, TIMEOUT)
    except Exception as exc:  # pragma: no cover - depends on live browser/runtime
        browser_error = f"{type(exc).__name__}: {exc}"
    browser_input = (
        SourceInput("浏览器渲染 DOM", SITE_URL, browser_source)
        if browser_source
        else None
    )
    browser_evaluation = (
        evaluate_sources([browser_input], target_period=TARGET_PERIOD)
        if browser_input is not None
        else None
    )
    combined_evaluation = (
        evaluate_sources(page_sources + [browser_input], target_period=TARGET_PERIOD)
        if browser_input is not None
        else None
    )
    if browser_evaluation is None:
        browser_conclusion = f"不可用：{browser_error}" if browser_error else "不可用"
    else:
        browser_conclusion = (
            "成功"
            if browser_evaluation.success
            else f"失败；{browser_evaluation.reason}"
        )

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    page_documents: list[dict[str, object]] = []
    for index, document in enumerate(bundle.documents):
        evidence = page_evaluation.documents[index]
        try:
            production_records = registry.parse(document, site)
            parser_error = ""
        except Exception as exc:  # pragma: no cover - diagnostic guard
            production_records = []
            parser_error = f"{type(exc).__name__}: {exc}"
        row = evidence_dict(evidence)
        row["raw_file"] = save_source(index, document.label, document.source, evidence.source_sha256)
        row["record_metadata"] = {
            "record_id": document.record_id,
            "parent_url": document.parent_url,
            "link_reference": document.link_reference,
        }
        row["production_parser_records"] = production_record_dicts(production_records)
        row["production_parser_error"] = parser_error
        page_documents.append(row)

    browser_document: dict[str, object] | None = None
    if browser_input is not None and browser_evaluation is not None:
        browser_evidence = browser_evaluation.documents[0]
        browser_row = evidence_dict(browser_evidence)
        browser_row["raw_file"] = save_source(
            len(bundle.documents),
            "browser_dom",
            browser_source,
            browser_evidence.source_sha256,
        )
        browser_payload = PayloadDocument("浏览器渲染 DOM", SITE_URL, browser_source)
        try:
            browser_records = registry.parse(browser_payload, site)
            browser_parser_error = ""
        except Exception as exc:  # pragma: no cover - diagnostic guard
            browser_records = []
            browser_parser_error = f"{type(exc).__name__}: {exc}"
        browser_row["production_parser_records"] = production_record_dicts(browser_records)
        browser_row["production_parser_error"] = browser_parser_error
        browser_document = browser_row

    observed: list[tuple[str, list[Record]]] = []
    replay_rows: list[dict[str, object]] = []
    for document in bundle.documents:
        records = registry.parse(document, site)
        if not records:
            continue
        observed.append((document.label, records))
        try:
            selected = select_record(records, TARGET_PERIOD, site)
            error = ""
        except Exception as exc:
            selected = None
            error = f"{type(exc).__name__}: {exc}"
        replay_rows.append(
            {
                "label": document.label,
                "url": document.url,
                "records": production_record_dicts(records),
                "top3": production_record_dicts(records[:3]),
                "selected": record_dict(selected),
                "error": error,
            }
        )
    try:
        validate_document_windows(observed, site, "page_and_scripts诊断", period=TARGET_PERIOD)
        conflict_error = ""
    except Exception as exc:
        conflict_error = f"{type(exc).__name__}: {exc}"

    payload = {
        "capture": {
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "python": sys.version,
            "command_scope": "fetch_payload(page_and_scripts)+render_browser_text+bound_parser+direction/conflicts only",
            "formal_single_period_service_called": False,
            "formal_cli_or_bat_called": False,
            "formal_cache_or_txt_written": False,
            "screenshot_value_used": False,
            "site": {
                "name": site.name,
                "url": site.url,
                "pick": site.pick,
                "parser": site.parser,
                "payload": site.payload,
                "target_period": TARGET_PERIOD,
                "field": "177792a.com『禁用资料』/禁用二肖",
            },
            "page_and_scripts": {
                "document_count": len(bundle.documents),
                "scan_complete": bundle.scan_complete,
                "isolated_evaluation": {
                    "success": page_evaluation.success,
                    "selected": record_dict(page_evaluation.selected),
                    "reason": page_evaluation.reason,
                },
                "documents": page_documents,
                "production_direction_replay": {
                    "observed_document_count": len(observed),
                    "documents": replay_rows,
                    "conflict_validation_error": conflict_error,
                },
            },
            "browser_rendered_dom": {
                "available": bool(browser_source),
                "error": browser_error,
                "isolated_evaluation": (
                    {
                        "success": browser_evaluation.success,
                        "selected": record_dict(browser_evaluation.selected),
                        "reason": browser_evaluation.reason,
                    }
                    if browser_evaluation is not None
                    else None
                ),
                "document": browser_document,
            },
            "combined_independent_sources": (
                {
                    "success": combined_evaluation.success,
                    "selected": record_dict(combined_evaluation.selected),
                    "reason": combined_evaluation.reason,
                }
                if combined_evaluation is not None
                else None
            ),
        }
    }
    write_text(OUT / "evidence.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    authoritative = [
        document
        for document in page_documents
        if document["anchor_count"] or document["all_legal_candidates"] or document["invalid_candidates"] or document["production_parser_records"]
    ]
    if browser_document is not None:
        authoritative.append(browser_document)
    lines = [
        "# 朱雀 Stage 1 隔离真实取证",
        "",
        f"- 采集时间（UTC）：{payload['capture']['captured_at_utc']}",
        f"- 目标：{SITE_URL}，top，{TARGET_PERIOD}期",
        "- 字段：`177792a.com『禁用资料』` 区块中的 `禁用二肖`",
        "- 截图值参与逻辑：否；本报告全部值来自本次实时文档",
        "- 正式单期服务/CLI/BAT/缓存/TXT：均未调用或写入",
        "",
        "## 结论",
        "",
        f"- `page_and_scripts` 隔离结论：{'成功' if page_evaluation.success else '失败'}；{page_evaluation.reason}",
        f"- 浏览器渲染 DOM 隔离结论：{browser_conclusion}",
        f"- 脚本与浏览器独立合并结论：{('未取得浏览器 DOM' if combined_evaluation is None else ('成功' if combined_evaluation.success else '失败；' + combined_evaluation.reason))}",
        f"- 当前抓取层逐文档方向重放：观察到{len(observed)}个有候选文档；冲突校验：{conflict_error or '通过'}",
        "",
        "## 看似权威文档的独立证据",
        "",
        "每行的锚点、区块边界、候选位置都在该文档自己的可见文本坐标中；没有跨文档拼接。完整 JSON 和原文快照见 `evidence.json` 与 `raw/`。",
        "",
        "| 来源标签 | URL | 锚点位置 | 区块边界 | top3（期/二肖@位置） | 216值@位置 | 文档结论 |",
        "|---|---|---:|---:|---|---|---|",
    ]
    lines.extend(markdown_document_row(document) for document in authoritative)
    lines.extend(
        [
            "",
            f"- `page_and_scripts` 原始独立文档总数：{len(page_documents)}；无目标锚点/合法候选的文档：{len(page_documents) - sum(1 for item in page_documents if item['anchor_count'] or item['all_legal_candidates'] or item['invalid_candidates'] or item['production_parser_records'])}。",
            "- “所有合法候选”不是只取前3条；top3 只在合法候选收集完成后按该文档原始位置取前3条。",
            "- 来源分歧安全门：脚本与浏览器 DOM 出现不同 216 值时，以跨文档同期冲突失败；若一个来源已有目标期而另一个同样权威来源的 top3 尚未出现目标期，则以来源状态/方向窗口分歧失败；两类情况都不会忽略任一来源。",
            "",
            "## 隔离测试",
            "",
            "由 `test_strategy.py` 覆盖真实期形状、相邻期、不存在期、top 越界、错字段、重复生肖、同文档同期冲突、跨文档借锚点、脚本/浏览器分歧及同值不同位置。",
        ]
    )
    write_text(OUT / "evidence.md", "\n".join(lines) + "\n")

    print(json.dumps({
        "documents": len(bundle.documents),
        "page_success": page_evaluation.success,
        "page_selected": record_dict(page_evaluation.selected),
        "browser_available": bool(browser_source),
        "browser_success": browser_evaluation.success if browser_evaluation else False,
        "browser_selected": record_dict(browser_evaluation.selected) if browser_evaluation else None,
        "combined_success": combined_evaluation.success if combined_evaluation else False,
        "combined_reason": combined_evaluation.reason if combined_evaluation else browser_error,
        "replay_conflict": conflict_error,
        "evidence": str(OUT / "evidence.json"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())

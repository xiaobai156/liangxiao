from __future__ import annotations

from dataclasses import dataclass


DOCUMENT_BOUNDARY = "\n" + "#" * 512 + "\n"
POSITION_KIND_VISIBLE_TEXT = "visible_text_offset_v1"
POSITION_KIND_LEGACY_INDEX = "candidate_index_v1"


@dataclass(frozen=True, slots=True)
class Site:
    name: str
    pick: str
    url: str
    parser: str = "named_block"
    title: str = ""
    record: str = ""
    stop: str = ""
    payload: str = "page"
    api_url: str = ""
    keywords: tuple[str, ...] = ()
    profile_id: str = ""
    linked_document_pattern: str = ""

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.name, self.url, self.pick


@dataclass(frozen=True, slots=True)
class Record:
    period: int
    zodiac: str
    open_result: str
    raw: str
    position: int = 0
    record_id: str = ""
    document_url: str = ""
    position_kind: str = POSITION_KIND_VISIBLE_TEXT
    anchor_text: str = ""
    anchor_offset: int = -1
    block_id: str = ""
    block_start: int = -1
    block_end: int = -1
    anchor_document_url: str = ""
    link_reference: str = ""
    source_positions: tuple[int, ...] = ()
    record_path: str = ""
    record_count: int = 0
    anchor_record_id: str = ""
    anchor_record_path: str = ""
    anchor_record_count: int = 0
    body_record_id: str = ""
    body_record_path: str = ""
    body_record_count: int = 0


@dataclass(frozen=True, slots=True)
class Result:
    site: Site
    record: Record | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.record is not None and not self.error


@dataclass(frozen=True, slots=True)
class HistoryResult:
    site: Site
    records: tuple[Record, ...]
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.records) and not self.error


@dataclass(frozen=True, slots=True)
class DuplicateFinding:
    first: str
    second: str
    start_period: int
    end_period: int
    consecutive: int
    classification: str

    @property
    def site_a(self) -> str:
        return self.first

    @property
    def site_b(self) -> str:
        return self.second


@dataclass(frozen=True, slots=True)
class CacheCoverage:
    missing_sites: tuple[str, ...]
    incomplete_sites: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PayloadDocument:
    label: str
    url: str
    source: str
    record_id: str = ""
    record_path: str = ""
    record_count: int = 0
    parent_url: str = ""
    link_reference: str = ""
    body_source_start: int = 0
    anchor_record_id: str = ""
    anchor_record_path: str = ""
    anchor_record_count: int = 0
    body_record_id: str = ""
    body_record_path: str = ""
    body_record_count: int = 0


@dataclass(frozen=True, slots=True)
class DocumentBundle:
    documents: tuple[PayloadDocument, ...]
    browser_error: str = ""
    scan_complete: bool = True

    @property
    def combined_source(self) -> str:
        return DOCUMENT_BOUNDARY.join(document.source for document in self.documents)


@dataclass(frozen=True, slots=True)
class AdminArticleMatch:
    record: dict[str, object]
    path: str
    article_count: int

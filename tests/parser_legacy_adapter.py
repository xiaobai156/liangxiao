from __future__ import annotations

from dataclasses import replace

from domain.errors import CandidateConflict, ScrapeFailure
from domain.models import PayloadDocument, Record, Result, Site
from parsers.chart import *  # noqa: F403
from parsers.dynamic import *  # noqa: F403
from parsers.forum import *  # noqa: F403
from parsers.helpers import *  # noqa: F403
from parsers.registry import ParserRegistry
from parsers.special import *  # noqa: F403
from cache.duplicates import audit_cache_coverage, detect_duplicate_findings
from cache.guard import guard_results_against_cache
from cache.repository import RecentCacheRepository, recent_periods
from validation.boundaries import document_is_linked
from validation.direction import direction_window, select_record as strict_select_record
from validation.records import validate_selected_record


def parse_records(source: str, site: Site) -> list[Record]:
    try:
        records = ParserRegistry.bind_sites([site]).parse(source, site)
    except CandidateConflict as exc:
        records = list(exc.candidates)
    except ScrapeFailure:
        return []
    return [replace(record, position=index) for index, record in enumerate(records)]


def select_record(records, period, site):
    selected = strict_select_record(records, period, site)
    for index, record in enumerate(records):
        if (
            record.period == selected.period
            and record.zodiac == selected.zodiac
            and record.position == selected.position
        ):
            return replace(selected, position=index)
    return selected


def mark_results_conflicting_with_cache(results, path, current_period):
    import json

    cache = json.loads(path.read_text(encoding="utf-8-sig"))
    return guard_results_against_cache(results, cache, current_period)


def build_history_cache_update_payload(results, path, current_period):
    return RecentCacheRepository(path).prepare_update(results, current_period)


def update_history_cache_from_current_results(results, path, current_period):
    repository = RecentCacheRepository(path)
    payload = repository.prepare_update(results, current_period)
    if payload is not None:
        repository.commit(payload)

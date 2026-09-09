from __future__ import annotations

from dataclasses import asdict

from domain.models import Record, Site


def serialize_cached_record(site: Site, record: Record) -> dict[str, object]:
    """One record representation for single-period, history and repair writes."""
    result: dict[str, object] = {
        "period": record.period,
        "zodiac": record.zodiac,
        "position": record.position,
        "source_positions": list(record.source_positions or (record.position,)),
        "position_kind": record.position_kind,
    }
    evidence = getattr(record, "evidence", ())
    if evidence:
        result["evidence"] = [asdict(item) for item in evidence]
    if site.payload == "admin_article_api" and record.record_id:
        result["article_id"] = record.record_id
    return result

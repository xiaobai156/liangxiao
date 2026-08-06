from __future__ import annotations

from domain.models import Record, Site
from validation.direction import direction_window
from validation.records import record_value_signature


def validate_document_windows(
    observed_documents: list[tuple[str, list[Record]]],
    site: Site,
    context: str,
    period: int | None = None,
) -> None:
    signatures_by_period: dict[int, set[str]] = {}
    for label, records in observed_documents:
        local: dict[int, set[str]] = {}
        window = direction_window(records, site)
        for record in window:
            signature = record_value_signature(record)
            local.setdefault(record.period, set()).add(signature)
            signatures_by_period.setdefault(record.period, set()).add(signature)
        local_conflicts = {period: values for period, values in local.items() if len(values) > 1}
        if local_conflicts:
            details = "；".join(
                f"{period}期 " + "、".join(sorted(values))
                for period, values in sorted(local_conflicts.items())
            )
            raise ValueError(f"数据存在冲突：{label}{context}近3条内{details}")
    conflicts = {period: values for period, values in signatures_by_period.items() if len(values) > 1}
    if conflicts:
        details = "；".join(
            f"{period}期 " + "、".join(sorted(values))
            for period, values in sorted(conflicts.items())
        )
        raise ValueError(f"数据存在冲突：{context}近3条结果不同：{details}")

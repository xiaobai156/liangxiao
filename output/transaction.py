from __future__ import annotations

import os
from pathlib import Path
import tempfile

from cache.repository import RecentCacheRepository
from domain.models import Result
from output.formatter import format_results


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def atomic_write_text(path: Path, value: str) -> None:
    atomic_write_bytes(path, value.encode("utf-8"))


def write_outputs(
    results: list[Result],
    output: Path,
    errors: Path,
    *,
    include_url: bool,
    append_fixed_tail: bool = False,
) -> None:
    success_text, failure_text = format_results(
        results,
        include_url=include_url,
        append_fixed_tail=append_fixed_tail,
    )
    atomic_write_text(output, success_text)
    if failure_text:
        atomic_write_text(errors, failure_text)
    else:
        errors.unlink(missing_ok=True)


def write_formal_outputs_and_cache(
    results: list[Result],
    output: Path,
    errors: Path,
    repository: RecentCacheRepository,
    prepared_cache: dict[str, object] | None,
    *,
    include_url: bool,
) -> bool:
    paths = tuple(dict.fromkeys((output, errors, repository.path)))
    snapshots = {path: path.read_bytes() if path.exists() else None for path in paths}
    try:
        write_outputs(results, output, errors, include_url=include_url, append_fixed_tail=True)
        if prepared_cache is not None:
            repository.commit(prepared_cache)
            return True
        return False
    except BaseException:
        for path, content in snapshots.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write_bytes(path, content)
        raise

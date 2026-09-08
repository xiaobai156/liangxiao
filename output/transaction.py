from __future__ import annotations

import os
import tempfile
from pathlib import Path

from cache.repository import RecentCacheRepository
from domain.models import Result
from output.formatter import format_results


LEGACY_FIXED_TAIL_FIRST_LINE = "黄杀"


class CacheUpdateError(RuntimeError):
    pass


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def _validate_distinct_paths(paths: tuple[tuple[str, Path], ...]) -> None:
    seen: dict[str, str] = {}
    for label, path in paths:
        key = _path_key(path)
        previous = seen.get(key)
        if previous is not None:
            raise ValueError(f"路径冲突：{previous}与{label}不能使用同一文件")
        seen[key] = label


def _snapshot(paths: tuple[Path, ...]) -> dict[Path, bytes | None]:
    return {path: path.read_bytes() if path.exists() else None for path in paths}


def _restore(snapshot: dict[Path, bytes | None]) -> None:
    for path, content in snapshot.items():
        if content is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write_bytes(path, content)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
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


def append_repaired_successes(
    results: list[Result],
    output: Path,
    *,
    include_url: bool,
) -> int:
    """Append verified repair results without changing existing success bytes."""
    lines: list[str] = []
    for result in results:
        if not result.ok or result.record is None:
            raise ValueError(f"修复结果尚未成功：{result.site.name}")
        line = f"{result.record.zodiac} {result.site.name}"
        if include_url:
            line += f" {result.site.url}"
        if line not in lines:
            lines.append(line)

    existing = output.read_bytes() if output.exists() else b""
    existing_lines = set(existing.decode("utf-8-sig").splitlines())
    missing = [line for line in lines if line not in existing_lines]
    if not missing:
        return 0

    newline = b"\r\n" if b"\r\n" in existing else b"\n"
    chunks = existing.splitlines(keepends=True)
    labels = [chunk.decode("utf-8-sig").strip() for chunk in chunks]
    insert_at = next(
        (
            index
            for index, label in enumerate(labels)
            if label == LEGACY_FIXED_TAIL_FIRST_LINE
        ),
        next(
            (
                index
                for index, label in enumerate(labels)
                if label in {"内容\t次数\t排名", "生肖次数排行榜"}
            ),
            len(chunks),
        ),
    )
    if (
        insert_at < len(chunks)
        and labels[insert_at] in {"内容\t次数\t排名", "生肖次数排行榜"}
        and insert_at
        and not labels[insert_at - 1]
    ):
        insert_at -= 1
    prefix = (
        b""
        if not chunks[:insert_at] or chunks[insert_at - 1].endswith((b"\n", b"\r"))
        else newline
    )
    inserted = prefix + newline.join(line.encode("utf-8") for line in missing) + newline
    atomic_write_bytes(
        output, b"".join(chunks[:insert_at]) + inserted + b"".join(chunks[insert_at:])
    )
    return len(missing)


def write_outputs(
    results: list[Result],
    output: Path,
    errors: Path,
    *,
    include_url: bool,
) -> None:
    _validate_distinct_paths((("成功TXT", output), ("失败TXT", errors)))
    success_text, failure_text = format_results(results, include_url=include_url)
    paths = (output, errors)
    snapshot = _snapshot(paths)
    try:
        atomic_write_text(output, success_text)
        if failure_text:
            atomic_write_text(errors, failure_text)
        else:
            errors.unlink(missing_ok=True)
    except BaseException:
        _restore(snapshot)
        raise


def write_formal_outputs_and_cache(
    results: list[Result],
    output: Path,
    errors: Path,
    repository: RecentCacheRepository,
    prepared_cache: dict[str, object] | None,
    *,
    include_url: bool,
) -> bool:
    _validate_distinct_paths(
        (("成功TXT", output), ("失败TXT", errors), ("缓存", repository.path))
    )
    write_outputs(results, output, errors, include_url=include_url)
    if prepared_cache is None:
        return False
    try:
        repository.commit(prepared_cache)
    except Exception as exc:
        raise CacheUpdateError(f"缓存更新未完成：{exc}") from exc
    return True

from __future__ import annotations

from collections import Counter

from domain.models import Result

ZODIACS = "牛马羊鸡狗猪鼠虎兔龙蛇猴"


def format_results(
    results: list[Result],
    *,
    include_url: bool,
) -> tuple[str, str]:
    ok_lines: list[str] = []
    seen: set[tuple[str, str]] = set()
    counter: Counter[str] = Counter()
    error_lines: list[str] = []
    for result in results:
        if result.ok and result.record is not None:
            key = (result.record.zodiac, result.site.name)
            if key in seen:
                continue
            seen.add(key)
            line = f"{result.record.zodiac} {result.site.name}"
            if include_url:
                line += f" {result.site.url}"
            ok_lines.append(line)
            counter.update(result.record.zodiac)
        else:
            error_lines.append(
                f"{result.site.name} {result.site.pick} {result.site.url} 原因：{result.error}"
            )
    rank: list[str] = []
    previous_count = -1
    dense_rank = 0
    for zodiac in sorted(
        counter, key=lambda item: (-counter[item], ZODIACS.index(item))
    ):
        count = counter[zodiac]
        if count != previous_count:
            dense_rank += 1
            previous_count = count
        rank.append(f"{zodiac}\t{count}\t{dense_rank}")
    success = "\n".join(
        ok_lines + (["", "内容\t次数\t排名", *rank] if ok_lines else [])
    )
    if ok_lines:
        success += "\n"
    failure = "\n\n".join(error_lines) + ("\n" if error_lines else "")
    return success, failure


def multi_failure_text(period_results: dict[int, list[Result]]) -> str:
    if not period_results:
        return ""
    periods = list(period_results)
    first_results = period_results[periods[0]]
    blocks: list[str] = []
    for index, first in enumerate(first_results):
        failures: list[str] = []
        for period in periods:
            result = period_results[period][index]
            if result.ok:
                break
            failures.append(f"{period}期：{result.error}")
        else:
            blocks.append(
                "\n".join(
                    [f"{first.site.name} {first.site.pick} {first.site.url}", *failures]
                )
            )
    return "\n\n".join(blocks) + ("\n" if blocks else "")

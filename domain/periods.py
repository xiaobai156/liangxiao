from __future__ import annotations


def previous_period(period: int) -> int:
    if type(period) is not int or not 1 <= period <= 365:
        raise ValueError(f"期数越界：{period}")
    return 365 if period == 1 else period - 1


def next_period(period: int) -> int:
    if type(period) is not int or not 1 <= period <= 365:
        raise ValueError(f"期数越界：{period}")
    return 1 if period == 365 else period + 1

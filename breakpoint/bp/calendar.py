"""Business-day calendar for Q3 2026 + October, and period-close rules."""
from datetime import date, timedelta
from functools import lru_cache

START = date(2026, 7, 1)
END = date(2026, 10, 23)
HOLIDAYS = {date(2026, 7, 3), date(2026, 9, 7)}  # Independence Day (observed), Labor Day


def is_business_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


@lru_cache(maxsize=1)
def business_days() -> tuple[date, ...]:
    out, d = [], START
    while d <= END:
        if is_business_day(d):
            out.append(d)
        d += timedelta(days=1)
    return tuple(out)


def next_bd(d: date, n: int = 1) -> date:
    step = 1 if n >= 0 else -1
    k = abs(n)
    while k:
        d += timedelta(days=step)
        if is_business_day(d):
            k -= 1
    return d


def bd_between(a: date, b: date) -> int:
    """Business days strictly after a, up to and including b."""
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if is_business_day(d):
            n += 1
    return n


def period_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def lock_date(period: str) -> date:
    """A month locks on the 3rd business day of the following month."""
    y, m = map(int, period.split("-"))
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    d = date(y, m, 1)
    if not is_business_day(d):
        d = next_bd(d)
    return next_bd(d, 2)

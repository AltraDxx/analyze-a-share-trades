#!/usr/bin/env python3
"""Confirm D1 and emit relative D1-Dn labels from a China trading calendar."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date, datetime, timedelta
from pathlib import Path


DATE_FIELDS = ("cal_date", "trade_date", "date", "日期", "交易日期")
OPEN_FIELDS = ("is_open", "open", "是否交易", "开市")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Confirm whether D1 is the analysis day or the next open session, then "
            "emit relative holding/review labels without natural holding dates."
        )
    )
    parser.add_argument("--as-of", required=True, help="Analysis date in YYYY-MM-DD.")
    parser.add_argument("--holding-min", type=int, default=2)
    parser.add_argument("--holding-max", type=int, default=22)
    parser.add_argument(
        "--calendar",
        type=Path,
        help="Required CSV/JSON trading calendar for formal advice.",
    )
    parser.add_argument(
        "--allow-weekday-fallback",
        action="store_true",
        help=(
            "Explicitly allow a Monday-Friday approximation for diagnostics only. "
            "Chinese exchange holidays are not removed."
        ),
    )
    parser.add_argument(
        "--review-days",
        default="2,3,5",
        help="Comma-separated inclusive review day numbers. Default: 2,3,5.",
    )
    return parser.parse_args()


def parse_date(value: str) -> date:
    cleaned = str(value).strip()
    try:
        return datetime.fromisoformat(cleaned.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported date: {value!r}")


def truthy_open(value: object) -> bool:
    if value is None or str(value).strip() == "":
        return True
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是", "开市", "交易"}


def first_present(row: dict[str, object], keys: tuple[str, ...]) -> object | None:
    lower_map = {str(key).strip().lower(): value for key, value in row.items()}
    for key in keys:
        if key.lower() in lower_map:
            return lower_map[key.lower()]
    return None


def load_calendar(path: Path) -> list[date]:
    if path.suffix.lower() == ".json":
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            raw = raw.get("records") or raw.get("data") or raw.get("dates") or []
        if not isinstance(raw, list):
            raise ValueError("JSON calendar must be a list or contain records/data/dates.")
        dates: list[date] = []
        for item in raw:
            if isinstance(item, str):
                dates.append(parse_date(item))
            elif isinstance(item, dict):
                day = first_present(item, DATE_FIELDS)
                opened = first_present(item, OPEN_FIELDS)
                if day is not None and truthy_open(opened):
                    dates.append(parse_date(str(day)))
        return sorted(set(dates))

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    dates = []
    for row in rows:
        day = first_present(row, DATE_FIELDS)
        opened = first_present(row, OPEN_FIELDS)
        if day is not None and truthy_open(opened):
            dates.append(parse_date(str(day)))
    return sorted(set(dates))


def weekday_fallback(start: date, count: int = 120) -> list[date]:
    dates: list[date] = []
    cursor = start
    while len(dates) < count:
        if cursor.weekday() < 5:
            dates.append(cursor)
        cursor += timedelta(days=1)
    return dates


def resolve_open_dates(
    as_of: date,
    holding_max: int,
    calendar: Path | None,
    allow_weekday_fallback: bool,
) -> tuple[list[date], str, str | None]:
    if calendar:
        return (
            [day for day in load_calendar(calendar) if day >= as_of],
            "provided_trading_calendar",
            None,
        )
    if not allow_weekday_fallback:
        raise ValueError(
            "A reliable exchange trading calendar is required. Pass --calendar; "
            "use --allow-weekday-fallback only for non-final diagnostics."
        )
    return (
        weekday_fallback(as_of, max(120, holding_max + 20)),
        "weekday_fallback_explicit",
        (
            "Chinese exchange holidays are not removed. This diagnostic result "
            "must not be used as a final D1 determination."
        ),
    )


def build_relative_payload(
    as_of: date,
    open_dates: list[date],
    holding_min: int,
    holding_max: int,
    review_numbers: list[int],
    quality: str,
    warning: str | None,
) -> dict[str, object]:
    d1_is_analysis_day = open_dates[0] == as_of
    return {
        "analysis_date": as_of.isoformat(),
        "d1": "D1",
        "d1_relation": (
            "analysis_day"
            if d1_is_analysis_day
            else "next_open_session_after_analysis_day"
        ),
        "as_of_is_trading_day": d1_is_analysis_day,
        "holding_window": {
            "start": "D1",
            "min_end": f"D{holding_min}",
            "max_end": f"D{holding_max}",
            "min_trading_days_inclusive": holding_min,
            "max_trading_days_inclusive": holding_max,
        },
        "review_points": [
            f"D{number}" for number in review_numbers if number <= len(open_dates)
        ],
        "calendar_quality": quality,
        "natural_holding_dates_exposed": False,
        "warning": warning,
    }


def main() -> int:
    args = parse_args()
    if args.holding_min < 1 or args.holding_max < args.holding_min:
        raise SystemExit("Require 1 <= holding-min <= holding-max.")
    as_of = parse_date(args.as_of)

    try:
        open_dates, quality, warning = resolve_open_dates(
            as_of,
            args.holding_max,
            args.calendar,
            args.allow_weekday_fallback,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    if len(open_dates) < args.holding_max:
        raise SystemExit("Trading calendar does not extend through the requested holding window.")

    review_numbers = sorted(
        {
            int(value)
            for value in args.review_days.split(",")
            if value.strip() and int(value) >= 1
        }
    )
    payload = build_relative_payload(
        as_of,
        open_dates,
        args.holding_min,
        args.holding_max,
        review_numbers,
        quality,
        warning,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

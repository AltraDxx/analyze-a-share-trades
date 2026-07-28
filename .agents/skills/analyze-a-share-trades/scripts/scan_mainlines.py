#!/usr/bin/env python3
"""Deterministically rank sector or industry strength from daily long-form CSV data."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import fmean
from typing import Any
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
RETURN_WINDOWS = (5, 10, 20)
ACTIVITY_RECENT_WINDOW = 5
ACTIVITY_BASELINE_WINDOW = 20

FIELD_ALIASES = {
    "date": (
        "date",
        "datetime",
        "trade_date",
        "trading_date",
        "日期",
        "时间",
        "交易日期",
    ),
    "entity": (
        "entity",
        "sector",
        "industry",
        "theme",
        "mainline",
        "name",
        "板块",
        "行业",
        "主题",
        "产业",
        "名称",
    ),
    "close": (
        "close",
        "index_close",
        "price",
        "value",
        "收盘",
        "收盘价",
        "指数收盘",
        "最新价",
    ),
    "activity": (
        "amount",
        "turnover",
        "turnover_amount",
        "volume",
        "成交额",
        "成交金额",
        "成交量",
    ),
    "breadth": (
        "advancing_breadth",
        "advance_ratio",
        "up_ratio",
        "breadth",
        "上涨家数占比",
        "上涨占比",
        "上涨宽度",
    ),
    "advancers": (
        "advancers",
        "advance_count",
        "up_count",
        "rising_count",
        "上涨家数",
        "上涨数",
    ),
    "decliners": (
        "decliners",
        "decline_count",
        "down_count",
        "falling_count",
        "下跌家数",
        "下跌数",
    ),
    "constituents": (
        "constituents",
        "constituent_count",
        "member_count",
        "total_count",
        "成分股数",
        "样本数",
        "总家数",
    ),
    "benchmark_close": (
        "benchmark_close",
        "benchmark_value",
        "benchmark_index_close",
        "基准收盘",
        "基准值",
        "基准指数收盘",
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compute deterministic 5/10/20-session sector or industry strength. "
            "The script emits descriptive evidence and never a buy/sell decision."
        )
    )
    parser.add_argument("input", type=Path, help="Long-form daily CSV input.")
    parser.add_argument(
        "--as-of",
        help=(
            "Inclusive analysis date/datetime in ISO form. "
            "Defaults to the latest valid date in the input."
        ),
    )
    parser.add_argument(
        "--benchmark",
        help=(
            "Optional entity name in the same CSV to use as the benchmark. "
            "That entity is excluded from the ranking."
        ),
    )
    parser.add_argument(
        "--top",
        type=int,
        help="Optional number of ranked entities to return. Defaults to all.",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON output path.")
    for key in FIELD_ALIASES:
        parser.add_argument(
            f"--{key.replace('_', '-')}-field",
            dest=f"{key}_field",
            help=f"Exact CSV column to use for {key}; otherwise aliases are detected.",
        )
    return parser.parse_args()


def choose_field(
    fieldnames: list[str], requested: str | None, aliases: tuple[str, ...]
) -> str | None:
    normalized = {field.strip().casefold(): field for field in fieldnames}
    if requested:
        matched = normalized.get(requested.strip().casefold())
        if not matched:
            raise SystemExit(
                f"Requested field {requested!r} was not found. "
                f"Detected headers: {', '.join(fieldnames)}"
            )
        return matched
    for alias in aliases:
        matched = normalized.get(alias.casefold())
        if matched:
            return matched
    return None


def parse_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text.casefold() in {"nan", "none", "null", "-", "--"}:
        return None
    multiplier = 1.0
    if text.endswith("亿"):
        multiplier = 100_000_000
        text = text[:-1]
    elif text.endswith("万"):
        multiplier = 10_000
        text = text[:-1]
    try:
        number = float(text.rstrip("%")) * multiplier
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def parse_trading_date(value: Any) -> date:
    cleaned = str(value).strip()
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(SHANGHAI)
        return parsed.date()
    except ValueError:
        pass
    for fmt in ("%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported date: {value!r}")


def rounded(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def pct_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return (current / previous - 1.0) * 100


def normalize_breadth(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    number = parse_number(value)
    if number is None:
        return None
    if "%" in text:
        breadth = number
    elif 0 <= number <= 1:
        breadth = number * 100
    else:
        breadth = number
    return breadth if 0 <= breadth <= 100 else None


def calculate_return(
    closes: dict[date, float | None], trading_dates: list[date], sessions: int
) -> float | None:
    if len(trading_dates) <= sessions:
        return None
    start_date = trading_dates[-sessions - 1]
    end_date = trading_dates[-1]
    start_close = closes.get(start_date)
    end_close = closes.get(end_date)
    if start_close is None or end_close is None:
        return None
    return pct_change(end_close, start_close)


def calculate_breadth(row: dict[str, Any], fields: dict[str, str | None]) -> float | None:
    if fields["breadth"]:
        return normalize_breadth(row.get("breadth_raw"))
    advancers = row.get("advancers")
    constituents = row.get("constituents")
    decliners = row.get("decliners")
    denominator = constituents
    if denominator is None and advancers is not None and decliners is not None:
        denominator = advancers + decliners
    if advancers is None or denominator is None or denominator <= 0:
        return None
    breadth = advancers / denominator * 100
    return breadth if 0 <= breadth <= 100 else None


def append_gap(gaps: list[str], gap: str) -> None:
    if gap not in gaps:
        gaps.append(gap)


def activity_summary(
    rows_by_date: dict[date, dict[str, Any]],
    trading_dates: list[date],
    gaps: list[str],
    activity_field: str | None,
) -> dict[str, Any]:
    summary = {
        "field": activity_field,
        "ratio_5d_vs_previous_20d": None,
        "recent_sessions_used": 0,
        "baseline_sessions_used": 0,
    }
    if not activity_field:
        append_gap(gaps, "activity_field_unavailable")
        return summary
    if len(trading_dates) < ACTIVITY_RECENT_WINDOW + 1:
        append_gap(
            gaps,
            "insufficient_history_for_activity_ratio:"
            f"have_{len(trading_dates)}_dates_need_at_least_6",
        )
        return summary

    recent_dates = trading_dates[-ACTIVITY_RECENT_WINDOW:]
    baseline_dates = trading_dates[
        max(0, len(trading_dates) - ACTIVITY_RECENT_WINDOW - ACTIVITY_BASELINE_WINDOW) :
        -ACTIVITY_RECENT_WINDOW
    ]
    recent_values = [
        rows_by_date[day]["activity"]
        for day in recent_dates
        if day in rows_by_date and rows_by_date[day]["activity"] is not None
    ]
    baseline_values = [
        rows_by_date[day]["activity"]
        for day in baseline_dates
        if day in rows_by_date and rows_by_date[day]["activity"] is not None
    ]
    summary["recent_sessions_used"] = len(recent_values)
    summary["baseline_sessions_used"] = len(baseline_values)
    if len(recent_values) != ACTIVITY_RECENT_WINDOW:
        append_gap(
            gaps,
            "recent_activity_incomplete:"
            f"have_{len(recent_values)}_of_{ACTIVITY_RECENT_WINDOW}",
        )
        return summary
    if len(baseline_values) < 5:
        append_gap(
            gaps,
            f"activity_baseline_too_short:have_{len(baseline_values)}_need_at_least_5",
        )
        return summary
    if len(baseline_values) < ACTIVITY_BASELINE_WINDOW:
        append_gap(
            gaps,
            "activity_baseline_short:"
            f"have_{len(baseline_values)}_of_{ACTIVITY_BASELINE_WINDOW}",
        )
    baseline_mean = fmean(baseline_values)
    if baseline_mean == 0:
        append_gap(gaps, "activity_baseline_is_zero")
        return summary
    summary["ratio_5d_vs_previous_20d"] = rounded(
        fmean(recent_values) / baseline_mean
    )
    return summary


def breadth_summary(
    rows_by_date: dict[date, dict[str, Any]],
    trading_dates: list[date],
    gaps: list[str],
    fields: dict[str, str | None],
) -> dict[str, Any]:
    has_breadth_source = bool(
        fields["breadth"]
        or (
            fields["advancers"]
            and (fields["constituents"] or fields["decliners"])
        )
    )
    summary: dict[str, Any] = {
        "source_fields": {
            key: fields[key]
            for key in ("breadth", "advancers", "decliners", "constituents")
            if fields[key]
        },
        "latest_pct": None,
        "mean_pct": {},
        "observed_sessions": {},
    }
    if not has_breadth_source:
        append_gap(gaps, "breadth_fields_unavailable")
        return summary

    breadth_by_date = {
        day: calculate_breadth(row, fields) for day, row in rows_by_date.items()
    }
    latest_date = trading_dates[-1]
    summary["latest_pct"] = rounded(breadth_by_date.get(latest_date))
    if summary["latest_pct"] is None:
        append_gap(gaps, f"latest_breadth_missing:{latest_date.isoformat()}")

    for window in RETURN_WINDOWS:
        window_dates = trading_dates[-window:]
        values = [
            breadth_by_date[day]
            for day in window_dates
            if breadth_by_date.get(day) is not None
        ]
        summary["observed_sessions"][f"{window}d"] = len(values)
        summary["mean_pct"][f"{window}d"] = (
            rounded(fmean(values)) if len(window_dates) == window and values else None
        )
        if len(window_dates) < window:
            append_gap(
                gaps,
                f"insufficient_global_history_for_{window}d_breadth:"
                f"have_{len(window_dates)}",
            )
        elif len(values) < window:
            append_gap(
                gaps,
                f"{window}d_breadth_incomplete:have_{len(values)}_of_{window}",
            )
    return summary


def positive_persistence_summary(
    closes: dict[date, float | None], trading_dates: list[date], gaps: list[str]
) -> dict[str, Any]:
    summary = {
        "positive_days": None,
        "observed_intervals": 0,
        "ratio_pct": None,
    }
    if len(trading_dates) < 6:
        append_gap(
            gaps,
            f"insufficient_global_history_for_5d_persistence:have_{len(trading_dates)}",
        )
        return summary
    dates = trading_dates[-6:]
    daily_returns: list[float] = []
    for previous_date, current_date in zip(dates, dates[1:]):
        previous_close = closes.get(previous_date)
        current_close = closes.get(current_date)
        if previous_close is None or current_close is None or previous_close == 0:
            continue
        daily_returns.append(current_close / previous_close - 1.0)
    summary["observed_intervals"] = len(daily_returns)
    if len(daily_returns) != 5:
        append_gap(
            gaps,
            f"5d_positive_persistence_incomplete:have_{len(daily_returns)}_of_5",
        )
        return summary
    positive_days = sum(value > 0 for value in daily_returns)
    summary["positive_days"] = positive_days
    summary["ratio_pct"] = rounded(positive_days / 5 * 100)
    return summary


def benchmark_from_column(
    rows: list[dict[str, Any]], field_name: str
) -> dict[date, float | None]:
    values_by_date: dict[date, list[float]] = defaultdict(list)
    for row in rows:
        value = row.get("benchmark_close")
        if value is not None:
            values_by_date[row["date"]].append(value)
    benchmark: dict[date, float | None] = {}
    for day, values in values_by_date.items():
        first = values[0]
        if any(
            not math.isclose(first, value, rel_tol=1e-9, abs_tol=1e-9)
            for value in values[1:]
        ):
            raise SystemExit(
                f"Conflicting {field_name!r} values for {day.isoformat()}."
            )
        benchmark[day] = first
    return benchmark


def descending_metric(value: float | None) -> tuple[int, float]:
    return (1, 0.0) if value is None else (0, -value)


def main() -> int:
    args = parse_args()
    if args.top is not None and args.top < 1:
        raise SystemExit("--top must be at least 1.")

    with args.input.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise SystemExit("Input CSV has no header.")
        fieldnames = list(reader.fieldnames)
        fields = {
            key: choose_field(
                fieldnames,
                getattr(args, f"{key}_field"),
                aliases,
            )
            for key, aliases in FIELD_ALIASES.items()
        }
        raw_rows = list(reader)

    missing_required = [
        key for key in ("date", "entity", "close") if not fields[key]
    ]
    if missing_required:
        raise SystemExit(
            "Input is missing required fields "
            f"{', '.join(missing_required)}. Detected headers: {', '.join(fieldnames)}"
        )

    parsed_rows: list[dict[str, Any]] = []
    skipped_invalid_date = 0
    skipped_missing_entity = 0
    for raw in raw_rows:
        try:
            trading_date = parse_trading_date(raw.get(fields["date"] or ""))
        except ValueError:
            skipped_invalid_date += 1
            continue
        entity = str(raw.get(fields["entity"] or "", "")).strip()
        if not entity:
            skipped_missing_entity += 1
            continue
        parsed_rows.append(
            {
                "date": trading_date,
                "entity": entity,
                "close": parse_number(raw.get(fields["close"] or "")),
                "activity": parse_number(raw.get(fields["activity"] or "")),
                "breadth_raw": raw.get(fields["breadth"] or ""),
                "advancers": parse_number(raw.get(fields["advancers"] or "")),
                "decliners": parse_number(raw.get(fields["decliners"] or "")),
                "constituents": parse_number(
                    raw.get(fields["constituents"] or "")
                ),
                "benchmark_close": parse_number(
                    raw.get(fields["benchmark_close"] or "")
                ),
            }
        )
    if not parsed_rows:
        raise SystemExit("No valid dated entity rows were found.")

    analysis_date = (
        parse_trading_date(args.as_of)
        if args.as_of
        else max(row["date"] for row in parsed_rows)
    )
    rows_after_as_of = [row for row in parsed_rows if row["date"] > analysis_date]
    parsed_rows = [row for row in parsed_rows if row["date"] <= analysis_date]
    if not parsed_rows:
        raise SystemExit(f"No rows exist on or before {analysis_date.isoformat()}.")

    duplicate_keys: list[tuple[str, date]] = []
    seen_keys: set[tuple[str, date]] = set()
    for row in parsed_rows:
        key = (row["entity"], row["date"])
        if key in seen_keys:
            duplicate_keys.append(key)
        seen_keys.add(key)
    if duplicate_keys:
        examples = ", ".join(
            f"{entity}@{day.isoformat()}" for entity, day in duplicate_keys[:5]
        )
        raise SystemExit(
            "Input must contain one row per entity and trading date. "
            f"Duplicate examples: {examples}"
        )

    entity_names = sorted({row["entity"] for row in parsed_rows}, key=str.casefold)
    benchmark_name: str | None = None
    if args.benchmark:
        matches = [
            name
            for name in entity_names
            if name.casefold() == args.benchmark.strip().casefold()
        ]
        if not matches:
            raise SystemExit(f"Benchmark entity {args.benchmark!r} was not found.")
        benchmark_name = matches[0]

    ranked_rows = [
        row for row in parsed_rows if row["entity"] != benchmark_name
    ]
    if not ranked_rows:
        raise SystemExit("No rankable entities remain after excluding the benchmark.")
    trading_dates = sorted({row["date"] for row in ranked_rows})

    rows_by_entity: dict[str, dict[date, dict[str, Any]]] = defaultdict(dict)
    for row in ranked_rows:
        rows_by_entity[row["entity"]][row["date"]] = row

    benchmark_closes: dict[date, float | None] | None = None
    benchmark_source: dict[str, Any] | None = None
    if benchmark_name:
        benchmark_closes = {
            row["date"]: row["close"]
            for row in parsed_rows
            if row["entity"] == benchmark_name
        }
        benchmark_source = {"type": "entity", "name": benchmark_name}
    elif fields["benchmark_close"]:
        benchmark_closes = benchmark_from_column(
            ranked_rows, fields["benchmark_close"]
        )
        benchmark_source = {
            "type": "column",
            "field": fields["benchmark_close"],
        }

    benchmark_returns = {
        f"{window}d": rounded(
            calculate_return(benchmark_closes, trading_dates, window)
        )
        if benchmark_closes is not None
        else None
        for window in RETURN_WINDOWS
    }

    global_warnings = [
        (
            "Sector/theme entities may overlap. Activity is computed within each "
            "entity only; cross-entity activity is never summed and no market-share "
            "measure is produced."
        )
    ]
    if skipped_invalid_date:
        global_warnings.append(
            f"Skipped {skipped_invalid_date} row(s) with invalid dates."
        )
    if skipped_missing_entity:
        global_warnings.append(
            f"Skipped {skipped_missing_entity} row(s) with empty entity names."
        )
    if rows_after_as_of:
        global_warnings.append(
            f"Excluded {len(rows_after_as_of)} row(s) after analysis date "
            f"{analysis_date.isoformat()}."
        )

    results: list[dict[str, Any]] = []
    expected_recent_dates = trading_dates[-21:]
    for entity in sorted(rows_by_entity, key=str.casefold):
        rows_by_date = rows_by_entity[entity]
        closes = {day: row["close"] for day, row in rows_by_date.items()}
        gaps: list[str] = []
        missing_close_rows = sum(
            row["close"] is None for row in rows_by_date.values()
        )
        if missing_close_rows:
            append_gap(gaps, f"missing_close_rows:{missing_close_rows}")
        missing_recent_dates = [
            day for day in expected_recent_dates if day not in rows_by_date
        ]
        if missing_recent_dates:
            append_gap(
                gaps,
                "missing_recent_trading_dates:"
                + ",".join(day.isoformat() for day in missing_recent_dates),
            )

        returns: dict[str, float | None] = {}
        relative_returns: dict[str, float | None] | None = (
            {} if benchmark_closes is not None else None
        )
        for window in RETURN_WINDOWS:
            value = rounded(calculate_return(closes, trading_dates, window))
            returns[f"{window}d"] = value
            if value is None:
                append_gap(
                    gaps,
                    f"{window}d_return_unavailable:need_endpoints_across_"
                    f"{window + 1}_trading_dates",
                )
            if relative_returns is not None:
                benchmark_value = benchmark_returns[f"{window}d"]
                relative_returns[f"{window}d"] = (
                    rounded(value - benchmark_value)
                    if value is not None and benchmark_value is not None
                    else None
                )
                if relative_returns[f"{window}d"] is None:
                    append_gap(gaps, f"{window}d_relative_return_unavailable")

        results.append(
            {
                "rank": None,
                "entity": entity,
                "observations": len(rows_by_date),
                "first_date": min(rows_by_date).isoformat(),
                "last_date": max(rows_by_date).isoformat(),
                "returns_pct": returns,
                "relative_returns_pct": relative_returns,
                "activity": activity_summary(
                    rows_by_date, trading_dates, gaps, fields["activity"]
                ),
                "advancing_breadth": breadth_summary(
                    rows_by_date, trading_dates, gaps, fields
                ),
                "positive_return_persistence_5d": (
                    positive_persistence_summary(closes, trading_dates, gaps)
                ),
                "data_gaps": gaps,
            }
        )

    available_windows = [
        window
        for window in reversed(RETURN_WINDOWS)
        if (
            benchmark_returns[f"{window}d"] is not None
            if benchmark_closes is not None
            else len(trading_dates) > window
        )
    ]
    ranking_window = available_windows[0] if available_windows else None
    relative_ranking = benchmark_closes is not None and ranking_window is not None
    if ranking_window is None:
        ranking_basis = "entity_name_due_to_insufficient_history"
    else:
        ranking_basis = (
            f"relative_return_{ranking_window}d"
            if relative_ranking
            else f"return_{ranking_window}d"
        )

    def ranking_value(item: dict[str, Any], window: int) -> float | None:
        if relative_ranking:
            relative = item["relative_returns_pct"]
            return relative[f"{window}d"] if relative else None
        return item["returns_pct"][f"{window}d"]

    def ranking_key(item: dict[str, Any]) -> tuple[Any, ...]:
        if ranking_window is None:
            return (item["entity"].casefold(),)
        tie_windows = [
            window for window in reversed(RETURN_WINDOWS) if window <= ranking_window
        ]
        return tuple(
            descending_metric(ranking_value(item, window))
            for window in tie_windows
        ) + (item["entity"].casefold(),)

    results.sort(key=ranking_key)
    for index, item in enumerate(results, start=1):
        item["rank"] = index
        item["ranking_metric_pct"] = (
            ranking_value(item, ranking_window)
            if ranking_window is not None
            else None
        )

    payload = {
        "mode": "deterministic_mainline_strength_scan",
        "analysis_as_of": analysis_date.isoformat(),
        "input": {
            "rows": len(raw_rows),
            "rows_used": len(parsed_rows),
            "entities_ranked": len(results),
            "trading_dates": len(trading_dates),
            "first_date": trading_dates[0].isoformat(),
            "last_date": trading_dates[-1].isoformat(),
            "field_mapping": fields,
        },
        "windows": {
            "return_sessions": list(RETURN_WINDOWS),
            "activity_ratio": "latest_5_session_mean / previous_up_to_20_session_mean",
            "positive_return_persistence": "positive_close_to_close_days_in_latest_5_intervals",
        },
        "benchmark": (
            {
                **(benchmark_source or {}),
                "returns_pct": benchmark_returns,
            }
            if benchmark_closes is not None
            else None
        ),
        "ranking": {
            "basis": ranking_basis,
            "tie_breakers": (
                "same-metric shorter windows, then entity name"
                if ranking_window is not None
                else "entity name"
            ),
            "weighted_composite_score": False,
        },
        "aggregation_policy": {
            "cross_entity_activity_sum": False,
            "market_share_calculated": False,
            "reason": "Sector/theme memberships may overlap.",
        },
        "warnings": global_warnings,
        "results": results[: args.top] if args.top else results,
        "decision": None,
        "decision_boundary": (
            "Descriptive strength evidence only; this script does not issue buy, "
            "sell, hold, or no-trade decisions."
        ),
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        sys.stdout.write(rendered + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

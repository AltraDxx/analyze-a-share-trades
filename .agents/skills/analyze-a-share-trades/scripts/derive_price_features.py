#!/usr/bin/env python3
"""Derive auditable daily or intraday OHLCV facts without making trade decisions."""

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime
from pathlib import Path
from statistics import fmean
from typing import Any
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
DAILY_COMPLETE_MINUTE = 15 * 60 + 10


ALIASES = {
    "datetime": ("datetime", "time", "date", "时间", "日期"),
    "open": ("open", "开盘"),
    "high": ("high", "最高"),
    "low": ("low", "最低"),
    "close": ("close", "收盘", "最新价"),
    "volume": ("volume", "成交量"),
    "amount": ("amount", "成交额"),
    "avg_price": ("avg_price", "average", "均价"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute basic daily/minute price-volume facts from CSV."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("--mode", choices=("auto", "daily", "intraday"), default="auto")
    parser.add_argument(
        "--as-of",
        type=parse_analysis_as_of,
        help=(
            "Analysis cutoff as an ISO datetime, with or without a timezone. "
            "Naive values use Asia/Shanghai; defaults to the current Shanghai time."
        ),
    )
    parser.add_argument(
        "--volume-unit",
        choices=("shares", "lots"),
        default="lots",
        help="Used only when deriving VWAP from amount/volume. AkShare minute volume is usually lots.",
    )
    return parser.parse_args()


def select_field(headers: list[str], aliases: tuple[str, ...]) -> str | None:
    normalized = {header.strip().lower(): header for header in headers}
    for alias in aliases:
        if alias.lower() in normalized:
            return normalized[alias.lower()]
    return None


def to_float(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "").strip())
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def parse_datetime(value: str) -> datetime:
    cleaned = value.strip()
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(SHANGHAI).replace(tzinfo=None)
        return parsed
    except ValueError:
        pass
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y%m%d",
    ):
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    raise ValueError(f"Unsupported date/time: {value!r}")


def parse_analysis_as_of(value: str) -> datetime:
    cleaned = value.strip()
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Unsupported --as-of datetime: {value!r}"
        ) from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI)


def filter_rows_as_of(
    rows: list[dict[str, Any]], analysis_as_of: datetime
) -> tuple[list[dict[str, Any]], list[str]]:
    cutoff = analysis_as_of.astimezone(SHANGHAI).replace(tzinfo=None)
    kept = [row for row in rows if row["datetime"] <= cutoff]
    excluded = [row for row in rows if row["datetime"] > cutoff]
    warnings: list[str] = []
    if excluded:
        warnings.append(
            f"Excluded {len(excluded)} row(s) timestamped after analysis as_of "
            f"{analysis_as_of.isoformat(timespec='seconds')}; excluded range "
            f"{excluded[0]['datetime'].isoformat(sep=' ')} to "
            f"{excluded[-1]['datetime'].isoformat(sep=' ')}."
        )
    return kept, warnings


def exclude_incomplete_daily_row(
    rows: list[dict[str, Any]], analysis_as_of: datetime
) -> tuple[list[dict[str, Any]], list[str]]:
    if not rows:
        return rows, []
    local_as_of = analysis_as_of.astimezone(SHANGHAI)
    minute_of_day = local_as_of.hour * 60 + local_as_of.minute
    latest_date = rows[-1]["datetime"].date()
    if latest_date == local_as_of.date() and minute_of_day < DAILY_COMPLETE_MINUTE:
        return rows[:-1], [
            f"Excluded incomplete current-session daily row {latest_date.isoformat()} "
            f"at analysis as_of {analysis_as_of.isoformat(timespec='seconds')}; "
            "use minute data for that session."
        ]
    return rows, []


def pct_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return (current / previous - 1.0) * 100


def rounded(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def max_drawdown(values: list[float]) -> float | None:
    if not values:
        return None
    peak = values[0]
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            worst = min(worst, value / peak - 1.0)
    return worst * 100


def daily_features(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [row["close"] for row in rows]
    highs = [row["high"] for row in rows]
    lows = [row["low"] for row in rows]
    volumes = [row["volume"] for row in rows if row["volume"] is not None]
    latest = rows[-1]

    returns: dict[str, float | None] = {}
    for sessions in (1, 5, 10, 20):
        returns[f"{sessions}d_pct"] = (
            rounded(pct_change(closes[-1], closes[-sessions - 1]))
            if len(closes) > sessions
            else None
        )

    moving_averages = {
        f"ma_{window}": rounded(fmean(closes[-window:])) if len(closes) >= window else None
        for window in (5, 10, 20, 60)
    }

    true_ranges: list[float] = []
    for index in range(1, len(rows)):
        current = rows[index]
        previous_close = rows[index - 1]["close"]
        true_ranges.append(
            max(
                current["high"] - current["low"],
                abs(current["high"] - previous_close),
                abs(current["low"] - previous_close),
            )
        )
    atr14 = fmean(true_ranges[-14:]) if len(true_ranges) >= 14 else None
    latest_volume = latest["volume"]
    prior_volume = [
        row["volume"]
        for row in rows[max(0, len(rows) - 21) : -1]
        if row["volume"] is not None
    ]
    volume_ratio = (
        latest_volume / fmean(prior_volume)
        if latest_volume is not None and prior_volume and fmean(prior_volume)
        else None
    )

    lookback = rows[-20:]
    high20 = max(row["high"] for row in lookback)
    low20 = min(row["low"] for row in lookback)
    range_position = (
        (latest["close"] - low20) / (high20 - low20) * 100 if high20 != low20 else None
    )
    return {
        "mode": "daily",
        "rows": len(rows),
        "as_of": latest["datetime"].isoformat(sep=" "),
        "latest": {
            "open": latest["open"],
            "high": latest["high"],
            "low": latest["low"],
            "close": latest["close"],
            "volume": latest["volume"],
            "amount": latest["amount"],
        },
        "returns": returns,
        "moving_averages": moving_averages,
        "atr_14": rounded(atr14),
        "atr_14_pct_of_close": rounded(atr14 / latest["close"] * 100) if atr14 else None,
        "latest_volume_vs_prior_20_mean": rounded(volume_ratio),
        "range_20d": {
            "high": rounded(high20),
            "low": rounded(low20),
            "close_position_pct": rounded(range_position),
            "max_drawdown_pct": rounded(max_drawdown(closes[-20:])),
        },
    }


def intraday_features(rows: list[dict[str, Any]], volume_unit: str) -> dict[str, Any]:
    latest_date = rows[-1]["datetime"].date()
    rows = [row for row in rows if row["datetime"].date() == latest_date]
    first = rows[0]
    latest = rows[-1]
    high_row = max(rows, key=lambda row: row["high"])
    low_row = min(rows, key=lambda row: row["low"])
    total_volume = sum(row["volume"] or 0 for row in rows)
    total_amount = sum(row["amount"] or 0 for row in rows)
    multiplier = 100 if volume_unit == "lots" else 1
    derived_vwap = (
        total_amount / (total_volume * multiplier)
        if total_amount > 0 and total_volume > 0
        else None
    )
    supplied_avg = latest.get("avg_price")
    vwap = supplied_avg if supplied_avg is not None else derived_vwap

    first_30 = [row for row in rows if row["datetime"].time() <= datetime.strptime("10:00", "%H:%M").time()]
    last_30 = [row for row in rows if row["datetime"].time() >= datetime.strptime("14:30", "%H:%M").time()]
    first30_return = (
        pct_change(first_30[-1]["close"], first["open"]) if first_30 else None
    )
    last30_return = (
        pct_change(latest["close"], last_30[0]["open"]) if last_30 else None
    )
    day_high = high_row["high"]
    day_low = low_row["low"]
    range_position = (
        (latest["close"] - day_low) / (day_high - day_low) * 100
        if day_high != day_low
        else None
    )
    segment_volume = {
        "open_to_10": sum(row["volume"] or 0 for row in first_30),
        "after_14_30": sum(row["volume"] or 0 for row in last_30),
        "total": total_volume,
    }
    return {
        "mode": "intraday",
        "rows": len(rows),
        "from": first["datetime"].isoformat(sep=" "),
        "as_of": latest["datetime"].isoformat(sep=" "),
        "latest": {
            "price": latest["close"],
            "return_from_open_pct": rounded(pct_change(latest["close"], first["open"])),
            "vs_vwap_pct": rounded(pct_change(latest["close"], vwap)) if vwap else None,
            "range_position_pct": rounded(range_position),
            "pullback_from_high_pct": rounded(pct_change(latest["close"], day_high)),
        },
        "session": {
            "open": first["open"],
            "high": day_high,
            "high_time": high_row["datetime"].isoformat(sep=" "),
            "low": day_low,
            "low_time": low_row["datetime"].isoformat(sep=" "),
            "vwap": rounded(vwap),
            "vwap_source": "supplied_avg_price" if supplied_avg is not None else "amount_div_volume",
            "first_30_return_pct": rounded(first30_return),
            "last_30_return_pct": rounded(last30_return),
        },
        "volume": segment_volume,
        "volume_unit": volume_unit,
        "warning": "These are descriptive facts, not proof of wash, test, accumulation, or distribution.",
    }


def main() -> int:
    args = parse_args()
    analysis_as_of = args.as_of or datetime.now(SHANGHAI)
    with args.input.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise SystemExit("Input CSV has no header.")
        headers = list(reader.fieldnames)
        fields = {key: select_field(headers, aliases) for key, aliases in ALIASES.items()}
        required = ("datetime", "open", "high", "low", "close")
        missing = [key for key in required if not fields[key]]
        if missing:
            raise SystemExit(f"Missing required fields: {', '.join(missing)}")
        raw_rows = list(reader)

    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        parsed = {
            "datetime": parse_datetime(str(raw[fields["datetime"]])),
            "open": to_float(raw[fields["open"]]),
            "high": to_float(raw[fields["high"]]),
            "low": to_float(raw[fields["low"]]),
            "close": to_float(raw[fields["close"]]),
            "volume": to_float(raw.get(fields["volume"] or "")),
            "amount": to_float(raw.get(fields["amount"] or "")),
            "avg_price": to_float(raw.get(fields["avg_price"] or "")),
        }
        if any(parsed[key] is None for key in ("open", "high", "low", "close")):
            continue
        rows.append(parsed)
    rows.sort(key=lambda row: row["datetime"])
    if not rows:
        raise SystemExit("No valid OHLC rows.")
    rows, data_quality_warnings = filter_rows_as_of(rows, analysis_as_of)
    if not rows:
        raise SystemExit(
            "No valid OHLC rows at or before analysis as_of "
            f"{analysis_as_of.isoformat(timespec='seconds')}."
        )

    mode = args.mode
    if mode == "auto":
        mode = "intraday" if any(row["datetime"].time().hour for row in rows) else "daily"
    if mode == "daily":
        rows, daily_warnings = exclude_incomplete_daily_row(rows, analysis_as_of)
        data_quality_warnings.extend(daily_warnings)
        if not rows:
            raise SystemExit(
                "Only an incomplete current-session daily row is available at analysis "
                f"as_of {analysis_as_of.isoformat(timespec='seconds')}; "
                "use intraday mode instead."
            )
        payload = daily_features(rows)
    else:
        payload = intraday_features(rows, args.volume_unit)
    payload["analysis_as_of"] = analysis_as_of.isoformat(timespec="seconds")
    if data_quality_warnings:
        payload["data_quality_warning"] = " ".join(data_quality_warnings)
        payload["data_quality_warnings"] = data_quality_warnings
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

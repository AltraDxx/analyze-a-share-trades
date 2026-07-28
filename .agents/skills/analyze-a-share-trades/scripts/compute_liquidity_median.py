#!/usr/bin/env python3
"""Compute auditable 20-session median turnover for explicit A-share candidates.

Inputs may be merged daily-history CSV files or directories containing per-symbol
CSV files. Network fetching is opt-in and is restricted to codes supplied through
--codes/--candidates; the script never discovers and scans the full market.
"""

from __future__ import annotations

import argparse
import csv
import importlib
import json
import math
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Iterable
from zoneinfo import ZoneInfo


WINDOW = 20
DEFAULT_MIN_OBSERVATIONS = 15
DEFAULT_MIN_MEDIAN_AMOUNT = 100_000_000
CODE_FIELDS = ("code", "ts_code", "symbol", "股票代码", "证券代码", "代码")
NAME_FIELDS = ("name", "股票简称", "证券简称", "股票名称", "名称")
DATE_FIELDS = ("date", "datetime", "trade_date", "日期", "交易日期", "时间")
AMOUNT_FIELDS = (
    "amount_rmb",
    "turnover_rmb",
    "成交额",
    "amount",
    "turnover",
)
FETCH_PROVIDERS = ("sina", "eastmoney")


class LiquidityBook:
    """In-memory daily turnover observations and audit provenance."""

    def __init__(self) -> None:
        self.amounts: dict[str, dict[date, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self.names: dict[str, str] = {}
        self.sources: dict[str, set[str]] = defaultdict(set)
        self.unit_assumptions: set[str] = set()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        action="append",
        type=Path,
        default=[],
        help=(
            "Daily-history CSV file or directory. Repeatable. Directories are read "
            "non-recursively using --glob."
        ),
    )
    parser.add_argument("--glob", default="*.csv", help="Directory CSV glob.")
    parser.add_argument(
        "--codes",
        action="append",
        default=[],
        help="Comma-separated explicit candidate codes. Repeatable.",
    )
    parser.add_argument(
        "--candidates",
        action="append",
        type=Path,
        default=[],
        help="CSV containing candidate code and optional name columns. Repeatable.",
    )
    parser.add_argument(
        "--amount-unit",
        choices=("auto", "yuan", "thousand-yuan", "ten-thousand-yuan"),
        default="auto",
        help=(
            "Unit for generic amount/turnover input columns. Auto recognizes explicit "
            "RMB headers and Tushare ts_code/trade_date files."
        ),
    )
    parser.add_argument(
        "--as-of",
        help=(
            "Latest analysis date in YYYY-MM-DD. A current in-session date is "
            "automatically excluded as an incomplete daily bar."
        ),
    )
    parser.add_argument(
        "--min-observations",
        type=int,
        default=DEFAULT_MIN_OBSERVATIONS,
        help="Minimum valid completed sessions required. Default: 15.",
    )
    parser.add_argument(
        "--min-median-amount",
        type=float,
        default=DEFAULT_MIN_MEDIAN_AMOUNT,
        help="Liquidity threshold in RMB. Default: 100,000,000.",
    )
    parser.add_argument(
        "--fetch-missing",
        action="store_true",
        help=(
            "Fetch missing daily history only for explicit --codes/--candidates. "
            "No full-market discovery is performed."
        ),
    )
    parser.add_argument(
        "--fetch-provider",
        choices=("auto", "sina", "eastmoney"),
        default="auto",
        help="AkShare daily-history provider; auto tries Sina then Eastmoney once each.",
    )
    parser.add_argument("--fetch-start", help="Optional fetch start date YYYY-MM-DD.")
    parser.add_argument("--fetch-end", help="Optional fetch end date YYYY-MM-DD.")
    parser.add_argument(
        "--max-fetch-candidates",
        type=int,
        default=50,
        help="Safety cap for one network-fetch run. Default: 50 explicit candidates.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output CSV. Defaults to stdout; file output also writes metadata.",
    )
    return parser.parse_args()


def extract_code(value: Any) -> str:
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", str(value or ""))
    return match.group(1) if match else ""


def parse_date(value: Any) -> date:
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported date: {value!r}")


def choose_field(headers: Iterable[str], aliases: tuple[str, ...]) -> str | None:
    normalized = {str(header).strip().lower(): str(header) for header in headers}
    for alias in aliases:
        match = normalized.get(alias.lower())
        if match:
            return match
    return None


def unit_multiplier(
    amount_field: str,
    headers: Iterable[str],
    requested_unit: str,
) -> tuple[float, str]:
    if requested_unit != "auto":
        multiplier = {
            "yuan": 1.0,
            "thousand-yuan": 1_000.0,
            "ten-thousand-yuan": 10_000.0,
        }[requested_unit]
        return multiplier, f"explicit_{requested_unit}"

    normalized_field = amount_field.strip().lower()
    normalized_headers = {str(header).strip().lower() for header in headers}
    if normalized_field in {"amount_rmb", "turnover_rmb"} or amount_field == "成交额":
        return 1.0, "explicit_rmb_header"
    if "ts_code" in normalized_headers and "trade_date" in normalized_headers:
        return 1_000.0, "tushare_amount_thousand_yuan"
    return 1.0, "generic_amount_assumed_yuan"


def parse_amount(value: Any, multiplier: float) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text or text.lower() in {"nan", "none", "null", "-", "--"}:
        return None
    suffix_multiplier = None
    if text.endswith("亿"):
        suffix_multiplier = 100_000_000.0
        text = text[:-1]
    elif text.endswith("万"):
        suffix_multiplier = 10_000.0
        text = text[:-1]
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    amount = number * (suffix_multiplier if suffix_multiplier else multiplier)
    return amount if amount > 0 else None


def completed_through(as_of: date, now: datetime | None = None) -> date:
    current = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    if as_of == current.date() and (current.hour, current.minute) < (15, 10):
        return as_of - timedelta(days=1)
    return as_of


def code_from_path(path: Path) -> str:
    return extract_code(path.stem)


def add_observation(
    book: LiquidityBook,
    code: str,
    day: date,
    amount_rmb: float,
    source: str,
    name: str = "",
) -> None:
    book.amounts[code][day].append(amount_rmb)
    book.sources[code].add(source)
    if name:
        book.names[code] = name


def iter_input_paths(inputs: list[Path], pattern: str) -> list[Path]:
    paths: list[Path] = []
    for item in inputs:
        if item.is_dir():
            paths.extend(path for path in item.glob(pattern) if path.is_file())
        elif item.is_file():
            paths.append(item)
        else:
            raise ValueError(f"Input path does not exist: {item}")
    return sorted(set(path.resolve() for path in paths))


def read_daily_csv(
    path: Path,
    book: LiquidityBook,
    requested_unit: str,
    allowed_codes: set[str] | None,
) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV has no header: {path}")
        headers = list(reader.fieldnames)
        code_field = choose_field(headers, CODE_FIELDS)
        name_field = choose_field(headers, NAME_FIELDS)
        date_field = choose_field(headers, DATE_FIELDS)
        amount_field = choose_field(headers, AMOUNT_FIELDS)
        inferred_code = code_from_path(path)
        if not code_field and not inferred_code:
            raise ValueError(
                f"CSV needs a code column or six-digit code in filename: {path}"
            )
        if not date_field or not amount_field:
            raise ValueError(
                f"CSV needs date and amount fields: {path}; headers={headers}"
            )
        multiplier, assumption = unit_multiplier(
            amount_field, headers, requested_unit
        )
        book.unit_assumptions.add(assumption)
        count = 0
        for row in reader:
            code = extract_code(row.get(code_field, "")) if code_field else inferred_code
            if not code or (allowed_codes is not None and code not in allowed_codes):
                continue
            try:
                day = parse_date(row.get(date_field, ""))
            except ValueError:
                continue
            amount = parse_amount(row.get(amount_field), multiplier)
            if amount is None:
                continue
            name = str(row.get(name_field, "")).strip() if name_field else ""
            add_observation(book, code, day, amount, str(path), name)
            count += 1
    return count


def read_candidates(path: Path) -> tuple[set[str], dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError(f"Candidate CSV has no header: {path}")
        code_field = choose_field(reader.fieldnames, CODE_FIELDS)
        name_field = choose_field(reader.fieldnames, NAME_FIELDS)
        if not code_field:
            raise ValueError(f"Candidate CSV needs a code field: {path}")
        codes: set[str] = set()
        names: dict[str, str] = {}
        for row in reader:
            code = extract_code(row.get(code_field))
            if not code:
                continue
            codes.add(code)
            if name_field and str(row.get(name_field, "")).strip():
                names[code] = str(row[name_field]).strip()
    return codes, names


def explicit_candidates(args: argparse.Namespace) -> tuple[set[str], dict[str, str]]:
    codes = {
        code
        for group in args.codes
        for value in group.split(",")
        if (code := extract_code(value))
    }
    names: dict[str, str] = {}
    for path in args.candidates:
        file_codes, file_names = read_candidates(path)
        codes.update(file_codes)
        names.update(file_names)
    return codes, names


def market_symbol(code: str) -> str:
    if code.startswith(("5", "6", "9")):
        return f"sh{code}"
    if code.startswith(("0", "1", "2", "3")):
        return f"sz{code}"
    raise ValueError(f"Cannot infer Shanghai/Shenzhen market for code: {code}")


def fetch_candidate_daily(
    code: str,
    start: date,
    end: date,
    provider: str,
    ak_module: Any | None = None,
) -> tuple[Any, str, list[dict[str, str]]]:
    ak = ak_module or importlib.import_module("akshare")
    providers = FETCH_PROVIDERS if provider == "auto" else (provider,)
    attempts: list[dict[str, str]] = []
    for current_provider in providers:
        if current_provider == "sina":
            api_name = "akshare.stock_zh_a_daily"
            function = getattr(ak, "stock_zh_a_daily")
            kwargs = {
                "symbol": market_symbol(code),
                "start_date": start.strftime("%Y%m%d"),
                "end_date": end.strftime("%Y%m%d"),
                "adjust": "",
            }
        else:
            api_name = "akshare.stock_zh_a_hist"
            function = getattr(ak, "stock_zh_a_hist")
            kwargs = {
                "symbol": code,
                "period": "daily",
                "start_date": start.strftime("%Y%m%d"),
                "end_date": end.strftime("%Y%m%d"),
                "adjust": "",
                "timeout": 10,
            }
        try:
            frame = function(**kwargs)
            if not hasattr(frame, "__len__") or len(frame) == 0:
                raise ValueError("source returned an empty dataset")
            attempts.append(
                {
                    "provider": current_provider,
                    "api": api_name,
                    "status": "success",
                }
            )
            return frame, current_provider, attempts
        except Exception as exc:  # AkShare providers raise heterogeneous exceptions
            attempts.append(
                {
                    "provider": current_provider,
                    "api": api_name,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                }
            )
    attempted = ", ".join(
        f"{item['api']}[{item['error_type']}]" for item in attempts
    )
    raise RuntimeError(f"candidate {code} fetch failed: {attempted}")


def ingest_fetched_frame(
    book: LiquidityBook,
    code: str,
    frame: Any,
    provider: str,
) -> int:
    records = frame.to_dict(orient="records")
    headers = list(getattr(frame, "columns", []))
    date_field = choose_field(headers, DATE_FIELDS)
    amount_field = choose_field(headers, AMOUNT_FIELDS)
    if not date_field or not amount_field:
        raise ValueError(
            f"Fetched {provider} frame lacks date/amount fields: {headers}"
        )
    count = 0
    for row in records:
        try:
            day = parse_date(row.get(date_field))
        except ValueError:
            continue
        amount = parse_amount(row.get(amount_field), 1.0)
        if amount is None:
            continue
        add_observation(
            book,
            code,
            day,
            amount,
            f"akshare:{provider}",
            book.names.get(code, ""),
        )
        count += 1
    return count


def valid_dates(book: LiquidityBook, code: str, through: date) -> list[date]:
    return sorted(day for day in book.amounts.get(code, {}) if day <= through)


def summarize_code(
    book: LiquidityBook,
    code: str,
    through: date,
    min_observations: int,
    min_median_amount: float,
    fetch_status: str = "not_requested",
    fetch_error: str = "",
) -> dict[str, Any]:
    dates = valid_dates(book, code, through)
    selected = dates[-WINDOW:]
    conflicts: list[date] = []
    values: list[float] = []
    for day in selected:
        observations = book.amounts[code][day]
        reference = observations[0]
        tolerance = max(0.01, abs(reference) * 1e-9)
        if any(abs(value - reference) > tolerance for value in observations[1:]):
            conflicts.append(day)
        values.append(reference)

    reason = "ok"
    verified = len(values) >= min_observations and not conflicts
    median_amount: float | None = median(values) if verified else None
    if conflicts:
        reason = "conflicting_amounts"
    elif not values:
        reason = "no_data"
    elif len(values) < min_observations:
        reason = "insufficient_valid_days"
    elif len(values) < WINDOW:
        reason = "ok_partial_window"

    return {
        "code": code,
        "name": book.names.get(code, ""),
        "amount_20d_median": median_amount,
        "valid_days": len(values),
        "window_start": selected[0].isoformat() if selected else "",
        "as_of": selected[-1].isoformat() if selected else "",
        "liquidity_verified": str(verified).lower(),
        "meets_minimum": str(
            bool(verified and median_amount is not None and median_amount >= min_median_amount)
        ).lower(),
        "reason": reason,
        "conflict_dates": ";".join(day.isoformat() for day in conflicts),
        "fetch_status": fetch_status,
        "fetch_error": fetch_error,
        "sources": ";".join(sorted(book.sources.get(code, set()))),
    }


def write_rows(rows: list[dict[str, Any]], output: Path | None) -> None:
    fieldnames = [
        "code",
        "name",
        "amount_20d_median",
        "valid_days",
        "window_start",
        "as_of",
        "liquidity_verified",
        "meets_minimum",
        "reason",
        "conflict_dates",
        "fetch_status",
        "fetch_error",
        "sources",
    ]
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        handle = output.open("w", encoding="utf-8-sig", newline="")
    else:
        handle = sys.stdout
    try:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    finally:
        if output:
            handle.close()


def main() -> int:
    args = parse_args()
    if not 1 <= args.min_observations <= WINDOW:
        raise SystemExit(f"Require 1 <= --min-observations <= {WINDOW}.")
    if args.max_fetch_candidates < 1:
        raise SystemExit("--max-fetch-candidates must be >= 1.")
    if args.output and args.output.suffix.lower() != ".csv":
        raise SystemExit("--output must end in .csv.")

    analysis_day = (
        parse_date(args.as_of)
        if args.as_of
        else datetime.now(ZoneInfo("Asia/Shanghai")).date()
    )
    through = completed_through(analysis_day)
    codes, candidate_names = explicit_candidates(args)
    if args.fetch_missing and not codes:
        raise SystemExit(
            "--fetch-missing requires explicit --codes or --candidates; "
            "full-market blind fetching is disabled."
        )
    if args.fetch_missing and len(codes) > args.max_fetch_candidates:
        raise SystemExit(
            f"Refusing to fetch {len(codes)} candidates; safety cap is "
            f"{args.max_fetch_candidates}. Raise --max-fetch-candidates explicitly "
            "only after narrowing the universe."
        )
    if not args.input and not args.fetch_missing:
        raise SystemExit("Provide --input or use --fetch-missing with explicit candidates.")

    book = LiquidityBook()
    book.names.update(candidate_names)
    input_paths = iter_input_paths(args.input, args.glob)
    allowed_codes = codes or None
    rows_read = 0
    for path in input_paths:
        rows_read += read_daily_csv(
            path,
            book,
            args.amount_unit,
            allowed_codes,
        )

    if not codes:
        codes = set(book.amounts)

    fetch_status: dict[str, str] = {code: "not_requested" for code in codes}
    fetch_errors: dict[str, str] = {}
    fetch_attempts: dict[str, list[dict[str, str]]] = {}
    if args.fetch_missing:
        fetch_end = min(
            parse_date(args.fetch_end) if args.fetch_end else through,
            through,
        )
        fetch_start = (
            parse_date(args.fetch_start)
            if args.fetch_start
            else fetch_end - timedelta(days=120)
        )
        if fetch_start > fetch_end:
            raise SystemExit("--fetch-start must not be after the completed fetch end.")
        for code in sorted(codes):
            if len(valid_dates(book, code, through)) >= WINDOW:
                fetch_status[code] = "not_needed"
                continue
            try:
                frame, provider, attempts = fetch_candidate_daily(
                    code,
                    fetch_start,
                    fetch_end,
                    args.fetch_provider,
                )
                ingest_fetched_frame(book, code, frame, provider)
                fetch_status[code] = "success"
                fetch_attempts[code] = attempts
            except Exception as exc:
                fetch_status[code] = "failed"
                fetch_errors[code] = f"{type(exc).__name__}: {exc}"

    output_rows = [
        summarize_code(
            book,
            code,
            through,
            args.min_observations,
            args.min_median_amount,
            fetch_status.get(code, "not_requested"),
            fetch_errors.get(code, ""),
        )
        for code in sorted(codes)
    ]
    write_rows(output_rows, args.output)

    metadata = {
        "window": WINDOW,
        "min_observations": args.min_observations,
        "min_median_amount_rmb": args.min_median_amount,
        "analysis_date": analysis_day.isoformat(),
        "completed_through": through.isoformat(),
        "fetched_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(
            timespec="seconds"
        ),
        "input_files": len(input_paths),
        "input_rows_accepted": rows_read,
        "candidate_count": len(codes),
        "network_fetch_enabled": args.fetch_missing,
        "fetch_attempts": fetch_attempts,
        "unit_assumptions": sorted(book.unit_assumptions),
    }
    if args.output:
        sidecar = args.output.with_suffix(args.output.suffix + ".meta.json")
        sidecar.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    else:
        print(json.dumps(metadata, ensure_ascii=False), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

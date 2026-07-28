#!/usr/bin/env python3
"""Filter a stock table to the skill's eligible Shanghai/Shenzhen main-board universe."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any


FIELD_ALIASES = {
    "code": ("code", "ts_code", "symbol", "股票代码", "证券代码", "代码"),
    "name": ("name", "股票简称", "证券简称", "股票名称", "名称"),
    "status": ("status", "trade_status", "交易状态", "状态"),
    "median_amount": (
        "amount_20d_median",
        "median_amount_20d",
        "20日成交额中位数",
        "近20日成交额中位数",
        "20日平均成交额",
    ),
    "amount": ("amount", "成交额", "当日成交额"),
}

SH_MAIN_PREFIXES = ("600", "601", "603", "605")
SZ_MAIN_PREFIXES = ("000", "001", "002", "003")
HALT_WORDS = ("停牌", "暂停", "halted", "suspended")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Filter to liquid, non-ST Shanghai/Shenzhen main-board A-shares."
    )
    parser.add_argument("input", type=Path, help="Input CSV file.")
    parser.add_argument(
        "--min-median-amount",
        type=float,
        default=100_000_000,
        help="Minimum 20-day median daily turnover in RMB. Default: 100,000,000.",
    )
    parser.add_argument(
        "--allow-current-amount-fallback",
        action="store_true",
        help="Use the current-day amount only when the 20-day median is unavailable.",
    )
    parser.add_argument(
        "--allow-missing-liquidity",
        action="store_true",
        help="Keep rows whose liquidity field is missing, but mark the reason.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON output path. Defaults to stdout.",
    )
    return parser.parse_args()


def choose_field(fieldnames: list[str], aliases: tuple[str, ...]) -> str | None:
    normalized = {name.strip().lower(): name for name in fieldnames}
    for alias in aliases:
        match = normalized.get(alias.lower())
        if match:
            return match
    return None


def extract_code(value: Any) -> str:
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", str(value or ""))
    return match.group(1) if match else ""


def parse_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text.lower() in {"nan", "none", "null", "-", "--"}:
        return None
    multiplier = 1.0
    if text.endswith("亿"):
        multiplier = 100_000_000
        text = text[:-1]
    elif text.endswith("万"):
        multiplier = 10_000
        text = text[:-1]
    try:
        return float(text.rstrip("%")) * multiplier
    except ValueError:
        return None


def is_main_board(code: str) -> bool:
    return code.startswith(SH_MAIN_PREFIXES + SZ_MAIN_PREFIXES)


def is_st_or_delisting(name: str) -> bool:
    normalized = re.sub(r"\s+", "", name.upper())
    return bool(re.match(r"^S?\*?ST", normalized)) or "退" in normalized


def is_halted(status: str) -> bool:
    normalized = status.strip().lower()
    return any(word in normalized for word in HALT_WORDS)


def main() -> int:
    args = parse_args()
    with args.input.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise SystemExit("Input CSV has no header.")
        fieldnames = list(reader.fieldnames)
        fields = {
            key: choose_field(fieldnames, aliases)
            for key, aliases in FIELD_ALIASES.items()
        }
        if not fields["code"] or not fields["name"]:
            raise SystemExit(
                "Input must contain code/name fields. "
                f"Detected headers: {', '.join(fieldnames)}"
            )
        rows = list(reader)

    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for row in rows:
        code = extract_code(row.get(fields["code"] or "", ""))
        name = str(row.get(fields["name"] or "", "")).strip()
        status = str(row.get(fields["status"] or "", "")).strip()
        liquidity = parse_number(row.get(fields["median_amount"] or "", ""))
        liquidity_source = fields["median_amount"]
        if liquidity is None and args.allow_current_amount_fallback:
            liquidity = parse_number(row.get(fields["amount"] or "", ""))
            liquidity_source = fields["amount"]

        reasons: list[str] = []
        warnings: list[str] = []
        if not code or not is_main_board(code):
            reasons.append("not_sh_sz_main_board")
        if is_st_or_delisting(name):
            reasons.append("st_or_delisting")
        if status and is_halted(status):
            reasons.append("halted")
        elif not status:
            warnings.append("trading_status_missing")
        if liquidity is None:
            if args.allow_missing_liquidity:
                warnings.append("liquidity_unverified")
            else:
                reasons.append("liquidity_missing")
        elif liquidity < args.min_median_amount:
            reasons.append("liquidity_below_threshold")

        item = {
            "code": code,
            "name": name,
            "liquidity_rmb": liquidity,
            "liquidity_source": liquidity_source,
            "reasons": reasons,
            "warnings": warnings,
            "raw": row,
        }
        if reasons:
            excluded.append(item)
        else:
            eligible.append(item)

    payload = {
        "rules": {
            "allowed_prefixes": list(SH_MAIN_PREFIXES + SZ_MAIN_PREFIXES),
            "exclude_st_delisting": True,
            "exclude_halted": True,
            "min_median_amount_rmb": args.min_median_amount,
            "allow_missing_liquidity": args.allow_missing_liquidity,
            "allow_current_amount_fallback": args.allow_current_amount_fallback,
        },
        "counts": {
            "input": len(rows),
            "eligible": len(eligible),
            "excluded": len(excluded),
        },
        "eligible": eligible,
        "excluded": excluded,
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

#!/usr/bin/env python3
"""Fetch raw A-share datasets from AkShare or Tushare with audit metadata.

Official references:
- https://akshare.akfamily.xyz/data/stock/stock.html
- https://tushare.pro/document/2

Credentials prefer environment variables and otherwise fall back to the
repository-root ``my_api_key.md`` through the local credential loader.
"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from credential_loader import CredentialError, load_credential


AK_DATASETS = {
    "snapshot",
    "index-snapshot",
    "daily",
    "minute",
    "trade-calendar",
    "industry-list",
    "industry-constituents",
    "industry-history",
    "concept-list",
    "concept-constituents",
    "concept-history",
    "news",
}
AK_SNAPSHOT_PROVIDERS = ("eastmoney", "tencent", "sina")
AK_INDEX_SNAPSHOT_PROVIDERS = ("sina", "eastmoney")
TS_DATASETS = {
    "stock-basic",
    "daily",
    "daily-basic",
    "trade-calendar",
    "moneyflow",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("akshare", "tushare"), required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--symbol", help="Six-digit code, TS code, or board name.")
    parser.add_argument("--start", help="Start date/time accepted by the source API.")
    parser.add_argument("--end", help="End date/time accepted by the source API.")
    parser.add_argument("--period", default="daily")
    parser.add_argument("--adjust", default="qfq")
    parser.add_argument("--trade-date", help="Tushare YYYYMMDD trade date.")
    parser.add_argument(
        "--ak-provider",
        choices=("auto", "eastmoney", "tencent", "sina"),
        default="auto",
        help=(
            "AkShare provider for snapshot/index-snapshot. With auto, each provider "
            "in the fixed fallback order is attempted once."
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help=(
            "Additional retries for a single provider or non-fallback dataset. "
            "AkShare auto fallback never repeats the provider chain."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def now_shanghai() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def current_session_daily_warning(dataset: str, end: str | None) -> str | None:
    if dataset != "daily" or not end:
        return None
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    digits = end.replace("-", "")[:8]
    if digits == now.strftime("%Y%m%d") and now.hour * 60 + now.minute < 15 * 60 + 10:
        return (
            "The current-date daily row may be an incomplete live-session snapshot. "
            "Do not treat it as a completed daily bar; use minute data for today."
        )
    return None


def require(value: str | None, label: str) -> str:
    if not value:
        raise ValueError(f"--{label} is required for this dataset.")
    return value


def akshare_call_specs(
    args: argparse.Namespace,
) -> list[tuple[str, str, dict[str, Any]]]:
    """Return the deterministic AkShare provider/API order without making requests."""
    dataset = args.dataset
    if dataset not in AK_DATASETS:
        raise ValueError(f"Unsupported AkShare dataset: {dataset}")

    if dataset == "snapshot":
        providers = (
            AK_SNAPSHOT_PROVIDERS
            if args.ak_provider == "auto"
            else (args.ak_provider,)
        )
        provider_calls = {
            "eastmoney": ("stock_zh_a_spot_em", {}),
            "tencent": ("stock_zh_a_spot_tx", {}),
            "sina": ("stock_zh_a_spot", {}),
        }
        return [
            (provider, *provider_calls[provider])
            for provider in providers
            if provider in provider_calls
        ]

    if dataset == "index-snapshot":
        providers = (
            AK_INDEX_SNAPSHOT_PROVIDERS
            if args.ak_provider == "auto"
            else (args.ak_provider,)
        )
        provider_calls = {
            "sina": ("stock_zh_index_spot_sina", {}),
            "eastmoney": (
                "stock_zh_index_spot_em",
                {"symbol": args.symbol or "沪深重要指数"},
            ),
        }
        specs = [
            (provider, *provider_calls[provider])
            for provider in providers
            if provider in provider_calls
        ]
        if not specs:
            raise ValueError(
                "index-snapshot supports AkShare providers: "
                f"{', '.join(AK_INDEX_SNAPSHOT_PROVIDERS)}."
            )
        return specs

    if args.ak_provider != "auto":
        raise ValueError(
            "--ak-provider is only supported for AkShare snapshot/index-snapshot."
        )

    if dataset == "daily":
        return [
            (
                "eastmoney",
                "stock_zh_a_hist",
                {
                    "symbol": require(args.symbol, "symbol").split(".")[0],
                    "period": args.period,
                    "start_date": require(args.start, "start").replace("-", ""),
                    "end_date": require(args.end, "end").replace("-", ""),
                    "adjust": args.adjust,
                },
            )
        ]
    if dataset == "minute":
        return [
            (
                "eastmoney",
                "stock_zh_a_hist_min_em",
                {
                    "symbol": require(args.symbol, "symbol").split(".")[0],
                    "start_date": require(args.start, "start"),
                    "end_date": require(args.end, "end"),
                    "period": args.period if args.period != "daily" else "1",
                    "adjust": args.adjust,
                },
            )
        ]
    if dataset == "trade-calendar":
        return [("sina", "tool_trade_date_hist_sina", {})]
    if dataset == "industry-list":
        return [("eastmoney", "stock_board_industry_name_em", {})]
    if dataset == "industry-constituents":
        return [
            (
                "eastmoney",
                "stock_board_industry_cons_em",
                {"symbol": require(args.symbol, "symbol")},
            )
        ]
    if dataset == "industry-history":
        return [
            (
                "eastmoney",
                "stock_board_industry_hist_em",
                {
                    "symbol": require(args.symbol, "symbol"),
                    "start_date": require(args.start, "start").replace("-", ""),
                    "end_date": require(args.end, "end").replace("-", ""),
                    "period": args.period,
                    "adjust": args.adjust,
                },
            )
        ]
    if dataset == "concept-list":
        return [("eastmoney", "stock_board_concept_name_em", {})]
    if dataset == "concept-constituents":
        return [
            (
                "eastmoney",
                "stock_board_concept_cons_em",
                {"symbol": require(args.symbol, "symbol")},
            )
        ]
    if dataset == "concept-history":
        return [
            (
                "eastmoney",
                "stock_board_concept_hist_em",
                {
                    "symbol": require(args.symbol, "symbol"),
                    "start_date": require(args.start, "start").replace("-", ""),
                    "end_date": require(args.end, "end").replace("-", ""),
                    "period": args.period,
                    "adjust": args.adjust,
                },
            )
        ]
    return [
        (
            "eastmoney",
            "stock_news_em",
            {"symbol": require(args.symbol, "symbol").split(".")[0]},
        )
    ]


def akshare_call(
    args: argparse.Namespace,
    ak_module: Any | None = None,
) -> tuple[Any, str, dict[str, Any], dict[str, Any]]:
    try:
        ak = ak_module or importlib.import_module("akshare")
    except ImportError as exc:
        raise RuntimeError(
            "AkShare is not installed. Install it in the repository virtual environment."
        ) from exc

    attempts: list[dict[str, str]] = []
    for provider, call_name, kwargs in akshare_call_specs(args):
        api_name = f"akshare.{call_name}"
        try:
            function = getattr(ak, call_name)
            frame = function(**kwargs)
            if not hasattr(frame, "__len__") or len(frame) == 0:
                raise ValueError("source returned an empty dataset")
            attempts.append(
                {"provider": provider, "api": api_name, "status": "success"}
            )
            return frame, api_name, kwargs, {
                "provider": provider,
                "attempted_apis": attempts,
                "fallback_used": len(attempts) > 1,
            }
        except Exception as exc:  # AkShare providers raise heterogeneous exceptions
            attempts.append(
                {
                    "provider": provider,
                    "api": api_name,
                    "status": "failed",
                    "error_type": type(exc).__name__,
                }
            )

    attempted = ", ".join(
        f"{item['api']}[{item['error_type']}]" for item in attempts
    )
    raise RuntimeError(f"All AkShare provider attempts failed: {attempted}")


def tushare_call(
    args: argparse.Namespace,
    ts_module: Any | None = None,
    credential_file: Path | None = None,
    environ: dict[str, str] | None = None,
) -> tuple[Any, str, dict[str, Any]]:
    try:
        ts = ts_module or importlib.import_module("tushare")
    except ImportError as exc:
        raise RuntimeError(
            "Tushare is not installed. Install it in the repository virtual environment."
        ) from exc
    token = load_credential(
        "TUSHARE_TOKEN",
        credential_file=credential_file,
        environ=environ,
    )
    if args.dataset not in TS_DATASETS:
        raise ValueError(f"Unsupported Tushare dataset: {args.dataset}")
    pro = ts.pro_api(token)
    kwargs: dict[str, Any]

    if args.dataset == "stock-basic":
        call_name = "stock_basic"
        kwargs = {
            "exchange": "",
            "list_status": "L",
            "fields": "ts_code,symbol,name,area,industry,market,list_date",
        }
    elif args.dataset == "daily":
        call_name = "daily"
        kwargs = {
            "ts_code": require(args.symbol, "symbol"),
            "start_date": require(args.start, "start").replace("-", ""),
            "end_date": require(args.end, "end").replace("-", ""),
        }
    elif args.dataset == "daily-basic":
        call_name = "daily_basic"
        kwargs = {
            "ts_code": args.symbol,
            "trade_date": args.trade_date,
            "start_date": args.start.replace("-", "") if args.start else None,
            "end_date": args.end.replace("-", "") if args.end else None,
        }
        kwargs = {key: value for key, value in kwargs.items() if value}
    elif args.dataset == "trade-calendar":
        call_name = "trade_cal"
        kwargs = {
            "exchange": "SSE",
            "start_date": require(args.start, "start").replace("-", ""),
            "end_date": require(args.end, "end").replace("-", ""),
            "is_open": "1",
        }
    else:
        call_name = "moneyflow"
        kwargs = {
            "ts_code": args.symbol,
            "trade_date": args.trade_date,
            "start_date": args.start.replace("-", "") if args.start else None,
            "end_date": args.end.replace("-", "") if args.end else None,
        }
        kwargs = {key: value for key, value in kwargs.items() if value}

    function = getattr(pro, call_name)
    return function(**kwargs), f"tushare.pro.{call_name}", kwargs


def dataframe_records(frame: Any) -> list[dict[str, Any]]:
    if not hasattr(frame, "to_json"):
        raise TypeError(f"Expected a pandas-like DataFrame, got {type(frame).__name__}.")
    return json.loads(frame.to_json(orient="records", force_ascii=False, date_format="iso"))


def normalize_dataset_time(value: Any) -> str:
    text = str(value).strip()
    if re.fullmatch(r"\d{2}:\d{2}(?::\d{2})?", text):
        if len(text) == 5:
            text += ":00"
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        return f"{today}T{text}+08:00"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return parsed.isoformat(timespec="seconds")


def infer_dataset_time(frame: Any) -> tuple[str | None, str | None]:
    candidates = (
        "时间",
        "时间戳",
        "datetime",
        "time",
        "日期",
        "trade_date",
        "cal_date",
        "报告日",
        "发布时间",
        "公告时间",
    )
    columns = {str(column).strip().lower(): column for column in getattr(frame, "columns", [])}
    for candidate in candidates:
        column = columns.get(candidate.lower())
        if column is None:
            continue
        values = frame[column].dropna()
        if len(values):
            return normalize_dataset_time(values.max()), str(column)
    return None, None


def write_output(
    frame: Any,
    output: Path,
    metadata: dict[str, Any],
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".csv":
        frame.to_csv(output, index=False, encoding="utf-8-sig")
        sidecar = output.with_suffix(output.suffix + ".meta.json")
        sidecar.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return
    if output.suffix.lower() != ".json":
        raise ValueError("--output must end in .csv or .json.")
    payload = {"metadata": metadata, "records": dataframe_records(frame)}
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    if args.retries < 0:
        raise SystemExit("--retries must be >= 0.")
    allowed = AK_DATASETS if args.source == "akshare" else TS_DATASETS
    if args.dataset not in allowed:
        raise SystemExit(
            f"{args.dataset!r} is unsupported for {args.source}. "
            f"Choose from: {', '.join(sorted(allowed))}"
        )
    safe_plan = {
        "source": args.source,
        "dataset": args.dataset,
        "symbol": args.symbol,
        "start": args.start,
        "end": args.end,
        "period": args.period,
        "adjust": args.adjust,
        "trade_date": args.trade_date,
        "ak_provider": args.ak_provider,
        "output": str(args.output),
    }
    if args.source == "akshare":
        safe_plan["provider_attempt_order"] = [
            {
                "provider": provider,
                "api": f"akshare.{call_name}",
                "request": kwargs,
            }
            for provider, call_name, kwargs in akshare_call_specs(args)
        ]
    if args.dry_run:
        print(json.dumps({"dry_run": True, "plan": safe_plan}, ensure_ascii=False, indent=2))
        return 0

    caller = akshare_call if args.source == "akshare" else tushare_call
    bounded_auto_fallback = (
        args.source == "akshare"
        and args.ak_provider == "auto"
        and args.dataset in {"snapshot", "index-snapshot"}
    )
    max_attempts = 1 if bounded_auto_fallback else args.retries + 1
    last_error: Exception | None = None
    for attempt in range(max_attempts):
        try:
            call_result = caller(args)
            if args.source == "akshare":
                frame, api_name, kwargs, provider_metadata = call_result
            else:
                frame, api_name, kwargs = call_result
                provider_metadata = {
                    "provider": "tushare",
                    "attempted_apis": [
                        {
                            "provider": "tushare",
                            "api": api_name,
                            "status": "success",
                        }
                    ],
                    "fallback_used": False,
                }
            as_of, as_of_field = infer_dataset_time(frame)
            session_warning = current_session_daily_warning(args.dataset, args.end)
            metadata = {
                "source": args.source,
                "provider": provider_metadata["provider"],
                "api": api_name,
                "attempted_apis": provider_metadata["attempted_apis"],
                "fallback_used": provider_metadata["fallback_used"],
                "dataset": args.dataset,
                "request": kwargs,
                "as_of": as_of,
                "as_of_field": as_of_field,
                "published_at": None,
                "fetched_at": now_shanghai(),
                "row_count": len(frame),
                "freshness": "unassessed",
                "quality": (
                    "current_session_may_be_partial"
                    if session_warning
                    else "raw_source_data_unvalidated"
                ),
                "warning": session_warning
                or "Verify field units, freshness, and source semantics before trading use.",
            }
            write_output(frame, args.output, metadata)
            print(
                json.dumps(
                    {
                        "output": str(args.output),
                        "api": api_name,
                        "provider": provider_metadata["provider"],
                        "fallback_used": provider_metadata["fallback_used"],
                        "rows": len(frame),
                        "fetched_at": metadata["fetched_at"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        except CredentialError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except Exception as exc:  # source libraries raise heterogeneous exceptions
            last_error = exc
            if attempt + 1 < max_attempts:
                time.sleep(1.5 * (2**attempt))
    assert last_error is not None
    print(f"Fetch failed: {type(last_error).__name__}: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

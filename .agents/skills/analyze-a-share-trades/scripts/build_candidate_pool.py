#!/usr/bin/env python3
"""Build a deterministic, unranked deep-analysis candidate pool."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from filter_universe import extract_code


SHANGHAI = ZoneInfo("Asia/Shanghai")

FIELD_ALIASES = {
    "code": ("code", "ts_code", "symbol", "股票代码", "证券代码", "代码"),
    "focus": (
        "focus",
        "direction",
        "theme",
        "mainline",
        "event",
        "关注方向",
        "主线",
        "主题",
        "事件",
    ),
    "mapping_status": (
        "mapping_status",
        "exposure_status",
        "mapping",
        "映射状态",
        "暴露状态",
    ),
    "source_kind": (
        "source_kind",
        "source_type",
        "来源类型",
    ),
    "industry_cluster": (
        "industry_cluster",
        "cluster",
        "产业簇",
        "行业簇",
    ),
    "segment": (
        "segment",
        "industry_segment",
        "细分环节",
        "产业环节",
    ),
    "industry_role": (
        "industry_role",
        "产业角色",
    ),
    "market_role": (
        "market_role",
        "盘面角色",
    ),
    "leader_type": (
        "leader_type",
        "leader_role",
        "龙头类型",
        "龙头角色",
    ),
    "evidence_ref": (
        "evidence_ref",
        "evidence",
        "source_ref",
        "证据",
        "证据引用",
    ),
}

QUALIFIED_MAPPING_STATUSES = {
    "verified",
    "confirmed",
    "direct",
    "direct_beneficiary",
    "strong",
    "已核验",
    "已确认",
    "直接受益",
    "直接暴露",
    "强映射",
}
DEFERRED_MAPPING_STATUSES = {
    "pending",
    "unverified",
    "needs_verification",
    "待核验",
    "待确认",
}
WEAK_MAPPING_STATUSES = {
    "weak",
    "weak_mapping",
    "pseudo",
    "弱映射",
    "伪概念",
}
PERMITTED_SOURCE_KINDS = {
    "mainline",
    "emerging_line",
    "event",
    "independent_event",
    "company",
    "主线",
    "新方向",
    "事件",
    "公司",
}
UNIVERSE_WARNINGS_REQUIRING_VERIFICATION = {
    "liquidity_unverified",
    "trading_status_missing",
}
QUALIFIED_MAPPING_KEYS = frozenset(
    item.casefold() for item in QUALIFIED_MAPPING_STATUSES
)
DEFERRED_MAPPING_KEYS = frozenset(
    item.casefold() for item in DEFERRED_MAPPING_STATUSES
)
WEAK_MAPPING_KEYS = frozenset(item.casefold() for item in WEAK_MAPPING_STATUSES)
PERMITTED_SOURCE_KEYS = frozenset(
    item.casefold() for item in PERMITTED_SOURCE_KINDS
)


class CandidatePoolError(ValueError):
    """Raised when deterministic pool construction cannot continue safely."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Join an eligible-universe JSON with verified focus/company relations. "
            "The output is a stable, unranked deep-analysis pool, never a buy list."
        )
    )
    parser.add_argument(
        "--universe",
        type=Path,
        required=True,
        help="JSON emitted by filter_universe.py.",
    )
    parser.add_argument(
        "--relations",
        type=Path,
        action="append",
        required=True,
        help="Long-form focus/company relation CSV. May be repeated.",
    )
    parser.add_argument(
        "--focus",
        action="append",
        required=True,
        help="Selected analysis focus. May be repeated; matching is exact and case-insensitive.",
    )
    parser.add_argument(
        "--analysis-as-of",
        required=True,
        help="Timezone-aware ISO 8601 analysis timestamp.",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=20,
        help=(
            "Maximum safe pool size. Overflow fails and asks the upstream analysis "
            "to narrow the focus; candidates are never truncated."
        ),
    )
    parser.add_argument("--output", type=Path, help="Optional JSON output path.")
    return parser.parse_args()


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_key(value: Any) -> str:
    return normalize_text(value).casefold()


def choose_field(fieldnames: list[str], aliases: tuple[str, ...]) -> str | None:
    normalized = {name.strip().casefold(): name for name in fieldnames}
    for alias in aliases:
        matched = normalized.get(alias.casefold())
        if matched:
            return matched
    return None


def parse_analysis_as_of(value: str) -> datetime:
    cleaned = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise CandidatePoolError(
            "--analysis-as-of must be a valid ISO 8601 timestamp."
        ) from exc
    if parsed.tzinfo is None:
        raise CandidatePoolError("--analysis-as-of must include a timezone offset.")
    return parsed.astimezone(SHANGHAI)


def load_universe(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidatePoolError(f"Unable to read universe JSON: {path}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("eligible"), list):
        raise CandidatePoolError(
            "Universe JSON must contain an 'eligible' list from filter_universe.py."
        )
    return payload


def load_relation_rows(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        try:
            handle = path.open("r", encoding="utf-8-sig", newline="")
        except OSError as exc:
            raise CandidatePoolError(f"Unable to read relation CSV: {path}") from exc
        with handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise CandidatePoolError(f"Relation CSV has no header: {path}")
            fields = {
                key: choose_field(list(reader.fieldnames), aliases)
                for key, aliases in FIELD_ALIASES.items()
            }
            missing = [
                key
                for key in ("code", "focus", "mapping_status")
                if not fields[key]
            ]
            if missing:
                raise CandidatePoolError(
                    f"Relation CSV {path} is missing required fields: "
                    f"{', '.join(missing)}."
                )
            for index, raw in enumerate(reader, start=2):
                item = {
                    key: normalize_text(raw.get(field or "", ""))
                    for key, field in fields.items()
                }
                item["_source_file"] = str(path)
                item["_source_row"] = index
                rows.append(item)
    return rows


def stable_values(values: Iterable[str]) -> list[str]:
    return sorted({value for value in values if value}, key=lambda item: item.casefold())


def universe_index(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for raw in payload["eligible"]:
        if not isinstance(raw, dict):
            continue
        code = extract_code(raw.get("code"))
        if not code:
            continue
        if code in index:
            raise CandidatePoolError(f"Universe contains duplicate eligible code: {code}")
        index[code] = raw
    return index


def mapping_category(value: str) -> str:
    normalized = normalize_key(value)
    if normalized in QUALIFIED_MAPPING_KEYS:
        return "qualified"
    if normalized in DEFERRED_MAPPING_KEYS:
        return "deferred"
    if normalized in WEAK_MAPPING_KEYS:
        return "weak"
    raise CandidatePoolError(
        f"Unsupported mapping_status {value!r}; use a verified, pending, or weak status."
    )


def build_candidate_pool(
    universe_payload: dict[str, Any],
    relation_rows: list[dict[str, Any]],
    selected_focuses: list[str],
    analysis_as_of: datetime,
    max_candidates: int,
) -> dict[str, Any]:
    if max_candidates <= 0:
        raise CandidatePoolError("--max-candidates must be positive.")

    focus_map: dict[str, str] = {}
    for value in selected_focuses:
        cleaned = normalize_text(value)
        if not cleaned:
            raise CandidatePoolError("--focus values cannot be empty.")
        focus_map.setdefault(cleaned.casefold(), cleaned)
    if not focus_map:
        raise CandidatePoolError("At least one --focus is required.")

    eligible = universe_index(universe_payload)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    excluded: list[dict[str, Any]] = []

    for row in relation_rows:
        code = extract_code(row.get("code"))
        focus_key = normalize_key(row.get("focus"))
        focus = focus_map.get(focus_key, normalize_text(row.get("focus")))
        base = {
            "code": code,
            "focus": focus,
            "source_file": row.get("_source_file"),
            "source_row": row.get("_source_row"),
        }
        if not code:
            excluded.append({**base, "reason": "invalid_code"})
            continue
        if focus_key not in focus_map:
            excluded.append({**base, "reason": "focus_not_selected"})
            continue
        source_kind = normalize_text(row.get("source_kind")) or "mainline"
        if normalize_key(source_kind) not in PERMITTED_SOURCE_KEYS:
            excluded.append({**base, "reason": "unsupported_source_kind"})
            continue
        category = mapping_category(normalize_text(row.get("mapping_status")))
        if category != "qualified":
            excluded.append(
                {
                    **base,
                    "reason": (
                        "mapping_requires_verification"
                        if category == "deferred"
                        else "weak_mapping"
                    ),
                }
            )
            continue
        if code not in eligible:
            excluded.append({**base, "reason": "not_in_eligible_universe"})
            continue
        grouped[code].append({**row, "focus": focus, "source_kind": source_kind})

    candidates: list[dict[str, Any]] = []
    for code in sorted(grouped):
        rows = grouped[code]
        universe_item = eligible[code]
        warnings = stable_values(
            normalize_text(item)
            for item in universe_item.get("warnings", [])
            if isinstance(item, str)
        )
        needs_verification = any(
            warning in UNIVERSE_WARNINGS_REQUIRING_VERIFICATION
            for warning in warnings
        )
        candidates.append(
            {
                "code": code,
                "name": normalize_text(universe_item.get("name")),
                "pool_status": (
                    "requires_universe_verification"
                    if needs_verification
                    else "ready_for_deep_analysis"
                ),
                "focuses": stable_values(row["focus"] for row in rows),
                "source_kinds": stable_values(row["source_kind"] for row in rows),
                "industry_clusters": stable_values(
                    normalize_text(row.get("industry_cluster")) for row in rows
                ),
                "segments": stable_values(
                    normalize_text(row.get("segment")) for row in rows
                ),
                "industry_roles": stable_values(
                    normalize_text(row.get("industry_role")) for row in rows
                ),
                "market_roles": stable_values(
                    normalize_text(row.get("market_role")) for row in rows
                ),
                "leader_types": stable_values(
                    normalize_text(row.get("leader_type")) for row in rows
                ),
                "mapping_statuses": stable_values(
                    normalize_text(row.get("mapping_status")) for row in rows
                ),
                "evidence_refs": stable_values(
                    normalize_text(row.get("evidence_ref")) for row in rows
                ),
                "universe_warnings": warnings,
                "universe_evidence": {
                    "liquidity_rmb": universe_item.get("liquidity_rmb"),
                    "liquidity_source": universe_item.get("liquidity_source"),
                },
                "relation_rows": len(rows),
            }
        )

    if len(candidates) > max_candidates:
        raise CandidatePoolError(
            f"Candidate pool has {len(candidates)} stocks, exceeding "
            f"--max-candidates={max_candidates}. Narrow the selected main line, "
            "industry segment, event, or company role upstream; candidates were not "
            "ranked or truncated."
        )

    excluded.sort(
        key=lambda item: (
            normalize_text(item.get("code")),
            normalize_key(item.get("focus")),
            normalize_text(item.get("reason")),
            normalize_text(item.get("source_file")),
            int(item.get("source_row") or 0),
        )
    )
    return {
        "mode": "deterministic_unranked_candidate_pool",
        "analysis_as_of": analysis_as_of.astimezone(SHANGHAI).isoformat(
            timespec="seconds"
        ),
        "selected_focuses": stable_values(focus_map.values()),
        "counts": {
            "eligible_universe": len(eligible),
            "relation_rows": len(relation_rows),
            "candidates": len(candidates),
            "excluded_relations": len(excluded),
        },
        "policy": {
            "hard_filter_input_required": True,
            "fixed_composite_score": False,
            "final_ranking": False,
            "top_n_truncation": False,
            "stable_order": "code_only_not_quality",
            "overflow_behavior": "fail_and_narrow_upstream",
            "final_buy_decision": "agent_after_deep_analysis",
        },
        "candidates": candidates,
        "excluded_relations": excluded,
        "decision": None,
    }


def main() -> int:
    args = parse_args()
    try:
        payload = build_candidate_pool(
            load_universe(args.universe),
            load_relation_rows(args.relations),
            args.focus,
            parse_analysis_as_of(args.analysis_as_of),
            args.max_candidates,
        )
    except CandidatePoolError as exc:
        raise SystemExit(str(exc)) from exc

    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        sys.stdout.write(rendered + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

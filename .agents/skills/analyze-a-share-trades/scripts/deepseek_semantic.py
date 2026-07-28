#!/usr/bin/env python3
"""Use DeepSeek for bounded text semantics, never for prices or final trade decisions."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from credential_loader import CredentialError, load_credential


API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-v4-flash"

TASKS = {
    "extract-events": {
        "instruction": (
            "从输入新闻或公告中提取可验证事件。不要补充输入中没有的事实。"
            "区分事实、公司表述、媒体推断和市场观点。"
        ),
        "schema": {
            "events": [
                {
                    "event_type": "业绩/订单/产能/减持/解禁/监管/政策/其他",
                    "entities": ["实体"],
                    "fact": "输入明确支持的事实",
                    "published_at": "若输入可得，否则 null",
                    "evidence_span": "短证据片段",
                    "uncertainty": "不确定性或缺失信息",
                }
            ]
        },
    },
    "map-industry": {
        "instruction": (
            "把输入中的公司、产品或事件映射到产业主题和细分环节。"
            "只根据输入给出直接映射、间接映射或证据不足；不得因概念标签证明受益。"
        ),
        "schema": {
            "mappings": [
                {
                    "entity": "公司或产品",
                    "theme": "主题",
                    "industry_cluster": "产业簇",
                    "chain_link": "细分环节",
                    "exposure_type": "直接/间接/弱映射/证据不足",
                    "supporting_evidence": ["证据"],
                    "missing_evidence": ["仍需验证"],
                }
            ]
        },
    },
    "bear-case": {
        "instruction": (
            "对输入中的投资假设做最强反方审查。不要生成新的市场事实，"
            "而要指出逻辑跳跃、替代解释、证伪条件和缺失数据。"
        ),
        "schema": {
            "thesis_summary": "原始假设",
            "strongest_counterarguments": ["反方论点"],
            "alternative_explanations": ["竞争性解释"],
            "falsifiers": ["可观察的证伪条件"],
            "missing_data": ["决策前仍需的数据"],
        },
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=tuple(TASKS), required=True)
    parser.add_argument(
        "--input",
        type=Path,
        help="UTF-8 text input. Reads stdin when omitted.",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--model",
        default=os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL),
        help=f"Default: DEEPSEEK_MODEL or {DEFAULT_MODEL}.",
    )
    parser.add_argument("--max-input-chars", type=int, default=120_000)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def load_text(path: Path | None) -> str:
    return path.read_text(encoding="utf-8") if path else sys.stdin.read()


def build_payload(task: str, text: str, model: str) -> dict:
    definition = TASKS[task]
    system = (
        "你是A股研究流程中的文本语义子模块，不是行情数据源或交易决策器。"
        "严格输出JSON。所有事实必须来自输入；不确定就写明缺失，不得猜测。"
        f"任务：{definition['instruction']} "
        f"JSON结构示例：{json.dumps(definition['schema'], ensure_ascii=False)}"
    )
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"请按要求输出JSON。\n\n输入：\n{text}"},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
        "max_tokens": 4096,
    }


def call_api(payload: dict, api_key: str, timeout: float) -> dict:
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_content(response: dict) -> dict:
    choices = response.get("choices") or []
    if not choices:
        raise ValueError("DeepSeek returned no choices.")
    content = ((choices[0].get("message") or {}).get("content") or "").strip()
    if not content:
        raise ValueError("DeepSeek returned empty content.")
    if content.startswith("```"):
        content = content.strip("`").removeprefix("json").strip()
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise ValueError("DeepSeek JSON output must be an object.")
    return parsed


def main() -> int:
    args = parse_args()
    text = load_text(args.input).strip()
    if not text:
        raise SystemExit("Input text is empty.")
    if len(text) > args.max_input_chars:
        raise SystemExit(
            f"Input has {len(text)} characters; limit is {args.max_input_chars}. "
            "Chunk by source and preserve source identifiers."
        )
    payload = build_payload(args.task, text, args.model)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "task": args.task,
                    "model": args.model,
                    "api_url": API_URL,
                    "input_chars": len(text),
                    "response_format": payload["response_format"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    try:
        api_key = load_credential("DEEPSEEK_API_KEY")
    except CredentialError as exc:
        raise SystemExit(str(exc)) from None

    last_error: Exception | None = None
    for attempt in range(args.retries + 1):
        try:
            response = call_api(payload, api_key, args.timeout)
            result = {
                "metadata": {
                    "task": args.task,
                    "model": args.model,
                    "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(
                        timespec="seconds"
                    ),
                    "input_chars": len(text),
                    "role": "semantic_support_only",
                },
                "result": parse_content(response),
            }
            rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered, encoding="utf-8")
            else:
                sys.stdout.write(rendered)
            return 0
        except (ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt < args.retries:
                time.sleep(1.5 * (2**attempt))
    assert last_error is not None
    print(f"DeepSeek semantic call failed: {type(last_error).__name__}: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

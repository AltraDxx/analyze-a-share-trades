#!/usr/bin/env python3
"""Load API credentials without exposing their values.

Environment variables take precedence. When a variable is absent or blank,
credentials are read from ``my_api_key.md`` at the repository root.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import parse_qs, urlparse


SUPPORTED_CREDENTIALS = {
    "TUSHARE_TOKEN": {
        "tushare",
        "tusharekey",
        "tushareapi",
        "tusharemcp",
        "tusharemcpurl",
        "tusharetoken",
        "tushareapikey",
        "tushareapitoken",
    },
    "DEEPSEEK_API_KEY": {
        "deepseek",
        "deepseekkey",
        "deepseekapi",
        "deepseektoken",
        "deepseekapikey",
        "deepseekapitoken",
    },
}
DEFAULT_CREDENTIAL_FILE = Path(__file__).resolve().parents[4] / "my_api_key.md"
VALUE_PATTERN = re.compile(r"^[A-Za-z0-9._/+\-=]+$")
PLACEHOLDERS = {
    "changeme",
    "replace_me",
    "replace-me",
    "your_api_key",
    "your-api-key",
    "your_token",
    "your-token",
}
TUSHARE_MCP_URL_PATTERN = re.compile(
    r"https://api\.tushare\.pro/mcp/\?[^\s`\"'<>)]+",
    re.IGNORECASE,
)


class CredentialError(RuntimeError):
    """A credential is unavailable or cannot be parsed safely."""


def _normalize_label(value: str) -> str:
    value = value.strip().strip("`*_# ")
    value = re.sub(r"^[+-]\s+", "", value)
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _credential_name(label: str) -> str | None:
    normalized = _normalize_label(label)
    for name, aliases in SUPPORTED_CREDENTIALS.items():
        if normalized in aliases:
            return name
    return None


def _clean_value(value: str) -> str | None:
    candidate = value.strip()
    candidate = re.sub(r"^[+-]\s+", "", candidate)
    if len(candidate) >= 2 and candidate[0] == candidate[-1]:
        if candidate[0] in {"'", '"', "`"}:
            candidate = candidate[1:-1].strip()
    if (
        len(candidate) < 8
        or any(character.isspace() for character in candidate)
        or not VALUE_PATTERN.fullmatch(candidate)
        or candidate.casefold() in PLACEHOLDERS
    ):
        return None
    return candidate


def _record_candidate(
    candidates: dict[str, set[str]],
    malformed: set[str],
    name: str,
    raw_value: str,
) -> None:
    if name == "TUSHARE_TOKEN":
        url_values = _tushare_tokens_from_urls(raw_value)
        if url_values:
            for url_value in url_values:
                _record_candidate(candidates, malformed, name, url_value)
            return
    value = _clean_value(raw_value)
    if value is None:
        malformed.add(name)
        return
    candidates[name].add(value)


def _tushare_tokens_from_urls(text: str) -> list[str]:
    tokens: list[str] = []
    for match in TUSHARE_MCP_URL_PATTERN.finditer(text):
        query = parse_qs(urlparse(match.group(0)).query)
        tokens.extend(query.get("token", []))
    return tokens


def _parse_tushare_mcp_urls(
    text: str,
    candidates: dict[str, set[str]],
    malformed: set[str],
) -> None:
    for raw_value in _tushare_tokens_from_urls(text):
        _record_candidate(
            candidates,
            malformed,
            "TUSHARE_TOKEN",
            raw_value,
        )


def _parse_json_credentials(
    text: str,
    candidates: dict[str, set[str]],
    malformed: set[str],
) -> bool:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        stripped = "\n".join(lines[1:-1]).strip()
    if not stripped.startswith("{"):
        return False
    try:
        payload = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(payload, dict):
        return False
    for label, raw_value in payload.items():
        name = _credential_name(str(label))
        if name is None:
            continue
        _record_candidate(candidates, malformed, name, str(raw_value))
    return True


def _parse_markdown_credentials(
    text: str,
    candidates: dict[str, set[str]],
    malformed: set[str],
) -> None:
    pending_name: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("```"):
            continue

        table_cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(table_cells) >= 2:
            table_name = _credential_name(table_cells[0])
            if table_name is not None:
                if table_cells[1]:
                    _record_candidate(
                        candidates,
                        malformed,
                        table_name,
                        table_cells[1],
                    )
                    pending_name = None
                else:
                    pending_name = table_name
                continue

        matched_inline = False
        for delimiter in ("=", "：", ":"):
            if delimiter not in line:
                continue
            label, raw_value = line.split(delimiter, 1)
            name = _credential_name(label)
            if name is None:
                continue
            if raw_value.strip():
                _record_candidate(candidates, malformed, name, raw_value)
                pending_name = None
            else:
                pending_name = name
            matched_inline = True
            break
        if matched_inline:
            continue

        standalone_name = _credential_name(line)
        if standalone_name is not None:
            pending_name = standalone_name
            continue

        if pending_name is not None:
            pending_value: str | None = None
            labeled_setting = False
            for delimiter in ("=", "：", ":"):
                if delimiter not in line:
                    continue
                generic_label, raw_value = line.split(delimiter, 1)
                labeled_setting = True
                normalized = _normalize_label(generic_label)
                if normalized in {"token", "key", "apikey", "apitoken", "value"}:
                    pending_value = raw_value
                elif "密钥" in generic_label:
                    pending_value = raw_value
                break
            if pending_value is not None:
                _record_candidate(
                    candidates,
                    malformed,
                    pending_name,
                    pending_value,
                )
                pending_name = None
            elif not labeled_setting and _clean_value(line) is not None:
                _record_candidate(
                    candidates,
                    malformed,
                    pending_name,
                    line,
                )
                pending_name = None

    if pending_name is not None:
        malformed.add(pending_name)


def parse_credentials(text: str) -> tuple[dict[str, str], set[str]]:
    """Parse supported credentials and return values plus malformed labels."""
    candidates = {name: set() for name in SUPPORTED_CREDENTIALS}
    malformed: set[str] = set()
    _parse_tushare_mcp_urls(text, candidates, malformed)
    parsed_as_json = _parse_json_credentials(text, candidates, malformed)
    if not parsed_as_json:
        _parse_markdown_credentials(text, candidates, malformed)

    parsed: dict[str, str] = {}
    for name, values in candidates.items():
        if len(values) == 1 and name not in malformed:
            parsed[name] = next(iter(values))
        elif len(values) > 1:
            malformed.add(name)
    return parsed, malformed


def load_credential(
    name: str,
    *,
    credential_file: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return one credential, preferring its environment variable."""
    if name not in SUPPORTED_CREDENTIALS:
        raise ValueError("Unsupported credential name.")

    environment = os.environ if environ is None else environ
    environment_value = environment.get(name)
    if environment_value is not None and environment_value.strip():
        value = _clean_value(environment_value)
        if value is None:
            raise CredentialError(f"{name} credential format is unsupported.")
        return value

    path = DEFAULT_CREDENTIAL_FILE if credential_file is None else credential_file
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise CredentialError(f"{name} is not configured.") from None
    except (OSError, UnicodeError):
        raise CredentialError(f"{name} credential format is unsupported.") from None

    parsed, malformed = parse_credentials(text)
    if name in parsed:
        return parsed[name]
    if name in malformed:
        raise CredentialError(f"{name} credential format is unsupported.")
    raise CredentialError(f"{name} is not configured.")

#!/usr/bin/env python3
"""Fast offline checks for the skill contract and deterministic helpers."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import compute_liquidity_median  # noqa: E402
import build_candidate_pool  # noqa: E402
import credential_loader  # noqa: E402
import deepseek_semantic  # noqa: E402
import derive_price_features  # noqa: E402
import fetch_market_data  # noqa: E402
import filter_universe  # noqa: E402
import scan_mainlines  # noqa: E402
import trade_window  # noqa: E402


def test_universe() -> None:
    assert filter_universe.extract_code("600000.SH") == "600000"
    assert filter_universe.is_main_board("600000")
    assert filter_universe.is_main_board("002001")
    assert not filter_universe.is_main_board("300001")
    assert not filter_universe.is_main_board("688001")
    assert filter_universe.is_st_or_delisting("*ST示例")
    assert filter_universe.is_st_or_delisting("S*ST示例")
    assert filter_universe.parse_number("1.5亿") == 150_000_000


def test_candidate_pool() -> None:
    universe = {
        "eligible": [
            {
                "code": "600000",
                "name": "公司甲",
                "liquidity_rmb": 300_000_000,
                "liquidity_source": "amount_20d_median",
                "warnings": [],
            },
            {
                "code": "000001",
                "name": "公司乙",
                "liquidity_rmb": None,
                "liquidity_source": None,
                "warnings": ["liquidity_unverified"],
            },
        ]
    }
    relations = [
        {
            "code": "600000.SH",
            "focus": "ai基础设施",
            "mapping_status": "verified",
            "source_kind": "mainline",
            "industry_cluster": "服务器",
            "segment": "供配电",
            "industry_role": "产业龙头",
            "market_role": "盘面龙头",
            "leader_type": "产业龙头",
            "evidence_ref": "公告A",
        },
        {
            "code": "600000",
            "focus": "AI基础设施",
            "mapping_status": "直接受益",
            "source_kind": "事件",
            "industry_cluster": "服务器",
            "segment": "供配电",
            "industry_role": "产业核心",
            "market_role": "趋势中军",
            "leader_type": "趋势核心",
            "evidence_ref": "公告B",
        },
        {
            "code": "000001",
            "focus": "AI基础设施",
            "mapping_status": "confirmed",
            "source_kind": "company",
            "industry_cluster": "数据中心",
            "segment": "液冷",
            "industry_role": "直接受益",
            "market_role": "阶段性新核心",
            "leader_type": "阶段性新核心",
            "evidence_ref": "财报C",
        },
        {
            "code": "600001",
            "focus": "AI基础设施",
            "mapping_status": "verified",
            "source_kind": "mainline",
            "industry_cluster": "服务器",
            "segment": "连接器",
            "industry_role": "产业核心",
            "market_role": "",
            "leader_type": "",
            "evidence_ref": "公告D",
        },
        {
            "code": "600000",
            "focus": "消费",
            "mapping_status": "verified",
            "source_kind": "mainline",
            "industry_cluster": "消费",
            "segment": "零售",
            "industry_role": "",
            "market_role": "",
            "leader_type": "",
            "evidence_ref": "公告E",
        },
        {
            "code": "000001",
            "focus": "AI基础设施",
            "mapping_status": "weak_mapping",
            "source_kind": "mainline",
            "industry_cluster": "数据中心",
            "segment": "光模块",
            "industry_role": "弱映射",
            "market_role": "跟随",
            "leader_type": "",
            "evidence_ref": "互动平台F",
        },
    ]
    as_of = build_candidate_pool.parse_analysis_as_of(
        "2026-07-28T14:30:00+08:00"
    )
    payload = build_candidate_pool.build_candidate_pool(
        universe,
        relations,
        ["AI基础设施"],
        as_of,
        5,
    )
    assert [item["code"] for item in payload["candidates"]] == [
        "000001",
        "600000",
    ]
    company_a = payload["candidates"][1]
    assert company_a["leader_types"] == ["产业龙头", "趋势核心"]
    assert company_a["market_roles"] == ["盘面龙头", "趋势中军"]
    assert company_a["evidence_refs"] == ["公告A", "公告B"]
    assert payload["candidates"][0]["pool_status"] == (
        "requires_universe_verification"
    )
    assert {item["reason"] for item in payload["excluded_relations"]} == {
        "focus_not_selected",
        "not_in_eligible_universe",
        "weak_mapping",
    }
    reversed_payload = build_candidate_pool.build_candidate_pool(
        universe,
        list(reversed(relations)),
        ["AI基础设施"],
        as_of,
        5,
    )
    assert payload == reversed_payload
    assert payload["decision"] is None
    for candidate in payload["candidates"]:
        assert "score" not in candidate
        assert "rank" not in candidate
        assert "recommendation" not in candidate
    assert payload["policy"]["fixed_composite_score"] is False
    assert payload["policy"]["final_ranking"] is False
    assert payload["policy"]["top_n_truncation"] is False

    try:
        build_candidate_pool.build_candidate_pool(
            universe,
            relations,
            ["AI基础设施"],
            as_of,
            1,
        )
    except build_candidate_pool.CandidatePoolError as exc:
        assert "were not ranked or truncated" in str(exc)
    else:
        raise AssertionError("Candidate overflow must fail instead of truncating.")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        universe_path = temp_path / "universe.json"
        relations_path = temp_path / "relations.csv"
        universe_path.write_text(
            json.dumps(universe, ensure_ascii=False),
            encoding="utf-8",
        )
        relations_path.write_text(
            "\n".join(
                (
                    "code,focus,mapping_status,source_kind,industry_role,"
                    "market_role,leader_type,evidence_ref",
                    "600000,AI基础设施,verified,mainline,产业龙头,"
                    "盘面龙头,产业龙头,公告A",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        loaded_payload = build_candidate_pool.build_candidate_pool(
            build_candidate_pool.load_universe(universe_path),
            build_candidate_pool.load_relation_rows([relations_path]),
            ["AI基础设施"],
            as_of,
            5,
        )
        assert loaded_payload["candidates"][0]["code"] == "600000"


def test_trade_dates() -> None:
    assert trade_window.parse_date("2026-07-27T00:00:00.000").isoformat() == "2026-07-27"
    try:
        trade_window.resolve_open_dates(
            trade_window.parse_date("2026-07-25"),
            3,
            None,
            False,
        )
    except ValueError as exc:
        assert "--calendar" in str(exc)
    else:
        raise AssertionError("Missing calendar must fail without explicit fallback.")
    resolved, quality, warning = trade_window.resolve_open_dates(
        trade_window.parse_date("2026-07-25"),
        3,
        None,
        True,
    )
    assert quality == "weekday_fallback_explicit"
    assert warning
    assert [item.isoformat() for item in resolved[:3]] == [
        "2026-07-27",
        "2026-07-28",
        "2026-07-29",
    ]
    relative = trade_window.build_relative_payload(
        trade_window.parse_date("2026-07-25"),
        resolved,
        2,
        7,
        [2, 3, 5],
        quality,
        warning,
    )
    assert relative["d1"] == "D1"
    assert relative["d1_relation"] == "next_open_session_after_analysis_day"
    assert relative["holding_window"]["min_end"] == "D2"
    assert relative["holding_window"]["max_end"] == "D7"
    assert relative["review_points"] == ["D2", "D3", "D5"]
    assert relative["natural_holding_dates_exposed"] is False
    assert "earliest_end" not in relative["holding_window"]
    dates = trade_window.weekday_fallback(trade_window.parse_date("2026-07-25"), 3)
    assert [item.isoformat() for item in dates] == [
        "2026-07-27",
        "2026-07-28",
        "2026-07-29",
    ]


def test_market_data_fallback() -> None:
    class FakeAkShare:
        @staticmethod
        def stock_zh_a_spot_em() -> list:
            raise ConnectionError("blocked")

        @staticmethod
        def stock_zh_a_spot_tx() -> list:
            return [{"code": "sh600000"}]

        @staticmethod
        def stock_zh_a_spot() -> list:
            raise AssertionError("Sina should not run after Tencent succeeds.")

    args = SimpleNamespace(
        dataset="snapshot",
        ak_provider="auto",
        symbol=None,
        start=None,
        end=None,
        period="daily",
        adjust="qfq",
    )
    frame, api, kwargs, metadata = fetch_market_data.akshare_call(
        args, ak_module=FakeAkShare()
    )
    assert len(frame) == 1
    assert api == "akshare.stock_zh_a_spot_tx"
    assert kwargs == {}
    assert metadata["provider"] == "tencent"
    assert metadata["fallback_used"]
    assert [item["status"] for item in metadata["attempted_apis"]] == [
        "failed",
        "success",
    ]

    index_args = SimpleNamespace(**{**vars(args), "dataset": "index-snapshot"})
    specs = fetch_market_data.akshare_call_specs(index_args)
    assert [provider for provider, _, _ in specs] == ["sina", "eastmoney"]
    assert fetch_market_data.normalize_dataset_time("13:05:01").endswith(
        "T13:05:01+08:00"
    )


def test_credential_fallback() -> None:
    fake_tushare_file_token = "fake-tushare-file-token"
    fake_tushare_env_token = "fake-tushare-env-token"
    fake_deepseek_file_key = "fake-deepseek-file-key"
    with tempfile.TemporaryDirectory() as temp_dir:
        credential_file = Path(temp_dir) / "my_api_key.md"
        credential_file.write_text(
            "\n".join(
                (
                    "# Local credentials",
                    f"TUSHARE_TOKEN: `{fake_tushare_file_token}`",
                    "| name | value |",
                    "| --- | --- |",
                    f"| DEEPSEEK_API_KEY | {fake_deepseek_file_key} |",
                )
            ),
            encoding="utf-8",
        )
        assert credential_loader.load_credential(
            "TUSHARE_TOKEN",
            credential_file=Path(temp_dir) / "must-not-be-read.md",
            environ={"TUSHARE_TOKEN": fake_tushare_env_token},
        ) == fake_tushare_env_token
        assert credential_loader.load_credential(
            "TUSHARE_TOKEN",
            credential_file=credential_file,
            environ={},
        ) == fake_tushare_file_token
        assert credential_loader.load_credential(
            "TUSHARE_TOKEN",
            credential_file=credential_file,
            environ={"TUSHARE_TOKEN": " \t "},
        ) == fake_tushare_file_token
        assert credential_loader.load_credential(
            "DEEPSEEK_API_KEY",
            credential_file=credential_file,
            environ={},
        ) == fake_deepseek_file_key

        sectioned_file = Path(temp_dir) / "sectioned.md"
        sectioned_file.write_text(
            "\n".join(
                (
                    "DeepSeek",
                    "API 地址：https://example.invalid/v1",
                    f"API 密钥：{fake_deepseek_file_key}",
                )
            ),
            encoding="utf-8",
        )
        assert credential_loader.load_credential(
            "DEEPSEEK_API_KEY",
            credential_file=sectioned_file,
            environ={},
        ) == fake_deepseek_file_key

        try:
            credential_loader.load_credential(
                "TUSHARE_TOKEN",
                credential_file=credential_file,
                environ={"TUSHARE_TOKEN": "not a valid token"},
            )
        except credential_loader.CredentialError as exc:
            assert str(exc) == "TUSHARE_TOKEN credential format is unsupported."
        else:
            raise AssertionError("A non-empty invalid environment value must fail.")

        mcp_file = Path(temp_dir) / "mcp-config.md"
        mcp_file.write_text(
            '{"mcpServers":{"tushareMcp":{"url":'
            f'"https://api.tushare.pro/mcp/?token={fake_tushare_file_token}"'
            "}}}",
            encoding="utf-8",
        )
        assert credential_loader.load_credential(
            "TUSHARE_TOKEN",
            credential_file=mcp_file,
            environ={},
        ) == fake_tushare_file_token

        class FakePro:
            @staticmethod
            def stock_basic(**kwargs: object) -> list[dict[str, object]]:
                return [kwargs]

        class FakeTushare:
            received_token: str | None = None

            @classmethod
            def pro_api(cls, token: str) -> FakePro:
                cls.received_token = token
                return FakePro()

        args = SimpleNamespace(
            dataset="stock-basic",
            symbol=None,
            start=None,
            end=None,
            trade_date=None,
        )
        _, api_name, _ = fetch_market_data.tushare_call(
            args,
            ts_module=FakeTushare,
            credential_file=credential_file,
            environ={},
        )
        assert api_name == "tushare.pro.stock_basic"
        assert FakeTushare.received_token == fake_tushare_file_token

        malformed_file = Path(temp_dir) / "malformed.md"
        malformed_file.write_text(
            f"TUSHARE_TOKEN: {fake_tushare_file_token}\n"
            "TUSHARE_TOKEN: another-fake-tushare-token\n",
            encoding="utf-8",
        )
        try:
            credential_loader.load_credential(
                "TUSHARE_TOKEN",
                credential_file=malformed_file,
                environ={},
            )
        except credential_loader.CredentialError as exc:
            assert "format is unsupported" in str(exc)
            assert fake_tushare_file_token not in str(exc)
        else:
            raise AssertionError("Ambiguous credentials must fail safely.")

        try:
            credential_loader.load_credential(
                "TUSHARE_TOKEN",
                credential_file=Path(temp_dir) / "missing.md",
                environ={},
            )
        except credential_loader.CredentialError as exc:
            assert str(exc) == "TUSHARE_TOKEN is not configured."
        else:
            raise AssertionError("Missing credentials must fail safely.")


def test_liquidity_median() -> None:
    book = compute_liquidity_median.LiquidityBook()
    start = compute_liquidity_median.parse_date("2026-06-01")
    for index in range(20):
        compute_liquidity_median.add_observation(
            book,
            "600000",
            start + timedelta(days=index),
            (index + 1) * 10_000_000,
            "fixture.csv",
            "浦发银行",
        )
    summary = compute_liquidity_median.summarize_code(
        book,
        "600000",
        start + timedelta(days=30),
        15,
        100_000_000,
    )
    assert summary["amount_20d_median"] == 105_000_000
    assert summary["liquidity_verified"] == "true"
    assert summary["meets_minimum"] == "true"
    assert summary["valid_days"] == 20

    for index in range(14):
        compute_liquidity_median.add_observation(
            book,
            "000001",
            start + timedelta(days=index),
            200_000_000,
            "fixture.csv",
        )
    insufficient = compute_liquidity_median.summarize_code(
        book,
        "000001",
        start + timedelta(days=30),
        15,
        100_000_000,
    )
    assert insufficient["amount_20d_median"] is None
    assert insufficient["reason"] == "insufficient_valid_days"

    conflict_day = start + timedelta(days=19)
    compute_liquidity_median.add_observation(
        book,
        "600000",
        conflict_day,
        999_000_000,
        "conflict.csv",
    )
    conflict = compute_liquidity_median.summarize_code(
        book,
        "600000",
        start + timedelta(days=30),
        15,
        100_000_000,
    )
    assert conflict["amount_20d_median"] is None
    assert conflict["reason"] == "conflicting_amounts"

    intraday = datetime(2026, 7, 27, 13, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert compute_liquidity_median.completed_through(
        intraday.date(), intraday
    ).isoformat() == "2026-07-26"
    after_close = intraday.replace(hour=15, minute=10)
    assert compute_liquidity_median.completed_through(
        after_close.date(), after_close
    ).isoformat() == "2026-07-27"


def make_daily_rows() -> list[dict]:
    start = datetime(2025, 1, 1)
    return [
        {
            "datetime": start + timedelta(days=index),
            "open": 10.0 + index,
            "high": 10.8 + index,
            "low": 9.5 + index,
            "close": 10.2 + index,
            "volume": 1_000 + index * 10,
            "amount": 1_000_000 + index * 10_000,
            "avg_price": None,
        }
        for index in range(25)
    ]


def test_price_features() -> None:
    daily = derive_price_features.daily_features(make_daily_rows())
    assert daily["mode"] == "daily"
    assert daily["rows"] == 25
    assert daily["moving_averages"]["ma_20"] is not None
    minute_start = datetime(2025, 1, 2, 9, 30)
    minute_rows = [
        {
            "datetime": minute_start + timedelta(minutes=index),
            "open": 10.0 + index * 0.01,
            "high": 10.1 + index * 0.01,
            "low": 9.9 + index * 0.01,
            "close": 10.0 + index * 0.01,
            "volume": 100 + index,
            "amount": (100 + index) * 100 * (10.0 + index * 0.01),
            "avg_price": None,
        }
        for index in range(40)
    ]
    intraday = derive_price_features.intraday_features(minute_rows, "lots")
    assert intraday["mode"] == "intraday"
    assert intraday["rows"] == 40
    assert intraday["session"]["vwap"] is not None


def test_analysis_as_of_filtering() -> None:
    shanghai = ZoneInfo("Asia/Shanghai")
    naive_as_of = derive_price_features.parse_analysis_as_of(
        "2026-07-27T13:12:27"
    )
    utc_as_of = derive_price_features.parse_analysis_as_of(
        "2026-07-27T05:12:27Z"
    )
    assert naive_as_of.isoformat() == "2026-07-27T13:12:27+08:00"
    assert utc_as_of == naive_as_of
    assert derive_price_features.parse_datetime(
        "2026-07-27T05:12:00Z"
    ) == datetime(2026, 7, 27, 13, 12)

    minute_rows = [
        {
            "datetime": datetime(2026, 7, 27, 13, minute),
            "open": 10.0,
            "high": 10.1,
            "low": 9.9,
            "close": 10.0,
            "volume": 100,
            "amount": 100_000,
            "avg_price": 10.0,
        }
        for minute in (11, 12, 13)
    ]
    filtered_minutes, minute_warnings = derive_price_features.filter_rows_as_of(
        minute_rows, naive_as_of
    )
    assert [row["datetime"].minute for row in filtered_minutes] == [11, 12]
    assert len(minute_warnings) == 1
    assert "Excluded 1 row(s)" in minute_warnings[0]
    assert "2026-07-27 13:13:00" in minute_warnings[0]

    daily_rows = [
        {
            "datetime": datetime(2026, 7, day),
            "open": 10.0,
            "high": 10.1,
            "low": 9.9,
            "close": 10.0,
            "volume": 100,
            "amount": 100_000,
            "avg_price": None,
        }
        for day in (24, 27, 28)
    ]
    bounded_daily, future_daily_warnings = (
        derive_price_features.filter_rows_as_of(daily_rows, naive_as_of)
    )
    completed_daily, incomplete_daily_warnings = (
        derive_price_features.exclude_incomplete_daily_row(
            bounded_daily, naive_as_of
        )
    )
    assert [row["datetime"].day for row in completed_daily] == [24]
    assert "2026-07-28 00:00:00" in future_daily_warnings[0]
    assert "incomplete current-session daily row 2026-07-27" in (
        incomplete_daily_warnings[0]
    )

    after_close = datetime(2026, 7, 27, 15, 10, tzinfo=shanghai)
    completed_after_close, after_close_warnings = (
        derive_price_features.exclude_incomplete_daily_row(
            bounded_daily, after_close
        )
    )
    assert [row["datetime"].day for row in completed_after_close] == [24, 27]
    assert not after_close_warnings


def test_mainline_metrics() -> None:
    start = datetime(2026, 6, 1)
    trading_dates = [(start + timedelta(days=index)).date() for index in range(25)]
    rows_by_date = {
        day: {
            "activity": 200.0 if index >= 20 else 100.0,
        }
        for index, day in enumerate(trading_dates)
    }
    closes = {
        day: 100.0 + index
        for index, day in enumerate(trading_dates)
    }
    gaps: list[str] = []
    activity = scan_mainlines.activity_summary(
        rows_by_date,
        trading_dates,
        gaps,
        "成交额",
    )
    persistence = scan_mainlines.positive_persistence_summary(
        closes,
        trading_dates,
        gaps,
    )
    assert activity["ratio_5d_vs_previous_20d"] == 2.0
    assert persistence["positive_days"] == 5
    assert persistence["ratio_pct"] == 100.0
    assert scan_mainlines.calculate_return(closes, trading_dates, 20) is not None


def test_document_contracts() -> None:
    skill_root = SCRIPT_DIR.parent
    reference_root = skill_root / "references"
    expected_references = {
        "data-access.md",
        "discover-candidates.md",
        "event-evidence.md",
        "execution.md",
        "market-sector-evidence.md",
        "price-flow-evidence.md",
    }
    actual_references = {path.name for path in reference_root.glob("*.md")}
    assert actual_references == expected_references

    skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    references = {
        name: (reference_root / name).read_text(encoding="utf-8")
        for name in sorted(expected_references)
    }
    joined = "\n".join((skill, *references.values()))

    assert len(skill) <= 4_500
    assert all(len(text) <= 4_000 for text in references.values())
    assert len(skill) + sum(map(len, references.values())) <= 20_000
    for name in expected_references:
        assert f"references/{name}" in skill

    assert "不向用户换算未来自然日期" in skill
    assert "D1 不是历史数据截断点" in skill
    assert "已持有天数" in skill
    assert "原事件是否延续、完成、失败或转化" in skill
    assert "历史事件研究必须先定义筛选规则" in references["event-evidence.md"]
    assert "纳入全部符合条件的样本" in references["event-evidence.md"]
    assert "真正可比样本不足 3 个" in references["price-flow-evidence.md"]
    assert "盘后固定价格交易扩展至 A 股" in references["data-access.md"]
    assert "产业核心" in references["market-sector-evidence.md"]
    assert "盘面核心" in references["market-sector-evidence.md"]
    assert "情绪核心" in references["market-sector-evidence.md"]
    assert "事件最终目标、所处阶段和当前动作" in references[
        "price-flow-evidence.md"
    ]
    assert "event_goal" in references["market-sector-evidence.md"]
    assert "信息事件" in references["event-evidence.md"]
    assert "不打分、不排名" in references["discover-candidates.md"]
    assert "先假设能够成交" in references["execution.md"]
    assert "未成交或部分成交" in references["execution.md"]
    assert "购买确认信息" in references["execution.md"]
    assert "0.75%" not in joined
    assert "1.5%" not in joined
    assert "35%" not in joined
    assert "40%" not in joined
    assert "current_tradeability" not in joined
    assert "起止自然日期" not in joined
    assert "复核日：Dn，YYYY-MM-DD" not in joined
    assert "已持有 X 个交易日" not in joined


def test_reasoning_engine_contract() -> None:
    skill_root = SCRIPT_DIR.parent
    skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    readme = (skill_root.parents[2] / "README.md").read_text(encoding="utf-8")
    metadata = (
        skill_root / "agents" / "openai.yaml"
    ).read_text(encoding="utf-8")

    required_phrases = (
        "Agent 自主选择最小分析范围",
        "不要求用户选择模式",
        "持仓处置",
        "指定股票买入",
        "全市场机会发现",
        "只有用户未给候选",
        "统一盘面事件推演引擎",
        "最终想实现什么",
        "当前处于什么阶段",
        "现在正在或必须完成什么",
        "简洁推断路径",
        "为什么偏偏此时发生",
        "在任何行情指标、个股列表或证据明细之前",
        "事件要做什么",
        "处于什么阶段",
        "它现在要做什么",
        "不得用“用户等待/买入”替代",
        "首个内容块必须包含上述四个同名短项",
        "若是普通波动本应出现却没有出现的反事实",
        "不得从预设的",
        "用户持仓只影响执行",
        "支持、削弱或证伪",
        "不是平行打分维度",
        "不得从标签出发寻找图形",
        "没有任何参考文件默认必读",
        "一次扩展一个最有区分力的证据源",
        "未持仓默认等待",
        "不得仅因“意图不知道”机械清仓",
        "不得把降级结果包装成主力判断",
    )
    for phrase in required_phrases:
        assert phrase in skill

    forbidden_mandates = (
        "形成最终答复时，读取 [output-contract.md]",
        "输出买卖、仓位、涨跌停尝试或组合判断时，读取",
        "全市场选股必须覆盖步骤 1—6、8—11",
        "先加载同日大盘博弈快照",
        "八个判断维度",
        "策略路由",
        "参与者及仓位、成本、期限",
        "未持仓默认保持空仓",
    )
    for phrase in forbidden_mandates:
        assert phrase not in skill

    assert "$analyze-a-share-trades" in metadata
    assert "最小分析范围" in metadata
    assert "事件要做什么" in metadata
    assert "推断路径" in metadata
    assert "大盘、板块和个股的战略互动" not in metadata
    assert "推断它最终想实现什么" in readme
    assert "哪些关键行为与普通波动不符" in readme
    assert "已经给出持仓或候选股票时不扫描全市场" in readme
    assert "分析 600000 现在是否值得买" in readme


def test_deepseek_payload() -> None:
    payload = deepseek_semantic.build_payload(
        "map-industry",
        "某公司公告称产品已小批量交付。",
        "deepseek-v4-flash",
    )
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["model"] == "deepseek-v4-flash"
    assert "不得猜测" in payload["messages"][0]["content"]


def main() -> int:
    tests = (
        test_universe,
        test_candidate_pool,
        test_trade_dates,
        test_market_data_fallback,
        test_credential_fallback,
        test_liquidity_median,
        test_price_features,
        test_analysis_as_of_filtering,
        test_mainline_metrics,
        test_document_contracts,
        test_reasoning_engine_contract,
        test_deepseek_payload,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS all ({len(tests)} groups)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

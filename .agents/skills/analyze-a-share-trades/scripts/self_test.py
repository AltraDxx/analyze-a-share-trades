#!/usr/bin/env python3
"""Fast offline checks for the skill's deterministic helper scripts."""

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


def test_relative_window_output_policy() -> None:
    contract = (
        SCRIPT_DIR.parent / "references" / "output-contract.md"
    ).read_text(encoding="utf-8")
    skill = (SCRIPT_DIR.parent / "SKILL.md").read_text(encoding="utf-8")
    game = (
        SCRIPT_DIR.parent / "references" / "intraday-game.md"
    ).read_text(encoding="utf-8")
    event = (
        SCRIPT_DIR.parent / "references" / "event-evidence.md"
    ).read_text(encoding="utf-8")
    mainline = (
        SCRIPT_DIR.parent / "references" / "mainline-industry-map.md"
    ).read_text(encoding="utf-8")
    market = (
        SCRIPT_DIR.parent / "references" / "market-regime.md"
    ).read_text(encoding="utf-8")
    strategy = (
        SCRIPT_DIR.parent / "references" / "strategy-playbooks.md"
    ).read_text(encoding="utf-8")
    risk = (
        SCRIPT_DIR.parent / "references" / "risk-and-portfolio.md"
    ).read_text(encoding="utf-8")
    data = (
        SCRIPT_DIR.parent / "references" / "data-and-models.md"
    ).read_text(encoding="utf-8")
    assert "不得计算、询问或输出已有持仓的已持有天数" in contract
    assert "不向用户换算未来自然日期" in skill
    assert "D1 只定义本次未来行动" in skill
    assert "不是历史数据截断点" in skill
    assert "不计算两次问询相隔的交易日" in skill
    assert "原策略失效/恢复/到期" in skill
    assert "旧策略生命周期" in contract
    assert "先定义历史事件筛选规则" in skill
    assert "所有符合预设规则的样本不足 3 个" in game
    assert "不得只挑支持" in game
    assert "纳入 `analysis_as_of` 之前所有符合预设规则的事件" in event
    assert "不表示已经识别某个真实账户" in game
    assert "盘后固定价格交易描述为散户专属" in game
    assert "产业龙头" in mainline
    assert "盘面龙头" in mainline
    assert "情绪龙头" in mainline
    assert "阶段性新核心" in mainline
    assert "不打分、不排名" in mainline
    assert "对每只候选建立贯穿大盘—板块—个股的一个主假设和一个竞争假设" in strategy
    assert "事件首先是所有交易分析都可以使用的证据维度" in strategy
    assert "假设能够成交" in risk
    assert "未成交/部分成交" in risk
    assert "不建立涨跌停可交易性三态" in risk
    assert "已无用户可使用的适用交易时段" in skill
    assert "不能仅凭“已收盘”机械跳到 D2" in contract
    assert "盘后固定价格交易适用品种扩展至 A 股" in data
    assert "一个主假设和一个竞争假设" in skill
    assert "两条完整的三层路径" in skill
    assert "不得预加载全部参考文件" in skill
    assert "意图不知道" in skill
    assert "未持仓默认保持空仓" in skill
    assert "纯数据/事件降级分析" in contract
    assert "不得把降级结果包装成主力判断" in skill
    assert "`market_intent_snapshot`" in skill
    assert "不得跨北京时间交易日沿用旧快照" in skill
    assert "`market_intent_snapshot`" in market
    assert "不得跨北京时间交易日复用" in market
    assert "同日大盘使用快照" in skill
    assert "顺势共振" in game
    assert "借势操作" in game
    assert "一个主假设和一个竞争假设" in strategy
    assert "退出至空仓" in risk
    assert "0.75%" not in risk
    assert "1.5%" not in risk
    assert "25%" not in risk
    assert "35%" not in risk
    assert "40%" not in risk
    joined_contracts = "\n".join(
        (skill, contract, game, event, mainline, market, strategy, risk, data)
    )
    assert "current_tradeability" not in joined_contracts
    assert "不强制排名、首选或备选" in contract
    assert "不设置“最大反证”固定栏目" in contract
    assert "不固定输出乐观/基准/悲观三情景" in contract
    assert "起止自然日期" not in contract
    assert "复核日：Dn，YYYY-MM-DD" not in contract
    assert "已持有 X 个交易日" not in contract


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
        test_relative_window_output_policy,
        test_deepseek_payload,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS all ({len(tests)} groups)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

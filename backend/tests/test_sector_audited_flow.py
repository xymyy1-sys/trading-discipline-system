from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services import sector_audited_flow
from app.services.sector_audited_flow import (
    SectorAuditedFlowConfiguration,
    fetch_sector_audited_flow,
)


class _Response:
    def __init__(self, body, *, status_code: int = 200):
        self._body = body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("http error")

    def json(self):
        return self._body


def _config() -> SectorAuditedFlowConfiguration:
    return SectorAuditedFlowConfiguration(
        endpoint="https://licensed.example.test/sector-flow",
        bearer_token="server-secret",
    )


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch):
    monkeypatch.setattr(
        sector_audited_flow,
        "_shanghai_now",
        lambda: datetime(2026, 7, 21, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )


def _valid_row(**updates):
    row = {
        "board_name": "半导体",
        "board_code": "BK1036",
        "trade_date": "2026-07-21",
        "non_leveraged_net_inflow": 12.34567,
        "non_leveraged_net_inflow_unit": "亿元",
        "methodology_id": "licensed-sector-flow-v1",
        "source": "持牌数据适配器",
        "source_url": "https://licensed.example.test/evidence/BK1036",
        "published_at": "2026-07-21T15:10:00+08:00",
        "observed_at": "2026-07-21T15:00:00+08:00",
        "data_quality": "audited",
    }
    row.update(updates)
    return row


def test_unconfigured_adapter_is_unavailable_and_never_calls_network(monkeypatch):
    monkeypatch.delenv("SECTOR_AUDITED_FLOW_URL", raising=False)
    monkeypatch.delenv("SECTOR_AUDITED_FLOW_TOKEN", raising=False)
    monkeypatch.delenv("SECTOR_AUDITED_FLOW_TOKEN_FILE", raising=False)
    monkeypatch.setattr(
        sector_audited_flow.requests,
        "get",
        lambda *_args, **_kwargs: pytest.fail("network must not be called"),
    )

    payload = fetch_sector_audited_flow("2026-07-21")

    assert payload["status"] == "unavailable"
    assert payload["configured"] is False
    assert payload["items"] == []
    assert payload["merge_map"] == {}
    assert any("绝不使用东方财富" in note for note in payload["notes"])


def test_token_file_and_envelope_contract_produce_name_and_code_lookup(monkeypatch, tmp_path):
    token_file = tmp_path / "sector-token"
    token_file.write_text("file-secret\n", encoding="utf-8")
    monkeypatch.setenv("SECTOR_AUDITED_FLOW_URL", "https://licensed.example.test/flow")
    monkeypatch.delenv("SECTOR_AUDITED_FLOW_TOKEN", raising=False)
    monkeypatch.setenv("SECTOR_AUDITED_FLOW_TOKEN_FILE", str(token_file))
    calls: list[dict] = []

    def fake_get(url, *, params, headers, timeout, allow_redirects):
        calls.append({
            "url": url,
            "params": params,
            "headers": headers,
            "timeout": timeout,
            "allow_redirects": allow_redirects,
        })
        return _Response({
            "trade_date": "2026-07-21",
            "source": "官方授权转接",
            "source_url": "https://official.example.test/daily/2026-07-21",
            "observed_at": "2026-07-21T15:00:00+08:00",
            "data_quality": "official",
            "non_leveraged_net_inflow_unit": "亿元",
            "methodology_id": "official-sector-flow-v2",
            "items": [
                {
                    "board_name": "半导体",
                    "board_code": "bk1036",
                    "non_leveraged_net_inflow": "12.34567",
                },
                {
                    "board_code": "BK0428",
                    "non_leveraged_net_inflow": -3,
                },
            ],
        })

    monkeypatch.setattr(sector_audited_flow.requests, "get", fake_get)
    payload = fetch_sector_audited_flow("2026-07-21")

    assert payload["status"] == "ok"
    assert payload["configured"] is True
    assert calls[0]["headers"]["Authorization"] == "Bearer file-secret"
    assert calls[0]["headers"]["Accept"] == "application/json"
    assert calls[0]["params"] == {"trade_date": "2026-07-21"}
    assert calls[0]["allow_redirects"] is False
    item = payload["by_name"]["半导体"]
    assert item["board_code"] == "BK1036"
    assert item["non_leveraged_net_inflow"] == 12.3457
    assert item["non_leveraged_flow_audited"] is True
    assert item["non_leveraged_net_inflow_unit"] == "亿元"
    assert item["methodology_id"] == "official-sector-flow-v2"
    assert item["data_quality"] == "official"
    assert payload["by_code"]["BK1036"] is item
    assert payload["merge_map"]["半导体"] is item
    assert payload["merge_map"]["BK0428"]["board_name"] == ""
    assert "file-secret" not in str(payload)


@pytest.mark.parametrize(
    "updates",
    [
        {"board_name": "", "board_code": ""},
        {"trade_date": "2026-07-20"},
        {"non_leveraged_net_inflow": "not-a-number"},
        {"non_leveraged_net_inflow": True},
        {"non_leveraged_net_inflow_unit": "元"},
        {"non_leveraged_net_inflow_unit": ""},
        {"methodology_id": ""},
        {"source_url": "http://insecure.example.test/source"},
        {"published_at": "", "observed_at": ""},
        {"published_at": "2026-07-21 15:00:00", "observed_at": ""},
        {"published_at": "2026-07-20T15:00:00+08:00", "observed_at": ""},
        {"published_at": "2026-07-21T16:10:00+08:00", "observed_at": ""},
        {"data_quality": "estimated"},
        {"data_quality": ""},
    ],
)
def test_incomplete_or_cross_date_row_invalidates_whole_snapshot(monkeypatch, updates):
    second = _valid_row(board_name="电力", board_code="BK0428")
    second.update(updates)
    body = [_valid_row(), second]
    monkeypatch.setattr(
        sector_audited_flow.requests,
        "get",
        lambda *_args, **_kwargs: _Response(body),
    )

    payload = fetch_sector_audited_flow("2026-07-21", configuration=_config())

    assert payload["status"] == "unavailable"
    assert payload["configured"] is True
    assert payload["items"] == []
    assert payload["by_name"] == {}
    assert "审计契约" in payload["notes"][0]


def test_non_object_row_and_duplicate_identity_are_rejected(monkeypatch):
    monkeypatch.setattr(
        sector_audited_flow.requests,
        "get",
        lambda *_args, **_kwargs: _Response([_valid_row(), "invalid"]),
    )
    invalid_type = fetch_sector_audited_flow("2026-07-21", configuration=_config())
    assert invalid_type["status"] == "unavailable"
    assert invalid_type["items"] == []

    monkeypatch.setattr(
        sector_audited_flow.requests,
        "get",
        lambda *_args, **_kwargs: _Response([
            _valid_row(),
            _valid_row(board_name="半导体", board_code="BK9999"),
        ]),
    )
    duplicate = fetch_sector_audited_flow("2026-07-21", configuration=_config())
    assert duplicate["status"] == "unavailable"
    assert "重复板块身份" in duplicate["notes"][0]


def test_source_timestamp_uses_shanghai_date_and_rejects_expired_fact(monkeypatch):
    monkeypatch.setattr(
        sector_audited_flow.requests,
        "get",
        lambda *_args, **_kwargs: _Response([
            _valid_row(
                published_at="2026-07-20T16:30:00-04:00",
                observed_at="",
            ),
        ]),
    )
    same_shanghai_day = fetch_sector_audited_flow(
        "2026-07-21",
        configuration=_config(),
    )
    assert same_shanghai_day["status"] == "ok"

    monkeypatch.setattr(
        sector_audited_flow,
        "_shanghai_now",
        lambda: datetime(2026, 7, 23, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )
    expired = fetch_sector_audited_flow("2026-07-21", configuration=_config())
    assert expired["status"] == "unavailable"
    assert "已过期" in expired["notes"][0]


def test_http_endpoint_redirect_and_adapter_error_all_fail_closed(monkeypatch):
    monkeypatch.setattr(
        sector_audited_flow.requests,
        "get",
        lambda *_args, **_kwargs: pytest.fail("insecure URL must not be called"),
    )
    insecure = fetch_sector_audited_flow(
        "2026-07-21",
        configuration=SectorAuditedFlowConfiguration(
            endpoint="http://licensed.example.test/flow",
            bearer_token="secret",
        ),
    )
    assert insecure["status"] == "unavailable"
    assert insecure["configured"] is False

    monkeypatch.setattr(
        sector_audited_flow.requests,
        "get",
        lambda *_args, **_kwargs: _Response(_valid_row(), status_code=302),
    )
    redirected = fetch_sector_audited_flow("2026-07-21", configuration=_config())
    assert redirected["status"] == "unavailable"
    assert redirected["items"] == []


def test_direct_environment_token_takes_precedence_over_token_file(monkeypatch, tmp_path):
    token_file = tmp_path / "sector-token"
    token_file.write_text("file-secret", encoding="utf-8")
    monkeypatch.setenv("SECTOR_AUDITED_FLOW_URL", "https://licensed.example.test/flow")
    monkeypatch.setenv("SECTOR_AUDITED_FLOW_TOKEN", "direct-secret")
    monkeypatch.setenv("SECTOR_AUDITED_FLOW_TOKEN_FILE", str(token_file))

    config = SectorAuditedFlowConfiguration.from_environment()

    assert config.configured is True
    assert config.bearer_token == "direct-secret"


def test_optional_new_high_and_true_etf_share_evidence_is_audited(monkeypatch):
    monkeypatch.setattr(
        sector_audited_flow.requests,
        "get",
        lambda *_args, **_kwargs: _Response([
            _valid_row(
                new_high_count=8,
                constituent_count=80,
                new_high_window=20,
                etf_share_net_change=1_250_000,
                etf_share_change_pct=1.25,
                etf_id="510300",
                etf_share_unit="份",
                etf_share_base=100_000_000,
                etf_methodology_id="official-etf-shares-v1",
            ),
        ]),
    )

    payload = fetch_sector_audited_flow("2026-07-21", configuration=_config())
    item = payload["items"][0]

    assert payload["status"] == "ok"
    assert item["new_high_count"] == 8
    assert item["constituent_count"] == 80
    assert item["new_high_window"] == 20
    assert item["etf_share_net_change"] == 1_250_000
    assert item["etf_share_change_pct"] == 1.25
    assert item["etf_id"] == "510300"
    assert item["etf_share_unit"] == "份"
    assert item["etf_share_base"] == 100_000_000
    assert item["etf_methodology_id"] == "official-etf-shares-v1"
    assert item["etf_flow_audited"] is True


@pytest.mark.parametrize(
    "updates",
    [
        {"new_high_count": 81, "constituent_count": 80},
        {"new_high_count": 8, "constituent_count": 80, "new_high_window": 10},
        {"new_high_count": 8},
        {"etf_share_net_change": 100},
        {"etf_share_change_pct": 1.0},
        {"etf_share_net_change": 100, "etf_share_change_pct": 1.0},
        {
            "etf_share_net_change": 100,
            "etf_share_change_pct": 1.0,
            "etf_id": "510300",
            "etf_share_unit": "股",
            "etf_share_base": 10_000,
            "etf_methodology_id": "official-etf-shares-v1",
        },
        {
            "etf_share_net_change": -100,
            "etf_share_change_pct": 1.0,
            "etf_id": "510300",
            "etf_share_unit": "份",
            "etf_share_base": 10_000,
            "etf_methodology_id": "official-etf-shares-v1",
        },
    ],
)
def test_optional_structure_and_etf_evidence_fail_closed_when_incomplete(monkeypatch, updates):
    monkeypatch.setattr(
        sector_audited_flow.requests,
        "get",
        lambda *_args, **_kwargs: _Response([_valid_row(**updates)]),
    )

    payload = fetch_sector_audited_flow("2026-07-21", configuration=_config())

    assert payload["status"] == "unavailable"
    assert payload["items"] == []

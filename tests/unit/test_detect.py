"""Proc-protocol v1.1 detect for the prassi Sources (AdE portal month pages, DocTrib interpelli month windows): no network, deterministic."""
from __future__ import annotations

import io
import json
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from jsonschema import Draft202012Validator

from conftest import Run, invoke_raw
from dp_it_prassi_detect import detect

Invoke = Callable[..., Run]
ADE_NULLS = {"tipologie": None, "annoFrom": None, "annoTo": None, "mesi": None, "skipBefore": None, "only": None, "maxDocs": None, "baseUrl": None,
             "tipoEstremi": None, "ente": None, "maxItems": None, "delaySeconds": None, "verifyTls": None, "refreshToken": None, "state": None}


def keys(run: Run) -> list[str]:
    return [item["key"] for item in run.result["items"]]


def test_spec_declares_protocol_11_with_valid_schemas(invoke: Invoke) -> None:
    run = invoke("spec")

    assert run.result["protocol"] == "1.1"
    for name in ("configSchema", "outputSchema"):
        Draft202012Validator.check_schema(run.result[name])
    properties = run.result["configSchema"]["properties"]
    assert {"tipologie", "tipoEstremi", "annoFrom", "annoTo", "mesi", "skipBefore", "only", "maxDocs", "maxItems", "refreshToken", "state"} <= set(properties)


def test_ade_three_types_one_year_twelve_months_is_thirty_six_items(invoke: Invoke) -> None:
    run = invoke("run", tipologie=["circolari", "risoluzioni", "provvedimenti"], annoFrom=2025, annoTo=2025)

    assert len(keys(run)) == 36
    assert keys(run)[0] == "ade:circolari:2025:01|v1"
    assert keys(run)[11] == "ade:circolari:2025:12|v1"
    assert keys(run)[12] == "ade:risoluzioni:2025:01|v1"
    assert keys(run)[-1] == "ade:provvedimenti:2025:12|v1"
    assert run.result["metrics"] == {"items": 36, "newItems": 36}


def test_ade_item_meta_url_and_kind(invoke: Invoke) -> None:
    run = invoke("run", tipologie=["risoluzioni"], annoFrom=2025, annoTo=2025, mesi=[10])

    item = run.result["items"][0]
    assert item["key"] == "ade:risoluzioni:2025:10|v1"
    assert item["meta"] == {"kind": "ade", "itemId": "ade:risoluzioni:2025:10", "version": "1", "tipologia": "risoluzioni", "anno": 2025, "mese": 10}
    assert item["url"] == "https://www.agenziaentrate.gov.it/portale/ottobre-2025-risoluzioni"


def test_ade_years_and_months_are_ordered_and_stable(invoke: Invoke) -> None:
    first = invoke("run", tipologie=["circolari", "risoluzioni"], annoFrom=2024, annoTo=2025, mesi=[3, 1])
    second = invoke("run", tipologie=["circolari", "risoluzioni"], annoFrom=2024, annoTo=2025, mesi=[3, 1])

    assert keys(first) == keys(second)
    assert keys(first) == [
        "ade:circolari:2024:01|v1", "ade:circolari:2024:03|v1", "ade:circolari:2025:01|v1", "ade:circolari:2025:03|v1",
        "ade:risoluzioni:2024:01|v1", "ade:risoluzioni:2024:03|v1", "ade:risoluzioni:2025:01|v1", "ade:risoluzioni:2025:03|v1",
    ]


def test_ade_default_skip_before_drops_the_months_the_portal_does_not_have(invoke: Invoke) -> None:
    run = invoke("run", tipologie=["circolari"], annoFrom=2018, annoTo=2019)

    assert keys(run) == [f"ade:circolari:2019:{month:02d}|v1" for month in range(6, 13)]


def test_ade_skip_before_can_be_disabled_to_show_the_true_coverage(invoke: Invoke) -> None:
    run = invoke("run", tipologie=["circolari"], annoFrom=2018, annoTo=2018, skipBefore="2000-01")

    assert len(keys(run)) == 12


def test_default_years_are_the_current_year(invoke: Invoke, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detect, "current_year", lambda: 2026)

    run = invoke("run", tipologie=["circolari"], mesi=[1])

    assert keys(run) == ["ade:circolari:2026:01|v1"]


def test_doctrib_one_item_per_type_and_month(invoke: Invoke) -> None:
    run = invoke("run", tipoEstremi=["Interpello"], annoFrom=2025, annoTo=2025, mesi=[9, 10])

    assert keys(run) == ["doctrib:interpello:2025-09|v1", "doctrib:interpello:2025-10|v1"]
    item = run.result["items"][1]
    assert item["meta"] == {"kind": "doctrib", "itemId": "doctrib:interpello:2025-10", "version": "1", "tipoEstremi": "Interpello", "ente": "Agenzia delle Entrate",
                            "anno": 2025, "mese": 10}
    assert item["url"].startswith("https://def.finanze.it/DocTribFrontend/")


def test_doctrib_ente_is_passed_to_the_item(invoke: Invoke) -> None:
    run = invoke("run", tipoEstremi=["Interpello"], ente="Agenzia delle Dogane e dei Monopoli", annoFrom=2025, annoTo=2025, mesi=[1])

    assert run.result["items"][0]["meta"]["ente"] == "Agenzia delle Dogane e dei Monopoli"


def test_sample_filters_pass_through_in_meta_without_trimming_the_catalog(invoke: Invoke) -> None:
    ade = invoke("run", tipologie=["circolari"], annoFrom=2025, annoTo=2025, maxDocs=2, only=[{"tipo": "circolari", "stem": "Circolare_n_9"}])
    doctrib = invoke("run", tipoEstremi=["Interpello"], annoFrom=2025, annoTo=2025, maxItems=3, only=["274"])

    assert len(keys(ade)) == 12
    assert ade.result["items"][0]["meta"]["maxDocs"] == 2
    assert ade.result["items"][0]["meta"]["only"] == [{"tipo": "circolari", "stem": "Circolare_n_9"}]
    assert len(keys(doctrib)) == 12
    assert doctrib.result["items"][0]["meta"]["maxItems"] == 3
    assert doctrib.result["items"][0]["meta"]["only"] == ["274"]


def test_refresh_token_changes_only_the_version(invoke: Invoke) -> None:
    plain = invoke("run", tipologie=["risoluzioni"], annoFrom=2025, annoTo=2025, mesi=[10])
    refreshed = invoke("run", tipologie=["risoluzioni"], annoFrom=2025, annoTo=2025, mesi=[10], refreshToken="2026-11-12")

    version = refreshed.result["items"][0]["meta"]["version"]
    assert version.startswith("1-")
    assert len(version) == 10
    assert keys(refreshed) == [f"ade:risoluzioni:2025:10|v{version}"]
    assert plain.result["items"][0]["meta"]["itemId"] == refreshed.result["items"][0]["meta"]["itemId"]
    assert invoke("run", tipologie=["risoluzioni"], annoFrom=2025, annoTo=2025, mesi=[10], refreshToken="2026-11-12").result["items"][0]["meta"]["version"] == version


def test_state_skips_a_known_key(invoke: Invoke) -> None:
    run = invoke("run", tipologie=["risoluzioni"], annoFrom=2025, annoTo=2025, mesi=[9, 10], state={"seen": ["ade:risoluzioni:2025:09|v1"]})

    assert keys(run) == ["ade:risoluzioni:2025:10|v1"]
    assert run.result["state"]["seen"] == ["ade:risoluzioni:2025:09|v1", "ade:risoluzioni:2025:10|v1"]
    assert run.result["metrics"] == {"items": 2, "newItems": 1}


def test_execute_is_an_alias_of_run(invoke: Invoke) -> None:
    assert keys(invoke("execute", tipologie=["risoluzioni"], annoFrom=2025, annoTo=2025, mesi=[10])) == ["ade:risoluzioni:2025:10|v1"]


def test_check_validates_without_items(invoke: Invoke) -> None:
    assert invoke("check", tipologie=["circolari"], annoFrom=2025, annoTo=2025).result == {"ok": True}
    assert invoke("check", tipologie=["circolari"], annoFrom=2026, annoTo=2025).error["code"] == "config_error"


@pytest.mark.parametrize(
    "params",
    [
        {"tipologie": ["circolari"], "annoFrom": 2026, "annoTo": 2025},
        {"tipologie": ["circolari"], "annoFrom": 1999, "annoTo": 2025},
        {"tipologie": ["circolari"], "annoFrom": 2025, "annoTo": 2200},
        {"tipologie": ["circolari"], "annoFrom": "2025", "annoTo": 2025},
        {"tipologie": ["circolari"], "annoFrom": 2025, "annoTo": 2025, "mesi": [0]},
        {"tipologie": ["circolari"], "annoFrom": 2025, "annoTo": 2025, "mesi": [13]},
        {"tipologie": ["circolari"], "annoFrom": 2025, "annoTo": 2025, "mesi": "1"},
        {"tipologie": ["gazzette"], "annoFrom": 2025, "annoTo": 2025},
        {"tipologie": [], "annoFrom": 2025, "annoTo": 2025},
        {"tipologie": ["circolari"], "annoFrom": 2025, "annoTo": 2025, "skipBefore": "2019/06"},
        {"tipologie": ["circolari"], "annoFrom": 2025, "annoTo": 2025, "maxDocs": 0},
        {"tipologie": ["circolari"], "annoFrom": 2025, "annoTo": 2025, "only": [{"tipo": "circolari"}]},
        {"tipoEstremi": ["Nota"], "annoFrom": 2025, "annoTo": 2025},
        {"tipoEstremi": ["Interpello"], "annoFrom": 2026, "annoTo": 2025},
        {"tipoEstremi": ["Interpello"], "annoFrom": 2025, "annoTo": 2025, "maxItems": 0},
        {"tipoEstremi": ["Interpello"], "tipologie": ["circolari"]},
        {},
    ],
)
def test_invalid_config_is_a_config_error(invoke: Invoke, params: dict[str, object]) -> None:
    run = invoke("run", **params)

    assert run.returncode == 1
    assert run.error["code"] == "config_error"


def test_invalid_state_and_refresh_token_are_config_errors(invoke: Invoke) -> None:
    assert invoke("run", tipologie=["circolari"], annoFrom=2025, annoTo=2025, state={"seen": [1]}).error["code"] == "config_error"
    assert invoke("run", tipologie=["circolari"], annoFrom=2025, annoTo=2025, refreshToken=5).error["code"] == "config_error"


def test_config_keys_the_host_resolves_to_null_count_as_absent(invoke: Invoke, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detect, "current_year", lambda: 2026)

    ade = invoke("run", **{**ADE_NULLS, "tipologie": ["risoluzioni"], "annoFrom": 2025, "annoTo": 2025, "mesi": [10]})
    doctrib = invoke("run", **{**ADE_NULLS, "tipoEstremi": ["Interpello"], "annoFrom": 2025, "annoTo": 2025, "mesi": [10]})

    assert keys(ade) == ["ade:risoluzioni:2025:10|v1"]
    assert keys(doctrib) == ["doctrib:interpello:2025-10|v1"]
    assert invoke("run", **ADE_NULLS).error["code"] == "config_error"  # nothing at all is still an error


def test_every_item_carries_its_kind_and_a_non_empty_url(invoke: Invoke) -> None:
    for params in ({"tipologie": ["circolari", "provvedimenti"], "annoFrom": 2025, "annoTo": 2025},
                   {"tipoEstremi": ["Interpello"], "annoFrom": 2025, "annoTo": 2025}):
        run = invoke("run", **params)
        assert all(item["meta"]["kind"] in {"ade", "doctrib"} and item["url"] and item["meta"]["itemId"] for item in run.result["items"])


def test_the_catalog_size_is_bounded(invoke: Invoke) -> None:
    run = invoke("run", tipologie=["circolari", "risoluzioni", "provvedimenti"], annoFrom=2000, annoTo=2100)

    assert run.error["code"] == "config_error"


def test_today_default_matches_the_clock() -> None:
    assert detect.current_year() == datetime.now(tz=UTC).year


def test_protocol_loop_emits_one_error_line_for_a_bad_request() -> None:
    run = invoke_raw("not json\n")

    assert run.returncode == 1
    assert [line["type"] for line in run.lines] == ["error"]


def test_protocol_loop_unknown_action_and_terminate() -> None:
    run = invoke_raw(json.dumps({"_action": "frobnicate", "_meta": {"executionId": "e"}}) + "\n" + json.dumps({"_action": "terminate", "_meta": {"executionId": "e"}}) + "\n")

    assert [line["type"] for line in run.lines] == ["error", "result"]


def test_stdout_carries_protocol_events_only(invoke: Invoke) -> None:
    run = invoke("run", tipologie=["circolari"], annoFrom=2025, annoTo=2025, mesi=[1])

    assert all(line.get("type") in {"progress", "result", "error"} for line in run.lines)
    assert io.StringIO(run.stdout).read().count("\n") == len(run.lines)

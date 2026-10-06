"""The graph-extraction scorer and the old-prompt adapter, offline. The recorded-output floors guard the shipped prompt."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import graph_eval as ge  # noqa: E402
from personal_os.graph_learn import canonical_type, normalize_predicate  # noqa: E402

# Measured on the recorded live run of the new prompt (deepseek-v4-flash, 2026-10-06): F1 0.941, negatives clean 1.0,
# invalidations exact 1.0; the baseline prompt scored F1 0.25 and invalidations exact 0.14. A prompt edit that drops below
# these needs a fresh --live --record run and a reason.
F1_FLOOR = 0.9
NEGATIVES_FLOOR = 1.0
INVALIDATION_FLOOR = 1.0

CASES = {c["id"]: c for c in ge.load_cases()}


def _t(s: str, p: str, o: str, conf: float = 1.0, **kw: Any) -> dict[str, Any]:
    return {"subject": s, "predicate": p, "object": o, "confidence": conf, **kw}


def test_canon_case_whitespace_quotes_the_and_aliases() -> None:
    assert ge.canon("  The   Rust ") == "rust"
    assert ge.canon("“Atlas”.") == "atlas"
    assert ge.canon("Me") == "user" and ge.canon("the user") == "user"
    assert ge.canon("Sam", {"sam": "Samantha Ortiz"}) == "samantha ortiz"
    assert ge.canon("Café") == ge.canon("Café")


def test_normalize_predicate() -> None:
    assert normalize_predicate("works at") == ("works_at", "")
    assert normalize_predicate("Lives in") == ("located_in", "")
    assert normalize_predicate("is friends with") == ("knows", "")
    assert normalize_predicate("acquired") == ("related_to", "acquired")
    assert normalize_predicate(None) == ("", "") and normalize_predicate(3) == ("", "")


def test_canonical_type_legacy_mapping() -> None:
    assert canonical_type("organization") == "org" and canonical_type(" Person ") == "person"
    assert canonical_type("concept") == "topic" and canonical_type("other") == "topic"
    assert canonical_type("zzz") == "topic" and canonical_type(None) == "topic"


def test_perfect_prediction_with_alias_optional_and_symmetric() -> None:
    case = CASES["alias_person"]
    pred = {"triples": [_t("Sam", "member_of", "Perception team", subject_type="person", object_type="org"),
                        _t("Perception team", "part_of", "Acme")], "ended": []}
    r = ge.score_case(case, pred)
    assert (r["tp"], r["fp"], r["fn"]) == (1, 0, 0)  # alias accepted; the optional triple is no false positive
    assert r["type_checked"] == 2 and r["type_correct"] == 2

    case = CASES["person_role_org"]
    pred = {"triples": [_t("Priya Raman", "works_at", "Northwind Logistics", subject_type="person", object_type="org"),
                        _t("Priya", "knows", "User", object_type="person")], "ended": []}  # knows stated the other way round
    r = ge.score_case(case, pred)
    assert (r["tp"], r["fp"], r["fn"]) == (2, 0, 0)
    t = ge.score_all([case], {case["id"]: pred})
    assert t["precision"] == t["recall"] == t["f1"] == 1.0


def test_triple_below_confidence_floor_is_ignored() -> None:
    case = CASES["user_employer"]
    pred = {"triples": [_t("User", "works_at", "Brightline Health", 0.2)], "ended": []}
    r = ge.score_case(case, pred)
    assert (r["tp"], r["fp"], r["fn"], r["kept"]) == (0, 0, 1, 0)


def test_supersede_is_simulated_for_single_valued_predicates() -> None:
    case = CASES["employer_change"]
    pred = {"triples": [_t("Priya Raman", "works_at", "Halden Freight")], "ended": []}
    r = ge.score_case(case, pred)
    assert r["inv_exact"] and r["inv_tp"] == 1 and r["tp"] == 1
    # an explicit ended entry in the old prompt's wording also counts
    r = ge.score_case(case, {"triples": [], "ended": [{"subject": "Priya", "predicate": "works for", "object": "Northwind Logistics"}]})
    assert r["inv_exact"]


def test_negative_case_false_positive_lowers_negatives_clean() -> None:
    cases = [CASES["neg_chitchat"], CASES["neg_weather_lookup"]]
    pred = {"neg_weather_lookup": {"triples": [_t("Tokyo", "related_to", "Weather")], "ended": []}}
    t = ge.score_all(cases, pred)
    assert t["fp"] == 1 and t["negatives_clean"] == 0.5 and t["precision"] == 0.0


def test_old_adapter_maps_relations_and_replaces() -> None:
    raw = json.dumps({
        "entities": [{"label": "Priya Raman", "type": "person"}, {"label": "Halden Freight", "type": "company"}],
        "relations": [{"source": "Priya Raman", "target": "Halden Freight", "relation": "works at",
                       "replaces": "Priya Raman|works at|Northwind Logistics"}],
        "ended": [{"source": "User", "target": "Berlin", "relation": "lives in"}],
    })
    out = ge.old_output_to_triples("```json\n" + raw + "\n```")
    assert out["triples"][0]["predicate"] == "works_at" and out["triples"][0]["object_type"] == "org"
    assert {"subject": "Priya Raman", "predicate": "works at", "object": "Northwind Logistics"} in out["ended"]
    assert len(out["ended"]) == 2


@pytest.mark.parametrize("raw", ["not json", "", None, "[1, 2]", '{"relations": [1, "x", {"source": 3}], "entities": [4], "ended": "no"}'])
def test_old_adapter_survives_junk(raw: Any) -> None:
    assert ge.old_output_to_triples(raw) == {"triples": [], "ended": []}


def _recorded(prompt: str) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    path = ge.FIXTURES / f"recorded_{prompt}.json"
    if not path.exists():
        pytest.skip(f"{path.name} not recorded yet")
    outputs = json.loads(path.read_text())["outputs"]
    cases = ge.load_cases()
    return cases, {c["id"]: ge.to_triples(prompt, outputs.get(c["id"], ""), c) for c in cases}


def test_recorded_old_prompt_scores() -> None:
    cases, predicted = _recorded("old")
    assert ge.score_all(cases, predicted)["cases"]


def test_recorded_new_prompt_meets_floor() -> None:
    cases, predicted = _recorded("new")
    t = ge.score_all(cases, predicted)
    assert t["f1"] >= F1_FLOOR and t["negatives_clean"] >= NEGATIVES_FLOOR and t["inv_exact_rate"] >= INVALIDATION_FLOOR

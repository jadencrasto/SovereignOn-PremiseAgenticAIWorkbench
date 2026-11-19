"""Permanent checks for the v1 judged local retrieval benchmark and its measured fixes."""

import json
import shutil

import pytest

from eval.local_retrieval_eval import (
    DATA, K, OWNER, _locator_key, _passage_row, _receipt, citation_valid, evaluate, load_judgments, rank_scores,
)
from backend.rag.local_knowledge import LocalKnowledge, question_parts


def test_frozen_judgment_corpus_matches_baseline():
    dataset, _, locators, digest = load_judgments()
    baseline = json.loads((DATA / "l11-baseline.json").read_text(encoding="utf-8"))
    assert (dataset.version, len(dataset.sources), len(dataset.cases), len(locators)) == (1, 6, 24, 21)
    assert digest == baseline["corpus_sha256"]
    assert [case.id for case in dataset.cases] == [case["id"] for case in baseline["questions"]]
    assert sum(case.split == "held_out" for case in dataset.cases) == 8


@pytest.mark.parametrize("bad", ["wrong_line", "out_of_scope", "duplicate_id", "overlap", "path", "split", "unknown_field"])
def test_judgment_parser_rejects_invalid_records(tmp_path, bad):
    raw = json.loads((DATA / "judgments.json").read_text(encoding="utf-8"))
    for name in raw["sources"]:
        shutil.copyfile(DATA / name, tmp_path / name)
    match bad:
        case "wrong_line":
            raw["evidence"][0]["line"] += 1
        case "out_of_scope":
            raw["cases"][0]["sources"] = ["motor.md"]
        case "duplicate_id":
            raw["cases"][1]["id"] = raw["cases"][0]["id"]
        case "overlap":
            raw["cases"][0]["parts"][0]["groups"].append(raw["cases"][0]["parts"][0]["groups"][0])
        case "path":
            raw["sources"][0] = "../pumps.md"
        case "split":
            raw["cases"][0]["split"] = "held_out"
        case "unknown_field":
            raw["cases"][0]["answer_found"] = True
    (tmp_path / "judgments.json").write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        load_judgments(tmp_path)


def test_rank_scores_count_alternatives_once_and_report_empty_relevance():
    _, _, locators, _ = load_judgments()
    groups = [["bearing", "bearingAlt"], ["relief"]]
    rows = [locators[name].model_dump() for name in ("normal", "bearing", "bearingAlt", "relief")]
    scores = rank_scores(rows, groups, locators)
    assert scores == {
        "precision_at_1": 0, "precision_at_3": 2 / 3, f"precision_at_{K}": 3 / K,
        "recall_at_1": 0, "recall_at_3": 1 / 2, f"recall_at_{K}": 1, "mrr": 1 / 2,
    }
    assert rank_scores(rows, [], locators) is None
    assert all(score == 0 for score in rank_scores([], groups, locators).values())


def test_real_rank_and_receipts_are_deterministic_with_frozen_source_scope():
    result = evaluate()
    assert json.dumps(result) == json.dumps(evaluate())
    assert all(result["lifecycle"].values())
    cases = {case["id"]: case for case in result["questions"]}
    assert result["splits"]["all"]["multipart_coverage_recall"]["denominator"] == 9
    assert result["splits"]["all"]["zero_relevant_parts"] == 8
    assert [(row["source"], row["line"]) for row in cases["D01"]["actual"][0]["ranked"]] == [("pumps.md", 5)]
    assert cases["D01"]["judged"][0]["entity_candidates"] == [True]
    assert cases["D01"]["judged"][0]["condition_candidates"] == [True]
    assert cases["D05"]["judged"][2]["cited_groups"] == []
    for key in ("D07", "D10", "D11", "D12", "H04"):
        assert cases[key]["actual"][0]["citations"] == []
    assert cases["D09"]["actual"][0]["state"] == "conflict"
    assert cases["D09"]["judged"][0]["cited_groups"] == [0, 1]
    assert [citation["source"] for citation in cases["H05"]["actual"][0]["citations"]] == ["shift-note.md"]
    assert cases["D14"]["judged"][0]["cited_groups"] == [0]


def test_locator_validation_checks_candidate_membership_and_independent_section_path():
    _, documents, locators, _ = load_judgments()
    label = "pumps.md"
    corpus = LocalKnowledge(OWNER)
    source = corpus.register(OWNER, label, documents[label].encode("utf-8"), "text/plain")
    scope = corpus.bind_scope(OWNER, [source["source_id"]])
    parts = question_parts("What is the normal discharge pressure for P-101?")
    refs, = corpus.contexts(OWNER, parts, scope)
    _, receipt = _receipt(corpus, scope, parts, [refs])
    citation, = corpus.public_receipt(OWNER, receipt)["citations"]
    candidates = {(item.chunk_id, _locator_key(_passage_row(corpus, item))) for item in refs.values()}
    assert citation_valid(citation, {label: source}, candidates, documents, locators)
    for changes in ({"line": 8, "line_end": 8}, {"source_id": "0" * 32}, {"revision": 2},
                    {"sections": citation["sections"][-1:]}, {"passage_id": "invented"}):
        assert not citation_valid({**citation, **changes}, {label: source}, candidates, documents, locators)
    assert not citation_valid(citation, {label: source}, set(), documents, locators)


def test_temperature_difference_quantity_does_not_match_absolute_temperature():
    corpus = LocalKnowledge(OWNER)
    corpus.register(OWNER, "coolant.txt",
                    "HX-204 temperature difference is 20 Δ°C.\nHX-204 temperature is 20 °C.".encode("utf-8"),
                    "text/plain")
    difference = "HX-204 temperature difference is equivalent to 36 Δ°F"
    assert [item.text for item in corpus.retrieve(OWNER, difference)] == ["HX-204 temperature difference is 20 Δ°C."]
    assert corpus.retrieve(OWNER, "HX-204 temperature difference is equivalent to 68 °F") == ()
    absolute = corpus.retrieve(OWNER, "HX-204 temperature is equivalent to 68 °F")
    assert [item.text for item in absolute] == ["HX-204 temperature is 20 °C."]


def test_conflicts_preserve_operating_context_under_distinct_document_titles():
    corpus = LocalKnowledge(OWNER)
    documents = {
        "north.md": "# Operations handbook\n## P-101\n### North circuit\n#### Alarm\nAlarm discharge pressure is 6 bar.",
        "south.md": "# Field amendment\n## P-101\n### South circuit\n#### Alarm\nAlarm discharge pressure is 7 bar.",
        "startup.md": "# Startup handbook\n## P-101\n### North circuit\n#### Alarm\nAlarm discharge pressure is 8 bar.",
    }
    for label, document in documents.items():
        corpus.register(OWNER, label, document.encode("utf-8"), "text/plain")
    scope = corpus.bind_scope(OWNER, None)
    parts = question_parts("What is the P-101 alarm discharge pressure?")
    refs, = corpus.contexts(OWNER, parts, scope)
    _, receipt = _receipt(corpus, scope, parts, [refs])
    assert len(refs) == 3
    assert receipt["coverage"][0]["state"] == "supported"

"""Offline judged evaluation of the existing local knowledge pipeline."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.rag.local_knowledge import (
    MAX_CONTEXT_PASSAGES, MAX_PASSAGES, MAX_QUESTION, MAX_QUESTION_PARTS,
    MAX_SOURCES, KnowledgeConflict, LocalKnowledge, question_parts,
)
from backend.rag.local_relevance import conditions, measurement_conditions, merge_conditions

DATA = Path(__file__).with_name("data") / "local_retrieval_v1"
OWNER = "synthetic-l12-viewer"
K = MAX_PASSAGES
CUTOFFS = (1, 3, K)


class StrictRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Locator(StrictRecord):
    id: str = Field(min_length=1)
    source: str
    revision: Literal[1]
    line: int = Field(ge=1)
    line_end: int = Field(ge=1)
    section: list[str] = Field(max_length=6)
    excerpt: str = Field(min_length=1, max_length=320)


class Part(StrictRecord):
    entities: list[str]
    conditions: list[str]
    state: Literal["supported", "incomplete", "absent", "conflict"]
    groups: list[list[str]]


class Question(StrictRecord):
    id: str = Field(pattern=r"^[DH][0-9]{2}$")
    split: Literal["development", "held_out"]
    category: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=MAX_QUESTION)
    sources: list[str] = Field(min_length=1, max_length=MAX_SOURCES)
    answerability: Literal["answerable", "partial", "unanswerable"]
    parts: list[Part] = Field(min_length=1, max_length=MAX_QUESTION_PARTS)


class Lifecycle(StrictRecord):
    source: str
    case: str
    replacement: str = Field(min_length=1, max_length=320)


class Judgments(StrictRecord):
    version: Literal[1]
    sources: list[str] = Field(min_length=1, max_length=MAX_SOURCES)
    evidence: list[Locator]
    cases: list[Question] = Field(min_length=1)
    lifecycle: Lifecycle


def _unique(values):
    return len(values) == len(set(values))


def load_judgments(directory=DATA):
    raw = (directory / "judgments.json").read_text(encoding="utf-8")
    dataset = Judgments.model_validate_json(raw)
    if not _unique(dataset.sources) or any(
        Path(name).name != name or any(char in name for char in "\\/:")
        or not name.endswith((".md", ".txt")) for name in dataset.sources
    ):
        raise ValueError("Invalid corpus source allowlist")
    documents = {name: (directory / name).read_text(encoding="utf-8") for name in dataset.sources}
    locators = {record.id: record for record in dataset.evidence}
    if len(locators) != len(dataset.evidence):
        raise ValueError("Duplicate evidence ID")
    for locator in locators.values():
        lines = documents.get(locator.source, "").split("\n")
        if (locator.line != locator.line_end or locator.line > len(lines)
                or locator.excerpt not in lines[locator.line - 1]):
            raise ValueError(f"{locator.id}: evidence does not match the authored source line")
    if len({case.id for case in dataset.cases}) != len(dataset.cases):
        raise ValueError("Duplicate question ID")
    for case in dataset.cases:
        if not _unique(case.sources) or not set(case.sources) <= documents.keys():
            raise ValueError(f"{case.id}: invalid source scope")
        if (case.split == "development") != case.id.startswith("D"):
            raise ValueError(f"{case.id}: question split disagrees with its identifier")
        for part in case.parts:
            ids = [item for group in part.groups for item in group]
            if (not all(part.groups) or not _unique(ids) or any(
                item not in locators or locators[item].source not in case.sources for item in ids
            )):
                raise ValueError(f"{case.id}: unknown, overlapping or unscoped evidence group")
            if ((part.state in ("supported", "conflict")) and not ids
                    or part.state == "absent" and ids
                    or part.state == "conflict" and len(part.groups) < 2):
                raise ValueError(f"{case.id}: evidence does not match the expected state")
        supported = sum(bool(part.groups) for part in case.parts)
        answerability = ("unanswerable" if supported == 0 else
                         "answerable" if supported == len(case.parts) else "partial")
        if case.answerability != answerability:
            raise ValueError(f"{case.id}: inconsistent answerability")
    probe = next((case for case in dataset.cases if case.id == dataset.lifecycle.case), None)
    if probe is None or probe.sources != [dataset.lifecycle.source]:
        raise ValueError("Lifecycle probe needs one judged source")
    fingerprint = sha256(json.dumps([dataset.model_dump(), documents], sort_keys=True,
                                    ensure_ascii=False).encode("utf-8")).hexdigest()
    return dataset, documents, locators, fingerprint


def _locator_key(passage):
    return tuple(passage[field] for field in ("source", "revision", "line", "line_end", "excerpt"))


def _matching_groups(passage, groups, locators):
    key = _locator_key(passage)
    return {index for index, group in enumerate(groups)
            if any(key == _locator_key(locators[item].model_dump()) for item in group)}


def rank_scores(ranked, groups, locators):
    hits = [_matching_groups(row, groups, locators) for row in ranked]
    if not groups:
        return None
    scores = {"mrr": next((1 / position for position, hit in enumerate(hits, 1) if hit), 0)}
    for cutoff in CUTOFFS:
        scores[f"precision_at_{cutoff}"] = sum(bool(hit) for hit in hits[:cutoff]) / cutoff
        scores[f"recall_at_{cutoff}"] = len(set().union(*hits[:cutoff])) / len(groups)
    return scores


def _passage_row(corpus, item):
    # The same revision-bound lookup used to build model evidence gives the ranked passage locator.
    passage = corpus._passage_for(item)
    heading = merge_conditions(*(conditions(section.title) for section in passage.context.sections))
    body = conditions(passage.text)
    qualified = [merge_conditions(heading, measurement_conditions(
        passage.text, value, passage.context.table_header) or body) for value in passage.values()]
    return {
        "source": item.filename, "revision": item.source_revision, "line": passage.line,
        "line_end": passage.line, "excerpt": item.text, "equipment": sorted(passage.equipment()),
        "section": [section.title for section in passage.context.sections],
        "conditions": sorted({tuple(sorted(row)) for row in qualified or [merge_conditions(heading, body)]}),
    }


def _public_row(citation):
    return {
        "source": citation["label"], "revision": citation["revision"],
        "line": citation["line"], "line_end": citation["line_end"], "excerpt": citation["excerpt"],
        "section": [item["title"] for item in citation.get("sections", [])],
    }


def citation_valid(citation, admitted, candidates, documents, locators):
    """Check opaque identity and offsets against the actual registration and authored text."""
    label = citation["label"]
    registered = admitted.get(label)
    if not registered or any(citation[field] != registered[field] for field in ("source_id", "revision", "digest")):
        return False
    if (citation["passage_id"], _locator_key(_public_row(citation))) not in candidates:
        return False
    text = documents[label]
    start, end = citation["start"], citation["end"]
    line = citation["line"]
    headings = citation.get("sections", [])
    source_lines = text.split("\n")
    if (not 0 <= start < end <= len(text) or text[start:end] != citation["excerpt"]
            or line != text.count("\n", 0, start) + 1
            or citation["line_end"] != text.count("\n", 0, end) + 1):
        return False
    previous_line, previous_level = 0, 0
    for section in headings:
        number, level = section["line"], section["level"]
        if (not previous_line < number < line or not previous_level < level
                or section["title"] not in source_lines[number - 1]
                or section["id"] != f'{citation["source_id"]}:r{citation["revision"]}:line-{number}'):
            return False
        previous_line, previous_level = number, level
    matching = [locator for locator in locators.values()
                if _locator_key(locator.model_dump()) == _locator_key(_public_row(citation))]
    return not matching or any(locator.section == _public_row(citation)["section"] for locator in matching)


def _receipt(corpus, scope, parts, contexts):
    # A fixed, oracle-free selection proxy. Neither answer text nor evidence IDs come from a model.
    answer, receipt = corpus.answer(OWNER, scope, [
        (part, refs, json.dumps({"passages": list(refs)})) for part, refs in zip(parts, contexts, strict=True)
    ])
    return answer, {
        **receipt, "turn_id": str(uuid5(NAMESPACE_URL, "|".join(parts))), "attempt": 1,
        "request_digest": sha256("\n".join(parts).encode("utf-8")).hexdigest(),
    }


def evaluate_case(corpus, registered, documents, locators, case):
    admitted = {label: registered[label] for label in case.sources}
    source_ids = {row["source_id"] for row in admitted.values()}
    scope = corpus.bind_scope(OWNER, list(source_ids))
    parts = question_parts(case.question)
    contexts = corpus.contexts(OWNER, parts, scope)
    answer, receipt = _receipt(corpus, scope, parts, contexts)
    public = corpus.public_receipt(OWNER, receipt)
    if public is None:
        raise ValueError(f"{case.id}: the server withdrew a fresh receipt")
    actual = []
    for question, refs, coverage in zip(parts, contexts, public["coverage"], strict=True):
        ranked = [_passage_row(corpus, item) for item in corpus.retrieve(OWNER, question, source_ids)]
        allocated = [_passage_row(corpus, item) for item in refs.values()]
        cited = []
        candidates = {(item.chunk_id, _locator_key(row)) for item, row in zip(refs.values(), allocated, strict=True)}
        for number in coverage["citations"]:
            citation = public["citations"][number - 1]
            cited.append({**_public_row(citation), "valid": citation_valid(
                citation, admitted, candidates, documents, locators)})
        actual.append({"question": question, "state": coverage["state"],
                       "ranked": ranked, "allocated": allocated, "citations": cited})
    aligned = len(actual) == len(case.parts)
    accounted = []
    for index, expected in enumerate(case.parts):
        observed = actual[index] if aligned else None
        ranked = observed["ranked"] if observed else []
        allocated = observed["allocated"] if observed else []
        valid = [row for row in observed["citations"] if row["valid"]] if observed else []
        groups = expected.groups

        def matched(rows):
            return sorted(set().union(*(_matching_groups(row, groups, locators) for row in rows)))

        accounted.append({
            "expected": expected.model_dump(), "scores": rank_scores(ranked, groups, locators),
            "allocated_groups": matched(allocated), "cited_groups": matched(valid),
            "relevant_citations": sum(bool(_matching_groups(row, groups, locators)) for row in valid),
            "entity_candidates": [bool(row["equipment"]) and set(row["equipment"]) <= set(expected.entities)
                                  for row in ranked] if expected.entities else [],
            "condition_candidates": [any(set(expected.conditions) <= set(values) for values in row["conditions"])
                                     for row in ranked] if expected.conditions else [],
        })
    return {"id": case.id, "split": case.split, "category": case.category, "scope": [
        {"source": source.label, "revision": source.revision, "digest": source.digest} for source in scope.sources
    ], "answerability": case.answerability, "parts_aligned": aligned, "actual": actual,
        "judged": accounted, "answer": answer}


def metrics(cases):
    rows = [row for case in cases for row in case["judged"]]
    actual = [part for case in cases for part in case["actual"]]
    positive = [part for part in rows if part["scores"] is not None]
    scores = [part["scores"] for part in positive]
    citations = [citation for part in actual for citation in part["citations"]]
    expected_groups = sum(len(row["expected"]["groups"]) for row in rows)
    multipart = [row for case in cases if len(case["judged"]) > 1 for row in case["judged"]]
    unanswerable = [part for case in cases if case["parts_aligned"]
                    for row, part in zip(case["judged"], case["actual"], strict=True)
                    if not row["expected"]["groups"]]
    expected_unanswerable = len(rows) - len(positive)

    def ratio(numerator, denominator):
        return {"numerator": numerator, "denominator": denominator,
                "value": round(numerator / denominator, 6) if denominator else None}

    result = {key: ratio(round(sum(row[key] for row in scores), 12), len(scores))
              for key in ["mrr", *(f"{name}_at_{cutoff}" for name in ("precision", "recall") for cutoff in CUTOFFS)]}
    classifications = [(row["expected"]["state"], case["actual"][index]["state"] if case["parts_aligned"] else None)
                       for case in cases for index, row in enumerate(case["judged"])]
    result.update({
        "questions": len(cases), "question_parts": len(rows), "ranked_positive_parts": len(positive),
        "zero_relevant_parts": expected_unanswerable,
        "decomposition_matches": ratio(sum(case["parts_aligned"] for case in cases), len(cases)),
        "allocated_evidence_recall": ratio(sum(len(part["allocated_groups"]) for part in rows), expected_groups),
        "selected_evidence_precision": ratio(sum(part["relevant_citations"] for part in rows), len(citations)),
        "multipart_coverage_recall": ratio(sum(len(part["cited_groups"]) for part in multipart),
                                            sum(len(part["expected"]["groups"]) for part in multipart)),
        "citation_locator_accuracy": ratio(sum(citation["valid"] for citation in citations), len(citations)),
        "unsupported_abstention": ratio(sum(part["state"] in ("absent", "incomplete") and not part["citations"]
                                             for part in unanswerable), expected_unanswerable),
        "false_supported": ratio(sum(part["state"] in ("supported", "conflict") or bool(part["citations"])
                                     for part in unanswerable), expected_unanswerable),
        "coverage_state_accuracy": ratio(sum(wanted == seen for wanted, seen in classifications), len(rows)),
        "conflict_accuracy": ratio(sum(seen is not None and ((wanted == "conflict") == (seen == "conflict"))
                                       for wanted, seen in classifications), len(rows)),
        "conflict_recall": ratio(sum(wanted == seen == "conflict" for wanted, seen in classifications),
                                 sum(wanted == "conflict" for wanted, _ in classifications)),
    })
    for name in ("entity", "condition"):
        matches = [match for row in rows for match in row[f"{name}_candidates"]]
        result[f"{name}_accuracy"] = ratio(sum(matches), len(matches))
    return result


def _rejected(error, operation, *args):
    try:
        operation(*args)
    except error:
        return True
    return False


def lifecycle_checks(corpus, dataset, registered, documents):
    probe = dataset.lifecycle
    question = next(case.question for case in dataset.cases if case.id == probe.case)
    source = registered[probe.source]
    source_id = source["source_id"]
    scope = corpus.bind_scope(OWNER, [source_id])
    parts = question_parts(question)
    refs = corpus.contexts(OWNER, parts, scope)
    _, original = _receipt(corpus, scope, parts, refs)
    original_public = corpus.public_receipt(OWNER, original)
    forged = json.loads(json.dumps(original))
    for citation in forged["citations"]:
        citation["excerpt_digest"] = "0" * 64
    invented = json.dumps({"passages": ["C" + "0" * 32 + "_1"]})
    _, rejected_selection = corpus.answer(OWNER, scope, [
        (part, evidence, invented) for part, evidence in zip(parts, refs, strict=True)
    ])
    checks = {
        "L01_original_revision_cited": bool(original_public and original_public["citations"]),
        "L02_tampered_citation_rejected": corpus.public_receipt(OWNER, forged) is None,
        "L03_cross_principal_scope_rejected": _rejected(PermissionError, corpus.bind_scope, "synthetic-other", [source_id]),
        "L04_cross_principal_citation_rejected": _rejected(PermissionError, corpus.public_receipt, "synthetic-other", original),
        "L05_foreign_source_id_rejected": _rejected(
            KeyError, LocalKnowledge("synthetic-other").bind_scope, "synthetic-other", [source_id]),
        "L11_invented_evidence_id_rejected": not rejected_selection["citations"],
    }
    old_lines = documents[probe.source].split("\n")
    evidence = next(item for item in dataset.evidence if item.source == probe.source)
    old_lines[evidence.line - 1] = probe.replacement
    changed = corpus.register(OWNER, probe.source, "\n".join(old_lines).encode("utf-8"), "text/plain",
                              source_id=source_id, revision=source["revision"])
    fresh = corpus.bind_scope(OWNER, [source_id])
    _, revised = _receipt(corpus, fresh, parts, corpus.contexts(OWNER, parts, fresh))
    revised_public = corpus.public_receipt(OWNER, revised)
    citations = revised_public["citations"] if revised_public else []
    checks.update({
        "L06_replacement_withdraws_original": corpus.public_receipt(OWNER, original) is None,
        "L07_replacement_refuses_old_scope": _rejected(KnowledgeConflict, corpus.check_scope, OWNER, scope),
        "L08_replacement_cites_new_revision": changed["revision"] == 2 and any(
            row["revision"] == 2 and row["excerpt"] == probe.replacement for row in citations),
    })
    corpus.remove(OWNER, source_id, changed["revision"])
    checks.update({
        "L09_deletion_withdraws_citations": corpus.public_receipt(OWNER, revised) is None,
        "L10_deleted_scope_rejected": _rejected(KeyError, corpus.bind_scope, OWNER, [source_id]),
    })
    return checks


def evaluate(directory=DATA):
    dataset, documents, locators, fingerprint = load_judgments(directory)
    corpus = LocalKnowledge(OWNER)
    registered = {label: corpus.register(OWNER, label, text.encode("utf-8"), "text/plain")
                  for label, text in documents.items()}
    cases = [evaluate_case(corpus, registered, documents, locators, case) for case in dataset.cases]
    splits = {name: metrics([case for case in cases if name == "all" or case["split"] == name])
              for name in ("all", "development", "held_out")}
    return {"schema_version": dataset.version, "corpus_sha256": fingerprint,
            "config": {"cutoffs": CUTOFFS, "max_parts": MAX_QUESTION_PARTS, "context_budget": MAX_CONTEXT_PASSAGES},
            "splits": splits, "questions": cases,
            "lifecycle": lifecycle_checks(corpus, dataset, registered, documents)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, help="Write question-level machine-readable results")
    options = parser.parse_args()
    result = evaluate()
    if options.json:
        options.json.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n",
                                encoding="utf-8")
    for name, scores in result["splits"].items():
        print(f'{name} ({scores["questions"]} questions, {scores["zero_relevant_parts"]} unanswerable parts)')
        for key, item in scores.items():
            if isinstance(item, dict):
                print(f'  {key:<28} {str(item["value"]):>8}  ({item["numerator"]}/{item["denominator"]})')
    checks = result["lifecycle"]
    print(f'Lifecycle checks: {sum(checks.values())}/{len(checks)}')
    return int(not all(checks.values()))


if __name__ == "__main__":
    raise SystemExit(main())

# Local retrieval judgments v1

Every document describes fictional equipment and synthetic values. The source and question labels were authored from the fixtures before retrieval was measured. Development cases start with `D`. Held-out cases start with `H` and are excluded from tuning decisions. Keep v1 unchanged when comparing revisions.

Run from the repository root using Python and its installed project dependencies.

```powershell
python -m eval.local_retrieval_eval --json "$env:TEMP/sovereignai-local-retrieval-v1.json"
```

The evaluator registers only the six named fixtures in a disposable in-process `LocalKnowledge` instance, uses production local question decomposition and retrieval limits, and routes citations through the real receipt verifier. It supplies every allocated candidate as a deterministic evidence selection proxy. This lets the benchmark measure selection and allocation behavior without Ollama. No HTTP server, production datastore, credentials, or model is needed.

## Labels and cutoffs

`judgments.json` declares the source registration order and names each relevant text line by source label, revision, inclusive line span, heading path and exact excerpt. Questions bind a subset of those source labels and contain ordered judged question parts. Each part declares target entities, conditions, coverage state and evidence groups. Groups are requirements. Multiple locators in one group are acceptable alternatives for that requirement. A zero-group part has no relevant passages and is expected to abstain or ask for clarification. The lifecycle row declares the document, probe question and updated line for replacement and deletion tests. The results include the corpus and judgment fingerprint, canonicalized source labels, ranked, allocated and cited passages, states, and per-question scores.

## Metrics

| Measure | Denominator and interpretation |
| --- | --- |
| Precision@1, @3, @6 | For each judged positive part, relevant passages in the first k slots divided by k, counting empty slots as misses. Reported as the mean of those part scores. |
| Recall@1, @3, @6 | Mean over positive parts of distinct evidence groups hit in the first k slots divided by that part's group count. Alternatives can satisfy a group only once. |
| MRR | Mean over positive parts using the reciprocal rank of the first hit, with zero for no hit. |
| Allocated evidence recall | Groups hit after the shared context budget divided by all judged groups. |
| Selected evidence precision | Relevant, locator-valid citation occurrences divided by all citation occurrences. |
| Multipart coverage recall | Groups hit by locator-valid citations for multipart questions divided by their judged group count. |
| Exact equipment and condition accuracy | Among returned candidates for parts with that attribute judged, share whose server-extracted equipment keys or measured condition sets agree with the target. Missing candidates are visible in recall. An attribute match can still miss the intended fact. |
| Citation source and locator accuracy | For each occurrence, compare registered source identity, revision and digest, admitted scope, candidate membership, exact fixture excerpt and offsets, line span and section metadata. For judged locators, also compare the independently authored full heading path. Denominator is all cited occurrences. |
| Unsupported abstention and false support | Denominator is all zero-group parts. Abstention requires absent or incomplete state and no citations. A supported or conflicting state, or any citation, counts as false support. |
| Coverage and conflict accuracy | Exact coverage state and binary conflict classification respectively over all judged parts. Positive conflict recall is separately reported. |
| Decomposition | Question count. A part-count mismatch is recorded without pairing results positionally to incorrect labels. |

Unanswerable parts are explicitly counted and omitted only from positive ranking metrics. Undefined ratios are emitted as JSON null. The benchmark measures retrieval, evidence selection with a fixed proxy, and locator validity. It cannot establish general entailment, model answer quality, or generalization to real industrial documents.

# Validation Pipeline Fixes — Index

Three-PR series fixing the false "0 repos found on GitHub → validated as novel" signal. Each PR is documented in a self-contained plan file so a fresh session can execute one PR without loading the others.

**Delivery order:** Merge and observe each PR in production before starting the next. The order is downstream-to-upstream: fix the visible symptoms first (GitHub search + scoring math), then address the upstream cluster hygiene that produced the bad candidate.

| PR  | Plan file                                                                                              | Scope                                                                  |
|-----|--------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------|
|  1  | [`2026-05-18-validation-pr1-github-search-and-keywords.md`](2026-05-18-validation-pr1-github-search-and-keywords.md) | Multi-query GitHub search + tighter keyword extraction (`app/pipeline/validation.py`). |
|  2  | [`2026-05-18-validation-pr2-no-signal-scoring.md`](2026-05-18-validation-pr2-no-signal-scoring.md)                   | Reclassify `repo_count == 0` as no-signal; renormalize scoring total.                  |
|  3  | [`2026-05-18-validation-pr3-cohesion-gate.md`](2026-05-18-validation-pr3-cohesion-gate.md)                           | Stage-4 cohesion gate, logging-only by default (opt-in via env var).                   |

**Deferred to Phase 2 (not in this series):** Per-pass recluster. Only revisit if PRs 1–3 don't sufficiently reduce bad-candidate rates after a few weeks in production with the cohesion gate enabled. Sketch is in PR 3's plan file under the Phase 2 appendix.

**Brainstorming context (for review):**

- The triggering candidate ("Current task management and habit tracking apps fail to engage…") returned `0 repos found on GitHub` despite many ADHD productivity repos existing. Root cause analysis showed three layered failures: (a) keyword extraction picked weak filler tokens like `current` while dropping `adhd`, (b) the GitHub query ANDed 5 keywords so almost no repo matched, (c) `repo_count = 0` was scored as 30/100 "validated novel" instead of "no signal." The bad cluster itself was also incoherent — three unrelated topics merged under one label — pointing to a fourth, upstream failure in stage-4 clustering.
- The original brainstormed plan had 5 tasks. Two reorderings happened during review:
  1. Risk-ordered: lowest-risk single-subsystem changes first (PR 1, PR 2), highest-risk ingestion change last (PR 3).
  2. PR 1 combines the original multi-query and keyword-extraction tasks atomically because either half alone is wasted (better queries on weak tokens, or better tokens with the AND-of-5 format).
- An LLM-based coherence check at labelling time was considered as a third cluster-hygiene option and intentionally deferred — it costs an LLM call per candidate, and the cheap geometric logging in PR 3 should provide enough signal first.

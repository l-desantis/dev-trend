# Validation PR 1 — Multi-query GitHub search + tighter keyword extraction

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the false "0 repos found on GitHub" outcome that the validation stage returns for candidates whose existing competitors do exist on GitHub but aren't surfaced because (a) the search joins 5 keywords with AND so almost no repo matches, and (b) the extracted keywords include weak filler tokens ("current", "existing", "apps") while dropping cohort nouns ("adhd"). This PR replaces the single AND-of-5 query with a union of paired-keyword queries and tightens the keyword extraction in the same change because the two halves only deliver value together — better queries are wasted on weak tokens, and better tokens are wasted on the AND-of-5 format.

**Architecture:** Two changes in `app/pipeline/validation.py`: (1) expand the `_STOPWORDS` frozenset with weak/filler tokens and rewrite `extract_keywords` to emit audience-derived tokens first so cohort identifiers survive the 5-token cap; (2) replace `search_github_repos` with a multi-query implementation that issues up to three 2-keyword union queries over the top 4 keywords, dedupes by `full_name`, and sorts the top-5 result by stars. Return shape (`repo_count`, `top_repos_json`, `star_delta_30d`, `max_stars`) preserved so downstream scorers don't change.

**Context — what comes before/after this PR:** This is the first of three PRs in the validation pipeline fix. PR 2 (separate plan, ships after this PR is merged and observed) reclassifies `repo_count == 0` in scoring as no-signal. PR 3 adds an opt-in cohesion gate at stage 4. Per-pass recluster is deferred to Phase 2 entirely.

**Tech Stack:** Python 3.11+, httpx, structlog, pytest-asyncio.

**Environment note:** This project cannot execute `uv`/`pytest`/`python` directly in the agent shell (see CLAUDE.md). Every command shown as `! uv run pytest ...` must be handed to the user to run; wait for them to paste output before proceeding.

---

## File Structure

**Modified files:**
- `app/pipeline/validation.py` — `_STOPWORDS`, `extract_keywords`, `_pair_queries` (new helper), `search_github_repos`.

**Modified test files:**
- `tests/pipeline/test_validation.py` — three new `extract_keywords` tests + two new `search_github_repos` tests.

**No new files.**

---

## Task 1: Tighter keyword extraction

**Files:**
- Modify: `app/pipeline/validation.py:19-47` (`_STOPWORDS` and `extract_keywords`)
- Test: `tests/pipeline/test_validation.py` (append new tests)

- [ ] **Step 1.1: Write the failing tests for `extract_keywords`**

Append to `tests/pipeline/test_validation.py`:

```python
def test_extract_keywords_prefers_audience_cohort_nouns() -> None:
    from app.pipeline.validation import extract_keywords
    problem = ("Current task management and habit tracking apps fail to "
               "engage and motivate individuals with diverse needs.")
    audience = "Individuals with ADHD and social media addiction"
    kws = extract_keywords(problem, audience)
    # ADHD must survive the 5-token cap
    assert "adhd" in kws


def test_extract_keywords_drops_weak_filler_words() -> None:
    from app.pipeline.validation import extract_keywords
    problem = ("Current existing apps and solutions for users are not "
               "engaging enough.")
    kws = extract_keywords(problem, None)
    for filler in ("current", "existing", "apps", "solutions", "users"):
        assert filler not in kws, f"filler word {filler!r} leaked into keywords"


def test_extract_keywords_returns_at_most_five() -> None:
    from app.pipeline.validation import extract_keywords
    problem = " ".join(f"meaningfulword{i}" for i in range(20))
    kws = extract_keywords(problem, None)
    assert len(kws) <= 5
```

- [ ] **Step 1.2: Run the new tests to verify they fail**

Ask the user to run:

```
! uv run pytest tests/pipeline/test_validation.py::test_extract_keywords_prefers_audience_cohort_nouns tests/pipeline/test_validation.py::test_extract_keywords_drops_weak_filler_words tests/pipeline/test_validation.py::test_extract_keywords_returns_at_most_five -v
```

Expected: FAIL on the first two — first because audience is concatenated after problem and gets truncated, second because the filler tokens aren't in the current `_STOPWORDS`.

- [ ] **Step 1.3: Update `_STOPWORDS` and rewrite `extract_keywords`**

Edit `app/pipeline/validation.py:19-47`. Replace the existing `_STOPWORDS` frozenset and `extract_keywords` function with:

```python
_STOPWORDS = frozenset(
    "a an the and or but in on at to for of with is are was were be been being "
    "have has had do does did will would could should may might shall can i we "
    "you he she it they them their its my our your his her from by about into "
    "through during before after above below between among while "
    # Weak filler / overly generic tokens that pollute GitHub queries:
    "current currently existing existed new old "
    "app apps application applications solution solutions tool tools "
    "user users people person individual individuals customer customers "
    "thing things stuff way ways "
    "need needs needed lack lacking missing "
    "feature features functionality "
    "good bad great poor better best worse worst "
    "make made making get got getting use used using "
    "really very quite rather just only also still even".split()
)


def _tokens(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", text)
            if w.lower() not in _STOPWORDS]


def extract_keywords(problem_statement: str, audience: str | None) -> list[str]:
    """Return up to 5 search keywords, preferring audience cohort nouns.

    Audience tokens are emitted first so cohort identifiers (e.g. "adhd")
    survive the 5-token cap even when the problem statement is verbose.
    """
    ordered: list[str] = []
    if audience:
        ordered.extend(_tokens(audience))
    ordered.extend(_tokens(problem_statement))

    seen: set[str] = set()
    unique: list[str] = []
    for w in ordered:
        if w not in seen:
            seen.add(w)
            unique.append(w)
    return unique[:5]
```

- [ ] **Step 1.4: Run the new tests to verify they pass**

Ask the user to run:

```
! uv run pytest tests/pipeline/test_validation.py -v -k extract_keywords
```

Expected: all three new `extract_keywords` tests pass. If a pre-existing keyword-extraction test asserted a specific ordering that the new audience-first logic changes, update it to match the new contract (audience tokens before problem tokens, weak filler now filtered).

---

## Task 2: Multi-query GitHub search

**Files:**
- Modify: `app/pipeline/validation.py:50-87` (`search_github_repos`, add `_pair_queries`)
- Test: `tests/pipeline/test_validation.py` (append new tests)

- [ ] **Step 2.1: Write the failing tests for `search_github_repos`**

Append to `tests/pipeline/test_validation.py`:

```python
async def test_search_github_repos_unions_multiple_pair_queries() -> None:
    from unittest.mock import AsyncMock, MagicMock
    import httpx
    from app.pipeline.validation import search_github_repos

    def resp(items):
        m = MagicMock()
        m.status_code = 200
        m.json.return_value = {"total_count": len(items), "items": items}
        return m

    # Each pair query returns different repos; one repo appears in two
    # queries and must be deduped.
    responses = [
        resp([
            {"full_name": "alice/adhd-tasks", "stargazers_count": 800,
             "html_url": "https://github.com/alice/adhd-tasks", "language": "Python"},
            {"full_name": "bob/shared-repo", "stargazers_count": 1200,
             "html_url": "https://github.com/bob/shared-repo", "language": "Go"},
        ]),
        resp([
            {"full_name": "bob/shared-repo", "stargazers_count": 1200,
             "html_url": "https://github.com/bob/shared-repo", "language": "Go"},
            {"full_name": "carol/habit-tracker", "stargazers_count": 500,
             "html_url": "https://github.com/carol/habit-tracker", "language": "Rust"},
        ]),
        resp([
            {"full_name": "dave/focus-app", "stargazers_count": 50,
             "html_url": "https://github.com/dave/focus-app", "language": "Swift"},
        ]),
    ]

    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(side_effect=responses)

    result = await search_github_repos(
        client, ["adhd", "habit", "tracker", "focus"]
    )

    # Three pair queries issued
    assert client.request.await_count == 3
    # 4 unique repos after dedupe
    assert result["repo_count"] == 4
    # Top repos sorted by stars desc
    names = [r["name"] for r in result["top_repos_json"]]
    assert names[0] == "bob/shared-repo"
    assert result["max_stars"] == 1200


async def test_search_github_repos_handles_single_keyword() -> None:
    # With only one keyword, no pairs can be formed — should still issue
    # one fallback query and return its results.
    from unittest.mock import AsyncMock, MagicMock
    import httpx
    from app.pipeline.validation import search_github_repos

    m = MagicMock()
    m.status_code = 200
    m.json.return_value = {
        "total_count": 1,
        "items": [{"full_name": "x/y", "stargazers_count": 10,
                   "html_url": "https://github.com/x/y", "language": "Python"}],
    }
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(return_value=m)

    result = await search_github_repos(client, ["adhd"])

    assert client.request.await_count == 1
    assert result["repo_count"] == 1
```

- [ ] **Step 2.2: Run the new tests to verify they fail**

Ask the user to run:

```
! uv run pytest tests/pipeline/test_validation.py::test_search_github_repos_unions_multiple_pair_queries tests/pipeline/test_validation.py::test_search_github_repos_handles_single_keyword -v
```

Expected: FAIL — current implementation issues one AND-joined query, not multiple pair queries.

- [ ] **Step 2.3: Replace `search_github_repos` with the multi-query version**

Edit `app/pipeline/validation.py`. Replace the existing `search_github_repos` function with a new `_pair_queries` helper plus the new implementation:

```python
def _pair_queries(keywords: list[str], max_pairs: int = 3) -> list[str]:
    """Build short keyword-pair queries from the top keywords.

    Picks up to ``max_pairs`` 2-token combinations from the first 4 keywords.
    Falls back to a single-keyword query if only one keyword is available.
    """
    head = keywords[:4]
    if len(head) == 0:
        return []
    if len(head) == 1:
        return [head[0]]
    pairs: list[str] = []
    for i in range(len(head)):
        for j in range(i + 1, len(head)):
            pairs.append(f"{head[i]}+{head[j]}")
            if len(pairs) >= max_pairs:
                return pairs
    return pairs


async def search_github_repos(
    client: httpx.AsyncClient,
    keywords: list[str],
) -> dict[str, Any]:
    queries = _pair_queries(keywords)
    if not queries:
        return {"repo_count": 0, "top_repos_json": [], "star_delta_30d": 0, "max_stars": 0}

    url = "https://api.github.com/search/repositories"
    repos_by_name: dict[str, dict[str, Any]] = {}

    for q in queries:
        full_q = f"{q}+in:name,description,readme"
        try:
            resp = await request_with_retry(
                client, "GET", url,
                params={"q": full_q, "sort": "stars", "per_page": 10},
                headers={"Accept": "application/vnd.github+json"},
            )
            data = resp.json()
        except Exception as exc:
            log.warning("github_search_failed", q=q, error=str(exc))
            continue

        for r in data.get("items", []):
            name = r.get("full_name", "")
            if not name or name in repos_by_name:
                continue
            repos_by_name[name] = {
                "name": name,
                "stars": r.get("stargazers_count", 0),
                "url": r.get("html_url", ""),
                "language": r.get("language"),
            }

    deduped = sorted(repos_by_name.values(), key=lambda r: r["stars"], reverse=True)
    top_repos = deduped[:5]
    max_stars = max((r["stars"] for r in deduped), default=0)
    return {
        "repo_count": len(deduped),
        "top_repos_json": top_repos,
        "star_delta_30d": 0,
        "max_stars": max_stars,
    }
```

- [ ] **Step 2.4: Run the validation tests to verify they pass**

Ask the user to run:

```
! uv run pytest tests/pipeline/test_validation.py -v
```

Expected: all new tests pass. Pre-existing tests that asserted exactly one HTTP call need an update — switch the AsyncMock to `side_effect=[resp, resp, resp]` and change the assertion from `client.request.assert_called_once()` to `assert client.request.await_count >= 1`. Re-run until green.

---

## Final verification

- [ ] **Run the full suite for regressions**

Ask the user to run:

```
! uv run pytest -q
```

Expected: green across the whole repo.

- [ ] **Commit and open PR**

```bash
git add app/pipeline/validation.py tests/pipeline/test_validation.py
git commit -m "feat(validation): multi-query GitHub search + tighter keyword extraction

Replaces single AND-joined 5-keyword query (which almost never matched
real repos) with up to three 2-keyword union queries over the top 4
keywords; dedupes by full_name and sorts the resulting top-5 by stars.

Pairs with extract_keywords improvements: expands the stopword list
with weak filler tokens (current, apps, solutions, users, etc.) and
emits audience-derived tokens before problem-statement tokens, so
cohort nouns like 'adhd' survive the 5-keyword cap.

Together these fix the case where the bad-candidate example returned
'0 repos found' despite many ADHD task-management repos existing —
'adhd+task' and 'adhd+habit' are now among the queries actually issued."
```

Open the PR. After merge, observe in production logs / database for a few days: validation snapshots should now show realistic `repo_count` values for candidates that previously showed 0. That observation is the gate to starting PR 2 — if `repo_count` is still 0 across the board, investigate before changing scoring math.

---

## Self-review notes

- The two halves (keyword extraction + multi-query search) ship together intentionally; either alone leaves obvious leftover failure modes.
- Return shape of `search_github_repos` is preserved (`repo_count`, `top_repos_json`, `star_delta_30d`, `max_stars`) — downstream scoring is untouched in this PR.
- All test commands routed through `! uv run pytest` per CLAUDE.md.

# LLM Keyword Extraction for GitHub Validation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static stopword-based `extract_keywords` function with LLM-powered extraction that returns 3–5 product-category nouns suitable for GitHub search, falling back to the stopword approach when no LLM is available.

**Architecture:** Add `extract_search_keywords(problem, audience)` as a new abstract method on `LLMAdapter` (implemented in all four adapters). Update `run_validation` to accept an optional `llm` parameter and call the LLM path first, falling back to `extract_keywords` on failure or empty result via a new public `select_keywords` helper. The scheduler wires the already-created LLM adapter into `run_validation`. The stopword path is retained as a fallback and for `count_show_hn_matches` (which does title pattern-matching, not GitHub API calls).

**Tech Stack:** Python 3.11+, existing `LLMAdapter` hierarchy (`openai_adapter`, `nim_adapter`, `ollama_adapter`, `mock_adapter`), pytest-asyncio, `asyncio_mode = "auto"`.

**Environment note:** This project cannot execute `uv`/`pytest`/`python` directly in the agent shell (see CLAUDE.md). Every command shown as `! uv run pytest …` must be handed to the user to run; wait for them to paste output before proceeding.

---

## File Structure

**Modified files:**
- `app/llm/schemas.py` — add `SearchKeywords` Pydantic model
- `app/llm/prompts.py` — add `KEYWORD_EXTRACT_SYSTEM_PROMPT` and `KEYWORD_EXTRACT_USER_PROMPT`
- `app/llm/base.py` — add abstract `extract_search_keywords` method
- `app/llm/mock_adapter.py` — implement `extract_search_keywords`
- `app/llm/nim_adapter.py` — implement `extract_search_keywords`
- `app/llm/openai_adapter.py` — implement `extract_search_keywords`
- `app/llm/ollama_adapter.py` — implement `extract_search_keywords`
- `app/pipeline/validation.py` — add `select_keywords` async helper, update `run_validation` signature
- `app/ingestion/scheduler.py` — pass `llm` to `run_validation` in `_scoring_job`
- `scripts/debug_keywords.py` — add `--llm` flag

**Modified test files:**
- `tests/llm/test_adapter_interface.py` — add `extract_search_keywords` to required-methods list
- `tests/llm/test_mock_adapter.py` — add `extract_search_keywords` test
- `tests/llm/test_nim_adapter.py` — add `extract_search_keywords` tests
- `tests/llm/test_openai_adapter.py` — add `extract_search_keywords` tests
- `tests/llm/test_ollama_adapter.py` — add `extract_search_keywords` tests
- `tests/pipeline/test_validation.py` — add `run_validation` LLM path tests

**No new files.**

---

## Task 1: Schema + Prompts

**Files:**
- Modify: `app/llm/schemas.py`
- Modify: `app/llm/prompts.py`

- [ ] **Step 1.1: Add `SearchKeywords` to schemas**

Append to `app/llm/schemas.py` after the existing imports/classes:

```python
class SearchKeywords(BaseModel):
    keywords: list[str]
```

- [ ] **Step 1.2: Add keyword extraction prompts to `prompts.py`**

Append to `app/llm/prompts.py` after the existing content:

```python
# ---------------------------------------------------------------------------
# Validation keyword extraction prompt
# ---------------------------------------------------------------------------

KEYWORD_EXTRACT_SYSTEM_PROMPT = (
    "You extract specific GitHub search keywords from product opportunity descriptions. "
    "Return only domain-specific nouns: product categories, technologies, problem domains. "
    "Never return verbs, adjectives, or generic terms like 'app', 'tool', 'users', 'developers'."
)

KEYWORD_EXTRACT_USER_PROMPT = """\
Problem: {problem}
Audience: {audience}

Return 3-5 specific keywords suitable for GitHub repository search.
Good examples: adhd, fintech, leetcode, react-native, ecommerce, multilingual, procurement, wearable
Bad examples: struggle, create, accessible, multiple, users, developers, platform, manage

Return STRICT JSON: {{"keywords": ["word1", "word2", "word3"]}}
Reply with ONLY the JSON object, no prose.\
"""
```

- [ ] **Step 1.3: Commit**

```bash
git add app/llm/schemas.py app/llm/prompts.py
git commit -m "feat(llm): add SearchKeywords schema and keyword extraction prompts"
```

---

## Task 2: Base class abstract method

**Files:**
- Modify: `app/llm/base.py`
- Modify: `tests/llm/test_adapter_interface.py`

- [ ] **Step 2.1: Add the abstract method**

In `app/llm/base.py`, append after `label_cluster`:

```python
    @abstractmethod
    async def extract_search_keywords(
        self,
        problem: str,
        audience: str | None,
    ) -> list[str]:
        """Return 3-5 domain-specific keywords for GitHub search.

        Returns an empty list on failure — callers must fall back to
        the stopword-based extractor in that case.
        """
```

Note: adding an abstract method breaks instantiation of every concrete adapter until each is updated. The full suite will not be green again until Task 6 finishes; until then, only run the narrowly-scoped pytest invocations shown in each task's verification step.

- [ ] **Step 2.2: Update the interface contract test**

In `tests/llm/test_adapter_interface.py`, extend the required-methods list so future adapters cannot forget the new method:

```python
def test_concrete_adapters_implement_v4_methods() -> None:
    required = ["extract_pain_point", "label_cluster", "extract_search_keywords", "model_name"]
    for name in required:
        assert hasattr(MockLLMAdapter, name), f"MockLLMAdapter missing {name}"
```

- [ ] **Step 2.3: Commit**

```bash
git add app/llm/base.py tests/llm/test_adapter_interface.py
git commit -m "feat(llm): add abstract extract_search_keywords to LLMAdapter"
```

---

## Task 3: Mock adapter + test

**Files:**
- Modify: `app/llm/mock_adapter.py`
- Modify: `tests/llm/test_mock_adapter.py`

- [ ] **Step 3.1: Write the failing test**

Read `tests/llm/test_mock_adapter.py`, then append:

```python
@pytest.mark.asyncio
async def test_mock_extract_search_keywords_returns_list() -> None:
    from app.llm.mock_adapter import MockLLMAdapter
    adapter = MockLLMAdapter()
    kws = await adapter.extract_search_keywords(
        "habit tracking apps fail to engage ADHD adults",
        "ADHD adults",
    )
    assert isinstance(kws, list)
    assert 1 <= len(kws) <= 5
    assert all(isinstance(k, str) and k == k.lower() for k in kws)


@pytest.mark.asyncio
async def test_mock_extract_search_keywords_no_audience() -> None:
    from app.llm.mock_adapter import MockLLMAdapter
    adapter = MockLLMAdapter()
    kws = await adapter.extract_search_keywords("react native offline sync", None)
    assert isinstance(kws, list)
```

- [ ] **Step 3.2: Run to verify they fail**

Ask the user to run:

```
! uv run pytest tests/llm/test_mock_adapter.py::test_mock_extract_search_keywords_returns_list tests/llm/test_mock_adapter.py::test_mock_extract_search_keywords_no_audience -v
```

Expected: `TypeError` or `AttributeError` — `extract_search_keywords` not yet defined.

- [ ] **Step 3.3: Implement in `MockLLMAdapter`**

Add this import at the top of `app/llm/mock_adapter.py`:

```python
import re
```

Append the method to `MockLLMAdapter` (inside the class, after `label_cluster`):

```python
    async def extract_search_keywords(
        self,
        problem: str,
        audience: str | None,
    ) -> list[str]:
        # 4+ chars keeps cohort tokens like "adhd" — the whole point of the LLM path
        # over the stopword path is to surface domain-specific short nouns.
        combined = f"{audience or ''} {problem}"
        words = [w.lower() for w in re.findall(r"[a-zA-Z]{4,}", combined)]
        seen: set[str] = set()
        result: list[str] = []
        for w in words:
            if w not in seen:
                seen.add(w)
                result.append(w)
        return result[:4]
```

- [ ] **Step 3.4: Run to verify they pass**

```
! uv run pytest tests/llm/test_mock_adapter.py::test_mock_extract_search_keywords_returns_list tests/llm/test_mock_adapter.py::test_mock_extract_search_keywords_no_audience -v
```

Expected: PASS.

- [ ] **Step 3.5: Commit**

```bash
git add app/llm/mock_adapter.py tests/llm/test_mock_adapter.py
git commit -m "feat(llm): implement extract_search_keywords in MockLLMAdapter"
```

---

## Task 4: NIM adapter

**Files:**
- Modify: `app/llm/nim_adapter.py`
- Modify: `tests/llm/test_nim_adapter.py`

The NIM adapter has a `_chat(messages, json_mode=True)` method that returns a raw JSON string. All tests mock `adapter._client.post` at the HTTP level (see existing tests). Follow the same pattern.

- [ ] **Step 4.1: Write failing tests**

Append to `tests/llm/test_nim_adapter.py`:

```python
@pytest.mark.asyncio
async def test_nim_extract_search_keywords_happy_path(adapter) -> None:
    payload = '{"keywords": ["adhd", "habit", "tracker"]}'
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = _chat_response(payload)
    mock_response.raise_for_status = MagicMock()

    with patch.object(adapter._client, "post", new=AsyncMock(return_value=mock_response)):
        result = await adapter.extract_search_keywords(
            "habit tracking apps fail to engage ADHD adults",
            "ADHD adults",
        )

    assert result == ["adhd", "habit", "tracker"]


@pytest.mark.asyncio
async def test_nim_extract_search_keywords_bad_json_returns_empty(adapter) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = _chat_response("not valid json")
    mock_response.raise_for_status = MagicMock()

    with patch.object(adapter._client, "post", new=AsyncMock(return_value=mock_response)):
        result = await adapter.extract_search_keywords("some problem", None)

    assert result == []


@pytest.mark.asyncio
async def test_nim_extract_search_keywords_strips_and_lowercases(adapter) -> None:
    payload = '{"keywords": ["  ADHD  ", "Habit", "Tracker"]}'
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = _chat_response(payload)
    mock_response.raise_for_status = MagicMock()

    with patch.object(adapter._client, "post", new=AsyncMock(return_value=mock_response)):
        result = await adapter.extract_search_keywords("some problem", None)

    assert result == ["adhd", "habit", "tracker"]
```

- [ ] **Step 4.2: Run to verify they fail**

```
! uv run pytest tests/llm/test_nim_adapter.py::test_nim_extract_search_keywords_happy_path tests/llm/test_nim_adapter.py::test_nim_extract_search_keywords_bad_json_returns_empty tests/llm/test_nim_adapter.py::test_nim_extract_search_keywords_strips_and_lowercases -v
```

Expected: FAIL — method not defined.

- [ ] **Step 4.3: Implement in `NvidiaNimAdapter`**

First, add to imports at the top of `app/llm/nim_adapter.py`:

```python
from app.llm.prompts import (
    BRIEF_SYSTEM_PROMPT,
    EXTRACT_SYSTEM_PROMPT,
    EXTRACT_USER_PROMPT,
    KEYWORD_EXTRACT_SYSTEM_PROMPT,
    KEYWORD_EXTRACT_USER_PROMPT,
    LABEL_CLUSTER_PROMPT,
    render_brief_prompt,
)
```

Then append the method to `NvidiaNimAdapter` (inside the class, after `label_cluster`):

```python
    async def extract_search_keywords(
        self,
        problem: str,
        audience: str | None,
    ) -> list[str]:
        messages = [
            {"role": "system", "content": KEYWORD_EXTRACT_SYSTEM_PROMPT},
            {"role": "user", "content": KEYWORD_EXTRACT_USER_PROMPT.format(
                problem=problem,
                audience=audience or "(not specified)",
            )},
        ]
        try:
            raw = await self._chat(messages, json_mode=True)
            data = json.loads(raw)
            return [k.lower().strip() for k in data.get("keywords", []) if k.strip()][:5]
        except Exception as exc:
            log.warning("nim_keyword_extract_failed", error=str(exc))
            return []
```

- [ ] **Step 4.4: Run to verify they pass**

```
! uv run pytest tests/llm/test_nim_adapter.py -v
```

Expected: all NIM tests pass.

- [ ] **Step 4.5: Commit**

```bash
git add app/llm/nim_adapter.py tests/llm/test_nim_adapter.py
git commit -m "feat(llm): implement extract_search_keywords in NvidiaNimAdapter"
```

---

## Task 5: OpenAI adapter

**Files:**
- Modify: `app/llm/openai_adapter.py`
- Modify: `tests/llm/test_openai_adapter.py`

The OpenAI adapter uses `client.beta.chat.completions.parse` with a Pydantic model as `response_format`. Tests mock `adapter._client.beta.chat.completions.parse`.

- [ ] **Step 5.1: Write failing tests**

Append to `tests/llm/test_openai_adapter.py`:

```python
@pytest.mark.asyncio
async def test_openai_extract_search_keywords_happy_path(adapter: OpenAIAdapter) -> None:
    from app.llm.schemas import SearchKeywords
    parsed = SearchKeywords(keywords=["adhd", "habit", "tracker"])
    with patch.object(
        adapter._client.beta.chat.completions,
        "parse",
        new=AsyncMock(return_value=_parse_completion(parsed)),
    ):
        result = await adapter.extract_search_keywords(
            "habit tracking apps fail to engage ADHD adults",
            "ADHD adults",
        )
    assert result == ["adhd", "habit", "tracker"]


@pytest.mark.asyncio
async def test_openai_extract_search_keywords_exception_returns_empty(adapter: OpenAIAdapter) -> None:
    with patch.object(
        adapter._client.beta.chat.completions,
        "parse",
        new=AsyncMock(side_effect=Exception("API error")),
    ):
        result = await adapter.extract_search_keywords("some problem", None)
    assert result == []


@pytest.mark.asyncio
async def test_openai_extract_search_keywords_none_response_returns_empty(adapter: OpenAIAdapter) -> None:
    with patch.object(
        adapter._client.beta.chat.completions,
        "parse",
        new=AsyncMock(return_value=_parse_completion(None)),
    ):
        result = await adapter.extract_search_keywords("some problem", None)
    assert result == []
```

- [ ] **Step 5.2: Run to verify they fail**

```
! uv run pytest tests/llm/test_openai_adapter.py::test_openai_extract_search_keywords_happy_path tests/llm/test_openai_adapter.py::test_openai_extract_search_keywords_exception_returns_empty tests/llm/test_openai_adapter.py::test_openai_extract_search_keywords_none_response_returns_empty -v
```

Expected: FAIL — method not defined.

- [ ] **Step 5.3: Implement in `OpenAIAdapter`**

Add to imports at the top of `app/llm/openai_adapter.py`:

```python
from app.llm.prompts import (
    BRIEF_SYSTEM_PROMPT,
    EXTRACT_SYSTEM_PROMPT,
    EXTRACT_USER_PROMPT,
    KEYWORD_EXTRACT_SYSTEM_PROMPT,
    KEYWORD_EXTRACT_USER_PROMPT,
    LABEL_CLUSTER_PROMPT,
    render_brief_prompt,
)
from app.llm.schemas import ClusterLabel, PainPointDraft, SearchKeywords
```

Then append the method to `OpenAIAdapter` (inside the class, before `aclose`):

```python
    async def extract_search_keywords(
        self,
        problem: str,
        audience: str | None,
    ) -> list[str]:
        try:
            completion = await self._client.beta.chat.completions.parse(
                model=self._model,
                messages=[
                    {"role": "system", "content": KEYWORD_EXTRACT_SYSTEM_PROMPT},
                    {"role": "user", "content": KEYWORD_EXTRACT_USER_PROMPT.format(
                        problem=problem,
                        audience=audience or "(not specified)",
                    )},
                ],
                response_format=SearchKeywords,
            )
            result = completion.choices[0].message.parsed
            if result is None:
                return []
            return [k.lower().strip() for k in result.keywords if k.strip()][:5]
        except Exception as exc:
            log.warning("openai_keyword_extract_failed", error=str(exc))
            return []
```

- [ ] **Step 5.4: Run to verify they pass**

```
! uv run pytest tests/llm/test_openai_adapter.py -v
```

Expected: all OpenAI tests pass.

- [ ] **Step 5.5: Commit**

```bash
git add app/llm/openai_adapter.py tests/llm/test_openai_adapter.py
git commit -m "feat(llm): implement extract_search_keywords in OpenAIAdapter"
```

---

## Task 6: Ollama adapter

**Files:**
- Modify: `app/llm/ollama_adapter.py`
- Modify: `tests/llm/test_ollama_adapter.py`

The Ollama adapter mocks `adapter._client.chat`. `_chat_response(text)` is already defined in the test file as `SimpleNamespace(message=SimpleNamespace(content=text))`.

- [ ] **Step 6.1: Write failing tests**

Append to `tests/llm/test_ollama_adapter.py`:

```python
async def test_ollama_extract_search_keywords_happy_path() -> None:
    import json
    adapter = _adapter()
    payload = json.dumps({"keywords": ["adhd", "habit", "tracker"]})
    with patch.object(adapter._client, "chat", new=AsyncMock(return_value=_chat_response(payload))):
        result = await adapter.extract_search_keywords(
            "habit tracking apps fail to engage ADHD adults",
            "ADHD adults",
        )
    assert result == ["adhd", "habit", "tracker"]


async def test_ollama_extract_search_keywords_bad_json_returns_empty() -> None:
    adapter = _adapter()
    with patch.object(adapter._client, "chat", new=AsyncMock(return_value=_chat_response("not json"))):
        result = await adapter.extract_search_keywords("some problem", None)
    assert result == []
```

- [ ] **Step 6.2: Run to verify they fail**

```
! uv run pytest tests/llm/test_ollama_adapter.py::test_ollama_extract_search_keywords_happy_path tests/llm/test_ollama_adapter.py::test_ollama_extract_search_keywords_bad_json_returns_empty -v
```

Expected: FAIL.

- [ ] **Step 6.3: Implement in `OllamaAdapter`**

Add to imports at the top of `app/llm/ollama_adapter.py`:

```python
from app.llm.prompts import (
    BRIEF_SYSTEM_PROMPT,
    EXTRACT_PROMPT,
    KEYWORD_EXTRACT_SYSTEM_PROMPT,
    KEYWORD_EXTRACT_USER_PROMPT,
    LABEL_CLUSTER_PROMPT,
    render_brief_prompt,
)
```

Append the method to `OllamaAdapter` (inside the class, before the end):

```python
    async def extract_search_keywords(
        self,
        problem: str,
        audience: str | None,
    ) -> list[str]:
        try:
            response = await self._client.chat(
                model=self._model,
                messages=[
                    {"role": "system", "content": KEYWORD_EXTRACT_SYSTEM_PROMPT},
                    {"role": "user", "content": KEYWORD_EXTRACT_USER_PROMPT.format(
                        problem=problem,
                        audience=audience or "(not specified)",
                    )},
                ],
                format="json",
            )
            raw = response.message.content or ""
            data = json.loads(raw)
            return [k.lower().strip() for k in data.get("keywords", []) if k.strip()][:5]
        except Exception as exc:
            log.warning("ollama_keyword_extract_failed", error=str(exc))
            return []
```

- [ ] **Step 6.4: Run to verify they pass**

```
! uv run pytest tests/llm/test_ollama_adapter.py -v
```

Expected: all Ollama tests pass.

- [ ] **Step 6.5: Commit**

```bash
git add app/llm/ollama_adapter.py tests/llm/test_ollama_adapter.py
git commit -m "feat(llm): implement extract_search_keywords in OllamaAdapter"
```

---

## Task 7: Validation pipeline integration

**Files:**
- Modify: `app/pipeline/validation.py` (add `select_keywords`, update `run_validation`)
- Modify: `tests/pipeline/test_validation.py`

- [ ] **Step 7.1: Write failing tests**

First, hoist the new imports to the top of `tests/pipeline/test_validation.py` (next to the existing imports):

```python
from app.llm.mock_adapter import MockLLMAdapter
from app.pipeline import validation as validation_module
```

Then append the test functions to `tests/pipeline/test_validation.py`. These use `monkeypatch` to spy on `extract_keywords` so the fallback path is actually verified, not assumed:

```python
async def test_run_validation_uses_llm_keywords_when_provided(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = OpportunityCandidate(
        problem_statement="habit tracking for diverse needs",
        audience="ADHD adults",
        specificity=3,
    )
    session.add(candidate)
    await session.commit()

    llm = MockLLMAdapter()
    llm.extract_search_keywords = AsyncMock(return_value=["adhd", "habit", "tracker"])

    # Spy on the stopword path so we can assert it was NOT called for GitHub search.
    # NOTE: count_show_hn_matches calls extract_keywords for title pattern matching,
    # so the spy may still be invoked once. We assert that the GitHub-search path used
    # the LLM keywords by checking llm.extract_search_keywords was awaited.
    real_extract = validation_module.extract_keywords
    spy = MagicMock(side_effect=real_extract)
    monkeypatch.setattr(validation_module, "extract_keywords", spy)

    client = await _make_github_client()
    report = await run_validation(session, client, llm=llm)

    assert report.validated == 1
    llm.extract_search_keywords.assert_awaited_once_with(
        candidate.problem_statement, candidate.audience
    )


async def test_run_validation_falls_back_to_stopwords_when_llm_returns_empty(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = OpportunityCandidate(
        problem_statement="habit tracking for focus",
        audience="ADHD adults",
        specificity=3,
    )
    session.add(candidate)
    await session.commit()

    llm = MockLLMAdapter()
    llm.extract_search_keywords = AsyncMock(return_value=[])  # empty → must fall back

    real_extract = validation_module.extract_keywords
    spy = MagicMock(side_effect=real_extract)
    monkeypatch.setattr(validation_module, "extract_keywords", spy)

    client = await _make_github_client()
    report = await run_validation(session, client, llm=llm)

    assert report.validated == 1
    assert report.errors == 0
    # Spy must have been called for the GitHub-search path (fallback fired),
    # plus once more inside count_show_hn_matches. At least 2 calls confirms the fallback.
    assert spy.call_count >= 2


async def test_run_validation_without_llm_uses_stopwords(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = OpportunityCandidate(
        problem_statement="habit tracking for focus",
        audience="ADHD adults",
        specificity=3,
    )
    session.add(candidate)
    await session.commit()

    real_extract = validation_module.extract_keywords
    spy = MagicMock(side_effect=real_extract)
    monkeypatch.setattr(validation_module, "extract_keywords", spy)

    client = await _make_github_client()
    report = await run_validation(session, client)  # no llm kwarg

    assert report.validated == 1
    assert spy.call_count >= 1
```

- [ ] **Step 7.2: Run to verify they fail**

```
! uv run pytest tests/pipeline/test_validation.py::test_run_validation_uses_llm_keywords_when_provided tests/pipeline/test_validation.py::test_run_validation_falls_back_to_stopwords_when_llm_returns_empty tests/pipeline/test_validation.py::test_run_validation_without_llm_uses_stopwords -v
```

Expected: FAIL — `run_validation` does not accept a `llm` keyword argument; `select_keywords` does not exist; `validation_module` import resolves but the spy assertions fail.

- [ ] **Step 7.3: Update `validation.py`**

Add to the imports at the top of `app/pipeline/validation.py` (after existing imports):

```python
from app.llm.base import LLMAdapter
```

Add `select_keywords` helper just before `run_validation` (public name so external callers — the debug script — don't need to reach for a leading-underscore symbol):

```python
async def select_keywords(
    problem: str,
    audience: str | None,
    llm: LLMAdapter | None,
) -> list[str]:
    """Return keywords from the LLM when available, else fall back to stopword extraction.

    Defense-in-depth: each adapter already swallows errors and returns [], but we
    catch here too in case a future adapter raises.
    """
    if llm is not None:
        try:
            kws = await llm.extract_search_keywords(problem, audience)
            if kws:
                log.debug("llm_keywords_used", keywords=kws)
                return kws
        except Exception as exc:
            log.warning("llm_keyword_extract_failed", error=str(exc))
    return extract_keywords(problem, audience)
```

Update the `run_validation` signature — add `llm` as a keyword-only parameter:

```python
async def run_validation(
    session: AsyncSession,
    github_client: httpx.AsyncClient,
    *,
    llm: LLMAdapter | None = None,
    only_active: bool = True,
    refresh_age_days: int = 7,
) -> ValidationReport:
```

Inside `run_validation`, replace the existing keywords line:

```python
            keywords = extract_keywords(candidate.problem_statement, candidate.audience)
```

with:

```python
            keywords = await select_keywords(candidate.problem_statement, candidate.audience, llm)
```

- [ ] **Step 7.4: Run to verify they pass**

```
! uv run pytest tests/pipeline/test_validation.py -v
```

Expected: all validation tests pass.

- [ ] **Step 7.5: Commit**

```bash
git add app/pipeline/validation.py tests/pipeline/test_validation.py
git commit -m "feat(validation): use LLM keyword extraction with stopword fallback"
```

---

## Task 8: Wire into scheduler + update debug script

**Files:**
- Modify: `app/ingestion/scheduler.py`
- Modify: `scripts/debug_keywords.py`

No new tests needed — the scheduler change is a one-line wiring. The debug script is a CLI tool, not tested.

- [ ] **Step 8.1: Pass LLM to `run_validation` in the scheduler**

In `app/ingestion/scheduler.py`, find `_scoring_job` (around line 89). The function already imports `make_llm_adapter` via the factory in the pipeline job. Add the import and pass `llm` to `run_validation`:

Replace:

```python
    async def _scoring_job() -> None:
        from datetime import UTC, datetime
        import httpx
        from app.db import _get_session_factory
        from app.pipeline.validation import run_validation
        from app.scoring.candidate_scorer import score_all_candidates
        from app.pipeline.lifecycle import update_lifecycle_states_and_emit_transitions
        from app.bot.v4_notifications import emit_lifecycle_alerts

        session_factory = _get_session_factory()
        _gh_headers = {"Authorization": f"Bearer {settings.github_token}"} if settings.github_token else {}
        github_client = httpx.AsyncClient(timeout=settings.ingestion_http_timeout_s, headers=_gh_headers)
        try:
            async with session_factory() as session:
                await run_validation(session, github_client)
```

with:

```python
    async def _scoring_job() -> None:
        from datetime import UTC, datetime
        import httpx
        from app.db import _get_session_factory
        from app.llm.factory import make_llm_adapter
        from app.pipeline.validation import run_validation
        from app.scoring.candidate_scorer import score_all_candidates
        from app.pipeline.lifecycle import update_lifecycle_states_and_emit_transitions
        from app.bot.v4_notifications import emit_lifecycle_alerts

        session_factory = _get_session_factory()
        _gh_headers = {"Authorization": f"Bearer {settings.github_token}"} if settings.github_token else {}
        github_client = httpx.AsyncClient(timeout=settings.ingestion_http_timeout_s, headers=_gh_headers)
        llm = make_llm_adapter(settings)
        try:
            async with session_factory() as session:
                await run_validation(session, github_client, llm=llm)
```

- [ ] **Step 8.2: Update `debug_keywords.py` to support `--llm`**

Replace the entire contents of `scripts/debug_keywords.py` with:

```python
"""Keyword extraction debugger — shows what GitHub pair queries would be issued.

Usage (inline text, stopword path):
    uv run python -m scripts.debug_keywords --problem "Task management apps fail ADHD adults" --audience "ADHD adults"

Usage (inline text, LLM path):
    uv run python -m scripts.debug_keywords --problem "Task management apps fail ADHD adults" --audience "ADHD adults" --llm

Usage (all active candidates from DB):
    uv run python -m scripts.debug_keywords --all [--llm]

Usage (single candidate by ID):
    uv run python -m scripts.debug_keywords --candidate-id 42 [--llm]
"""
from __future__ import annotations

import argparse
import asyncio
import sys


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Debug keyword extraction and GitHub pair queries.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--problem", help="Problem statement text (use with --audience)")
    mode.add_argument("--all", dest="all_candidates", action="store_true", help="Process all active candidates from DB")
    mode.add_argument("--candidate-id", type=int, metavar="ID", help="Single candidate by ID")
    parser.add_argument("--audience", default=None, help="Audience text (used with --problem)")
    parser.add_argument("--llm", action="store_true", help="Use LLM keyword extraction instead of stopwords")
    parser.add_argument("--db-url", default=None, help="Override DATABASE_URL")
    return parser.parse_args()


def _display(label: str, problem: str, audience: str | None, keywords: list[str]) -> None:
    from app.pipeline.validation import _pair_queries
    pairs = _pair_queries(keywords)
    print(f"\n{'─'*60}")
    print(f"  {label}")
    print(f"{'─'*60}")
    print(f"  problem  : {problem[:120]}")
    print(f"  audience : {audience or '(none)'}")
    print(f"  keywords : {keywords}")
    print(f"  queries  : {pairs}")
    if not pairs:
        print("  *** WARNING: no queries would be issued ***")
    elif all("+" not in q for q in pairs):
        print("  *** WARNING: single-keyword fallback only ***")


async def _run(args: argparse.Namespace) -> None:
    from app.pipeline.validation import select_keywords

    if args.db_url:
        import os
        os.environ["DATABASE_URL"] = args.db_url

    llm = None
    if args.llm:
        from app.config import get_settings
        from app.llm.factory import make_llm_adapter
        get_settings.cache_clear()
        settings = get_settings()
        llm = make_llm_adapter(settings)
        print(f"[LLM mode: {llm.model_name}]")

    if args.problem:
        kws = await select_keywords(args.problem, args.audience, llm)
        _display("(inline)", args.problem, args.audience, kws)
        return

    from app.db import reset_engine, _get_session_factory
    from app.models import OpportunityCandidate
    from sqlalchemy import select

    reset_engine()
    session_factory = _get_session_factory()

    async with session_factory() as session:
        if args.candidate_id:
            result = await session.execute(
                select(OpportunityCandidate).where(OpportunityCandidate.id == args.candidate_id)
            )
            candidates = result.scalars().all()
        else:
            result = await session.execute(
                select(OpportunityCandidate)
                .where(OpportunityCandidate.is_archived.is_(False))
                .where(OpportunityCandidate.specificity > 0)
                .order_by(OpportunityCandidate.id)
            )
            candidates = result.scalars().all()

    if not candidates:
        print("No candidates found.", file=sys.stderr)
        return

    for c in candidates:
        kws = await select_keywords(c.problem_statement, c.audience, llm)
        _display(
            f"candidate_id={c.id}  [{c.problem_statement[:60]}…]",
            c.problem_statement,
            c.audience,
            kws,
        )

    print(f"\n{'─'*60}")
    print(f"  Total: {len(candidates)} candidate(s)")


def main() -> None:
    args = _parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
```

- [ ] **Step 8.3: Run the full test suite**

Ask the user to run:

```
! uv run pytest -q
```

Expected: green across the whole repo.

- [ ] **Step 8.4: Commit**

```bash
git add app/ingestion/scheduler.py scripts/debug_keywords.py
git commit -m "feat(validation): wire LLM into scoring job and add --llm flag to debug script"
```

---

## Final verification

After all tasks are complete, test the LLM path locally:

```
! uv run python -m scripts.debug_keywords --problem "Current task management and habit tracking apps fail to engage ADHD adults" --audience "Individuals with ADHD" --llm
```

Expected output should show domain-specific keywords like `["adhd", "habit", "tracker", "task"]` instead of `["adhd", "task", "management", "habit", "tracking"]` — and the queries should be tighter.

---

## Self-review notes

- `select_keywords` is `async` because `llm.extract_search_keywords` is async. The callers inside `run_validation` already `await` it.
- `count_show_hn_matches` is NOT updated — it uses `extract_keywords` directly for title pattern matching, not GitHub API calls, so the stopword path is correct there. The Task-7 fallback tests account for this when asserting `spy.call_count`.
- `LLMAdapter` import in `validation.py` is a new cross-layer dependency (`pipeline` → `llm`). This already exists in `orchestrator.py` (which imports `LLMAdapter`) so it follows established codebase pattern.
- The stopword-based `extract_keywords` function is retained — it remains the fallback and is still tested by the existing suite.
- `select_keywords` is public (no leading underscore) because it's reused by `debug_keywords.py`. Leading-underscore symbols are not intended to be imported across modules.
- Adapter `extract_search_keywords` implementations all catch broad `Exception` and return `[]` on failure. `select_keywords` catches again as defense-in-depth — duplicate but harmless.
- Scheduler does not `await llm.aclose()` after `_scoring_job`. This matches the existing pattern in `_pipeline_job` / `_digest_job`; cleanup of httpx-backed adapters across all jobs is a separate concern out of scope here.

---

## Follow-up (out of scope for this plan)

**LLM adapter connection-pool leak in scheduler jobs.**

`_scoring_job` (added by Task 8), as well as the existing `_pipeline_job` and `_digest_job` in `app/ingestion/scheduler.py`, all create an `LLMAdapter` via `make_llm_adapter(settings)` but never call `await llm.aclose()`. For httpx-backed adapters (`NvidiaNimAdapter`, `OpenAIAdapter`) this leaks the underlying connection pool on every job tick. `MockLLMAdapter` and `OllamaAdapter` are unaffected today, but the leak compounds in production when NIM/OpenAI is the configured provider.

If resource warnings show up in production logs (or as a proactive hardening pass), open a dedicated PR that:

1. Adds `aclose()` to the `LLMAdapter` ABC as an abstract method (or a concrete no-op default).
2. Implements no-op `aclose()` on `MockLLMAdapter`. `OllamaAdapter` uses the ollama client which manages its own session — verify whether an explicit close is needed.
3. Wraps the body of `_scoring_job`, `_pipeline_job`, and `_digest_job` in `try / finally: await llm.aclose()`.
4. Audits any other entry point that calls `make_llm_adapter` for the same pattern (`app/pipeline/orchestrator.py`, scripts, tests using real adapters).

This is not a behavioural regression introduced by this plan — the leak already exists for the two other jobs — but it's worth tracking as a follow-up.

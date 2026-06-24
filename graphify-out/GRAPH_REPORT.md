# Graph Report - .  (2026-06-24)

## Corpus Check
- 211 files · ~159,828 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1275 nodes · 3377 edges · 108 communities (95 shown, 13 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 145 edges (avg confidence: 0.57)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Candidate Data Models|Candidate Data Models]]
- [[_COMMUNITY_App Config & Database|App Config & Database]]
- [[_COMMUNITY_Trend Feature Engineering|Trend Feature Engineering]]
- [[_COMMUNITY_Validation & HTTP Ingestion|Validation & HTTP Ingestion]]
- [[_COMMUNITY_Project Concepts & Architecture|Project Concepts & Architecture]]
- [[_COMMUNITY_Bot Handlers & Connector Registry|Bot Handlers & Connector Registry]]
- [[_COMMUNITY_Reddit RSS Migration|Reddit RSS Migration]]
- [[_COMMUNITY_LLM Adapter Interface|LLM Adapter Interface]]
- [[_COMMUNITY_NVIDIA NIM Adapter|NVIDIA NIM Adapter]]
- [[_COMMUNITY_Alembic DB Migrations|Alembic DB Migrations]]
- [[_COMMUNITY_Brief Generation Adapters|Brief Generation Adapters]]
- [[_COMMUNITY_DB Health & Backfill Scripts|DB Health & Backfill Scripts]]
- [[_COMMUNITY_Pain Point Clustering & Identity|Pain Point Clustering & Identity]]
- [[_COMMUNITY_OpenAI LLM Adapter|OpenAI LLM Adapter]]
- [[_COMMUNITY_Candidate Resolution & Recluster|Candidate Resolution & Recluster]]
- [[_COMMUNITY_LLM Factory & Tests|LLM Factory & Tests]]
- [[_COMMUNITY_Core Models & Category|Core Models & Category]]
- [[_COMMUNITY_Base Connector Interface|Base Connector Interface]]
- [[_COMMUNITY_App Discovery & Play Store|App Discovery & Play Store]]
- [[_COMMUNITY_Fresh Deploy Integration Tests|Fresh Deploy Integration Tests]]
- [[_COMMUNITY_LLM Rate Limiting & Factory|LLM Rate Limiting & Factory]]
- [[_COMMUNITY_FastAPI App & Scheduler|FastAPI App & Scheduler]]
- [[_COMMUNITY_Source Item Extraction|Source Item Extraction]]
- [[_COMMUNITY_Clustering Pipeline|Clustering Pipeline]]
- [[_COMMUNITY_Mock LLM Adapter|Mock LLM Adapter]]
- [[_COMMUNITY_Pipeline Orchestrator|Pipeline Orchestrator]]
- [[_COMMUNITY_Data Ingestion Connectors|Data Ingestion Connectors]]
- [[_COMMUNITY_Reddit Connector Tests|Reddit Connector Tests]]
- [[_COMMUNITY_OpenAI Embedding Adapter|OpenAI Embedding Adapter]]
- [[_COMMUNITY_Backfill Progress Tracking|Backfill Progress Tracking]]
- [[_COMMUNITY_Test Fixtures & Config|Test Fixtures & Config]]
- [[_COMMUNITY_Play Store Reviews Connector|Play Store Reviews Connector]]
- [[_COMMUNITY_NIM Embedding Adapter|NIM Embedding Adapter]]
- [[_COMMUNITY_Ollama LLM Adapter|Ollama LLM Adapter]]
- [[_COMMUNITY_Bulk Backfill Pipeline|Bulk Backfill Pipeline]]
- [[_COMMUNITY_API Health & Config Sources|API Health & Config Sources]]
- [[_COMMUNITY_App Settings & Parsing|App Settings & Parsing]]
- [[_COMMUNITY_Cluster Labelling Pipeline|Cluster Labelling Pipeline]]
- [[_COMMUNITY_Bot Middleware & Auth|Bot Middleware & Auth]]
- [[_COMMUNITY_M3 Scoring Milestone|M3 Scoring Milestone]]
- [[_COMMUNITY_Mock Embedding Adapter|Mock Embedding Adapter]]
- [[_COMMUNITY_Embedding Index|Embedding Index]]
- [[_COMMUNITY_Brief Generation Pipeline|Brief Generation Pipeline]]
- [[_COMMUNITY_Clustering Run & Tests|Clustering Run & Tests]]
- [[_COMMUNITY_Embedding Pipeline|Embedding Pipeline]]
- [[_COMMUNITY_Product Roadmap & v4 Plans|Product Roadmap & v4 Plans]]
- [[_COMMUNITY_Ollama Adapter Tests|Ollama Adapter Tests]]
- [[_COMMUNITY_Logging Configuration|Logging Configuration]]
- [[_COMMUNITY_Maintenance & Pruning State|Maintenance & Pruning State]]
- [[_COMMUNITY_M5 Notification Milestone|M5 Notification Milestone]]
- [[_COMMUNITY_Deployment Runbooks|Deployment Runbooks]]
- [[_COMMUNITY_Bot E2E Push Tests|Bot E2E Push Tests]]
- [[_COMMUNITY_Integration Test Connectors|Integration Test Connectors]]
- [[_COMMUNITY_v4 Pipeline Architecture Plans|v4 Pipeline Architecture Plans]]
- [[_COMMUNITY_Pruning Tests|Pruning Tests]]
- [[_COMMUNITY_Ollama Embedding Adapter|Ollama Embedding Adapter]]
- [[_COMMUNITY_Logging Improvements|Logging Improvements]]
- [[_COMMUNITY_Play Store Spike Scripts|Play Store Spike Scripts]]
- [[_COMMUNITY_M4 LLM Provider Plans|M4 LLM Provider Plans]]
- [[_COMMUNITY_Reddit Ingestion Connector|Reddit Ingestion Connector]]
- [[_COMMUNITY_Schema Migration Scripts|Schema Migration Scripts]]
- [[_COMMUNITY_LLM Adapter Interface Tests|LLM Adapter Interface Tests]]
- [[_COMMUNITY_Telegram Digest Output|Telegram Digest Output]]
- [[_COMMUNITY_Backfill CLI Plans|Backfill CLI Plans]]
- [[_COMMUNITY_M5.5 & M6 Milestone Plans|M5.5 & M6 Milestone Plans]]
- [[_COMMUNITY_iOS RSS Connector Tests|iOS RSS Connector Tests]]
- [[_COMMUNITY_v4 Embedding & Brief Plans|v4 Embedding & Brief Plans]]
- [[_COMMUNITY_v4b Code Review Bugs|v4b Code Review Bugs]]
- [[_COMMUNITY_NIM Rate Limiter Plans|NIM Rate Limiter Plans]]
- [[_COMMUNITY_Debug Run Job Scripts|Debug Run Job Scripts]]
- [[_COMMUNITY_Reddit Data Source Plans|Reddit Data Source Plans]]
- [[_COMMUNITY_Bot Scheduler Hooks|Bot Scheduler Hooks]]
- [[_COMMUNITY_Run Job Script Plan|Run Job Script Plan]]
- [[_COMMUNITY_Project CLAUDE|Project CLAUDE.md]]
- [[_COMMUNITY_CICD Pipeline Concept|CI/CD Pipeline Concept]]
- [[_COMMUNITY_M4 Opportunity State|M4 Opportunity State]]
- [[_COMMUNITY_Roadmap Phase 2|Roadmap Phase 2]]
- [[_COMMUNITY_DevTrend Package|DevTrend Package]]
- [[_COMMUNITY_CICD Docker Compose Plans|CI/CD Docker Compose Plans]]
- [[_COMMUNITY_Skeleton Scaffold Plan|Skeleton Scaffold Plan]]
- [[_COMMUNITY_Initial Directory Structure|Initial Directory Structure]]
- [[_COMMUNITY_GHCR Pruning Workflow|GHCR Pruning Workflow]]

## God Nodes (most connected - your core abstractions)
1. `OpportunityCandidate` - 112 edges
2. `SourceItem` - 68 edges
3. `MockLLMAdapter` - 66 edges
4. `PainPoint` - 62 edges
5. `get_settings()` - 52 edges
6. `Settings` - 46 edges
7. `ConnectorRunRegistry` - 45 edges
8. `LLMAdapter` - 38 edges
9. `MockEmbeddingAdapter` - 32 edges
10. `CandidateScoreHistory` - 32 edges

## Surprising Connections (you probably didn't know these)
- `NicheMatcher Keyword Regex` --semantically_similar_to--> `Smart Stopwords List`  [INFERRED] [semantically similar]
  docs/m2-implementation-plan.md → data/smart_stopwords.txt
- `_MockConnector` --uses--> `Settings`  [INFERRED]
  tests/integration/test_fresh_deploy.py → app/config.py
- `_MockConnector` --uses--> `NormalizedItem`  [INFERRED]
  tests/integration/test_fresh_deploy.py → app/ingestion/base.py
- `TestAllowlistMiddleware` --uses--> `RunStatus`  [INFERRED]
  tests/test_bot_handlers.py → app/ingestion/base.py
- `_MockConnector` --uses--> `ConnectorRunRegistry`  [INFERRED]
  tests/integration/test_fresh_deploy.py → app/ingestion/base.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Daily Pipeline Flow: Ingest → Extract → Embed → Cluster → Score → Push** — concept_pain_point_extraction, concept_hdbscan_clustering, concept_identity_resolution, concept_scoring_dimensions, concept_pipeline_orchestrator, concept_telegram_push [EXTRACTED 0.95]
- **CI/CD Workflow Chain: CI → Build&Push → Deploy** — workflows_ci, workflows_build_and_push, workflows_deploy, concept_health_gated_deploy, concept_sops_age_secrets [EXTRACTED 0.95]
- **Candidate Identity Management: Identity Resolution + Re-cluster + Embedding Isolation + Merge DAG** — concept_identity_resolution, concept_weekly_recluster, concept_embedding_model_isolation, concept_merged_into_id [EXTRACTED 0.95]
- **v4 Five-Stage Pain-Point Discovery Pipeline** — plans_v4a_pipeline_extract, plans_v4a_pipeline_embed, plans_v4a_pipeline_identity, plans_v4a_pipeline_clustering, plans_v4a_pipeline_labelling [EXTRACTED 1.00]
- **M3 Three-Dimension Scoring Pipeline (Growth, Demand, Novelty)** — docs_m3_implementation_plan_signal_aggregator, docs_m3_implementation_plan_trend_features, docs_m3_implementation_plan_scoring [EXTRACTED 1.00]
- **M4 LangGraph Agent Pipeline (fetcher→retriever→forecaster→reporter→reviewer)** — docs_m4_implementation_plan_agent_nodes, docs_m4_implementation_plan_agent_graph, docs_m4_implementation_plan_opportunity_state [EXTRACTED 1.00]
- **Validation Pipeline Fix Three-PR Series** — plans_2026_05_18_validation_pr1_github_search_and_keywords_pr1_plan, plans_2026_05_18_validation_pr2_no_signal_scoring_pr2_plan, plans_2026_05_18_validation_pr3_cohesion_gate_pr3_plan [EXTRACTED 1.00]
- **Logging Unification: Mute + ProcessorFormatter + ConsoleRenderer** — plans_2026_05_16_logging_noise_reduction_mute_noisy_loggers, plans_2026_05_16_coherent_json_logging_process_formatter, specs_2026_05_16_logging_noise_reduction_design_console_renderer_swap [INFERRED 0.85]
- **Keyword Extraction: Stopwords + LLM + GitHub Multi-Query** — plans_2026_05_18_validation_pr1_github_search_and_keywords_extract_keywords, plans_2026_05_19_llm_keyword_extraction_select_keywords, plans_2026_05_18_validation_pr1_github_search_and_keywords_pair_queries [INFERRED 0.85]

## Communities (108 total, 13 thin omitted)

### Community 0 - "Candidate Data Models"
Cohesion: 0.06
Nodes (84): CandidateBrief, CandidateFeedback, CandidateScoreHistory, LifecycleEvent, OpportunityCandidate, Bot, cmd_feedback_callback(), Inline button feedback callback handler: 👍/👎. (+76 more)

### Community 1 - "App Config & Database"
Cohesion: 0.06
Nodes (77): get_settings(), _get_engine(), get_session(), _get_session_factory(), AsyncEngine, cmd_view_callback(), Inline-button 'view:' callback — opens the full opportunity scorecard., Handles 'view:<candidate_id>:<brief_id|none>' inline-button callbacks.      Repl (+69 more)

### Community 2 - "Trend Feature Engineering"
Cohesion: 0.07
Nodes (51): percentile_rank(), Pure numerical helpers for trend scoring. No I/O, no DB., Return target's percentile rank (0..100) against history.      Uses the standa, Ordinary least squares slope over evenly spaced x = 0, 1, 2, ...      Returns, rolling_slope(), Composite candidate scorer — writes CandidateScoreHistory rows., Score all active above-gate candidates and persist CandidateScoreHistory., score_all_candidates() (+43 more)

### Community 3 - "Validation & HTTP Ingestion"
Cohesion: 0.08
Nodes (43): CandidateValidation, AsyncClient, request_with_retry(), MonkeyPatch, _make_github_client(), _make_github_response(), Tests for Stage 6 — validation.py, # NOTE: count_show_hn_matches calls extract_keywords for title pattern matching, (+35 more)

### Community 4 - "Project Concepts & Architecture"
Cohesion: 0.10
Nodes (45): DevTrend Project Document v3, BaseConnector Abstraction, Bulk Backfill on Empty DB at Startup, ConnectorRunRegistry In-Memory Status, Data Retention Policy (90d source / 30d signals), Embedding-Model Isolation (per-provider buckets), GitHub Stars as Validation Signal, HDBSCAN Clustering of Pain-Points (+37 more)

### Community 5 - "Bot Handlers & Connector Registry"
Cohesion: 0.14
Nodes (12): help_handler(), start_handler(), ConnectorRunRegistry, RunStatus, _make_update(), Bot handler tests — v4 trimmed., TestAllowlistSecurityPath, TestBotCommandMenuRegistered (+4 more)

### Community 6 - "Reddit RSS Migration"
Cohesion: 0.07
Nodes (30): Reddit Atom/RSS Feed Endpoint, _entry_to_child Atom adapter, Per-sub Graceful Degradation Pattern, RedditConnector (RSS rewrite), RedditRateLimited (removed), Reddit RSS Migration Plan, False 0-repos-found Validation Bug, Validation Pipeline Fixes Index (+22 more)

### Community 7 - "LLM Adapter Interface"
Cohesion: 0.12
Nodes (16): BaseModel, LLMAdapter, Return 3-5 domain-specific keywords for GitHub search.          Returns an emp, Stable model identifier used as the extraction cache key (e.g. 'qwen2.5')., NVIDIA NIM LLM adapter — OpenAI-compatible chat-completions endpoint., Ollama adapter — calls qwen2.5 via the ollama Python client., OpenAI LLM adapter — uses the official openai Python SDK with structured outputs, ClusterLabel (+8 more)

### Community 8 - "NVIDIA NIM Adapter"
Cohesion: 0.14
Nodes (14): NvidiaNimAdapter, adapter(), _chat_response(), _make_adapter(), Tests for NvidiaNimAdapter., test_chat_acquires_on_each_retry(), test_chat_calls_rate_limiter(), test_chat_no_limiter_works() (+6 more)

### Community 9 - "Alembic DB Migrations"
Cohesion: 0.12
Nodes (18): do_run_migrations(), _get_url(), Alembic env.py — async-aware, reads DATABASE_URL from app settings., Generate SQL without a live connection (`alembic upgrade --sql ...`)., run_async_migrations(), run_migrations_offline(), run_migrations_online(), Application (+10 more)

### Community 10 - "Brief Generation Adapters"
Cohesion: 0.11
Nodes (7): Any, _format_evidence(), Shared prompt templates for all LLM adapters (Ollama, NIM, etc.)., Render the user-prompt body for `LLMAdapter.generate_brief()`., render_brief_prompt(), _PromptRenderingAdapter, LLM adapter that calls render_brief_prompt and returns the rendered text.

### Community 11 - "DB Health & Backfill Scripts"
Cohesion: 0.15
Nodes (21): check_db_reachable(), Fail fast if the database is unreachable; rely on Alembic for schema., Reset cached engine and session factory. Intended for use in tests only., reset_engine(), Namespace, main(), _parse_args(), _print_estimate() (+13 more)

### Community 12 - "Pain Point Clustering & Identity"
Cohesion: 0.20
Nodes (18): PainPoint, Candidates with different embedding_model must not match cross-model pain points, test_identity_resolution_filters_by_embedding_model(), ClusteringReport, IdentityResolutionReport, Stage 3 — identity resolution: attach PainPoints to existing candidates., Stage 3: attach unmatched PainPoints to the nearest active candidate., run_identity_resolution() (+10 more)

### Community 13 - "OpenAI LLM Adapter"
Cohesion: 0.16
Nodes (14): OpenAIAdapter, adapter(), _create_completion(), _parse_completion(), Tests for OpenAIAdapter., test_extract_pain_point_content_filter_returns_no_signal(), test_extract_pain_point_exception_returns_no_signal(), test_extract_pain_point_happy_path() (+6 more)

### Community 14 - "Candidate Resolution & Recluster"
Cohesion: 0.19
Nodes (19): Return the surviving (non-archived) candidate at the end of a merge chain., resolve_candidate_root(), Tests for weekly re-cluster pass and candidate_resolution helper., Coherent clusters: 0 merges, 0 splits., Two candidates whose pain-points converge should be merged., Candidates with >30% new pain-points since last labelling should be reset., Overbroad candidate spanning two sub-clusters should be split., Pain points with different embedding_models must not be co-clustered. (+11 more)

### Community 15 - "LLM Factory & Tests"
Cohesion: 0.26
Nodes (19): make_embedding_adapter(), make_llm_adapter(), Tests for the adapter factory., _settings(), test_disabled_means_no_limiter(), test_factory_raises_when_nim_embedding_key_missing(), test_factory_raises_when_nim_key_missing(), test_factory_raises_when_openai_embedding_key_missing() (+11 more)

### Community 16 - "Core Models & Category"
Cohesion: 0.19
Nodes (13): Base, Category, Helper for traversing OpportunityCandidate merge chains., Read categories.yaml and upsert each row into the categories table.      Exist, sync_categories_from_yaml(), DeclarativeBase, Path, Stage 5 — label unlabelled OpportunityCandidates. (+5 more)

### Community 17 - "Base Connector Interface"
Cohesion: 0.18
Nodes (6): BaseConnector, NormalizedItem, _hn_role(), IosRssReviewsConnector, _parse_entry(), iOS App Store RSS connector — optional, behind enable_ios_rss flag.  Disabled

### Community 18 - "App Discovery & Play Store"
Cohesion: 0.18
Nodes (15): Play Store (and optionally iOS) apps we track for review ingestion., TrackedApp, AppListing, load_seed_apps(), Play Store app discovery — static seed loader (C-00 chose path b).  The seed Y, Load the curated seed YAML., Upsert TrackedApp rows from the seed YAML. Returns number of rows upserted., refresh_app_list() (+7 more)

### Community 19 - "Fresh Deploy Integration Tests"
Cohesion: 0.21
Nodes (16): _make_settings(), End-to-end fresh-deploy integration test.  Exercises the complete pipeline fro, Bulk backfill with mock connector seeds SourceItems and runs the pipeline., generate_briefs_for produces at least one CandidateBrief after pipeline runs., Digest job calls bot.send_message with the top candidate's problem statement., Feedback callback inserts a CandidateFeedback row., Second pipeline run attaches new pain-points to existing candidates via identity, Full pipeline on 30 synthetic source items with mocked LLM + embeddings. (+8 more)

### Community 20 - "LLM Rate Limiting & Factory"
Cohesion: 0.16
Nodes (12): _maybe_nim_limiter(), _nim_limiter_for(), AsyncRateLimiter, Async sliding-window rate limiter for NIM API calls., Allow at most `max_requests` per `window_seconds`. Sliding window., Unit tests for AsyncRateLimiter., test_allows_burst_up_to_max(), test_blocks_when_full() (+4 more)

### Community 21 - "FastAPI App & Scheduler"
Cohesion: 0.32
Nodes (14): lifespan(), AsyncIOScheduler, FastAPI, GithubConnector, HNConnector, RedditConnector, build_scheduler(), CLI smoke test for scripts/run_backfill.py (A-22 coverage). (+6 more)

### Community 22 - "Source Item Extraction"
Cohesion: 0.24
Nodes (14): SourceItem, ExtractionReport, Stage 1 — extract pain points from SourceItems., Stage 1: extract pain points from pending SourceItems with role='extraction'., run_extraction(), _make_item(), Tests for Stage 1 — extract., test_extract_creates_painpoint_for_high_signal() (+6 more)

### Community 23 - "Clustering Pipeline"
Cohesion: 0.20
Nodes (15): Re-cluster on coherent data: 0 merges, 0 splits., test_fresh_deploy_recluster_stable(), ndarray, _cluster_labels(), Stage 4 — cluster unmatched PainPoints into new OpportunityCandidates., Return cluster label array (-1 = noise). Tries HDBSCAN, falls back to Agglomerat, _check_relabel_needed(), _cosine_sim() (+7 more)

### Community 24 - "Mock LLM Adapter"
Cohesion: 0.18
Nodes (11): MockLLMAdapter, adapter(), Tests for MockLLMAdapter deterministic behaviour., test_extract_deterministic(), test_extract_returns_no_signal_for_low_signal_text(), test_extract_returns_signal_for_high_signal_text(), test_label_cluster_deterministic(), test_label_cluster_specificity_scales_with_size() (+3 more)

### Community 25 - "Pipeline Orchestrator"
Cohesion: 0.28
Nodes (14): async_sessionmaker, Run all 5 pipeline stages sequentially. Each stage uses its own session., run_pipeline(), Tests for the pipeline orchestrator., _seed_items(), session_factory(), _settings(), test_orchestrator_idempotent() (+6 more)

### Community 26 - "Data Ingestion Connectors"
Cohesion: 0.23
Nodes (11): datetime, test_utc_day_bounds_is_24_hours(), test_utc_day_bounds_start_is_midnight(), test_utc_now_is_aware(), test_utc_start_of_day_aware(), test_utc_start_of_day_naive_treated_as_utc(), test_utc_start_of_day_non_utc_tz_converts(), UTC datetime helpers shared across agents, features, and forecasting. (+3 more)

### Community 27 - "Reddit Connector Tests"
Cohesion: 0.20
Nodes (13): _feed_for_sub(), _make_connector(), A non-2xx for one sub must not abort the whole run., Return the fixture filtered to entries whose link path includes /r/{sub}/., test_delay_between_subreddits(), test_falls_back_to_link_for_id_when_atom_id_is_non_t3(), test_graceful_skip_on_http_error(), test_graceful_skip_on_malformed_xml() (+5 more)

### Community 28 - "OpenAI Embedding Adapter"
Cohesion: 0.18
Nodes (9): OpenAIEmbeddingAdapter, OpenAI embedding adapter — uses the official openai Python SDK., adapter(), _embed_response(), Tests for OpenAIEmbeddingAdapter., test_dim_is_1536(), test_embed_batch_count_matches_input(), test_embed_returns_vectors() (+1 more)

### Community 29 - "Backfill Progress Tracking"
Cohesion: 0.13
Nodes (4): _BackfillProgress, Rich Live progress display driven by structlog events., prog(), Unit tests for _BackfillProgress structlog processor.  Exercises the processor

### Community 30 - "Test Fixtures & Config"
Cohesion: 0.12
Nodes (13): TestClient, _apply_migrations(), _clean_db(), client(), database_url(), Shared test fixtures., A fresh AsyncSession per test., Return a Postgres URL for the test session.      In CI we read DATABASE_URL fr (+5 more)

### Community 31 - "Play Store Reviews Connector"
Cohesion: 0.26
Nodes (12): PlayStoreReviewsConnector, Play Store reviews connector — wraps google-play-scraper., _to_normalized(), _fake_review(), _make_connector(), Tests for PlayStoreReviewsConnector., Reviews older than since should be filtered during fetch (not normalize)., test_normalize_dedupes_by_external_id() (+4 more)

### Community 32 - "NIM Embedding Adapter"
Cohesion: 0.15
Nodes (5): NvidiaNimEmbeddingAdapter, NVIDIA NIM embedding adapter., nim_embedder(), Tests for NIM embedding adapter and identity-resolution filtering., test_embed_calls_rate_limiter()

### Community 33 - "Ollama LLM Adapter"
Cohesion: 0.21
Nodes (7): OllamaAdapter, _fake_chat_response(), The LLM-side review is heuristic-only in Phase 1; the adapter just     delegate, test_generate_brief_returns_model_text(), test_review_brief_flags_short_text(), test_review_brief_returns_no_issues_dict(), test_summarize_evidence_returns_string()

### Community 34 - "Bulk Backfill Pipeline"
Cohesion: 0.21
Nodes (8): ABC, BackfillReport, bulk_backfill(), Bulk backfill orchestrator for fresh installs.  Runs once on startup when the, Split [since, until) into consecutive 7-day windows., One-shot bulk backfill: fetch → v4 pipeline.      Connectors are called sequen, _weekly_windows(), EmbeddingAdapter

### Community 35 - "API Health & Config Sources"
Cohesion: 0.21
Nodes (9): health(), _CommaSepDotEnvSource, _CommaSepEnvSource, _CommaSepMixin, Mixin: fall back to comma-split for list-type env vars that aren't valid JSON., HealthResponse, DotEnvSettingsSource, EnvSettingsSource (+1 more)

### Community 36 - "App Settings & Parsing"
Cohesion: 0.21
Nodes (8): Settings, BaseSettings, PydanticBaseSettingsSource, Tests for Settings defaults., test_config_v4_defaults(), test_reddit_max_subreddits_env(), test_reddit_settings_defaults(), _settings()

### Community 37 - "Cluster Labelling Pipeline"
Cohesion: 0.44
Nodes (12): AsyncSession, Stage 5: label all candidates where labeller_model IS NULL., run_labelling(), Tests for Stage 5 — labelling., _seed_candidate_with_pps(), _seed_wellness(), test_labelling_assigns_category_when_known_slug(), test_labelling_continues_on_single_cluster_error() (+4 more)

### Community 38 - "Bot Middleware & Auth"
Cohesion: 0.24
Nodes (7): _allowlist_check(), _make_callback_update(), Tests verifying that allowlist middleware gates callback queries (B-18)., Simulate a CallbackQuery update — effective_chat resolves from the message's cha, test_middleware_allows_callback_from_allowed_chat(), test_middleware_blocks_callback_from_unallowed_chat(), TestAllowlistMiddleware

### Community 39 - "M3 Scoring Milestone"
Cohesion: 0.26
Nodes (13): ADR-004 — Three-dimension scoring design, Milestone 3 — Features and Scoring, daily_scoring cron job wired into AsyncIOScheduler, forecasting/scoring.py — composite scorer with percentile normalisation, signal_aggregator — daily SourceItem to NicheSignal aggregation, trend_features — rolling_slope and percentile_rank helpers, ADR-005 — Agent graph design (LangGraph), agent graph — build_graph + run_brief_for_niche orchestrator (+5 more)

### Community 40 - "Mock Embedding Adapter"
Cohesion: 0.23
Nodes (8): MockEmbeddingAdapter, _vec(), mock_embedder(), Tests for EmbeddingAdapter implementations., test_mock_embedding_batch(), test_mock_embedding_deterministic(), test_mock_embedding_different_texts_differ(), test_mock_embedding_dim()

### Community 41 - "Embedding Index"
Cohesion: 0.22
Nodes (8): EmbeddingIndex, Brute-force cosine similarity over an in-memory matrix.      Suitable for <10k, Tests for EmbeddingIndex (cosine similarity)., test_index_empty(), test_index_handles_zero_vector(), test_index_multiple_vectors(), test_index_nearest_returns_self_at_top(), test_index_threshold_filters()

### Community 42 - "Brief Generation Pipeline"
Cohesion: 0.30
Nodes (10): _build_evidence_snapshot(), generate_briefs_for(), Stage 9 — Brief generation for top-N candidates at digest time., Generate LLM briefs for given candidates. Idempotent within same day., Tests for Stage 9 — brief_generation.py, _seed(), test_generate_brief_idempotent_same_day(), test_generate_brief_persists() (+2 more)

### Community 43 - "Clustering Run & Tests"
Cohesion: 0.47
Nodes (11): Stage 4: cluster unattached PainPoints and create new OpportunityCandidates., run_clustering(), _make_pp(), Tests for Stage 4 — clustering., _seed_item(), test_clustering_creates_unlabelled_candidates(), test_clustering_groups_similar_points(), test_clustering_propagates_embedding_model() (+3 more)

### Community 44 - "Embedding Pipeline"
Cohesion: 0.29
Nodes (10): EmbeddingReport, Stage 2 — embed pain-point texts., Stage 2: embed all PainPoints that have no embedding yet., run_embedding(), Tests for Stage 2 — embed., _seed_pain_points(), test_embed_handles_empty_batch(), test_embed_populates_null_embeddings() (+2 more)

### Community 45 - "Product Roadmap & v4 Plans"
Cohesion: 0.24
Nodes (11): Phase 1 — Core MVP (M1-M6) — Superseded by v4, DevTrend Roadmap, v4 — Opportunity Discovery Engine, Plan v4.A — Foundation and Pipeline Core, v4 ORM models — Category, PainPoint, OpportunityCandidate, etc., bot/feedback.py — inline 👍/👎 callback handler, Plan v4.B — Scoring, Lifecycle, Bot UX and Feedback, bot/v4_handlers.py — /opportunities, /opportunity, /categories, /emerging (+3 more)

### Community 46 - "Ollama Adapter Tests"
Cohesion: 0.42
Nodes (10): _adapter(), _chat_response(), Tests for OllamaAdapter v4 methods (extract_pain_point + label_cluster)., test_extract_pain_point_invalid_json_falls_back_to_no_signal(), test_extract_pain_point_returns_draft(), test_extract_pain_point_truncates_long_text(), test_label_cluster_invalid_specificity_raises(), test_label_cluster_returns_label() (+2 more)

### Community 47 - "Logging Configuration"
Cohesion: 0.38
Nodes (9): _configure_logging(), main(), CaptureFixture, _isolated_root_logger(), Snapshot/restore root handlers + level so tests don't leak global state., test_configure_logging_installs_single_processor_formatter_handler(), test_configure_logging_is_idempotent(), test_configure_logging_mutes_noisy_loggers() (+1 more)

### Community 48 - "Maintenance & Pruning State"
Cohesion: 0.33
Nodes (6): MaintenanceState, One-row table tracking maintenance job state (e.g. last pruning run)., prune_old_data(), PruneReport, Weekly data pruning: trims old SourceItem, CandidateValidation, and LifecycleEve, Delete stale rows and update MaintenanceState.last_pruned_at.      - SourceIte

### Community 49 - "M5 Notification Milestone"
Cohesion: 0.28
Nodes (9): ADR-006 — Daily digest delivery and spike-alert chaining, bot/formatter.py — MarkdownV2 helpers, bot handlers — /briefing, /niches, /niche, /trending, Milestone 5 — Full Telegram Bot, bot/notifications.py — build_daily_digest, build_spike_alert, bot/scheduler_hooks.py — push_daily_digest, push_spike_alerts, pipeline/lifecycle.py — derive_lifecycle_state + transitions, emit_lifecycle_alerts — capped Telegram lifecycle alert push (+1 more)

### Community 50 - "Deployment Runbooks"
Cohesion: 0.25
Nodes (9): Forward-Only Migration Policy, Migration Safety Runbook, Stub downgrade() Pattern, Plan D Cutover Runbook (SQLite to PostgreSQL), SOPS + age Secrets Encryption, VPS Bootstrap Runbook (Hetzner CX22), CI/CD Infrastructure Design for DevTrend v4, Four-Plan CI/CD Decomposition (A→B→C→D) (+1 more)

### Community 51 - "Bot E2E Push Tests"
Cohesion: 0.36
Nodes (6): _make_github_client(), End-to-end push flow: scoring_job → lifecycle alerts → digest (B-19)., Full scoring → lifecycle → alerts → digest chain with mocked bot., _seed_candidates(), _settings(), test_scoring_pipeline_end_to_end()

### Community 52 - "Integration Test Connectors"
Cohesion: 0.29
Nodes (4): _MockConnector, Play Store ingestion with mocked google_play_scraper produces extraction-role So, Inline mock connector that yields synthetic SourceItems directly into the test s, test_fresh_deploy_playstore_ingestion()

### Community 53 - "v4 Pipeline Architecture Plans"
Cohesion: 0.25
Nodes (8): ClusterLabel — Pydantic schema for cluster labelling output, EmbeddingIndex — NumPy brute-force cosine similarity, PainPointDraft — Pydantic schema for LLM extraction output, pipeline/clustering.py — Stage 4 HDBSCAN clustering, pipeline/extract.py — Stage 1 pain point extraction, pipeline/identity_resolution.py — Stage 3 identity resolution, pipeline/labelling.py — Stage 5 cluster labelling, pipeline/orchestrator.py — run_pipeline sequential stage runner

### Community 54 - "Pruning Tests"
Cohesion: 0.50
Nodes (7): Tests for v4 pruning job (rewritten from v3 in Plan C)., Run the pruning logic directly against the in-memory DB., _run_prune(), Session(), test_prune_deletes_old_lifecycle_events(), test_prune_keeps_latest_validation_per_candidate(), test_prune_painpoints_cascade_with_source_items()

### Community 56 - "Logging Improvements"
Cohesion: 0.38
Nodes (7): Coherent JSON Logging Plan, _configure_logging function, structlog ProcessorFormatter Unification, Logging Noise Reduction Plan, Mute Noisy Third-Party Loggers, ConsoleRenderer Swap (JSON to human-readable), Logging Noise Reduction Design Spec

### Community 57 - "Play Store Spike Scripts"
Cohesion: 0.57
Nodes (6): check_app_metadata(), check_list(), check_reviews(), main(), Play Store viability spike — permanent smoke check for google-play-scraper.  R, _result()

### Community 58 - "M4 LLM Provider Plans"
Cohesion: 0.47
Nodes (6): OllamaAdapter — qwen2.5 LLM adapter, app/llm/openai_adapter.py — OpenAI LLM adapter, app/llm/openai_embedding_adapter.py — OpenAI embedding adapter (1536-dim), Plan — Add OpenAI (GPT-4.1 nano) as LLM provider, app/llm/factory.py — make_llm_adapter + make_embedding_adapter, app/llm/nim_adapter.py — NVIDIA NIM LLM adapter

### Community 59 - "Reddit Ingestion Connector"
Cohesion: 0.47
Nodes (3): Element, _entry_to_child(), _extract_name()

### Community 60 - "Schema Migration Scripts"
Cohesion: 0.40
Nodes (5): Migration script must run idempotently on an already-migrated DB., test_c18_migration_idempotent(), main(), migrate(), Schema migration: add embedding_model and merged_into_id columns (v4.2).  Run

### Community 61 - "LLM Adapter Interface Tests"
Cohesion: 0.33
Nodes (4): Tests that all concrete adapters implement the v4 LLMAdapter interface., A subclass that forgets to implement an abstract method cannot be instantiated., test_abc_raises_on_missing_method(), test_mock_adapter_model_name()

### Community 62 - "Telegram Digest Output"
Cohesion: 0.40
Nodes (6): _candidate_card (digest card fix), cmd_view_callback (details button handler), _render_opportunity_card helper, Telegram Digest Output Fix Plan, ^view: CallbackQueryHandler Registration, Telegram Digest Output Design Spec

### Community 63 - "Backfill CLI Plans"
Cohesion: 0.40
Nodes (5): app/ingestion/backfill.py — bulk_backfill orchestrator, rebuild_historical_signals — bins SourceItems by created_at into NicheSignal rows, Plan — Progress bar for scripts/run_backfill.py, Plan — Backfill Dry-Run Token Estimator, app/pipeline/token_estimator.py — char-based LLM cost estimator

### Community 64 - "M5.5 & M6 Milestone Plans"
Cohesion: 0.40
Nodes (5): Bulk Backfill on Empty DB at Startup, MaintenanceState ORM model — one-row pruning state, Milestone 6 — Hardening and Evaluation, app/maintenance/pruning.py — weekly data retention pruning, scripts/run_replay.py — mock historical replay harness

### Community 65 - "iOS RSS Connector Tests"
Cohesion: 0.60
Nodes (4): _make_connector(), Tests for iOS RSS connector., test_ios_rss_disabled_by_flag(), test_ios_rss_happy_path()

### Community 66 - "v4 Embedding & Brief Plans"
Cohesion: 0.40
Nodes (5): pipeline/embed.py — Stage 2 embedding, pipeline/brief_generation.py — Stage 9 brief generation, F-03 — Fix render_brief_prompt for v4 candidate context, F-01 — Tag PainPoints with embedding_model at embed time, Plan — v4.C Review Fixes

### Community 67 - "v4b Code Review Bugs"
Cohesion: 0.40
Nodes (5): I-4: Empty-string new_state Written to LifecycleEvent, C-2: _extract_brief_id Wrong Attribution Bug, C-1: Lifecycle History Slice Bug, I-3: Score Subquery Reads Peak Score Ever, v4.B Code Review (2026-04-30)

### Community 68 - "NIM Rate Limiter Plans"
Cohesion: 0.67
Nodes (4): LLM Adapter Connection Pool Leak (deferred), AsyncRateLimiter sliding-window limiter, NVIDIA NIM Client-side Rate Limiter Plan, Shared NIM Rate Limiter (lru_cache keyed on api_key)

### Community 70 - "Reddit Data Source Plans"
Cohesion: 0.67
Nodes (3): Plan — Reddit Connector Migration to PullPush.io, Plan — Reddit Connector Rate Limiting and Request Hygiene, RedditRateLimited exception — fail-soft on 429/403

## Knowledge Gaps
- **66 isolated node(s):** `devtrend`, `CI Workflow`, `Prune GHCR Workflow`, `SOPS Age Encryption Config`, `CLAUDE.md Project Instructions` (+61 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **13 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `OpportunityCandidate` connect `Candidate Data Models` to `NIM Embedding Adapter`, `App Config & Database`, `Trend Feature Engineering`, `Validation & HTTP Ingestion`, `Cluster Labelling Pipeline`, `Brief Generation Pipeline`, `Clustering Run & Tests`, `Pain Point Clustering & Identity`, `Brief Generation Adapters`, `Candidate Resolution & Recluster`, `Core Models & Category`, `Bot E2E Push Tests`, `Integration Test Connectors`, `Fresh Deploy Integration Tests`, `Pruning Tests`, `Clustering Pipeline`, `Pipeline Orchestrator`?**
  _High betweenness centrality (0.079) - this node is a cross-community bridge._
- **Why does `get_settings()` connect `App Config & Database` to `Candidate Data Models`, `Trend Feature Engineering`, `API Health & Config Sources`, `App Settings & Parsing`, `Validation & HTTP Ingestion`, `Bot Middleware & Auth`, `Reddit Connector Tests`, `Alembic DB Migrations`, `DB Health & Backfill Scripts`, `Logging Configuration`, `Base Connector Interface`, `Bot E2E Push Tests`, `FastAPI App & Scheduler`, `Data Ingestion Connectors`, `Reddit Ingestion Connector`, `Schema Migration Scripts`, `Test Fixtures & Config`, `Play Store Reviews Connector`?**
  _High betweenness centrality (0.069) - this node is a cross-community bridge._
- **Why does `LLMAdapter` connect `LLM Adapter Interface` to `Candidate Data Models`, `Ollama LLM Adapter`, `Bulk Backfill Pipeline`, `Validation & HTTP Ingestion`, `Cluster Labelling Pipeline`, `NVIDIA NIM Adapter`, `Brief Generation Adapters`, `Brief Generation Pipeline`, `Pain Point Clustering & Identity`, `OpenAI LLM Adapter`, `LLM Factory & Tests`, `Core Models & Category`, `LLM Rate Limiting & Factory`, `Source Item Extraction`, `Mock LLM Adapter`, `Pipeline Orchestrator`, `LLM Adapter Interface Tests`?**
  _High betweenness centrality (0.062) - this node is a cross-community bridge._
- **Are the 8 inferred relationships involving `OpportunityCandidate` (e.g. with `_MockConnector` and `ClusteringReport`) actually correct?**
  _`OpportunityCandidate` has 8 INFERRED edges - model-reasoned connections that need verification._
- **Are the 9 inferred relationships involving `SourceItem` (e.g. with `BaseConnector` and `ConnectorRunRegistry`) actually correct?**
  _`SourceItem` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `MockLLMAdapter` (e.g. with `_MockConnector` and `ClusterLabel`) actually correct?**
  _`MockLLMAdapter` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 8 inferred relationships involving `PainPoint` (e.g. with `_MockConnector` and `ClusteringReport`) actually correct?**
  _`PainPoint` has 8 INFERRED edges - model-reasoned connections that need verification._
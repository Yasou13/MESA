# MESA Tam Dosya ve Modül Ağacı

> İnceleme kaynağı: `MESA-main (39).zip`

## Hariç tutulan üretilmiş/ikili asset kökleri

- `mesa-benchmark/mesa_benchmark/dashboard/static/` — 18 dosya; ana ağaçta içerikleri açılmadı.
- `demo/assets/` — 11 dosya; ana ağaçta içerikleri açılmadı.
- `mesa_dashboard/public/brand/` — 8 dosya; ana ağaçta içerikleri açılmadı.

## Tam ağaç

```text
MESA-main/
├── .agents/
│   ├── mcp_config.example.json
│   ├── mcp_config.json
│   ├── rules.md
│   └── skills/
│       ├── ask-matt/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── batch-grill-me/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── claude-handoff/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── code-review/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── codebase-design/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   ├── DEEPENING.md
│       │   ├── DESIGN-IT-TWICE.md
│       │   └── SKILL.md
│       ├── design-an-interface/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── diagnosing-bugs/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   ├── scripts/
│       │   │   └── hitl-loop.template.sh
│       │   └── SKILL.md
│       ├── domain-modeling/
│       │   ├── ADR-FORMAT.md
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   ├── CONTEXT-FORMAT.md
│       │   └── SKILL.md
│       ├── edit-article/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── git-guardrails-claude-code/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   ├── scripts/
│       │   │   └── block-dangerous-git.sh
│       │   └── SKILL.md
│       ├── grill-me/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── grill-with-docs/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── grilling/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── handoff/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── implement/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── improve-codebase-architecture/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   ├── HTML-REPORT.md
│       │   └── SKILL.md
│       ├── loop-me/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── migrate-to-shoehorn/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── obsidian-vault/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── prototype/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   ├── LOGIC.md
│       │   ├── SKILL.md
│       │   └── UI.md
│       ├── qa/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── request-refactor-plan/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── research/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── resolving-merge-conflicts/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── scaffold-exercises/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── setup-matt-pocock-skills/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   ├── domain.md
│       │   ├── issue-tracker-github.md
│       │   ├── issue-tracker-gitlab.md
│       │   ├── issue-tracker-local.md
│       │   ├── SKILL.md
│       │   └── triage-labels.md
│       ├── setup-pre-commit/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── setup-ts-deep-modules/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   ├── dependency-cruiser.config.cjs
│       │   └── SKILL.md
│       ├── tdd/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   ├── mocking.md
│       │   ├── SKILL.md
│       │   └── tests.md
│       ├── teach/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   ├── GLOSSARY-FORMAT.md
│       │   ├── LEARNING-RECORD-FORMAT.md
│       │   ├── MISSION-FORMAT.md
│       │   ├── RESOURCES-FORMAT.md
│       │   └── SKILL.md
│       ├── to-questionnaire/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── to-spec/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── to-tickets/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── triage/
│       │   ├── AGENT-BRIEF.md
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   ├── OUT-OF-SCOPE.md
│       │   └── SKILL.md
│       ├── ubiquitous-language/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── wayfinder/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── wizard/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   ├── SKILL.md
│       │   └── template.sh
│       ├── writing-beats/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── writing-fragments/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   └── SKILL.md
│       ├── writing-great-skills/
│       │   ├── agents/
│       │   │   └── openai.yaml
│       │   ├── GLOSSARY.md
│       │   └── SKILL.md
│       └── writing-shape/
│           ├── agents/
│           │   └── openai.yaml
│           └── SKILL.md
├── .codex/
│   ├── config.toml
│   ├── hooks/
│   │   ├── mesa_post_compact.py
│   │   ├── mesa_session_end.py
│   │   └── mesa_session_start.py
│   └── hooks.json
├── .dockerignore
├── .env.example
├── .githooks/
│   └── pre-push
├── .github/
│   ├── dependabot.yml
│   └── workflows/
│       ├── benchmark-quality.yml
│       ├── ci.yml
│       └── external-release-gates.yml
├── .gitignore
├── AGENTS.md
├── ARCHITECTURE.md
├── BENCHMARK_METHODOLOGY.md
├── CHANGELOG.md
├── cold_path_trace.txt
├── conftest.py
├── CONTRIBUTING.md
├── data/
│   └── raw/
│       └── ma_dataset.json
├── demo/
│   ├── assets/  [İÇERİK HARİÇ: 11 dosya]
│   ├── console/
│   │   ├── components/
│   │   │   ├── inspector.js
│   │   │   ├── modal.js
│   │   │   ├── sidebar.js
│   │   │   ├── status-badge.js
│   │   │   └── table.js
│   │   ├── console.css
│   │   ├── console.js
│   │   ├── data/
│   │   │   ├── mock-benchmarks.json
│   │   │   ├── mock-graph.json
│   │   │   ├── mock-logs.json
│   │   │   ├── mock-memories.json
│   │   │   └── mock-retrieval.json
│   │   ├── index.html
│   │   └── pages/
│   │       ├── benchmarks.js
│   │       ├── graph.js
│   │       ├── logs.js
│   │       ├── memories.js
│   │       ├── overview.js
│   │       ├── playground.js
│   │       └── retrieval.js
│   ├── demo_server.py
│   ├── index.html
│   ├── Untitled-1.md
│   └── visualizer/
│       ├── app.js
│       ├── index.html
│       └── style.css
├── deploy/
│   └── systemd/
│       ├── mcp-gateway.env.example
│       └── mesa-mcp-gateway.service
├── docker-compose.v4.yml
├── docker-compose.yml
├── Dockerfile
├── docs/
│   ├── adr/
│   │   ├── 0001-use-sqlite-wal-and-lancedb-for-async-memory.md
│   │   ├── 0002-pivot-from-rrf-to-alpha-reranking.md
│   │   ├── 0003-adaptive-llm-routing-with-telemetry.md
│   │   ├── 0004-async-background-tasks-vs-celery.md
│   │   ├── 0005-pagerank-quarantine.md
│   │   ├── 0006-spreading-activation.md
│   │   ├── 0007-wal-queue-phantom-write.md
│   │   ├── 0008-benchmark-architecture.md
│   │   ├── 0009-v4-ledger-and-single-storage-owner.md
│   │   ├── 0010-v4-rrf-and-pagerank-observation.md
│   │   └── 0011-v4-tenant-dataset-authorization.md
│   ├── api-reference.md
│   ├── architecture-v4.md
│   ├── ci-assurance.md
│   ├── colab_kurulum_rehberi.md
│   ├── historical_benchmarks/
│   │   ├── BENCHMARK_INTEGRITY_LOG.md
│   │   ├── v0.2.0_results.md
│   │   ├── v0.4.2_results.md
│   │   ├── v0.5.1_contradiction_results.md
│   │   └── v0.6.0_final_results.md
│   ├── installation.md
│   ├── mesa-benchmark-#U00e7al#U0131#U015ft#U0131rma.md
│   ├── Mesa_Control_Panel.md
│   ├── prometheus_alerts.yml
│   ├── release.md
│   └── RUNBOOK.md
├── examples/
│   └── legal_assistant.py
├── install.sh
├── LICENSE
├── Makefile
├── MANIFEST.in
├── mesa-benchmark/
│   ├── .env.example
│   ├── .gitignore
│   ├── dashboard-ui/
│   │   ├── index.html
│   │   ├── package-lock.json
│   │   ├── package.json
│   │   ├── playwright.config.ts
│   │   ├── public/
│   │   │   └── brand/
│   │   │       ├── apple-touch-icon.png
│   │   │       ├── favicon-16x16.png
│   │   │       ├── favicon-32x32.png
│   │   │       ├── favicon.ico
│   │   │       ├── icon-192.png
│   │   │       ├── icon-512-maskable.png
│   │   │       ├── icon-512.png
│   │   │       └── manifest.webmanifest
│   │   ├── src/
│   │   │   ├── api.ts
│   │   │   ├── App.test.tsx
│   │   │   ├── App.tsx
│   │   │   ├── main.tsx
│   │   │   ├── styles.css
│   │   │   └── types.ts
│   │   ├── tests/
│   │   │   └── console.spec.ts
│   │   ├── tsconfig.app.json
│   │   ├── tsconfig.json
│   │   ├── tsconfig.node.json
│   │   └── vite.config.ts
│   ├── datasets/
│   │   └── legacy/
│   │       └── beam/
│   │           └── v1/
│   │               └── dataset.json
│   ├── Dockerfile
│   ├── mesa_benchmark/
│   │   ├── __init__.py
│   │   ├── __main__.py
│   │   ├── cli.py
│   │   ├── clients/
│   │   │   ├── __init__.py
│   │   │   ├── base.py
│   │   │   ├── dense_rag_client.py
│   │   │   ├── dummy_client.py
│   │   │   ├── letta_client.py
│   │   │   ├── mem0_client.py
│   │   │   ├── mesa_client.py
│   │   │   └── zep_client.py
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── config.py
│   │   │   ├── exceptions.py
│   │   │   ├── generation.py
│   │   │   ├── paths.py
│   │   │   ├── preflight.py
│   │   │   ├── progress.py
│   │   │   ├── runner.py
│   │   │   ├── state_manager.py
│   │   │   └── suite.py
│   │   ├── dashboard/
│   │   │   ├── __init__.py
│   │   │   ├── app.py
│   │   │   ├── catalog.py
│   │   │   ├── exporter.py
│   │   │   ├── jobs.py
│   │   │   ├── models.py
│   │   │   ├── ollama.py
│   │   │   ├── planner.py
│   │   │   ├── registry.py
│   │   │   └── static/  [İÇERİK HARİÇ: 18 dosya]
│   │   ├── datasets/
│   │   │   ├── __init__.py
│   │   │   ├── external_loader.py
│   │   │   ├── loader.py
│   │   │   ├── manifest.py
│   │   │   └── schemas.py
│   │   ├── evaluators/
│   │   │   ├── __init__.py
│   │   │   ├── agreement.py
│   │   │   ├── base.py
│   │   │   ├── exact_match.py
│   │   │   ├── llm_judge.py
│   │   │   ├── multi_model_judge.py
│   │   │   ├── qa_metrics.py
│   │   │   ├── recall_at_k.py
│   │   │   ├── regex.py
│   │   │   └── verdict.py
│   │   ├── metrics/
│   │   │   ├── __init__.py
│   │   │   └── calculator.py
│   │   ├── reports/
│   │   │   ├── __init__.py
│   │   │   ├── reporter.py
│   │   │   └── statistics.py
│   │   ├── resources/
│   │   │   ├── configs/
│   │   │   │   ├── internal/
│   │   │   │   │   ├── contradiction_v3.yaml
│   │   │   │   │   ├── holdout_600.yaml
│   │   │   │   │   ├── multi_hop_graph.yaml
│   │   │   │   │   ├── multi_hop_raw.yaml
│   │   │   │   │   └── smoke_dense.yaml
│   │   │   │   ├── legacy/
│   │   │   │   │   ├── default.yaml
│   │   │   │   │   ├── letta.yaml
│   │   │   │   │   ├── mem0.yaml
│   │   │   │   │   ├── mini_mem0.yaml
│   │   │   │   │   ├── mini_mesa.yaml
│   │   │   │   │   ├── reranking.yaml
│   │   │   │   │   └── zep.yaml
│   │   │   │   ├── release/
│   │   │   │   │   ├── beam_128k.yaml
│   │   │   │   │   ├── longmemeval.yaml
│   │   │   │   │   └── memoryagentbench.yaml
│   │   │   │   └── research/
│   │   │   │       ├── beam_10m_capacity.yaml
│   │   │   │       ├── beam_1m.yaml
│   │   │   │       ├── beam_500k.yaml
│   │   │   │       ├── beam_512_64.yaml
│   │   │   │       ├── locomo.yaml
│   │   │   │       └── memoryagentbench_recsys.yaml
│   │   │   ├── fixtures/
│   │   │   │   ├── internal/
│   │   │   │   │   ├── comprehensive_multihop_only.json
│   │   │   │   │   ├── comprehensive_multihop_raw_v2.json
│   │   │   │   │   ├── contradiction_v3.json
│   │   │   │   │   ├── internal_holdout_600.json
│   │   │   │   │   └── mini_dataset.json
│   │   │   │   └── legacy/
│   │   │   │       ├── comprehensive_200_dataset.json
│   │   │   │       ├── contradiction_200.json
│   │   │   │       └── stress_dataset.json
│   │   │   ├── manifests/
│   │   │   │   ├── external/
│   │   │   │   │   ├── beam-1m.json
│   │   │   │   │   ├── beam-500k.json
│   │   │   │   │   ├── beam-v2.json
│   │   │   │   │   ├── locomo.json
│   │   │   │   │   ├── longmemeval.json
│   │   │   │   │   ├── memoryagentbench-recsys.json
│   │   │   │   │   └── memoryagentbench.json
│   │   │   │   ├── internal/
│   │   │   │   │   ├── comprehensive-v2.json
│   │   │   │   │   ├── contradiction-v2.json
│   │   │   │   │   ├── contradiction-v3.json
│   │   │   │   │   ├── internal-holdout-600.json
│   │   │   │   │   ├── mini-v1.json
│   │   │   │   │   ├── multihop-raw-v2.json
│   │   │   │   │   └── multihop-v2.json
│   │   │   │   └── SOURCES.json
│   │   │   ├── suites/
│   │   │   │   ├── release.yaml
│   │   │   │   ├── research.yaml
│   │   │   │   └── smoke.yaml
│   │   │   └── tokenizers/
│   │   │       ├── cl100k_base.tiktoken
│   │   │       └── o200k_base.tiktoken
│   │   └── sync_tools/
│   │       ├── __init__.py
│   │       ├── download_beam.py
│   │       ├── download_locomo.py
│   │       ├── download_longmemeval.py
│   │       ├── download_memoryagentbench.py
│   │       ├── generate_beam_capacity.py
│   │       ├── generate_beam_chunk_ablation.py
│   │       ├── generate_quality_datasets.py
│   │       └── tokenizers.py
│   ├── README.md
│   ├── scripts/
│   │   ├── download_beam.py
│   │   ├── download_locomo.py
│   │   ├── download_longmemeval.py
│   │   ├── download_memoryagentbench.py
│   │   ├── generate_beam_capacity.py
│   │   ├── generate_beam_chunk_ablation.py
│   │   ├── generate_quality_datasets.py
│   │   ├── legacy/
│   │   │   ├── generate_comprehensive_dataset.py
│   │   │   ├── generate_stress_dataset.py
│   │   │   └── run_comparison.py
│   │   └── publish_to_hf.py
│   ├── tests/
│   │   ├── test_benchmark_layout.py
│   │   ├── test_dashboard.py
│   │   ├── test_dataset_quality_v2.py
│   │   ├── test_evaluators.py
│   │   ├── test_hardening.py
│   │   ├── test_metrics.py
│   │   ├── test_pipeline.py
│   │   ├── test_reporter.py
│   │   └── test_state_manager.py
│   └── USAGE_GUIDE.md
├── mesa_api/
│   ├── __init__.py
│   ├── router.py
│   ├── routers/
│   │   └── control/
│   │       └── router.py
│   ├── schemas.py
│   └── v4_router.py
├── mesa_client/
│   ├── __init__.py
│   ├── client.py
│   └── langchain.py
├── mesa_dashboard/
│   ├── .gitignore
│   ├── .oxlintrc.json
│   ├── index.html
│   ├── package-lock.json
│   ├── package.json
│   ├── public/
│   │   ├── brand/  [İÇERİK HARİÇ: 8 dosya]
│   │   ├── favicon.svg
│   │   └── icons.svg
│   ├── README.md
│   ├── src/
│   │   ├── api/
│   │   │   └── controlApi.ts
│   │   ├── App.css
│   │   ├── App.tsx
│   │   ├── assets/
│   │   │   ├── hero.png
│   │   │   ├── react.svg
│   │   │   └── vite.svg
│   │   ├── components/
│   │   │   └── Layout.tsx
│   │   ├── index.css
│   │   ├── main.tsx
│   │   └── pages/
│   │       ├── Activity.tsx
│   │       ├── Approvals.tsx
│   │       ├── Clients.tsx
│   │       ├── Connections.tsx
│   │       ├── Memories.tsx
│   │       ├── Overview.tsx
│   │       └── Settings.tsx
│   ├── tsconfig.app.json
│   ├── tsconfig.json
│   ├── tsconfig.node.json
│   └── vite.config.ts
├── mesa_evals/
│   ├── __init__.py
│   ├── __main__.py
│   ├── benchmark_adapters/
│   │   ├── __init__.py
│   │   ├── barerag_adapter.py
│   │   ├── base.py
│   │   ├── factory.py
│   │   ├── letta_adapter.py
│   │   ├── mem0_adapter.py
│   │   ├── mesa_adapter.py
│   │   └── zep_adapter.py
│   ├── dataset.py
│   ├── evals.py
│   ├── gatekeeper.py
│   ├── generator.py
│   ├── legal_audit.py
│   ├── legal_generator.py
│   ├── load_test.py
│   ├── recall_harness.py
│   ├── run_beam_eval.py
│   ├── soak_test.py
│   ├── sweep.py
│   └── v4_rrf_ablation.py
├── mesa_mcp/
│   ├── __init__.py
│   ├── adapter.py
│   ├── antigravity_bridge.py
│   ├── antigravity_cli.py
│   ├── codex_cli.py
│   ├── codex_hooks.py
│   ├── configuration.py
│   ├── errors.py
│   ├── gateway/
│   │   ├── __init__.py
│   │   ├── anomaly.py
│   │   ├── app.py
│   │   ├── auth.py
│   │   ├── codex_transport.py
│   │   ├── heartbeat.py
│   │   ├── http_gateway.py
│   │   ├── middleware.py
│   │   ├── operations.py
│   │   └── policy/
│   │       ├── __init__.py
│   │       ├── engine.py
│   │       └── simulator.py
│   ├── http_service.py
│   ├── security.py
│   ├── server.py
│   ├── service.py
│   ├── v4_service.py
│   └── workspace.py
├── mesa_memory/
│   ├── adapter/
│   │   ├── base.py
│   │   ├── claude.py
│   │   ├── factory.py
│   │   ├── live.py
│   │   ├── ollama.py
│   │   └── tokenizer.py
│   ├── api/
│   │   ├── middleware.py
│   │   └── server.py
│   ├── config.py
│   ├── consolidation/
│   │   ├── lock.py
│   │   ├── loop.py
│   │   ├── parser.py
│   │   ├── router.py
│   │   ├── schemas.py
│   │   ├── validator.py
│   │   └── writer.py
│   ├── container_health.py
│   ├── DEPRECATION_NOTICE.md
│   ├── extraction/
│   │   ├── rebel_pipeline.py
│   │   └── triplet_extractor.py
│   ├── observability/
│   │   ├── http.py
│   │   ├── logger.py
│   │   ├── metrics.py
│   │   └── tracer.py
│   ├── retrieval/
│   │   ├── core.py
│   │   ├── decomposition.py
│   │   ├── hybrid.py
│   │   ├── legal_resolver.py
│   │   └── reranker.py
│   ├── runtime_entrypoint.py
│   ├── security/
│   │   ├── admin_cli.py
│   │   ├── api_keys.py
│   │   ├── rbac.py
│   │   └── rbac_constants.py
│   ├── utils.py
│   ├── valence/
│   │   ├── __init__.py
│   │   ├── core.py
│   │   ├── drift.py
│   │   └── novelty.py
│   └── worker_runtime.py
├── mesa_storage/
│   ├── __init__.py
│   ├── alembic/
│   │   ├── env.py
│   │   ├── README
│   │   ├── script.py.mako
│   │   └── versions/
│   │       ├── 076eef5d1b6c_add_daily_limits.py
│   │       ├── 087de6628c51_add_mcp_activity_approval.py
│   │       ├── 1e7a061f7f9e_add_mcp_clients_and_bindings.py
│   │       ├── 41402f316580_add_temporal_validity_to_nodes.py
│   │       ├── 4933fb5fd0ea_initial_schema.py
│   │       ├── 9a1b2c3d4e5f_add_v4_catalog_provenance.py
│   │       ├── 9c6c7ae69ed7_add_mcp_policy_rules.py
│   │       ├── a1d2e3f4b5c6_add_wal_projection_states.py
│   │       ├── a94df5d14fce_add_connections_and_settings.py
│   │       ├── b2e3f4a5c6d7_add_session_finalization_journal.py
│   │       ├── bb2355d0cdd4_add_epistemic_columns.py
│   │       ├── c3d4e5f6a7b8_replace_daily_limit_credential_subject.py
│   │       ├── c4f1a8e2d9b0_add_purge_journal.py
│   │       ├── c5d6e7f8a9b0_add_mcp_operation_ledger.py
│   │       ├── d4e5f6a7b8c9_add_v4_mutation_ledger.py
│   │       ├── d6e7f8a9b0c1_add_mcp_client_credentials.py
│   │       ├── e7f8a9b0c1d2_add_mcp_codex_profiles.py
│   │       ├── e9b7c3a1d4f2_add_claim_leases.py
│   │       ├── f6d4a7b8c9e0_add_dispatch_journal.py
│   │       ├── f7e5b9c0d1a2_add_dispatch_queue_payload_bytes.py
│   │       ├── f8a6c0d1e2b3_add_dispatch_completion_receipts.py
│   │       └── f8a9b0c1d2e3_generalize_mcp_context_profiles.py
│   ├── alembic.ini
│   ├── control/
│   │   ├── __init__.py
│   │   ├── activity_repo.py
│   │   ├── approval_repo.py
│   │   ├── client_repo.py
│   │   ├── codex_profile_repo.py
│   │   ├── connection_repo.py
│   │   ├── credential_repo.py
│   │   ├── policy_repo.py
│   │   ├── retention_repo.py
│   │   └── settings_repo.py
│   ├── dao.py
│   ├── kuzu_migration.py
│   ├── kuzu_provider.py
│   ├── kuzu_schema_migration.py
│   ├── kuzu_setup.py
│   ├── recovery.py
│   ├── schema_contract.py
│   ├── schemas.py
│   ├── sqlite_engine.py
│   └── vector_engine.py
├── mesa_workers/
│   ├── __init__.py
│   ├── entity_consolidation_worker.py
│   ├── ingestion_worker.py
│   ├── maintenance.py
│   ├── maintenance_pagerank.py
│   ├── projection_worker.py
│   ├── rem_cycle.py
│   └── supervision.py
├── notebooks/
│   └── MESA_Colab_Demo.ipynb
├── pyproject.toml
├── README.md
├── README_MCP.md
├── scripts/
│   ├── canary_smoke_test.py
│   ├── check_mypy_override_ratchet.py
│   ├── down_migrate.py
│   ├── fix_dao.py
│   ├── health_check.py
│   ├── migrate_kuzu_schema.py
│   ├── migrate_raw_logs_agent_id.py
│   ├── migrate_to_kuzu.py
│   ├── release_preflight.py
│   ├── release_v0.4.2.sh
│   ├── release_v0.5.2.sh
│   ├── reproduce_benchmark.py
│   ├── reproduce_deadlock.py
│   ├── run_ablation.py
│   ├── run_all_benchmarks.sh
│   ├── run_demo_rag.py
│   ├── run_server.py
│   ├── test_deadlock.py
│   ├── test_manual.sh
│   ├── test_prometheus_alerts.py
│   ├── test_rate_limiting.py
│   └── verify_deadlock_fix.py
├── SECURITY.md
├── skills-lock.json
├── tests/
│   ├── bench/
│   │   ├── __init__.py
│   │   └── locustfile.py
│   ├── bench_async_io.py
│   ├── conftest.py
│   ├── fixtures/
│   │   └── vectors.py
│   ├── go_live_proofs/
│   │   ├── mock_ollama.py
│   │   ├── test_backup_restore.py
│   │   ├── verify_r10_mcp_spoofing.py
│   │   └── verify_r19_payload.py
│   ├── integration/
│   │   └── __init__.py
│   ├── test_adapter.py
│   ├── test_adapter_factory.py
│   ├── test_adapter_live.py
│   ├── test_adapters.py
│   ├── test_adaptive_router.py
│   ├── test_antigravity_bridge_hardening.py
│   ├── test_api_control_router_wiring.py
│   ├── test_api_key_store.py
│   ├── test_api_logging_contract.py
│   ├── test_api_router.py
│   ├── test_api_schemas.py
│   ├── test_async_client_auth_contract.py
│   ├── test_async_lock_loop.py
│   ├── test_async_storage.py
│   ├── test_auth_configuration_contract.py
│   ├── test_chaos.py
│   ├── test_ci_coverage_contracts.py
│   ├── test_codex_cli.py
│   ├── test_codex_control_api.py
│   ├── test_codex_gateway_credentials.py
│   ├── test_codex_streamable_transport.py
│   ├── test_confidence_revision.py
│   ├── test_config.py
│   ├── test_config_edge_cases.py
│   ├── test_conflict_resolution.py
│   ├── test_consolidation.py
│   ├── test_control_activity.py
│   ├── test_control_activity_approval.py
│   ├── test_control_approval.py
│   ├── test_control_client_repo.py
│   ├── test_control_conn_settings.py
│   ├── test_control_middleware.py
│   ├── test_control_policy.py
│   ├── test_crossencoder_reranking.py
│   ├── test_dao.py
│   ├── test_dao_coverage.py
│   ├── test_dao_extended.py
│   ├── test_deployment_assets.py
│   ├── test_dispatch_completion_contract.py
│   ├── test_domain_extraction.py
│   ├── test_downstream_fence_reconciliation_contract.py
│   ├── test_durable_dispatch_contract.py
│   ├── test_durable_dlq_contract.py
│   ├── test_embedding.py
│   ├── test_entity_consolidation_worker.py
│   ├── test_fault_tolerance.py
│   ├── test_graph_v2_identity.py
│   ├── test_graph_v2_purge.py
│   ├── test_ingestion_trace_path.py
│   ├── test_ingestion_worker.py
│   ├── test_kuzu_isolation.py
│   ├── test_kuzu_migration_coordinator.py
│   ├── test_kuzu_performance.py
│   ├── test_legal_audit.py
│   ├── test_logging_contract.py
│   ├── test_maintenance_worker.py
│   ├── test_mcp_api_boundary.py
│   ├── test_mcp_gateway_operations.py
│   ├── test_mcp_operation_finality.py
│   ├── test_mcp_v4_service.py
│   ├── test_mcp_v4_tools.py
│   ├── test_mem0.py
│   ├── test_mesa_benchmark_enhancements.py
│   ├── test_metrics.py
│   ├── test_migration_closure.py
│   ├── test_optional_adapter_imports.py
│   ├── test_optional_mem0_isolation.py
│   ├── test_p0a_batch.py
│   ├── test_p0b_missing.py
│   ├── test_p0c_loop.py
│   ├── test_pagerank_coverage.py
│   ├── test_principal_authorization.py
│   ├── test_purge_journal_contract.py
│   ├── test_queue_admission_contract.py
│   ├── test_queue_trusted_root_contract.py
│   ├── test_rate_limit_subject_contract.py
│   ├── test_rbac.py
│   ├── test_rbac_edge_cases.py
│   ├── test_rbac_leak.py
│   ├── test_rebel_pipeline.py
│   ├── test_recovery_contract.py
│   ├── test_release_preflight.py
│   ├── test_rem_cycle.py
│   ├── test_retrieval.py
│   ├── test_retrieval_edge_cases.py
│   ├── test_router_coverage.py
│   ├── test_runtime_profiles_contract.py
│   ├── test_sdk_retry_contract.py
│   ├── test_search_response_contract.py
│   ├── test_session_finalization_contract.py
│   ├── test_session_lifecycle.py
│   ├── test_session_principal_route_isolation.py
│   ├── test_sqlite_engine_coverage.py
│   ├── test_storage_unification.py
│   ├── test_tech_debt_fixes.py
│   ├── test_tier3_resilience.py
│   ├── test_tier3_validator.py
│   ├── test_triple_store_mutation_contract.py
│   ├── test_turkish_extraction.py
│   ├── test_typing_ratchet.py
│   ├── test_v4_admin_cli.py
│   ├── test_v4_api_contract.py
│   ├── test_v4_catalog_ownership.py
│   ├── test_v4_ingestion_contract.py
│   ├── test_v4_projection_integration.py
│   ├── test_v4_rrf_ablation.py
│   ├── test_v4_sdk_contract.py
│   ├── test_valence.py
│   ├── test_valence_persistence.py
│   ├── test_vector_engine.py
│   ├── test_vector_engine_coverage.py
│   ├── test_vector_model_isolation.py
│   ├── test_wal_claim_replay_contract.py
│   ├── test_wal_recovery.py
│   ├── test_worker_dispatch_consumer.py
│   ├── test_worker_logging_contract.py
│   ├── test_worker_runtime_contract.py
│   ├── test_worker_supervision_contract.py
│   └── utils/
│       ├── __init__.py
│       └── storage_helpers.py
├── typing/
│   └── mypy-progressive-overrides.json
└── uv.lock
```

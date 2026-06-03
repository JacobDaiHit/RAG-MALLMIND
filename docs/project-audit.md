# Project Audit: MallMind RAG Shopping Agent

Last updated: 2026-05-27

## Current runnable pipeline

The current backend is runnable and test-covered around one primary path:

```text
frontend/index.html + frontend/app.js
  -> rag.api.recommendation_app FastAPI
  -> normalize attachments / preprocess input
  -> parse_requirement / intent_route
  -> load data/ecommerce_products/products.json
  -> optional Milvus evidence retrieval when enabled
  -> score_products
  -> build one ecommerce recommendation (`single_product` or `shopping_bundle`)
  -> product_cards / candidate_scope / comparison_table
  -> SSE or JSON response
```

The strongest part of the project today is the P0 single-product and bundle recommendation loop. It already grounds product ids, prices, images, FAQ, reviews, and SKU information in the local dataset, and the tests protect the basic data and API contract.

2026-05-25 update: the basic README loop now has API coverage through `POST /api/chat/stream`, `POST /api/products/compare`, and `POST /api/cart/actions`. The web UI can keep using the existing endpoints, while Android can target the README-aligned chat/cart interfaces.

## Target pipeline from README

The README target is broader:

```text
Android native client
  -> FastAPI streaming service
  -> Input Preprocessor: text / voice / image
  -> Intent Router
  -> Constraint Parser
  -> structured filtering + vector retrieval + rerank
  -> tool/rule layer
  -> LLM grounded generator
  -> product cards / PC build cards / cart operations
```

For PC builds, the target is not ordinary product retrieval. It requires a rule-driven planner that combines CPU, GPU, motherboard, memory, SSD, PSU, case, and cooler into a compatible purchasable plan.

## Gap analysis

| Area | Current state | Target gap | Priority |
| --- | --- | --- | --- |
| Client | Web debug UI in `frontend/` | README expects Android native Kotlin + Compose | P1 for course requirement |
| Input preprocessing | Text cleanup plus normalized attachment signals | Real ASR and VLM/OCR model execution is only partial/fallback | P1 |
| Intent routing | Route is explicit in result trace | Route does not yet dispatch to separate domain handlers | P0 |
| Constraint parsing | Rules + optional LLM parse category, price, terms, exclusions | Attributes are still mostly keyword-based; no typed product-attribute parser | P0 |
| Structured filtering | `structured_filter.py` now applies stock, exclusion, must-have and budget before scoring, with budget relaxation when no exact match exists | Need typed attributes and better synonym mapping | P0 |
| Vector retrieval | Milvus path exists but is disabled by default | Need deterministic local index build and demo data ingestion path | P1 |
| Rerank | Scoring is custom weighted ranking | No separate reranker stage or evaluator | P2 |
| LLM generation | LLM enriches explanation when configured | LLM is not yet a strict final generator with validation before output | P1 |
| Product cards | `product_cards` are emitted | Frontend still needs stronger card-first UX and Android parity | P1 |
| Multi-turn | `session_state.py` keeps last goal/result and supports short follow-up merging | References such as "the second one" still need richer entity resolution | P0 |
| Product comparison | `comparison_table` exists for plans; `/api/products/compare` supports 2-3 real products | Need frontend/Android UI for item-level comparison | P0 |
| Cart | `/api/cart/actions` supports add/remove/set quantity/clear in demo memory | Need persistence and order draft model | P1 |
| PC build | Route can identify intent; docs define rules | Dataset lacks PC part categories and compatibility fields; no planner/checker yet | P1/P0 highlight |
| Anti-hallucination | Product ids and prices come from dataset | Need explicit `validate_answer()` and `validate_pc_plan()` gates | P0 |

## Codebase findings

1. `rag/api/recommendation_app.py` is too large.

   It still contains API definitions, image-attachment parsing, recommendation response shaping, session/cart wiring, and feedback in one file. The worst old plan-help and plan-adjustment logic has been removed, but the file is still too large for the README target architecture.

2. Legacy API-component concepts have been removed from the API entrypoint.

   2026-05-26 update: `ComponentCategory` now only describes ecommerce product categories. The old `/api/plan-help`, `/api/adjust-plan`, and default-plan injection code has been removed from `rag/api/recommendation_app.py`; PC build-plan help/adjustment should be rebuilt later from `docs/advice_pc.md` as a dedicated PC planner instead of reusing the old API-stack plan helpers. The old API-product alias fields have also been removed from `ApiProduct`; ecommerce code now uses `product_id/title/brand`.

3. The current package name `rag` is too generic for the target app.

   It still houses ecommerce ingestion, Milvus storage, optional RAG utilities, schemas, and recommendation code together. The target app would be clearer with `server/app/{api,agent,domain,rag,schemas}` or a transitional `mallmind/` package.

4. PC build is currently blocked by data, not by endpoint plumbing.

   The system can recognize PC-build intent, but `data/ecommerce_products/products.json` only has beauty, digital, clothing, and food. There are no CPU/GPU/motherboard categories, sockets, wattage, GPU length, PSU headroom, case limits, or compatibility tags.

5. Filtering is not strict enough yet.

   `score_products()` removes excluded terms and categories, but budget/category filtering is still partly score-driven. For a reliable demo, hard constraints should narrow candidates first, then scoring should rank the survivors.

6. Tests are useful but still aligned to the transitional architecture.

   They verify product data integrity, recommendation output, SSE validation, embedding, and Milvus helpers. They do not yet assert multi-turn session state, cart operations, strict anti-hallucination, or PC compatibility.

## Cleanup already performed

- Removed `.pytest_cache/`.
- Removed all `__pycache__/` folders and `.pyc` files under project directories.
- Removed `.codex-web-stdout.log` and `.codex-web-stderr.log`.
- Moved root design notes into `docs/`:
  - `SOMECLOUD.md` -> `docs/pc-build-challenges.md`
  - `RULES.md` -> `docs/pc-build-rules.md`
- Removed empty root `workflow.md`.
- Removed legacy `scripts/run.py`, which launched the old generic document RAG path instead of the ecommerce guide loop.
- Removed legacy `rag/pipeline/`, which was a generic document-question-answer LangGraph path and was not used by the ecommerce shopping flow.
- Removed legacy `rag/ingestion/document_loader.py`, which loaded PDF/Word/Excel files for old three-level document chunking.
- Added `rag/ingestion/product_chunks.py` and `scripts/index_ecommerce_products.py` so optional Milvus indexing works from ecommerce product profiles, SKU, FAQ, and review chunks.
- Replaced the old PDF chunk-quality integration test with ecommerce product chunk tests.
- Removed API-component enum values from `ComponentCategory`; the enum now only contains `beauty`, `digital`, `clothing`, and `food`.
- Changed attachment handling to image-only ecommerce guidance; PDF parsing and knowledge-base candidate logic have been removed from `rag/api/attachments.py`.
- Removed `/api/plan-help`, `/api/adjust-plan`, default-plan selection, and their old API-stack plan helper functions from `rag/api/recommendation_app.py`.
- Replaced three-tier recommendation plans with a single ecommerce-native `single_product` or `shopping_bundle` recommendation type in `rag/recommendation/package_builder.py`.
- Removed legacy period price fields from `CostEstimate`; pricing now uses `total_price_min` / `total_price_max` and SKU-level `selected_total_price`.
- Removed legacy API-product alias compatibility fields from product schemas, product loading, product chunks, Milvus writer/retriever payloads, and the debug frontend.
- Renamed model/runtime-style scoring dimensions to ecommerce-native product scores: scenario, attributes, price, reputation, availability, SKU completeness, and detail quality.

## Directory recommendation

Recommended target layout:

```text
server/
  app/
    main.py
    api/
      chat.py
      products.py
      cart.py
      pc_build.py
    agent/
      input_preprocessor.py
      intent_router.py
      constraint_parser.py
      session_state.py
    domain/
      ecommerce_recommendation.py
      pc_compatibility.py
      cart.py
      anti_hallucination.py
    rag/
      ingest.py
      retriever.py
      reranker.py
      generator.py
    schemas/
      product.py
      recommendation.py
      chat.py
data/
  ecommerce_products/
  pc_components/
frontend/
docs/
tests/
```

Recommended transitional layout without a disruptive rename:

```text
rag/
  api/
    recommendation_app.py      # keep as main FastAPI entry for now
    products.py
    attachments.py
  recommendation/
    input_preprocessor.py
    intent_router.py
    recommendation_pipeline.py
    product_loader.py
    scorer.py
    package_builder.py
    pc/
      compatibility.py         # next
      planner.py               # next
  schemas/
  ingestion/                   # ecommerce product chunk builder + embedding service
  storage/                     # keep only if Milvus/RAG ingestion remains in demo
```

## Deletion candidates, with risk

Safe to delete now:

- generated caches: `__pycache__/`, `.pytest_cache/`, `.codex-web-*.log`
- empty scratch files

Do not delete yet:

- `rag/ingestion/embedding.py`, `rag/ingestion/product_chunks.py`, `rag/storage/`, and `scripts/index_ecommerce_products.py`: optional Milvus retrieval and tests still depend on them.
- `rag/utils/rag_utils.py`: optional query expansion, rerank, and auto-merge helpers still support `rag/recommendation/retrieval.py`.
- `scripts/`: several scripts are still useful for demo startup, import, indexing, and diagnostics.

Candidate for next cleanup after refactor:

- Split ecommerce API handlers out of `recommendation_app.py`.
- Remove `ApiProduct` compatibility field names from client-facing responses once frontend/Android only read `product_id/title/brand`.
- Retire the `recommend_api_stack()` compatibility alias after scripts/tests have migrated to `recommend_shopping_products()`.
- Remove unused LangChain graph dependencies if the final demo keeps only deterministic local scoring, optional LLM calls, and optional Milvus retrieval.

## Next implementation plan

1. Split `recommendation_app.py`.

   Move image attachment parsing, chat/session handlers, feedback, and product-facing endpoints into smaller modules. Rebuild PC build-plan generation later as a separate route/module guided by `docs/advice_pc.md`.

2. Strengthen ecommerce-native multi-turn state.

   Store session constraints, last candidate list, selected product ids, and last recommended plan.

3. Strengthen item-level comparison.

   Compare selected products by price, rating, category, SKU, evidence, pros/cons, and suitability.

4. Persist cart CRUD.

   The demo memory cart is working; next step is SQLite-backed sessions and order drafts.

5. Add PC dataset and planner.

   Create `data/pc_components/products.json`, add PC part schemas, implement `validate_pc_plan()`, and return `pc_build_plan` events.

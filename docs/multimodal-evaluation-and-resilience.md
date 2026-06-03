# Multimodal Evaluation And Resilience

Last updated: 2026-06-01

## Current Evaluation Baseline

The offline multimodal smoke suite lives in `tests/test_multimodal_eval.py`. It uses mocked VLM clients so it can run without network access, API keys, or real image fixtures.

Covered scenarios:

| Scenario | Expected behavior |
| --- | --- |
| Image success | VLM JSON is normalized into `summary`, `visual_query_terms`, and `visual_attributes`. |
| Vision model unconfigured | Attachment returns `analysis_status=skipped` and remains usable as image context. |
| Vision model error | Attachment returns `analysis_status=fallback` with a visible error summary. |
| Product screenshot OCR | OCR text and price/product terms enter chat trace and requirement parsing. |
| Street same-style request | Visual terms such as category, color, and clothing subtype enter recommendation constraints. |
| PC config screenshot | OCR component clues are preserved in attachment analysis and chat routes to PC build planning when text also indicates PC intent. |

Run it with:

```bash
pytest tests/test_multimodal_eval.py -q
```

The suite is intentionally small. It should stay fast enough to run on every change to `rag/api/attachments.py`, `rag/api/recommendation_app.py`, and recommendation parsing.

## Current Implementation Level

The system now has a usable first-pass image shopping loop:

1. Browser reads images with `FileReader.readAsDataURL`.
2. `/api/analyze-attachments` or `/api/chat/stream` receives image data URLs.
3. The backend validates type/size and calls an OpenAI-compatible VLM when configured.
4. The VLM returns JSON with OCR, visual search terms, and visual attributes.
5. The backend cleans the JSON through an allowlist.
6. The cleaned visual terms are folded into the normal text recommendation path.

This is not true visual similarity search yet. Same-style matching depends on the VLM extracting useful words and the catalog having matching textual fields.

2026-06-01 update: a first local image-vector retrieval path now exists in `rag/recommendation/image_retrieval.py`. It builds `data/image_vectors.json` from product images and uses a deterministic pixel embedding for upload-image similarity search. This is a real image embedding and retrieval path, but it is not a semantic CLIP/SigLIP model yet.

Build the local image index with:

```bash
python scripts/index_product_images.py
```

Control it with:

```env
RECOMMENDATION_ENABLE_IMAGE_RETRIEVAL=true
IMAGE_RETRIEVAL_TOP_K=8
```

## Concurrency And Timeout Plan

Current state:

- Attachment analysis is synchronous inside the request path.
- The OpenAI-compatible client has one request timeout via `LLM_TIMEOUT_SECONDS`.
- Attachments are bounded by count and size, but there is no global request queue or per-route concurrency limit.

Recommended next implementation:

1. Add dedicated multimodal timeout settings.
   - `VISION_TIMEOUT_SECONDS`, default lower than general LLM timeout.
   - `VISION_MAX_ATTACHMENTS_PER_REQUEST`, default 4 for chat and 6 for explicit analysis.
   - `VISION_MAX_CONCURRENT_REQUESTS`, enforced with a process-level semaphore.

2. Make attachment analysis fail soft.
   - If semaphore is saturated, return `analysis_status=fallback`, `analysis_source=vision_capacity_limited`.
   - If model times out, return `analysis_status=fallback`, `analysis_source=vision_model_timeout`.
   - If JSON parsing fails, return `analysis_status=fallback`, `analysis_source=vision_model_error`.
   - Never block the text-only recommendation path after the fallback summary is built.

3. Add cache for repeated image analysis.
   - Hash decoded bytes with SHA-256.
   - Cache normalized attachment analysis for a short TTL or in SQLite.
   - Include model name and prompt version in the cache key.

4. Stream user-visible status early.
   - Emit `attachment_analysis_started` before model calls if analysis may be slow.
   - Emit `attachment_analysis` after success or fallback.
   - Keep `progress` messages short and human readable.

5. Split heavy work when productionizing.
   - Chat route should stay responsive.
   - For large images or multiple files, enqueue analysis and return a pending state, or run a bounded background task with polling.
   - For demo mode, synchronous analysis is acceptable as long as count/size/time limits are strict.

## Degradation Matrix

| Failure | User-visible status | Recommendation behavior |
| --- | --- | --- |
| No VLM config | `skipped / vision_model_unconfigured` | Continue with image metadata and ask user to supplement details if needed. |
| Decode failure | `fallback / decode_error` | Ignore bytes, keep filename/type/size in trace. |
| Too large | `fallback / too_large` | Keep metadata, avoid model call. |
| Timeout | `fallback / vision_model_timeout` | Continue text recommendation with fallback summary. |
| Capacity limited | `fallback / vision_capacity_limited` | Continue text recommendation and ask user to retry image analysis later. |
| Invalid JSON | `fallback / vision_model_error` | Continue with summary error and no structured visual fields. |

## Next Checks Before Image Embeddings

- Add timeout/capacity tests with a fake slow VLM and semaphore saturation.
- Add a small catalog-based ranking assertion: a street-style image with `卫衣` should rank clothing items above unrelated products.
- Persist evaluation examples as JSON fixtures once real sample images are available.
- Replace the current pixel embedding with CLIP/SigLIP image embeddings, keeping the same `ImageRetrievalEvidence` fusion contract.
- Add a Milvus-backed image collection after the local JSON index has stable behavior under tests.

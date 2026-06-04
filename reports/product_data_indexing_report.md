# Product Data Indexing Report

Date: 2026-06-04

## Scope

- Ecommerce product text source: `data/ecommerce_products/products.json`
- Ecommerce image source: `data/ecommerce_products/images`
- PC product text source: `data/jd_pc_products/products.json`

Excluded from text Milvus indexing:

- Image files and image vectors
- `parts.json` classified card exports
- manifests, reports, caches, source CSV files, scripts, screenshots, and legacy docs

## Phase 1: Compile Checks

Status: partial pass

The requested `rag/recommendation/product_chunks.py` and `rag/recommendation/embedding.py` paths do not exist in this repository. The actual modules are:

- `rag/ingestion/product_chunks.py`
- `rag/ingestion/embedding.py`

Compiled successfully:

- `rag/recommendation/product_loader.py`
- `rag/recommendation/retrieval.py`
- `rag/recommendation/image_retrieval.py`
- `rag/storage/milvus_client.py`
- `rag/storage/milvus_writer.py`
- `scripts/rebuild_product_vector_index.py`
- `scripts/index_ecommerce_products.py`
- `scripts/index_product_images.py`
- `scripts/eval_retrieval.py`
- `rag/ingestion/product_chunks.py`
- `rag/ingestion/embedding.py`

## Phase 2: Text Chunk Dry Run

Status: pass

Command:

```powershell
python scripts/rebuild_product_vector_index.py --dry-run --provider local
```

Result:

- Total text chunks: 884
- Ecommerce product chunks: 400
- PC product chunks: 484
- Image-like text chunks: 0

Chunk types:

- `profile`: 342
- `sku`: 342
- `faq`: 100
- `review`: 100

## Phase 3: Image Index Dry Run

Status: pass

Command:

```powershell
python scripts/index_product_images.py --dry-run
```

Result:

- Catalog products: 100
- Indexable images: 100
- Image embedding version: `pixel-hist-v1`
- Image embedding dim: 61
- Output path: `data/image_vectors.json`

## Phase 4: Image Index Build

Status: pass

Command:

```powershell
python scripts/index_product_images.py
```

Result:

- Indexed images: 100
- Output path: `data/image_vectors.json`
- PC image entries: 0
- Bad-dimension entries: 0
- Vector length distribution: `{61: 100}`

Minimal fix applied:

- Corrected `IMAGE_EMBEDDING_DIM` from `53` to `61` to match the actual pixel embedding output.

## Phase 5: Text Milvus Index Build

Status: blocked by environment

Command:

```powershell
python scripts/rebuild_product_vector_index.py --provider local --batch-size 10
```

Result:

- Text chunks ready: 884
- Provider: `local`
- Model: `BAAI/bge-m3`
- Dim: 1024
- Status: failed
- Error: Milvus is not reachable at `127.0.0.1:19530`

No text chunks were inserted into Milvus in this environment.

## Verification

Passed:

```powershell
python -m pytest tests/test_image_retrieval.py -q --basetemp .pytest_tmp\pytest_image
```

Result:

- 2 passed

## Final Status

- Ecommerce text chunks: ready for Milvus, not inserted because Milvus is unavailable.
- PC text chunks: ready for Milvus, not inserted because Milvus is unavailable.
- Ecommerce image index: built successfully as independent local image index.
- Text and image vectors remain separated.
- Source product JSON files were not modified.

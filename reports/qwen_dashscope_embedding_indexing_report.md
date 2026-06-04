# Qwen DashScope Embedding Indexing Report

Date: 2026-06-04

## Provider Check

Code-supported external providers:

- `dashscope`
- `openai_compatible`

Preferred provider selected:

- Embedding provider: `dashscope`
- Embedding model: `text-embedding-v4`
- Expected dense dim: 1024
- Batch size: 10
- Local embedding used: no
- BAAI/bge-m3 used: no

The DashScope provider passes `dimensions=1024` to the compatible embeddings request.

## Configuration

Non-secret embedding configuration is set to:

```env
EMBEDDING_PROVIDER=dashscope
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIM=1024
DENSE_EMBEDDING_DIM=1024
EMBEDDING_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_BATCH_SIZE=10
EMBEDDING_TIMEOUT_SECONDS=30
EMBEDDING_MAX_RETRIES=3
```

`DASHSCOPE_API_KEY` is present in `.env`. The key was not printed.

## Smoke Check

Command:

```powershell
python scripts/check_embedding_provider.py
```

Result:

- Provider: `dashscope`
- Model: `text-embedding-v4`
- Expected dim: 1024
- Actual dim: not confirmed
- Status: failed

Failure reason:

The sandbox blocked network access to `dashscope.aliyuncs.com:443`.

The same command was retried with elevated network permission twice, but automatic approval review timed out both times.

## Indexing Status

Text chunks were not inserted into Milvus in this run.

- Inserted chunks: 0
- Required final inserted chunks: 884
- Reason: Qwen/DashScope embedding smoke check could not confirm `actual_dim=1024`.

Per instruction, the process stopped and did not fall back to local embeddings.

## Next Command After Network Is Available

First confirm the provider:

```powershell
python scripts/check_embedding_provider.py
```

Proceed only if the output contains:

- `provider`: `dashscope`
- `model`: `text-embedding-v4`
- `expected_dim`: `1024`
- `actual_dim`: `1024`
- `status`: `ok`

Then run dry-run without local:

```powershell
python scripts/rebuild_product_vector_index.py --dry-run --provider dashscope
```

Then, with Milvus reachable:

```powershell
python scripts/rebuild_product_vector_index.py --provider dashscope --batch-size 10
```

Use `--recreate` only when explicitly rebuilding the development collection.

import os


# Unit tests and demo runs should never block on Milvus, torch, or a local
# embedding model. Integration tests opt in explicitly with RUN_MILVUS_TESTS=1.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("RECOMMENDATION_ENABLE_MILVUS", "false")
os.environ.setdefault("RECOMMENDATION_LLM_GUIDANCE", "false")
os.environ.setdefault("RECOMMENDATION_STREAM_USE_LLM", "false")

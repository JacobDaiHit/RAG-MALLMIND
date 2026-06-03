# test_init_only.py
import os

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.milvus]

if os.getenv("RUN_MILVUS_TESTS") != "1":
    pytest.skip(
        "Milvus manager integration test skipped. Set RUN_MILVUS_TESTS=1 after starting Milvus.",
        allow_module_level=True,
    )

from rag.storage.milvus_client import MilvusManager


def test_milvus_manager_connects_and_reads_collection_state():
    """MilvusManager should connect to the configured Milvus service."""

    manager = MilvusManager()
    assert isinstance(manager.has_collection(), bool)

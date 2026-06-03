import sys
import os
from pathlib import Path

# 必须将这行代码放在所有 import 语句之前！
# 将当前文件所在目录（RAG）加入模块搜索路径
sys.path.append(str(Path(__file__).resolve().parents[1]))

# 现在可以正常导入同级目录下的模块了
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.milvus]

if os.getenv("RUN_MILVUS_TESTS") != "1":
    pytest.skip(
        "Milvus integration test skipped. Set RUN_MILVUS_TESTS=1 after starting Milvus and installing embedding dependencies.",
        allow_module_level=True,
    )

from rag.ingestion.embedding import EmbeddingService
from rag.storage.milvus_client import MilvusManager
from rag.storage.milvus_writer import MilvusWriter



# ... 后续代码保持不变

@pytest.fixture(scope="module")
def embedding_service():
    """测试用例：封装 embedding service 相关逻辑，供上层流程复用。"""
    return EmbeddingService()

@pytest.fixture(scope="module")
def milvus_manager():
    # 确保 Milvus 服务已启动！
    """测试用例：封装 milvus manager 相关逻辑，供上层流程复用。"""
    return MilvusManager()

@pytest.fixture(scope="module")
def milvus_writer(embedding_service, milvus_manager):
    """测试用例：封装 milvus writer 相关逻辑，供上层流程复用。"""
    return MilvusWriter(
        embedding_service=embedding_service,
        milvus_manager=milvus_manager
    )

def test_write_and_hybrid_retrieve(milvus_writer, embedding_service, milvus_manager):
    """测试用例：验证 write and hybrid retrieve 行为符合预期。"""
    print("🚀 测试开始...")

    # ---------- 1. 准备数据 ----------
    print("📦 准备测试数据...")
    test_chunks = [
        {
            "text": "Milvus 是一个开源向量数据库，支持密集和稀疏向量混合检索。",
            "filename": "test.pdf",
            "file_type": "pdf",
            "page_number": 1,
            "chunk_idx": 0,
            "chunk_id": "test-chunk-0",
            "parent_chunk_id": "test-parent-0",
            "root_chunk_id": "test-root-0",
            "chunk_level": 3,
        },
        {
            "text": "BM25 是一种经典的信息检索算法，常用于关键词搜索。",
            "filename": "test.pdf",
            "file_type": "pdf",
            "page_number": 1,
            "chunk_idx": 1,
            "chunk_id": "test-chunk-1",
            "parent_chunk_id": "test-parent-0",
            "root_chunk_id": "test-root-0",
            "chunk_level": 3,
        },
    ]

    # ---------- 2. 写入 ----------
    print("✍️ 开始写入 Milvus...")
    try:
        milvus_writer.write_documents(test_chunks, batch_size=2)
        print("✅ 写入完成！")
    except Exception as e:
        print(f"❌ 写入阶段发生异常: {e}")
        pytest.fail(f"写入失败: {e}") # 直接让测试失败并打印错误

    # ---------- 3. 查询向量化 ----------
    print("🧠 生成查询向量...")
    query_text = "Milvus 支持混合检索吗？"
    try:
        dense_vec, sparse_vec = embedding_service.get_all_embeddings([query_text])
    except Exception as e:
        print(f"❌ 向量化阶段发生异常: {e}")
        pytest.fail(f"向量化失败: {e}")

    # ---------- 4. 检索 ----------
    print("🔎 开始混合检索...")
    try:
        results = milvus_manager.hybrid_retrieve(
            dense_embedding=dense_vec[0],
            sparse_embedding=sparse_vec[0],
            top_k=2,
            filter_expr='filename == "test.pdf"'
        )
    except Exception as e:
        print(f"❌ 检索阶段发生异常: {e}")
        pytest.fail(f"检索失败: {e}")

        # ---------- 5. 检查结果 ----------
    print(f"🔍 查询结果数量: {len(results)}")
    
    # ✅ 关键修复：如果无结果，必须主动让测试失败！
    if not results:
        pytest.fail("❌ 测试失败：未检索到任何结果，请检查写入/检索配置或数据是否真实存在")
    
    # 如果有结果，再检查内容
    print("✅ 检索到结果，正在检查文本内容...")
    for i, r in enumerate(results[:2]):
        print(f"  结果 {i+1} 文本片段: {r.get('text', '')[:50]}...") 
    
    assert any("Milvus" in r["text"] for r in results), "未命中期望文本"
    print("🎉 测试通过：成功写入并检索到数据")
    

    
    
    
    
    
    
    
    
    

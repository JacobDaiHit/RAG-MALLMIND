def test_api_request_models_are_importable():
    from rag.api.request_models import FeedbackRequest, GoalRequest, ChatStreamRequest

    assert GoalRequest
    assert ChatStreamRequest
    assert FeedbackRequest


def test_app_context_does_not_define_request_models():
    import rag.api.app_context as app_context

    assert "GoalRequest" not in app_context.__dict__
    assert "ChatStreamRequest" not in app_context.__dict__
    assert "FeedbackRequest" not in app_context.__dict__


def test_retrieval_postprocess_import_is_lightweight():
    import rag.utils.retrieval_postprocess as postprocess

    assert hasattr(postprocess, "_rerank_documents")
    assert hasattr(postprocess, "_auto_merge_documents")


def test_canonical_retrieval_service_importable():
    from rag.recommendation import retrieval

    assert hasattr(retrieval, "retrieve_requirement_evidence")

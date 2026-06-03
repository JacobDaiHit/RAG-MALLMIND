import logging
from typing import Any, Dict

from fastapi import HTTPException

from rag.utils.runtime_errors import public_error


logger = logging.getLogger(__name__)


def parse_model_payload(model_cls: Any, payload: Dict[str, Any]) -> Any:
    """Restore a Pydantic v1/v2 model from a frontend payload."""

    try:
        if hasattr(model_cls, "model_validate"):
            return model_cls.model_validate(payload)
        return model_cls.parse_obj(payload)
    except Exception as exc:
        logger.warning("Invalid model payload for %s: %s", model_cls.__name__, exc)
        raise HTTPException(status_code=400, detail=f"invalid {model_cls.__name__}: {public_error(exc)}") from exc

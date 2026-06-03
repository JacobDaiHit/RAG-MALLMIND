"""Legacy LangChain tool wrappers.

The main shopping agent uses rag.recommendation.tool_router and
rag.recommendation.tool_handlers. This module is kept only for older scripts
or experiments that still import LangChain tools from rag.utils.tools.
"""

from rag.legacy.tools import *  # noqa: F401,F403

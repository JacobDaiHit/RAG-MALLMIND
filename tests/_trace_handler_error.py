"""Trace exactly where handle_recommend crashes in the server context."""
import os, sys, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding='utf-8')
os.environ['APP_ENV'] = 'development'

from dotenv import load_dotenv
load_dotenv()

from rag.recommendation.session_state import get_session
from rag.recommendation.tool_router import route_shopping_tool_call
from rag.api.app_context import prepare_recommendation_context
from rag.recommendation.tool_handlers import handle_recommend

sid = 'trace_handler'
msg = '推荐一款面霜'
session = get_session(sid)

# Route
tool_call = route_shopping_tool_call(msg, session, use_llm=True)
print(f'Route: {tool_call.get("name")}')

# Context
contextual_goal, attachments, attachment_report = prepare_recommendation_context(
    msg, [], session, use_vision_llm=False)
print(f'Goal: {contextual_goal[:60]}')

# Call handle_recommend and collect events
print('Calling handle_recommend...')
events = []
try:
    for sse_str in handle_recommend(
        session=session,
        message=msg,
        raw_attachments=[],
        contextual_goal=contextual_goal,
        attachments=attachments,
        attachment_report=attachment_report,
        llm_stream_enabled=True,
        tool_call=tool_call,
        recommendation_fn=None,
        image_retrieval_fn=None,
        use_llm_guidance=True,
        use_milvus_retrieval=False,
        use_rag_query_expansion=False,
    ):
        events.append(sse_str[:100])
    print(f'SUCCESS: {len(events)} events')
    for e in events[:10]:
        print(f'  {e[:100]}')
except Exception as e:
    print(f'FAILED: {type(e).__name__}: {e}')
    traceback.print_exc()

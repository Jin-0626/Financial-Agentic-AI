import logging
from langchain_core.runnables import RunnableConfig
from contracts.context import MemoryContext
from app.research.output import format_result, error_result
from langgraph.types import Command
from app.research import messages
from app.research import resources

logger = logging.getLogger(__name__)


async def _chat(
    message,
    thread_id,
    user_id,
    org_id,
    response_schema="financial",
    resume=None,
    terminal_context=None,
):
    trace = resources.traces.begin(org_id, user_id, thread_id, message)
    try:
        await resources._ensure_user_habits(resources.resources.store, user_id, org_id)
        state = await resources.resources.agent.ainvoke(
            Command(resume=resume)
            if resume is not None
            else {"messages": [{"role": "user", "content": message}], "todos": []},
            context=MemoryContext(
                user_id=user_id,
                org_id=org_id,
                response_schema=response_schema,
                terminal_context=terminal_context,
            ),
            config=RunnableConfig(
                configurable={"thread_id": thread_id},
                metadata={"org_id": org_id, "user_id": user_id},
                callbacks=[trace],
            ),
        )
        if state.get("__interrupt__"):
            trace.finish("interrupted")
            result = error_result("Research paused for a decision", thread_id)
            result["interruptions"] = messages._interruptions(state)
            return result
        # Format raw state so unfinished tool calls remain a completion boundary.
        result = format_result(state, thread_id)
        trace.finish("success" if result["success"] else "error", result["error"])
        return result
    except Exception as exc:
        logger.exception("Chat failed")
        trace.finish("error", exc)
        return error_result(exc, thread_id)
    finally:
        trace.finish("interrupted")

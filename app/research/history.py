import logging
from typing import List, Optional, Tuple
from app.research.schemas import MessageItem, ChatHistoryResponse, ThreadInfo
from app.research import messages as message_formatting
from app.research import resources

logger = logging.getLogger(__name__)

async def _get_history(thread_id: str) -> ChatHistoryResponse:
    try:
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = await resources.resources.agent.aget_state(config)
        messages: List[MessageItem] = []
        if snapshot and snapshot.values:
            messages = message_formatting._get_messages_from_state(snapshot.values)
        return ChatHistoryResponse(thread_id=thread_id, messages=messages)
    except Exception as e:
        logger.error(f"Get history error: {e}", exc_info=True)
        return ChatHistoryResponse(thread_id=thread_id, messages=[])


def _parse_thread_owner(thread_id: str) -> Tuple[Optional[str], Optional[str]]:
    parts = thread_id.split("__", 2)
    if len(parts) >= 3:
        return parts[0], parts[1]
    return None, None


async def _list_threads(
    org_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> List[ThreadInfo]:
    try:
        threads: List[ThreadInfo] = []
        seen_threads: set[str] = set()
        tuples = [
            item
            async for item in resources.resources.checkpointer.alist(
                None,
                filter={"org_id": org_id, "user_id": user_id},
                limit=1000,
            )
        ]
        for checkpoint_tuple in tuples:
            config = getattr(checkpoint_tuple, "config", {}) or {}
            configurable = config.get("configurable", {}) or {}
            if configurable.get("checkpoint_ns", ""):
                continue
            thread_id = configurable.get("thread_id") or "unknown"
            if not thread_id or thread_id == "unknown":
                continue
            if thread_id in seen_threads:
                continue

            thread_org, thread_user = _parse_thread_owner(thread_id)
            if org_id is not None:
                if thread_org is None or thread_org != org_id:
                    continue
            if user_id is not None:
                if thread_user is None or thread_user != user_id:
                    continue

            info = ThreadInfo(thread_id=thread_id)
            try:
                # Modern message channels may contain deltas, not a materialized list.
                # Reconstruct through the graph instead of treating checkpoint blobs as UI state.
                snapshot = await resources.resources.agent.aget_state(config)
                values = snapshot.values if snapshot else {}
                if values:
                    msgs = message_formatting._get_messages_from_state(values)
                    if msgs:
                        last_user_msg = next(
                            (m for m in reversed(msgs) if m.role == "user"), None
                        )
                        info.last_message = (
                            last_user_msg.content
                            if last_user_msg
                            else (msgs[-1].content[:50] if msgs else None)
                        )
            except Exception:
                pass
            seen_threads.add(thread_id)
            threads.append(info)
        return threads
    except Exception as e:
        logger.error(f"List threads error: {e}", exc_info=True)
        return []


async def _delete_thread(thread_id: str):
    await resources.resources.checkpointer.adelete_thread(thread_id)


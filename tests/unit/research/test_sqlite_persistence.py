"""Real local persistence tests; no model, provider, or database service required."""

import asyncio
import tempfile
import unittest
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Annotated, TypedDict
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.infrastructure.config import research_path
from app.infrastructure.persistence import (
    AsyncSqliteSaver,
    AsyncSqliteStore,
    configure_sqlite,
    prepare_research_path,
)
from app.research.history import _list_threads
from app.research import resources


class State(TypedDict):
    messages: Annotated[list, add_messages]


def graph(saver):
    builder = StateGraph(State)
    builder.add_node("reply", lambda state: {"messages": [AIMessage(content="Saved response")]})
    builder.add_edge(START, "reply")
    builder.add_edge("reply", END)
    return builder.compile(checkpointer=saver)


class SQLitePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def open_resources(self, directory, stack):
        path = await prepare_research_path(directory / "nested" / "research.sqlite3")
        store = await stack.enter_async_context(AsyncSqliteStore.from_conn_string(path))
        saver = await stack.enter_async_context(AsyncSqliteSaver.from_conn_string(path))
        for resource in (store, saver):
            await configure_sqlite(resource)
            await resource.setup()
        return store, saver

    async def test_checkpoint_memory_reopening_identity_and_deletion(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            configs = [
                {
                    "configurable": {
                        "thread_id": f"{org}__alice__thread",
                        "org_id": org,
                        "user_id": "alice",
                    }
                }
                for org in ("org-a", "org-b")
            ]
            async with AsyncExitStack() as stack:
                store, saver = await self.open_resources(directory, stack)
                agent = graph(saver)
                for org, config in zip(("org-a", "org-b"), configs, strict=True):
                    await store.aput(("memories", org, "alice"), "habits.md", {"content": org})
                    await agent.ainvoke({"messages": [HumanMessage(content=org)]}, config)
            async with AsyncExitStack() as stack:
                store, saver = await self.open_resources(directory, stack)
                agent = graph(saver)
                snapshot = await agent.aget_state(configs[0])
                self.assertEqual(
                    [m.content for m in snapshot.values["messages"]], ["org-a", "Saved response"]
                )
                await agent.ainvoke({"messages": [HumanMessage(content="Continue")]}, configs[0])
                self.assertEqual(len((await agent.aget_state(configs[0])).values["messages"]), 4)
                self.assertEqual(
                    (await store.aget(("memories", "org-b", "alice"), "habits.md")).value[
                        "content"
                    ],
                    "org-b",
                )
                self.assertIsNone(await store.aget(("memories", "org-a", "bob"), "habits.md"))
                rows = await store.asearch(("memories", "org-a"))
                self.assertEqual(len(rows), 1)
                with (
                    patch.object(resources.resources, "checkpointer", saver),
                    patch.object(resources.resources, "agent", agent),
                ):
                    threads = await _list_threads("org-a", "alice")
                self.assertEqual([t.thread_id for t in threads], ["org-a__alice__thread"])
                await saver.adelete_thread("org-a__alice__thread")
                self.assertIsNone(await saver.aget_tuple(configs[0]))
                self.assertIsNotNone(await saver.aget_tuple(configs[1]))
                self.assertIsNotNone(await store.aget(("memories", "org-a", "alice"), "habits.md"))

    async def test_concurrent_memory_updates_and_namespace_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            async with AsyncExitStack() as stack:
                store, _ = await self.open_resources(Path(temporary), stack)
                await asyncio.gather(
                    *[
                        store.aput(("memories", "org", "alice"), str(i), {"index": i})
                        for i in range(25)
                    ]
                )
                await store.aput(("memories", "organization", "alice"), "outside", {"index": 100})
                self.assertEqual(len(await store.asearch(("memories", "org"), limit=100)), 25)
                rows = await store.asearch(("memories", "org"), filter={"index": 2})
                self.assertEqual([row.key for row in rows], ["2"])
                await store.adelete(("memories", "org", "alice"), "2")
                self.assertIsNone(await store.aget(("memories", "org", "alice"), "2"))

    async def test_deep_agent_memory_and_delta_messages_survive_reopening(self):
        from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
        from orchestrator import agent as factory
        from contracts.context import MemoryContext

        class Model(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self

        config = {
            "configurable": {"thread_id": "org__alice__deep", "org_id": "org", "user_id": "alice"}
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for turn in range(2):
                async with AsyncExitStack() as stack:
                    store, saver = await self.open_resources(directory, stack)
                    await factory._ensure_agents_memory(store)
                    await factory._ensure_user_habits(store, "alice", "org")
                    model = Model(responses=[AIMessage(content="Persisted reply")])
                    with patch.object(factory, "_configured_model", return_value=model):
                        agent = factory.create_agent(saver, store)
                    await agent.ainvoke(
                        {"messages": [HumanMessage(content=f"Hello {turn}")]},
                        config,
                        context=MemoryContext(org_id="org", user_id="alice", response_schema=None),
                    )
                    snapshot = await agent.aget_state(config)
                    self.assertEqual(len(snapshot.values["messages"]), 2 * (turn + 1))
                    self.assertIsNotNone(
                        await store.aget(("memories", "org", "alice"), "/habits.md")
                    )

    async def test_path_override_is_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(
                "os.environ", {"RESEARCH_SQLITE_PATH": str(Path(temporary) / "research.sqlite3")}
            ):
                self.assertEqual(research_path(), Path(temporary).resolve() / "research.sqlite3")

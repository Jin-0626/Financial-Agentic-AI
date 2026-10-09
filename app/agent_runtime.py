"""Organization-scoped native tools with one Deep Agents factory."""

import asyncio


class AgentRuntime:
    def __init__(self, checkpointer, store, engine):
        from orchestrator.agent import create_agent

        self.checkpointer = checkpointer
        self.store = store
        self.engine = engine
        self.base = create_agent(checkpointer, store)
        self.agents = {}
        self.lock = asyncio.Lock()

    async def resolve(self, context):
        from orchestrator.agent import create_agent

        org = context.org_id
        async with self.lock:
            if org not in self.agents:
                tools = await self.engine.agent_tools(org)
                self.agents[org] = create_agent(
                    self.checkpointer, self.store, native_tools=tools
                )
            return self.agents[org]

    async def ainvoke(self, *args, context, **kwargs):
        graph = await self.resolve(context)
        return await graph.ainvoke(*args, context=context, **kwargs)

    async def astream(self, *args, context, **kwargs):
        graph = await self.resolve(context)
        async for event in graph.astream(*args, context=context, **kwargs):
            yield event

    async def aget_state(self, config):
        return await self.base.aget_state(config)

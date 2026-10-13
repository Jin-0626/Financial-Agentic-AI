"""Feature-local research resources configured by application lifespan."""
from dataclasses import dataclass
from typing import Any
from app.infrastructure.traces import traces

@dataclass
class ResearchResources:
    store: Any = None
    checkpointer: Any = None
    agent: Any = None

    def configure(self, store, checkpointer, agent):
        self.store, self.checkpointer, self.agent = store, checkpointer, agent

resources = ResearchResources()

async def _ensure_user_habits(*args, **kwargs):
    from orchestrator.agent import _ensure_user_habits
    return await _ensure_user_habits(*args, **kwargs)

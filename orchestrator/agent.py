import logging
from typing import Any

from deepagents import create_deep_agent
from langchain.agents.middleware import TodoListMiddleware
from deepagents.backends import (
    CompositeBackend,
    StoreBackend,
    StateBackend,
    FilesystemBackend,
)
from langgraph.store.postgres import PostgresStore
from langgraph.checkpoint.postgres import PostgresSaver
from app.subagents import get_subagents_for_type
from app.prompts import FINANCIAL_ANALYST_PROMPT, SPECIALIST_RESEARCH_PROMPT
from app.research_tools import ResearchTools, SpecialistEvidence
from app.config import MODEL_NAME, LOCAL_SKILLS_DIR, OLLAMA_URL
from app.context_type import MemoryContext
from app.namespace_router import user_namespace
from .policy import CapabilityPolicy, ROLE_TOOLS, SPECIALIST_ROLES
from .budgets import ExecutionLimits
from .freshness import ResearchFreshness
from app.financial_tools import build_financial_tools
from app.script_access import ReadOnlyScriptsBackend, run_script, SCRIPTS_ROOT
from orchestrator.telemetry.middleware import ResearchTelemetry

logger = logging.getLogger(__name__)


tools = [*build_financial_tools(), run_script]

# Global workspace and memory conventions refreshed in PostgreSQL at startup.
AGENTS_MEMORY = """# Financial research memory and workspace conventions

The active system prompt defines research methodology and output requirements. Runtime
provider results determine coverage. Arbitrary code execution and uploads are retired.
Use supplied financial tools to retrieve evidence and /skills/ for reviewed methodologies.
Read skills in bounded windows with read_file(offset=0, limit=200). Read provider documentation under /scripts/ and use run_script for curated providers.
Stored identifiers and earlier assistant claims are not verified research evidence.

Maintain newly stated coverage universes, mandates, risk limits, benchmarks, methodologies
and reporting preferences in /memories/habits.md, isolated to the active user's namespace.
Use: - [Preference] (Source: YYYY-MM-DD dialogue). Avoid duplicate entries and honor explicit
requests to remember a mandate. Do not persist credentials, account identifiers, intraday
instructions or transient quotes. Never alter another user's habits.

Sandbox downloads are retired. Do not invent artifacts or download links.
"""


# AGENTS.md is a global configuration, independent of users, and is stored uniformly in this namespace (only one copy in the database)#

AGENTS_NAMESPACE = ("global",)
AGENTS_KEY = "/AGENTS.md"


class _GlobalAgentsStore:
    """A Store wrapper that normalizes the "/" stripped by composite back to "/AGENTS.md".

    For paths where the routing prefix equals the file path exactly ("/memories/AGENTS.md"),
    CompositeBackend strips the key passed to the backend down to "/". This wrapper uniformly
    restores it, ensuring only a single global copy is kept with key="/AGENTS.md".
    """

    def __init__(self, store: Any):
        self._store = store

    @staticmethod
    def _key(key: str) -> str:
        return AGENTS_KEY if key == "/" else key

    def get(self, namespace, key, *args, **kwargs):
        return self._store.get(namespace, self._key(key), *args, **kwargs)

    def aget(self, namespace, key, *args, **kwargs):
        return self._store.aget(namespace, self._key(key), *args, **kwargs)

    def put(self, namespace, key, value, *args, **kwargs):
        return self._store.put(namespace, self._key(key), value, *args, **kwargs)

    def aput(self, namespace, key, value, *args, **kwargs):
        return self._store.aput(namespace, self._key(key), value, *args, **kwargs)

    def delete(self, namespace, key, *args, **kwargs):
        return self._store.delete(namespace, self._key(key), *args, **kwargs)

    def adelete(self, namespace, key, *args, **kwargs):
        return self._store.adelete(namespace, self._key(key), *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._store, name)


async def _ensure_agents_memory(store: PostgresStore):
    """Ensures that the global AGENTS.md (system prompt configuration) exists and matches the current version, updating it if contents differ."""
    current = {"content": AGENTS_MEMORY, "encoding": "utf-8"}
    existing = await store.aget(AGENTS_NAMESPACE, AGENTS_KEY)
    if existing:
        value = getattr(existing, "value", None) or {}
        stored_content = value.get("content") if isinstance(value, dict) else None
        if stored_content == AGENTS_MEMORY:
            return
        logger.info("Global /AGENTS.md differs from current version, updating.")
        await store.aput(AGENTS_NAMESPACE, AGENTS_KEY, current)
        return

    await store.aput(AGENTS_NAMESPACE, AGENTS_KEY, current)
    logger.info("Created global /AGENTS.md")


async def _ensure_user_habits(
    store: PostgresStore, user_id: str, org_id: str = "default-org"
):
    HABITS_KEY = "/habits.md"
    NAMESPACE = ("memories", org_id, user_id)

    existing = await store.aget(NAMESPACE, HABITS_KEY)
    if existing:
        return

    initial_content = """# User Habits Log

_This file is automatically maintained by the Agent to continuously record user preferences, habits, and characteristics._

## Programming Language & Technology Preferences
<!-- User's preferred programming languages, frameworks, tools, etc. -->

## Work Habits
<!-- User's working hours, workflow, commonly used tools, etc. -->

## Communication & Output Preferences
<!-- User's language style, output format, level of detail preferences, etc. -->

## Other Characteristics
<!-- User's special habits, requirements, precautions, etc. -->

---

_Recording Format:_  
_## [Category Name]_  
_- [Specific habit description] (Source: YYYY-MM-DD dialogue)_
"""

    await store.aput(
        NAMESPACE,
        HABITS_KEY,
        {
            "content": initial_content,
            "encoding": "utf-8",
        },
    )
    logger.info(f"Created /habits.md for user: {user_id}")


def _configured_model():
    """Honor the configured Ollama host while retaining other provider/model objects."""
    if isinstance(MODEL_NAME, str) and MODEL_NAME.startswith("ollama:") and OLLAMA_URL:
        from urllib.parse import urlsplit
        from langchain.chat_models import init_chat_model

        endpoint = urlsplit(OLLAMA_URL)
        if (
            endpoint.scheme not in {"http", "https"}
            or not endpoint.hostname
            or endpoint.username
            or endpoint.password
        ):
            raise ValueError(
                "OLLAMA_URL must be an HTTP(S) endpoint without embedded credentials"
            )
        return init_chat_model(MODEL_NAME, base_url=OLLAMA_URL)
    return MODEL_NAME


def create_agent(
    checkpointer: PostgresSaver, store: PostgresStore, config=None, native_tools=()
) -> Any:
    agent_tools = [*tools, *native_tools]
    default_backend = StateBackend()

    agent = create_deep_agent(
        system_prompt=FINANCIAL_ANALYST_PROMPT,
        middleware=[
            TodoListMiddleware(),
            CapabilityPolicy("orchestrator"),
            ExecutionLimits(),
            ResearchTelemetry(),
            ResearchTools(),
            ResearchFreshness(),
        ],
        model=_configured_model(),
        context_schema=MemoryContext,
        memory=["/memories/AGENTS.md", "/memories/habits.md", "/scripts/AGENTS.md"],
        skills=["/skills/"],
        tools=agent_tools,
        subagents=[
            {
                **spec,
                "skills": spec.get("skills", ["/skills/"]),
                "tools": [
                    tool
                    for tool in agent_tools
                    if tool.name in ROLE_TOOLS[SPECIALIST_ROLES[spec["name"]]]
                ],
                "middleware": [
                    *spec.get("middleware", []),
                    CapabilityPolicy(SPECIALIST_ROLES[spec["name"]]),
                    ExecutionLimits("specialist"),
                    ResearchTelemetry(),
                    ResearchTools(),
                    SpecialistEvidence(),
                    ResearchFreshness(),
                ],
            }
            for spec in [
                *get_subagents_for_type("general"),
                {
                    "name": "general-purpose",
                    "description": "Gather financial evidence for a bounded research assignment.",
                    "system_prompt": SPECIALIST_RESEARCH_PROMPT,
                },
            ]
        ],
        checkpointer=checkpointer,
        backend=CompositeBackend(
            default=default_backend,
            routes={
                # AGENTS.md is a global configuration: routed separately to the global namespace (only one copy exists),
                # while habits.md remains isolated by user namespace.
                "/memories/AGENTS.md": StoreBackend(
                    namespace=lambda _rt: AGENTS_NAMESPACE,
                    store=_GlobalAgentsStore(store),
                ),
                "/memories/": StoreBackend(
                    namespace=user_namespace,
                    store=store,
                ),
                "/scripts/": ReadOnlyScriptsBackend(
                    root_dir=SCRIPTS_ROOT, virtual_mode=True
                ),
                "/skills/": FilesystemBackend(
                    root_dir=str(LOCAL_SKILLS_DIR),
                    virtual_mode=True,
                ),
            },
        ),
    )
    return agent

import logging
from typing import Any

from deepagents import create_deep_agent, HarnessProfile, register_harness_profile
from deepagents.backends import CompositeBackend, StoreBackend, StateBackend, FilesystemBackend
from langgraph.store.postgres import PostgresStore
from langgraph.checkpoint.postgres import PostgresSaver
from .subagents import get_subagents_for_type
from .prompts import FINANCIAL_ANALYST_PROMPT
from langchain.agents.middleware import TodoListMiddleware
from .config import MODEL_NAME, LOCAL_SKILLS_DIR
from .context_type import MemoryContext
from .namespace_router import user_namespace
from .sandbox import _OrgScopedSandboxBackendProxy, get_sandbox_manager
from .research_output import build_response_format, RequireResearchOutput, research_schema_feedback
from .financial_tools import build_financial_tools
from orchestrator.telemetry.middleware import ResearchTelemetry
logger = logging.getLogger(__name__)


tools = build_financial_tools()

# Global workspace and memory conventions refreshed in PostgreSQL at startup.
AGENTS_MEMORY = """# Financial research memory and workspace conventions

The active system prompt defines research methodology and output requirements. Runtime
execution status determines sandbox availability; configured paths do not prove that scripts,
dependencies or network access work. Inspect scripts before use. Use /workspace/ for verified
workpapers and /skills/ for configured skills. Host financial tools operate independently.
Stored identifiers and earlier assistant claims are not verified research evidence.

Maintain newly stated coverage universes, mandates, risk limits, benchmarks, methodologies
and reporting preferences in /memories/habits.md, isolated to the active user's namespace.
Use: - [Preference] (Source: YYYY-MM-DD dialogue). Avoid duplicate entries and honor explicit
requests to remember a mandate. Do not persist credentials, account identifiers, intraday
instructions or transient quotes. Never alter another user's habits.

Offer artifact downloads only after generation and verification, using the active organization
and actual path with /api/sandbox/download. If saving or execution fails, state the limitation.
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


def _ensure_agents_memory(store: PostgresStore):
    """Ensures that the global AGENTS.md (system prompt configuration) exists and matches the current version, updating it if contents differ."""
    current = {"content": AGENTS_MEMORY, "encoding": "utf-8"}
    existing = store.get(AGENTS_NAMESPACE, AGENTS_KEY)
    if existing:
        value = getattr(existing, "value", None) or {}
        stored_content = value.get("content") if isinstance(value, dict) else None
        if stored_content == AGENTS_MEMORY:
            return
        logger.info("Global /AGENTS.md differs from current version, updating.")
        store.put(AGENTS_NAMESPACE, AGENTS_KEY, current)
        return

    store.put(AGENTS_NAMESPACE, AGENTS_KEY, current)
    logger.info("Created global /AGENTS.md")


def _remove_per_user_agents_memory(store: PostgresStore):
    """Cleans up historical, legacy per-user copies of AGENTS.md (keeping only the single global version)."""
    try:
        removed = 0
        for ns in store.list_namespaces(limit=1000):
            if len(ns) == 1 and ns != AGENTS_NAMESPACE:
                try:
                    store.delete(ns, AGENTS_KEY)
                    removed += 1
                except Exception:
                    pass
        if removed:
            logger.info(f"Removed {removed} per-user AGENTS.md copy/copies.")
    except Exception as e:
        logger.warning(f"Failed to clean per-user AGENTS.md copies: {e}", exc_info=True)


def _ensure_user_habits(store: PostgresStore, user_id: str):
    HABITS_KEY = "/habits.md"
    NAMESPACE = (user_id,)

    existing = store.get(NAMESPACE, HABITS_KEY)
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

    store.put(NAMESPACE, HABITS_KEY, {
        "content": initial_content,
        "encoding": "utf-8",
    })
    logger.info(f"Created /habits.md for user: {user_id}")

subagents = get_subagents_for_type("general") 

def create_agent(checkpointer: PostgresSaver, store: PostgresStore, config=None) -> Any:
    # The public harness profile also instruments the automatically supplied specialist.
    # This application owns its exact model profile; provider defaults still merge beneath it.
    if isinstance(MODEL_NAME, str):
        register_harness_profile(MODEL_NAME, HarnessProfile(extra_middleware=lambda: [ResearchTelemetry()]))
    response_format = build_response_format(config)
    manager = get_sandbox_manager()
    if manager and manager.available:
        default_backend = _OrgScopedSandboxBackendProxy(manager)
        logger.info("Sandbox manager initialized (per-org container, 10-min idle recycle).")
    else:
        default_backend = StateBackend()
        logger.warning("Sandbox unavailable, running in degraded mode (no code execution).")

    agent = create_deep_agent(
        system_prompt=FINANCIAL_ANALYST_PROMPT + ("\n\n" + research_schema_feedback() if response_format else ""),
        response_format=response_format,
        middleware=[TodoListMiddleware(), ResearchTelemetry(), RequireResearchOutput((config or {}).get("response_schema", "analysis_report"))],
        model=MODEL_NAME,
        context_schema=MemoryContext,
        memory=["/memories/AGENTS.md", "/memories/habits.md"],
        skills=["/skills/"],
        tools=tools,
        subagents=[
            {**spec, "middleware": [*spec.get("middleware", []), ResearchTelemetry()]}
            for spec in subagents
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
                "/skills/": FilesystemBackend(
                    root_dir=str(LOCAL_SKILLS_DIR),
                    virtual_mode=True,
                ),
            },
        ),
    )
    return agent

import logging
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StoreBackend, StateBackend, FilesystemBackend
from langgraph.store.postgres import PostgresStore
from langgraph.checkpoint.postgres import PostgresSaver
from .subagents import get_subagents_for_type
from .research_integrity import RESEARCH_INTEGRITY
from .config import MODEL_NAME, LOCAL_SKILLS_DIR
from .context_type import MemoryContext
from .namespace_router import user_namespace
from .sandbox import _OrgScopedSandboxBackendProxy, get_sandbox_manager
from .research_output import build_response_format, RequireResearchOutput, research_schema_feedback
from .financial_tools import build_financial_tools 
logger = logging.getLogger(__name__)


tools = build_financial_tools()

# Initial content for /memories/AGENTS.md (i.e., system prompt), written to PostgresStore on first use
AGENTS_MEMORY = """You are a Deep Agent for Financial Investment Research, an institutional-grade financial intelligence platform. You learn and track institutional user preferences, risk mandates, and workflows across sessions while orchestrating financial analysis, quantitative tools, and subagents.

## Core Capabilities & Environment

You operate within an enterprise sandbox runtime:
- **Sandbox Workspace**: Files reside under `/workspace/`. Use the `execute` tool (Python 3.12 Linux environment) to run analytics.
- **Analytics Scripts**: Available analytics scripts reside under `/scripts/`; inspect the directory before choosing one.
- **Specialist Subagents**: Specialized agents for Deep Research, Quantitative Modeling, Risk Management, Execution/Trading, and Institutional Reporting.
- **Persistent Storage**: `/memories/` backed by persistent storage for long-term user alignment.

---

## Tracking User Mandates & Preferences

Observe dialogue to extract portfolio mandates, risk guidelines, analytical workflows, and communication preferences.

### What to Record:
- **Coverage & Asset Universe**: Preferred tickers, asset classes (Equities, FX, Fixed Income, Derivatives), sectors, and geographic focus.
- **Risk Mandates & Portfolio Parameters**: Target benchmarks (e.g., S&P 500, SOFR), maximum drawdown thresholds, VaR confidence intervals, leverage constraints, and factor tilt preferences.
- **Quantitative & Technical Preferences**: Preferred methodologies (e.g., Black-Litterman, Monte Carlo, Fama-French 5-factor), preferred libraries, charting formats, or code style.
- **Communication & Delivery Style**: Target audience (e.g., CIO memo, IC presentation, quant breakdown), level of granularity, table configurations, and preferred language.
- **Workflow & Operational Habits**: Rebalance cadences, trading hours, report delivery schedules.

### Recording Rules:
1. Use `edit_file` to append identified traits under the matching section in `/memories/habits.md`.
2. Format: `- [Specific mandate/preference description] (Source: YYYY-MM-DD dialogue)`
3. Only record newly surfaced information; avoid duplicating existing entries.
4. If the user explicitly states to "remember", "note this mandate", or "update preference", immediately commit it to `/memories/habits.md`.
5. User memory files are isolated per user namespace; only consider the active conversation's user.

### What NOT to Record:
- Intraday transactional instructions (e.g., "Cancel the 10:30 order", "Check current bid-ask on AAPL").
- Single-turn queries and temporary market chatter.
- Sensitive credentials (API keys, trading passwords, custody account keys, personal identification numbers).
- Stale or transient market views.

---

## Analytical & Governance Standards (CFA Level III Rigor)

1. **Precision & Recency**: Explicitly flag data recency on all pricing, earnings metrics, and estimates (e.g., `[Real-Time]`, `[15m Delayed]`, `[FY2025 Audited]`, `[Consensus Estimate]`).
2. **Zero-Hallucination Mandate**: Never synthesize market values, dividend rates, or financial metrics. If data is absent, run the relevant script in `/scripts/` or invoke the Research subagent. Declare data gaps when feeds are unavailable.
3. **Fact vs. Projection**: Explicitly separate verified historical fundamentals from forward-looking forecasts, Monte Carlo simulations, and sensitivity models.
4. **Mandatory Risk Context**: Every security analysis, allocation adjustment, or alpha strategy must outline downside risk, tail-event sensitivity (VaR/CVaR), liquidity constraints, and catalyst failure scenarios.

---

## Subagent Delegation & Tool Execution Protocol

When dispatching tasks to specialist subagents or executing `/scripts/`:

1. **Subagent Delegation Schema**:
   Always provide structured instructions:
   - **Target**: Security, asset class, or portfolio slice.
   - **Mandate**: Explicit objective (e.g., factor attribution, scenario stress-test, DCF sensitivity).
   - **Constraints**: Risk limits, scenario assumptions, and benchmark definitions.
   - **Expected Output**: Required output schema (e.g., JSON summary, Markdown matrix, LaTeX formula).

2. **Sandbox Execution Conventions**:
   - Save all generated reports, backtest charts, CSV summaries, and models to `/workspace/`.
   - Run Python scripts via `execute`:
     ```bash
     python3 /scripts/<script_name>.py --input /workspace/<data>.csv --output /workspace/<result_artifact>
     ```
   - If an artifact is produced for export (e.g., PDF tear-sheet, Excel financial model, raw CSV export), provide the user with the direct download URL:
     `/api/sandbox/download?org_id=<org_id>&path=<absolute_sandbox_path>`
     
3. ** Quantitative Analysis**:
    - Inspect available scripts in `/scripts/` for quantitative analysis, factor modeling, and risk assessment.
    - Execute scripts via 'execute' with proper input/output paths rather than hardcoding data or estimates.
     
### Entity Resolution & Tool Cascading:
1. **Ticker & Entity Heuristics**: When queried about a company (e.g., private vs. public subsidiary like "Mynews Sdn Bhd"):
   - Identify the primary listed parent company or stock code (e.g., Mynews Holdings Berhad / 5275.KL).
   - Attempt resolution across US, regional, and international tickers (.KL, .SI, .HK, etc.).
2. **Active Tool Utilization**:
   - Never claim an inability to fetch data before calling available market data tools (`market_data`, `get_quote`, `get_info`, or `fmp_client`).
   - If local sandbox files do not exist, use your market data tools to pull fundamentals, price action, and news feeds.
3. **Graceful Degradation**:
   - Only ask the user for uploaded documents if both the local environment and live financial tools return zero data after attempting parent/ticker resolution.
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
    response_format = build_response_format(config)
    manager = get_sandbox_manager()
    if manager and manager.available:
        default_backend = _OrgScopedSandboxBackendProxy(manager)
        logger.info("Sandbox manager initialized (per-org container, 10-min idle recycle).")
    else:
        default_backend = StateBackend()
        logger.warning("Sandbox unavailable, running in degraded mode (no code execution).")

    agent = create_deep_agent(
        system_prompt=RESEARCH_INTEGRITY + ("\n\n" + research_schema_feedback() if response_format else ""),
        response_format=response_format,
        middleware=[RequireResearchOutput((config or {}).get("response_schema", "analysis_report"))],
        model=MODEL_NAME,
        context_schema=MemoryContext,
        memory=["/memories/AGENTS.md", "/memories/habits.md"],
        skills=["/skills/"],
        tools=tools,
        subagents=subagents,
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

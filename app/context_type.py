from dataclasses import dataclass, field
from orchestrator.budgets import RunBudget


@dataclass(frozen=True)
class MemoryContext:
    user_id: str = "local-user"
    org_id: str = "default-org"
    response_schema: str | None = "fincept"
    run_budget: RunBudget = field(default_factory=RunBudget, repr=False, compare=False)

from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryContext:
    user_id: str = "local-user"
    org_id: str = "default-org"
    response_schema: str | None = "analysis_report"

from typing import Optional, List, Literal

from pydantic import BaseModel, Field

from research_schema import (AnalysisReport, FinancialResearchOutput, ResearchSource,
                             ResearchMetric, ResearchFinding, ResearchToolError)


class ChatRequest(BaseModel):
    message: str
    response_schema: Optional[Literal["analysis_report"]] = "analysis_report"
    resume: Optional[dict] = None
    thread_id: str = "default-thread"
    user_id: str = "local-user"
    org_id: str = "default-org"


class MessageItem(BaseModel):
    role: str
    content: str
    timestamp: Optional[str] = None
    reasoning: Optional[str] = None


class ChatHistoryResponse(BaseModel):
    thread_id: str
    messages: List[MessageItem]


class ThreadInfo(BaseModel):
    thread_id: str
    created_at: Optional[str] = None
    last_message: Optional[str] = None


class FileAnalysisResponse(BaseModel):
    file_id: str
    filename: str
    file_type: str
    row_count: int
    columns: List[str]
    preview: str
    analysis_type: str
    code: Optional[str] = None


class ChatWithFileRequest(BaseModel):
    message: str
    response_schema: Optional[Literal["analysis_report"]] = "analysis_report"
    resume: Optional[dict] = None
    thread_id: str = "default-thread"
    user_id: str = "local-user"
    org_id: str = "default-org"
    file_id: Optional[str] = None




class ChatResponse(BaseModel):
    """Successful chat response; research availability is separate from HTTP success."""
    status: Literal["ok", "interrupted"]
    thread_id: str
    reply: str
    reasoning: Optional[str]
    messages: List[MessageItem]
    structured_response: Optional[AnalysisReport] = None
    interruptions: List[dict] = Field(default_factory=list)

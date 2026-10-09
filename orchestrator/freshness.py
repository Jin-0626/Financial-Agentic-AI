"""Runtime date context for coordinators and every specialist; never cached in memory."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage


def now_utc():
    return datetime.now(timezone.utc)


class ResearchFreshness(AgentMiddleware):
    @staticmethod
    def current(request):
        current = now_utc()
        local = current.astimezone(ZoneInfo("Asia/Kuala_Lumpur"))
        text = f"Research clock: {current.isoformat()} UTC; current date in Asia/Kuala_Lumpur: {local.date().isoformat()}. "
        text += (
            "Default research horizon: as of today, including company analysis without an explicit date. "
            "Before write_todos or task delegation, use this clock to choose the research horizon. "
            "Plans must state as of today's date and seek the latest available annual and interim releases; "
            "discover fiscal periods from providers instead of guessing them from model knowledge. "
            "Never impose a historical cutoff from a previous plan, assistant answer or remembered preference. "
            "Only an explicit historical scope in the user's active request or clearly continued historical assignment "
            "permits a historical horizon; old years mentioned as comparisons are not a cutoff for current research. "
            "Replace stale pending plans when the user asks for updated research. "
            "For current research, fetch fresh provider evidence during this request. "
            "Previous conversation answers, memory and model knowledge are not current evidence. "
            "Do not reuse an old snapshot_id for a current valuation. Pass the current date and requested period to specialists. "
            "Use the latest available quarterly statements for current results; request annual statements separately for annual comparisons. "
            "Check actual publication dates and fiscal periods, not retrieval timestamps. Unknown publication dates cannot verify current news. "
            "If the latest available release is old, disclose its as-of date and the coverage gap. "
            "For explicitly historical questions, preserve the requested dates and use financial_news(days=None) when appropriate."
        )
        blocks = (
            list(request.system_message.content_blocks)
            if request.system_message
            else []
        )
        return request.override(
            system_message=SystemMessage(
                content=[*blocks, {"type": "text", "text": text}]
            )
        )

    def wrap_model_call(self, request, handler):
        return handler(self.current(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self.current(request))

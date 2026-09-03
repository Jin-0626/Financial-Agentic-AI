import json
from concurrent.futures import Future, ThreadPoolExecutor
from time import sleep
from typing import Any, cast
from uuid import uuid4

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from research_agent.reporting import clean_visible_report, enforce_financial_statement_table
from research_agent.tools import YFinanceUtils, resolve_bursa_ticker

st.set_page_config(page_title="Bursa DeepAgent Analyst", layout="wide", page_icon=":material/monitoring:")


@st.cache_resource(show_spinner=False)
def get_agent_executor() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_workers=1)


@st.cache_resource(show_spinner=False)
def get_agent_graph() -> Any:
    from agent import graph  # noqa: PLC0415 - lazy import avoids model setup during Streamlit startup.

    return graph


@st.cache_data(ttl=600, show_spinner=False)
def cached_stock_info(symbol: str) -> dict[str, Any]:
    return YFinanceUtils.get_stock_info(symbol)


@st.cache_data(ttl=600, show_spinner=False)
def cached_stock_history(symbol: str, period: str) -> pd.DataFrame:
    return YFinanceUtils.get_stock_history(symbol, period=period)


def render_price_chart(df_history: pd.DataFrame, title: str) -> None:
    required_columns = {"Open", "High", "Low", "Close"}
    if df_history.empty or not required_columns.issubset(df_history.columns):
        st.warning("No price history available for the selected stock.")
        return

    fig = go.Figure()
    fig.add_trace(
        go.Candlestick(
            x=df_history.index,
            open=df_history["Open"],
            high=df_history["High"],
            low=df_history["Low"],
            close=df_history["Close"],
            name="Price",
        )
    )
    fig.update_layout(
        title=title,
        yaxis_title="Price (MYR)",
        template="plotly_white",
        xaxis_rangeslider_visible=False,
        height=430,
        margin={"l": 20, "r": 20, "t": 48, "b": 20},
    )
    st.plotly_chart(fig, width="stretch")


def latest_message_content(result: dict[str, Any]) -> str:
    messages = result.get("messages", []) if isinstance(result, dict) else []
    if not messages:
        return "No report was returned by the agent."
    content = getattr(messages[-1], "content", None)
    return clean_visible_report(content or str(messages[-1]))


def run_agent_report(prompt: str) -> str:
    result = get_agent_graph().invoke({"messages": [{"role": "user", "content": prompt.strip()}]})
    report = latest_message_content(result)
    return enforce_financial_statement_table(report, extract_financial_table(result))


def extract_financial_table(result: dict[str, Any]) -> str:
    messages = result.get("messages", []) if isinstance(result, dict) else []
    for message in reversed(messages):
        if getattr(message, "type", None) != "tool":
            continue
        content = getattr(message, "content", "")
        try:
            payload = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            continue
        table = payload.get("financial_statement_table_markdown")
        if isinstance(table, str):
            return table
    return ""


def initialize_run_state() -> None:
    defaults = {
        "last_report": None,
        "active_future": None,
        "active_run_id": None,
        "cancelled_run_ids": set[str](),
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def start_agent_run(prompt: str) -> None:
    run_id = uuid4().hex
    st.session_state.active_run_id = run_id
    st.session_state.active_future = get_agent_executor().submit(run_agent_report, prompt)


def stop_agent_run() -> None:
    future = st.session_state.active_future
    if future is not None:
        future.cancel()
    st.session_state.cancelled_run_ids.add(st.session_state.active_run_id)
    st.session_state.active_future = None
    st.session_state.active_run_id = None


def collect_agent_run() -> None:
    future: Future[str] | None = st.session_state.active_future
    run_id: str | None = st.session_state.active_run_id
    if future is None or not future.done():
        return

    st.session_state.active_future = None
    st.session_state.active_run_id = None
    if run_id in st.session_state.cancelled_run_ids:
        st.session_state.cancelled_run_ids.discard(run_id)
        return

    try:
        st.session_state.last_report = future.result()
    except Exception as exc:  # noqa: BLE001 - UI boundary: surface any agent/tool/model failure cleanly.
        st.session_state.last_report = (
            "# Research could not be completed\n\n"
            f"Reason: {str(exc) or type(exc).__name__}\n\n"
            "This research is for education only and is not personal financial advice."
        )


st.title("Bursa Malaysia DeepAgent Financial Analyst")
st.caption("Official-first equity research powered by Ollama Cloud and LangGraph DeepAgents.")
initialize_run_state()
collect_agent_run()

with st.sidebar:
    st.header("Stock Selection")
    search_query = st.text_input("Search stock name or code", value="5275")
    search_results = YFinanceUtils.search_stock_by_name(search_query)

    try:
        if search_results:
            options = {item["display"]: item["symbol"] for item in search_results}
            selected_display = st.selectbox("Select result", list(options.keys()))
            selected_ticker = options[selected_display]
        else:
            selected_ticker = resolve_bursa_ticker(search_query)
    except ValueError as exc:
        st.error(str(exc))
        selected_ticker = ""

    st.write(f"Active ticker: `{selected_ticker or 'N/A'}`")
    period = cast(str, st.segmented_control("Chart horizon", ["1mo", "3mo", "6mo", "1y", "5y", "max"], default="1y"))
    run_analysis = st.button(
        "Run research",
        type="primary",
        width="stretch",
        disabled=not selected_ticker or st.session_state.active_future is not None,
    )
    stop_analysis = st.button(
        "Stop run",
        width="stretch",
        disabled=st.session_state.active_future is None,
    )

if stop_analysis:
    stop_agent_run()
    st.warning("Research run stopped. Any late model response will be ignored.")


try:
    stock_info = cached_stock_info(selected_ticker) if selected_ticker else {}
except Exception as exc:  # noqa: BLE001 - UI boundary: data providers can raise transport/library-specific errors.
    st.warning(f"Stock snapshot unavailable: {str(exc) or type(exc).__name__}")
    stock_info = {}
try:
    df_history = cached_stock_history(selected_ticker, period) if selected_ticker else pd.DataFrame()
except Exception as exc:  # noqa: BLE001 - UI boundary: yfinance/curl exceptions vary by version.
    st.warning(f"Price history unavailable: {str(exc) or type(exc).__name__}")
    df_history = pd.DataFrame()

company_name = stock_info.get("company_name") or selected_ticker or "Selected company"
st.subheader(f"{company_name} ({selected_ticker or 'N/A'})")

with st.container(horizontal=True):
    current_price_label = f"MYR {stock_info.get('current_price')}" if stock_info.get("current_price") else "N/A"
    dividend_yield_label = (
        f"{stock_info.get('dividend_yield')}%" if stock_info.get("dividend_yield") is not None else "N/A"
    )
    st.metric("Current price", current_price_label, border=True)
    st.metric("P/E", stock_info.get("pe_ratio") or "N/A", border=True)
    st.metric("Dividend yield", dividend_yield_label, border=True)
    st.metric("Sector", stock_info.get("sector", "N/A"), border=True)

render_price_chart(df_history, f"{company_name} Price Action")

if run_analysis and selected_ticker:
    prompt = f"""
Write an equity research report for {company_name} ({selected_ticker}).
First call build_bursa_research_snapshot once.
Use financial_statement_table_markdown exactly in section 2.
Keep its 4Q financial statement table and latest valuation-ratio table.
If 4Q data is incomplete, inspect missing_quarter_retry and use only explicit searched figures; otherwise N/A.
Output sections: Executive Summary; Financial Statements, Key Ratios, Historical Performance;
Sector Insight, Forecast Explanation, Valuation, Risks; Final Investment View.
Use exactly these four numbered sections as ## headings; no bold-only headings and no extra headings.
No sources, tool names, confidence, or data-quality notes. No invented peer/sector/consensus numbers.
Summary must include rating, current price, target price, and upside/downside.
End with the education-only disclaimer. Target length: 500-700 words.
"""
    start_agent_run(prompt)

if st.session_state.active_future is not None:
    st.status("DeepAgent research in progress... use Stop run in the sidebar to interrupt.", expanded=False)
    sleep(1)
    st.rerun()

if st.session_state.last_report:
    display_report = clean_visible_report(st.session_state.last_report)
    st.session_state.last_report = display_report
    st.markdown("---")
    st.markdown(display_report)
    st.download_button(
        "Download Markdown Report",
        data=display_report,
        file_name=f"{selected_ticker or 'bursa'}_research_report.md",
        mime="text/markdown",
        width="stretch",
    )

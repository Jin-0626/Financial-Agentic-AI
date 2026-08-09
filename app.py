import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from agent_library import CHATBOT_PROMPT
from model import get_heavy_llm
from observability import summarize_telemetry
from settings import settings
from tools import YFinanceUtils, resolve_bursa_ticker
from workflow import create_bursa_agent_graph, langsmith_config

st.set_page_config(page_title="Bursa Agentic AI Analyst", layout="wide", page_icon=":material/monitoring:")


@st.cache_data(ttl=600, show_spinner=False)
def cached_stock_info(symbol: str):
    return YFinanceUtils.get_stock_info(symbol)


@st.cache_data(ttl=600, show_spinner=False)
def cached_stock_history(symbol: str, period: str):
    return YFinanceUtils.get_stock_history(symbol, period=period)


def _fmt_int(value):
    return "Unknown" if value is None else f"{value:,}"


def _fmt_seconds(ms):
    return f"{(ms or 0) / 1000:.2f}s"


def render_execution_panel(final_state: dict):
    node_metrics = list(final_state.get("node_metrics", []))
    summary = final_state.get("workflow_metrics") or summarize_telemetry(node_metrics)

    st.subheader("Execution Summary")
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Runtime", _fmt_seconds(summary.get("runtime_ms")))
    col2.metric("Total Tokens", _fmt_int(summary.get("total_tokens")))
    col3.metric("LLM Calls", _fmt_int(summary.get("llm_calls")))
    col4.metric("Tool Calls", _fmt_int(summary.get("tool_calls")))
    col5.metric("Fallbacks", _fmt_int(summary.get("fallbacks")))
    col6.metric("Errors", _fmt_int(summary.get("errors")))

    e1, e2, e3, e4, e5 = st.columns(5)
    e1.metric("Task Success Rate", f"{float(summary.get('task_success_rate') or 0) * 100:.1f}%")
    e2.metric("Tool Error Rate", f"{float(summary.get('tool_call_error_rate') or 0) * 100:.1f}%")
    e3.metric("p99 Latency", _fmt_seconds(summary.get("p99_latency_ms")))
    e4.metric("Retries", _fmt_int(summary.get("retry_count")))
    e5.metric("Downgrades", _fmt_int(summary.get("downgraded_count")))

    if node_metrics:
        rows = []
        for metric in node_metrics:
            rows.append(
                {
                    "Agent": metric.get("node", "").replace("_", " ").title(),
                    "Runtime": _fmt_seconds(metric.get("latency_ms")),
                    "Prompt Tokens": _fmt_int(metric.get("prompt_tokens")),
                    "Completion Tokens": _fmt_int(metric.get("completion_tokens")),
                    "Total": _fmt_int(metric.get("total_tokens")),
                    "Retries": _fmt_int(metric.get("retry_count")),
                    "Downgraded": "Yes" if metric.get("downgraded") else "No",
                    "Status": str(metric.get("status", "")).title(),
                }
            )
        with st.expander("Agent Performance", expanded=True):
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    slowest = summary.get("slowest_node")
    token_heavy = summary.get("highest_token_node")
    c1, c2 = st.columns(2)
    if slowest:
        c1.metric(
            "Slowest Agent",
            str(slowest.get("node", "N/A")).replace("_", " ").title(),
            f"{float(slowest.get('runtime_share') or 0) * 100:.1f}% runtime share",
        )
    if token_heavy:
        c2.metric(
            "Highest Token Consumer",
            str(token_heavy.get("node", "N/A")).replace("_", " ").title(),
            f"{_fmt_int(token_heavy.get('total_tokens'))} tokens",
        )


st.title("Bursa Malaysia Agentic AI Financial Analyst")
st.caption("Real-time Technical Charting & Multi-Agent Financial Research Engine")

st.sidebar.header("Stock Selection")
search_query = st.sidebar.text_input("Search Stock Name / Code", value="MYNEWS")
search_results = YFinanceUtils.search_stock_by_name(search_query)

try:
    selected_ticker = "5275.KL"
    if search_results:
        options = {item["display"]: item["symbol"] for item in search_results}
        selected_display = st.sidebar.selectbox("Select Search Result:", list(options.keys()))
        selected_ticker = options[selected_display]
    else:
        selected_ticker = resolve_bursa_ticker(search_query)
except ValueError as exc:
    st.sidebar.error(str(exc))
    selected_ticker = ""

st.sidebar.write(f"Active Ticker Symbol: `{selected_ticker or 'N/A'}`")
st.sidebar.markdown("---")
st.sidebar.subheader("Chart Timeline")
timeline_options = ["1mo", "3mo", "6mo", "1y", "5y", "max"]
selected_period = st.sidebar.radio("Select Horizon", timeline_options, index=3, horizontal=True)
st.sidebar.markdown("---")
st.sidebar.subheader("Agent Execution")
run_analysis_btn = st.sidebar.button(
    "Activate Research System", type="primary", width="stretch", disabled=not selected_ticker
)

st.subheader(f"Price Chart & Fundamentals: `{selected_ticker or 'N/A'}`")
stock_info = cached_stock_info(selected_ticker) if selected_ticker else {}
try:
    df_history = cached_stock_history(selected_ticker, period=selected_period) if selected_ticker else pd.DataFrame()
except Exception:
    df_history = pd.DataFrame()

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Current Price", f"MYR {stock_info.get('current_price')}" if stock_info.get("current_price") is not None else "N/A")
col2.metric("P/E Ratio", f"{stock_info.get('pe_ratio')}" if stock_info.get("pe_ratio") else "N/A")
col3.metric("Div Yield", f"{stock_info.get('dividend_yield')}%" if stock_info.get("dividend_yield") is not None else "N/A")
col4.metric("Sector", stock_info.get("sector", "N/A"))
col5.metric("Industry", stock_info.get("industry", "N/A"))

if not df_history.empty:
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
        title=f"{stock_info.get('company_name', selected_ticker)} ({selected_ticker}) - Price Action",
        yaxis_title="Price (MYR)",
        template="plotly_dark",
        xaxis_rangeslider_visible=False,
        height=450,
    )
    st.plotly_chart(fig, width="stretch")
else:
    st.warning("No price history available for the selected stock.")

st.markdown("---")

if "final_report" not in st.session_state:
    st.session_state.final_report = None
if "last_state" not in st.session_state:
    st.session_state.last_state = None
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

if run_analysis_btn and selected_ticker:
    st.session_state.chat_history = []
    app_graph = create_bursa_agent_graph()
    initial_state = {
        "ticker": selected_ticker,
        "debate_rounds": 0,
        "audit_trace": [],
        "node_metrics": [],
        "errors": [],
    }

    status_container = st.status("Multi-Agent System Executing Workflow...", expanded=True)
    final_state = None
    try:
        for output in app_graph.stream(
            initial_state,
            config=langsmith_config(
                selected_ticker,
                period=selected_period,
                company_name=stock_info.get("company_name"),
            ),
        ):
            for node_name, node_state in output.items():
                status_container.write(f"Completed step: **{node_name}**")
                for trace in node_state.get("audit_trace", []):
                    st.toast(trace)
                final_state = node_state
    except Exception as exc:
        final_state = {
            "workflow_status": "FAILED",
            "failed_stage": "workflow",
            "error_message": str(exc) or type(exc).__name__,
            "final_report": (
                "# Analysis could not be completed\n\n"
                f"Failed stage: workflow\n\nReason: {str(exc) or type(exc).__name__}\n\n"
                "No investment recommendation was generated."
            ),
            "node_metrics": [],
        }

    if final_state:
        summary = summarize_telemetry(list(final_state.get("node_metrics", [])))
        if "workflow_metrics" in final_state and final_state["workflow_metrics"]:
            summary.update(final_state["workflow_metrics"])
        final_state["workflow_metrics"] = summary
        st.session_state.last_state = final_state
        st.session_state.final_report = final_state.get("final_report")

    if final_state and final_state.get("workflow_status") == "FAILED":
        status_container.update(label="Analysis failed safely", state="error", expanded=False)
        st.warning("Analysis could not be completed.")
        st.write(f"Failed stage: **{final_state.get('failed_stage', 'Unknown')}**")
        st.write(f"Reason: {final_state.get('error_message', 'Unknown failure')}")
        with st.expander("Technical details"):
            st.json({"errors": final_state.get("errors", []), "audit_trace": final_state.get("audit_trace", [])})
    else:
        status_container.update(label="Analysis Complete!", state="complete", expanded=False)

if st.session_state.final_report:
    st.markdown(st.session_state.final_report)
    if st.session_state.last_state:
        render_execution_panel(st.session_state.last_state)
    st.sidebar.markdown("---")
    st.sidebar.subheader("Export Options")
    st.sidebar.download_button(
        label="Export Research Report (.md)",
        data=st.session_state.final_report,
        file_name=f"{selected_ticker}_Investment_Report.md",
        mime="text/markdown",
        width="stretch",
    )

    st.markdown("---")
    st.subheader("Post-Analysis Research Chatbot")
    st.caption("Ask questions about the compiled investment report above.")
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if user_prompt := st.chat_input("Ask about valuation assumptions, risks, or targets..."):
        st.session_state.chat_history.append({"role": "user", "content": user_prompt})
        with st.chat_message("user"):
            st.markdown(user_prompt)
        with st.chat_message("assistant"):
            try:
                llm = get_heavy_llm()
                formatted_prompt = CHATBOT_PROMPT.format(
                    report_context=st.session_state.final_report,
                    user_query=user_prompt,
                )
                response = llm.invoke(formatted_prompt)
                content = getattr(response, "content", "").strip()
                if not content:
                    raise ValueError("Chatbot returned an empty response.")
                st.markdown(content)
                st.session_state.chat_history.append({"role": "assistant", "content": content})
            except Exception as exc:
                st.error(f"Chatbot request failed: {str(exc) or type(exc).__name__}")

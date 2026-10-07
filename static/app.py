import sys
from pathlib import Path

# Streamlit adds the script directory, so expose the shared project modules too.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from research_schema import AnalysisReport, render_report
import json
import os
import time
import requests
import streamlit as st

BACKEND_BASE_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

st.set_page_config(
    page_title="AI Chat Assistant",
    page_icon="💬",
    layout="wide",
)

# Custom Styling to match original theme
st.markdown(
    """
    <style>
    .stApp {
        background-color: #f5f7fb;
    }
    .user-badge {
        font-family: monospace;
        font-size: 12px;
        background-color: #f3f4f6;
        padding: 4px 8px;
        border-radius: 6px;
        color: #4b5563;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ----------------- Session State Initialization -----------------
if "user_id" not in st.session_state:
    st.session_state.user_id = "local-user"
if "org_id" not in st.session_state:
    st.session_state.org_id = "default-org"
if "current_thread_id" not in st.session_state:
    st.session_state.current_thread_id = None
if "messages" not in st.session_state:
    st.session_state.messages = []
if "uploaded_file_info" not in st.session_state:
    st.session_state.uploaded_file_info = None


# ----------------- API Helpers -----------------
def generate_thread_id():
    timestamp = int(time.time() * 1000)
    rand_str = os.urandom(3).hex()
    return f"{st.session_state.org_id}__{st.session_state.user_id}__thread-{timestamp}-{rand_str}"

def fetch_threads():
    params = {"org_id": st.session_state.org_id}
    if st.session_state.get("only_mine", False):
        params["only_mine"] = "true"
        params["user_id"] = st.session_state.user_id
    try:
        res = requests.get(f"{BACKEND_BASE_URL}/api/threads", params=params, timeout=10)
        return res.json() if res.status_code == 200 else []
    except Exception:
        return []

def load_thread_history(thread_id):
    try:
        res = requests.get(f"{BACKEND_BASE_URL}/api/history/{thread_id}", timeout=10)
        if res.status_code == 200:
            st.session_state.messages = res.json().get("messages", [])
            st.session_state.current_thread_id = thread_id
    except Exception as e:
        st.error(f"Failed to load chat history: {e}")

def delete_thread(thread_id):
    try:
        res = requests.delete(f"{BACKEND_BASE_URL}/api/threads/{thread_id}", timeout=10)
        if res.status_code == 200:
            if st.session_state.current_thread_id == thread_id:
                start_new_chat()
            st.rerun()
    except Exception as e:
        st.error(f"Failed to delete thread: {e}")

def start_new_chat():
    st.session_state.current_thread_id = generate_thread_id()
    st.session_state.messages = []
    st.session_state.uploaded_file_info = None

def upload_file_to_backend(uploaded_file):
    try:
        files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
        data = {"org_id": st.session_state.org_id}
        res = requests.post(f"{BACKEND_BASE_URL}/api/files/upload", files=files, data=data, timeout=60)
        if res.status_code == 200:
            return res.json()
        st.error(f"Upload failed: {res.text}")
    except Exception as e:
        st.error(f"Error uploading file: {e}")
    return None

def remove_uploaded_file():
    if st.session_state.uploaded_file_info:
        file_id = st.session_state.uploaded_file_info.get("file_id")
        try:
            requests.delete(f"{BACKEND_BASE_URL}/api/files/{file_id}", timeout=10)
        except Exception:
            pass
        st.session_state.uploaded_file_info = None


# ----------------- Sidebar -----------------
with st.sidebar:
    st.title("💬 AI Chat Assistant")

    if st.button("➕ New Chat", use_container_width=True, type="primary"):
        start_new_chat()
        st.rerun()

    st.checkbox("Only Show My Chats", key="only_mine")

    st.markdown("---")
    st.subheader("Chat History")
    threads = fetch_threads()

    for thread in threads:
        t_id = thread.get("thread_id")
        preview = thread.get("last_message") or "New Chat"
        title = (preview[:22] + "...") if len(preview) > 22 else preview

        col1, col2 = st.columns([0.8, 0.2])
        is_active = t_id == st.session_state.current_thread_id

        with col1:
            btn_label = f"👉 {title}" if is_active else title
            if st.button(btn_label, key=f"sel_{t_id}", use_container_width=True):
                load_thread_history(t_id)
                st.rerun()
        with col2:
            if st.button("✕", key=f"del_{t_id}", help="Delete chat"):
                delete_thread(t_id)

# ----------------- Main Chat Header -----------------
if not st.session_state.current_thread_id:
    if threads:
        load_thread_history(threads[0]["thread_id"])
    else:
        start_new_chat()

h_col1, h_col2, h_col3 = st.columns([0.5, 0.25, 0.25])
with h_col1:
    st.subheader("Chat Session")
    st.caption(f"Thread: `{st.session_state.current_thread_id[:24]}...`")

with h_col2:
    st.text_input("User:", key="user_id", help="User identity identifier")

with h_col3:
    st.text_input("Org:", key="org_id", help="Organization scope identifier")

# ----------------- File Attachment Bar -----------------
with st.expander("📎 Attach File (CSV, Excel, TXT)", expanded=False):
    uploaded_file = st.file_uploader(
        "Upload a document or dataset",
        type=["csv", "xlsx", "xls", "tsv", "txt"],
        key="file_picker",
    )
    if uploaded_file and not st.session_state.uploaded_file_info:
        with st.spinner("Uploading and indexing file..."):
            file_data = upload_file_to_backend(uploaded_file)
            if file_data:
                st.session_state.uploaded_file_info = file_data
                st.success(f"Attached: {file_data.get('filename')}")

    if st.session_state.uploaded_file_info:
        f_info = st.session_state.uploaded_file_info
        st.info(f"📄 **{f_info.get('filename')}** ({f_info.get('analysis_type', 'file')})")
        if st.button("Remove File"):
            remove_uploaded_file()
            st.rerun()

# ----------------- Render Messages -----------------
if not st.session_state.messages:
    st.markdown(
        """
        <div style="text-align: center; padding: 60px 0; color: #9ca3af;">
            <h1 style="font-size: 50px;">🤖</h1>
            <p>Hello, how can I assist you today?</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

for msg in st.session_state.messages:
    if msg.get("role") == "system":
        continue
    with st.chat_message(msg.get("role")):
        if msg.get("reasoning"):
            with st.expander("💭 Thinking Process", expanded=False):
                st.markdown(f"*{msg['reasoning']}*")
        st.markdown(msg.get("content", ""))

# ----------------- Chat Input & Streaming -----------------
research_mode = st.checkbox("Structured financial research", value=True)
prompt = st.chat_input("Enter your message...")

if prompt:
    user_payload_text = prompt
    if st.session_state.uploaded_file_info:
        fname = st.session_state.uploaded_file_info.get("filename")
        user_payload_text = f"📎 [Attached: {fname}]\n{prompt}"

    # Append user turn
    st.session_state.messages.append({"role": "user", "content": user_payload_text})
    with st.chat_message("user"):
        st.markdown(user_payload_text)

    # Stream Assistant Turn
    with st.chat_message("assistant"):
        status_container = st.empty()
        reasoning_expander = None
        reasoning_text_area = None
        answer_placeholder = st.empty()

        full_reply = ""
        full_reasoning = ""
        completed = False
        stream_failed = False
        validated_report = None

        # Prepare request
        has_file = st.session_state.uploaded_file_info is not None
        endpoint = "/api/chat-with-file/stream" if has_file else "/api/chat/stream"
        payload = {
            "message": prompt,
            "response_schema": "analysis_report" if research_mode else None,
            "thread_id": st.session_state.current_thread_id,
            "user_id": st.session_state.user_id,
            "org_id": st.session_state.org_id,
        }
        if has_file:
            payload["file_id"] = st.session_state.uploaded_file_info.get("file_id")
            if st.session_state.uploaded_file_info.get("analysis_type") == "code_execution":
                status_container.info("⏳ Analyzing file, running sandbox code...")

        try:
            res = requests.post(
                f"{BACKEND_BASE_URL}{endpoint}",
                json=payload,
                stream=True,
                timeout=180,
            )
            res.raise_for_status()

            for line in res.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue

                raw_data = line[5:].strip()
                if not raw_data:
                    continue

                try:
                    ev = json.loads(raw_data)
                except Exception:
                    continue

                ev_type = ev.get("type")
                if ev_type == "token" and not research_mode:
                    full_reply += ev.get("content", "")
                    answer_placeholder.markdown(full_reply + "▌")
                elif ev_type == "reasoning_token":
                    full_reasoning += ev.get("content", "")
                    if reasoning_expander is None:
                        reasoning_expander = st.expander("💭 Thinking Process", expanded=True)
                        reasoning_text_area = reasoning_expander.empty()
                    reasoning_text_area.markdown(f"*{full_reasoning}*")
                elif ev_type == "tool_call":
                    status_container.caption(f"🔧 Calling tool: `{ev.get('name')}`...")
                elif ev_type == "tool_result":
                    status_container.caption("✅ Tool execution completed")
                elif ev_type == "report":
                    validated_report = AnalysisReport.model_validate(ev.get("structured_response"))
                    full_reply = render_report(validated_report)
                elif ev_type == "done":
                    if research_mode and validated_report is None:
                        stream_failed = True
                        st.error("Response completed without a validated research report.")
                        break
                    if not research_mode:
                        full_reply = ev.get("reply", full_reply)
                    completed = True
                    if ev.get("reasoning"):
                        full_reasoning = ev.get("reasoning")
                elif ev_type == "interrupted":
                    stream_failed = True
                    st.info("Research paused for a decision. No completed report was saved.")
                    break
                elif ev_type == "error":
                    stream_failed = True
                    st.error(ev.get("message", "Stream execution error"))
                    break

            status_container.empty()
            if completed and not stream_failed:
                answer_placeholder.markdown(full_reply)
                st.session_state.messages.append(
                    {"role": "assistant", "content": full_reply, "reasoning": full_reasoning}
                )
                if has_file:
                    remove_uploaded_file()
            else:
                answer_placeholder.empty()
                if not stream_failed:
                    st.error("Response ended before a validated research report was received. Please retry.")

        except Exception as e:
            answer_placeholder.empty()
            st.error(f"Error during response generation: {e}")

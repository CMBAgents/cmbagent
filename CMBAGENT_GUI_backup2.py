# CMBAGENT_GUI.py

import streamlit as st
import os
import re
import io
from PIL import Image
import pandas as pd
from IPython.display import Markdown as IPyMarkdown, Image as IPyImage
from PIL.Image import Image as PILImage  # <-- this is the actual class
from pandas.io.formats.style import Styler
os.environ["CMBAGENT_DEBUG"] = "false"
os.environ["ASTROPILOT_DISABLE_DISPLAY"] = "true"

import copy
import cmbagent
from cmbagent.cmbagent import CMBAgent, planning_and_control, one_shot
import json
import requests
import sys
from contextlib import redirect_stdout
from streamlit import components
import glob          # <-- NEW

from langchain_community.chat_message_histories import ChatMessageHistory
# from langchain.callbacks.base import BaseCallbackHandler

class StreamHandler:
    def __init__(self, container):
        # container is the st.empty() you passed in
        self.container = container
        self.text = ""

    def write(self, token: str, **kwargs):
        self.text += token
        # update the same bubble
        self.container.markdown(self.text + "▌")

    def flush(self):
        pass

    def render_structured(self, results):
        # clear previous streaming
        self.container.empty()

        # build one markdown blob for all chat_history entries
        lines = []
        for turn in results.get("chat_history", []):
            role    = turn.get("role", "assistant")
            content = turn.get("content")
            # assume content is already a plain string or markdown
            lines.append(f"**{role}:** {content}")
        self.container.markdown("\n\n".join(lines))


# --- Helper Functions ---
def save_encrypted_key(encrypted_key, username):
    try:
        filename = f"{username}_encrypted_api_key" if username else ".encrypted_api_key"
        with open(filename, "w") as f:
            f.write(encrypted_key)
        return True
    except Exception:
        return False

def load_encrypted_key(username):
    try:
        filename = f"{username}_encrypted_api_key" if username else ".encrypted_api_key"
        with open(filename, "r") as f:
            return f.read()
    except FileNotFoundError:
        return None

def read_keys_from_file(file_path):
    with open(file_path, 'r') as file:
        return json.load(file)

def read_prompt_from_file(path):
    with open(path, 'r') as f:
        return f.read()

st.set_page_config(
    page_title="CMB Agent",
    page_icon="🪐",
    layout="wide",
    initial_sidebar_state="auto"
)

def merge_agent_history(history):
    for m in history:
        st.session_state.messages.append({
            "role": m.get("role"),
            "content": m.get("content")
        })
        save_chat_history(st.session_state.get("username"), st.session_state.messages)

def retrieve_context(question):
    docs = st.session_state.vector_store.similarity_search(question, k=4)
    return "\n\n".join([doc.page_content for doc in docs])

def render_turn(turn):
    """
    Replays one assistant 'to_display' structure or a plain string.
    """
    if isinstance(turn, str):
        st.markdown(turn)
        return
    
    

    # turn is a list of (who, content) tuples –- same as `to_display`
    for who, content in turn:
        st.markdown(f"**Message from {who}:**")
        if isinstance(content, IPyMarkdown):
            st.markdown(content.data, unsafe_allow_html=True)
        # elif isinstance(content, pd.io.formats.style.Styler):
        #     st.dataframe(content.data)
        elif isinstance(content, IPyImage):
            img = getattr(content, "data", None) or getattr(content, "_data", None)
            st.image(img, use_container_width=True)

        elif isinstance(content, PILImage):
            st.image(content, use_container_width=True)

        elif isinstance(content, (dict, list)):
            st.json(content)

        # handle Pandas Styler if available
        elif Styler is not None and isinstance(content, Styler):
            st.dataframe(content.data)
            continue

        elif isinstance(content, pd.DataFrame):
            # in case you already unwrapped to a DataFrame
            st.dataframe(content)

        else:
            st.markdown(content)

# --- Chat-history persistence helpers ----------------------------------------
def _history_filename(username: str | None) -> str:
    # put all history files in ./history  (create the dir once on startup)
    hist_dir = os.path.join(os.path.dirname(__file__), "history")
    os.makedirs(hist_dir, exist_ok=True)
    name = f"{username or 'anon'}_chat_history.json"
    return os.path.join(hist_dir, name)

def _restore_special(obj):
    # convert {"_type": "image_path", "path": "..."} back to a PIL image
    if isinstance(obj, dict) and obj.get("_type") == "image_path":
        p = obj.get("path", "")
        try:
            return Image.open(p) if p else "<image>"
        except Exception:
            return "<image>"
    return obj

# ------------------------------------------------------------------ utils/json
def _json_default(obj):
    # plain types
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj

    # NEW ➜ turn tuples into lists so JSON can write them
    if isinstance(obj, tuple):
        return list(obj)

    # IPython markdown
    if isinstance(obj, IPyMarkdown):
        return obj.data

    # Pandas Styler
    if isinstance(obj, pd.io.formats.style.Styler):
        return obj.data.to_dict()

    # PIL / IPython images
    if isinstance(obj, (PILImage, IPyImage)):
        path = getattr(obj, "filename", None) or ""
        return {"_type": "image_path", "path": path}

    # fallback
    return str(obj)

# ------------------------------------------------------------------ utils/json

import tempfile, shutil
from json import JSONDecodeError
from datetime import datetime
# ── utils/filenames ──────────────────────────────────────────────────────────
import unicodedata
import string

def _slugify(text: str, max_len: int = 40) -> str:
    """
    ASCII-only, filesystem-safe slug of *text*.
    • keeps letters & digits
    • collapses everything else to “_”
    • trims to *max_len* characters
    """
    # normalise accents → ASCII
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    # keep alnum, replace the rest with _
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    return text[:max_len] or "untitled"


# def _new_history_path(username: str | None) -> str:
#     ts = datetime.now().strftime("%Y%m%d-%H%M%S")
#     root = f"{username or 'anon'}_{ts}_chat_history.json"
#     return os.path.join(os.path.dirname(__file__), "history", root)

# ──────────────────────────────────────────────────────────────────────
def save_chat_history(username: str | None, messages: list[dict]) -> None:
    """
    Atomically write the current page, chat_mode, and messages to disk.
    """
    data = {
        "page":       st.session_state.page,
        "chat_mode":  st.session_state.chat_mode,
        "messages":   messages
    }
    fn = st.session_state.get("cur_hist") or _history_filename(username)
    dir_name = os.path.dirname(fn)

    # 1. write to a temp-file in the same directory
    with tempfile.NamedTemporaryFile("w",
                                     encoding="utf-8",
                                     dir=dir_name,
                                     delete=False) as tmp:
        json.dump(data, tmp,
                  ensure_ascii=False, indent=2, default=_json_default)
        tmp.flush()
        os.fsync(tmp.fileno())

    # 2. atomically replace the old file
    os.replace(tmp.name, fn)


def load_chat_history(username: str | None) -> list[dict]:
    """
    Read the history file.  If it’s a wrapped object, restore page/chat_mode,
    then return just the messages list.  If it’s the old format (just a list),
    fall back to list.
    """
    fn = _history_filename(username)
    if not os.path.exists(fn):
        return []

    try:
        with open(fn, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except JSONDecodeError:
        broken = fn + ".broken"
        shutil.move(fn, broken)
        print(f"⚠️  History file was corrupt – renamed to {broken}")
        return []

    # if we see our 3-key wrapper, peel off page + chat_mode
    if isinstance(raw, dict) and "messages" in raw:
        # restore page & chat_mode
        st.session_state.page      = raw.get("page", "mode_select")
        st.session_state.chat_mode = raw.get("chat_mode", None)
        msgs = raw["messages"]
    else:
        # legacy: file was just a list
        msgs = raw

    # now walk/msg-restore exactly as before
    def walk(x):
        if isinstance(x, list):
            return [walk(i) for i in x]
        if isinstance(x, tuple):
            return tuple(walk(i) for i in x)
        return _restore_special(x)

    return walk(msgs)


    # walk the nested structure and restore images / special objects
    def walk(x):
        if isinstance(x, list):
            return [walk(i) for i in x]
        if isinstance(x, tuple):
            return tuple(walk(i) for i in x)
        return _restore_special(x)

    return walk(data)
# ──────────────────────────────────────────────────────────────────────


# --- Initialize Session State ---
def init_session():
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # NEW – hydrate from disk (do this once at startup only)
    stored = load_chat_history(st.session_state.get("username"))
    if stored and not st.session_state.messages:
        st.session_state.messages = stored

    if "debug" not in st.session_state:
        st.session_state.debug = False
    if "llm" not in st.session_state:
        st.session_state.llm = None
    if "llmBG" not in st.session_state:
        st.session_state.llmBG = None
    if "memory" not in st.session_state:
        st.session_state.memory = ChatMessageHistory()
    if "vector_store" not in st.session_state:
        st.session_state.vector_store = None
    if "last_token_count" not in st.session_state:
        st.session_state.last_token_count = 0
    if "selected_model" not in st.session_state:
        st.session_state.selected_model = "gpt-4o-mini"
    if "greeted" not in st.session_state:
        st.session_state.greeted = False
    if "debug_messages" not in st.session_state:
        st.session_state.debug_messages = []
    if "page" not in st.session_state:
        st.session_state.page = "mode_select"
    if "page" not in st.session_state:
        st.session_state.page = "mode_select"
    if "chat_mode" not in st.session_state:
        st.session_state.chat_mode = None
    if "nav_intent" not in st.session_state:
        st.session_state.nav_intent = None


init_session()

# --- Sidebar: Always visible controls ---
with st.sidebar:
    st.header("🔐 API Provider")

    provider_oai        = st.text_input("OpenAI API Key",      type="password", key="api_key_oai")
    provider_anthropic  = st.text_input("Anthropic API Key",    type="password", key="api_key_anthropic")
    provider_gemini     = st.text_input("Gemini API Key",       type="password", key="api_key_gemini")
    provider_perplexity = st.text_input("Perplexity API Key",   type="password", key="api_key_perplexity")


    # ——— Set environment variables on every run ———
    if provider_oai:
        os.environ["OPENAI_API_KEY"] = provider_oai
    if provider_anthropic:
        os.environ["ANTHROPIC_API_KEY"] = provider_anthropic
    if provider_gemini:
        os.environ["GEMINI_API_KEY"] = provider_gemini
    if provider_perplexity:
        os.environ["PERPLEXITY_API_KEY"] = provider_perplexity

    # --- API Key Validation ---
    def validate_openai_key(api_key):
        try:
            headers = {"Authorization": f"Bearer {api_key}"}
            resp = requests.get("https://api.openai.com/v1/models", headers=headers, timeout=5)
            return resp.status_code == 200
        except Exception:
            # print("⚠️  OpenAI API Key validation failed.")
            return False

    def validate_anthropic_key(api_key, version="2023-06-01"):
        """
        Return True iff the key is able to hit the /v1/models endpoint.
        Anthropic requires the `anthropic-version` header.
        """
        headers = {
            "x-api-key": api_key,
            "anthropic-version": version,
            "accept": "application/json",
        }
        try:
            r = requests.get(
                "https://api.anthropic.com/v1/models",
                headers=headers,
                timeout=5,
            )
            return r.status_code == 200
        except requests.RequestException:
            return False

    def validate_gemini_key(api_key: str) -> bool:
        """
        Verify a Google Gemini API key by calling the free *models.list* endpoint.
        
        Returns **True** when the key is accepted (HTTP 200), **False** otherwise.
        """

        base_urls = [
            "https://generativelanguage.googleapis.com/v1/models",
            "https://generativelanguage.googleapis.com/v1beta/models",  # fallback
        ]

        for url in base_urls:
            try:
                r = requests.get(url, params={"key": api_key}, timeout=5)
                if r.status_code == 200:
                    return True          # valid key
                if r.status_code in (401, 403):   # invalid / revoked key
                    return False
                # for 404 etc. try the next base URL
            except requests.RequestException:
                pass

        return False                      # every attempt failed

    def validate_perplexity_key(api_key: str) -> bool:
        """
        Return True iff *api_key* can successfully call Perplexity's
        /chat/completions endpoint.

        • Uses the lightweight 'sonar' model  
        • Asks for a 1-token reply to minimise cost  
        • Treats HTTP 200 as success, anything else as failure
        """
        url = "https://api.perplexity.ai/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = {
            "model": "sonar",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "stream": False,
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=5)
            return resp.status_code == 200
        except requests.RequestException:
            return False
    if provider_oai:
        if validate_openai_key(provider_oai):
            st.success("OpenAI API Key is valid.")
        else:
            st.error("Invalid OpenAI API Key.")
    if provider_anthropic:
        if validate_anthropic_key(provider_anthropic):
            st.success("Anthropic API Key is valid.")
        else:
            st.error("Invalid Anthropic API Key.")
    if provider_gemini:
        if validate_gemini_key(provider_gemini):
            st.success("Gemini API Key is valid.")
        else:
            st.error("Invalid Gemini API Key.")
    if provider_perplexity:
        if validate_perplexity_key(provider_perplexity):
            st.success("Perplexity API Key is valid.")
        else:
            st.error("Invalid Perplexity API Key.")

    st.markdown("---")
    st.header("📂 Agents in charge")

    agents = {
        "engineer": {
            "label": "Engineer",
            "models": [
                "gpt-4.1", "gpt-4.1-mini", "gpt-4.5-preview", "gpt-4o", "gpt-4o-mini", "o3", "o4-mini", "o3-mini",
                "Claude 3.7 Sonnet", "Claude 3.5 Haiku", "Claude 3.5 Sonnet",
                "gemini-2.5-flash-preview-04-17", "gemini-2.5-pro-preview-03-25", "gemini-2.0-flash",
                "sonar-pro", "sonar"
            ]
        },
        "researcher": {
            "label": "Researcher",
            "models": [
                "gpt-4.1", "gpt-4.1-mini", "gpt-4.5-preview", "gpt-4o", "gpt-4o-mini", "o3", "o4-mini", "o3-mini",
                "Claude 3.7 Sonnet", "Claude 3.5 Haiku", "Claude 3.5 Sonnet",
                "gemini-2.5-flash-preview-04-17", "gemini-2.5-pro-preview-03-25", "gemini-2.0-flash",
                "sonar-pro", "sonar"
            ]
        }
    }

    def get_provider_for_model(model):
        oai = {"gpt-4.1", "gpt-4.1-mini", "gpt-4.5-preview", "gpt-4o", "gpt-4o-mini", "o3", "o4-mini", "o3-mini"}
        anth = {"Claude 3.7 Sonnet", "Claude 3.5 Haiku", "Claude 3.5 Sonnet"}
        gem = {"gemini-2.5-flash-preview-04-17", "gemini-2.5-pro-preview-03-25", "gemini-2.0-flash"}
        per = {"sonar-pro", "sonar"}

        if model in oai:
            return "OpenAI"
        if model in anth:
            return "Anthropic"
        if model in gem:
            return "Gemini"
        if model in per:
            return "Perplexity"
        return None

    # Initialize session_state.agent_models once
    if "agent_models" not in st.session_state:
        st.session_state.selected_model = {
            name: info["models"][0] for name, info in agents.items()
        }

    for key, info in agents.items():
        with st.expander(f"🧑‍💻 {info['label']}"):
            choice = st.selectbox(
                f"Select LLM model for {info['label']}",
                info["models"],
                key=f"{key}_model",
                index=info["models"].index(st.session_state.selected_model[key])
            )
            st.session_state.selected_model[key] = choice

    # ------------------- sidebar : "🗂️ Chat history" -------------------
    st.markdown("---")
    st.header("🗂️ Chat history")
    hist_dir = os.path.join(os.path.dirname(__file__), "history")
    chat_files = sorted([
        f for f in os.listdir(hist_dir) if f.endswith("_chat_history.json")
    ])

    # Unique key to persist the selection
    chat_choice = st.selectbox(
        "Open conversation",
        ["— choose —"] + chat_files,
        label_visibility="collapsed",
        key="chat_history_choice"
    )

    # Load only when a new file is selected to avoid rerun loops
    if chat_choice != "— choose —" \
    and st.session_state.get("loaded_chat_file") != chat_choice:

        file_path = os.path.join(hist_dir, chat_choice)

        # 1) infer mode from filename: "<user>_<page>_…"
        m = re.match(r'^[^_]+_(one_shot|planning_and_control)_', chat_choice)
        if m:
            saved_page = m.group(1)
            saved_chatmode = "one-shot" if saved_page == "one_shot" \
                            else "planning-and-control"
        else:
            # fallback to whatever you have now
            saved_page     = st.session_state.page
            saved_chatmode = st.session_state.chat_mode

        # 2) load and unwrap JSON
        with open(file_path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        if isinstance(raw, dict) and "messages" in raw:
            saved_msgs = raw["messages"]
        else:
            saved_msgs = raw

        # 3) restore any images/special objects
        def walk(x):
            if isinstance(x, list):
                return [walk(i) for i in x]
            if isinstance(x, tuple):
                return tuple(walk(i) for i in x)
            return _restore_special(x)

        st.session_state.messages         = walk(saved_msgs)
        st.session_state.cur_hist         = file_path
        st.session_state.loaded_chat_file = chat_choice

        # 4) **restore the mode you inferred from the filename**
        st.session_state.page      = saved_page
        st.session_state.chat_mode = saved_chatmode

        st.rerun()
    # -------------------------------------------------------------------


    st.markdown("---")
    if st.button("Reset Session", key="reset_session"):
        st.session_state.clear()
        st.rerun()

# --- Main UI Layout ---
if st.session_state.page == "mode_select":
    st.title("🪐 Multi-agent system for cosmological discovery")
    logo_path = os.path.join(os.path.dirname(__file__), "cmbagent", "org_logo.jpg")
    st.image(logo_path, width=400)
    st.markdown("### Select a Chat Mode")

    col1, col2 = st.columns([1, 1], gap="large")
    with col1:
        if st.button("**🟦\nOne Shot**", key="one_shot_btn", use_container_width=True, type="primary"):
            st.session_state.page        = "one_shot"
            st.session_state.chat_mode   = "one-shot"
            st.session_state.messages    = []
            # inside the mode-select buttons
            st.session_state.cur_hist = None      # ← we’ll create it after the first prompt

            # st.session_state.cur_hist    = _new_history_path(username)   # ← NEW
            # save_chat_history(username, [])                              # ← NEW (touch file)
            st.rerun()
    with col2:
        if st.button("**🟩\nPlanning and Control**", key="planning_btn", use_container_width=True, type="primary"):
            st.session_state.page        = "planning_and_control"
            st.session_state.chat_mode   = "planning-and-control"
            st.session_state.messages    = []
            # inside the mode-select buttons
            st.session_state.cur_hist = None      # ← we’ll create it after the first prompt

            # st.session_state.cur_hist    = _new_history_path(username)   # ← NEW
            # save_chat_history(username, [])                              # ← NEW
            st.rerun()
    st.markdown("---")
    st.markdown("#### About\nChoose a mode to start chatting with the CMB Agent. Settings on the left are always available.")

else:
    # Back button + title
    back_col, title_col = st.columns([1, 8])
    with back_col:
        if st.button("⬅️ Back", key="back_btn"):
            st.session_state.page = "mode_select"
            st.session_state.chat_mode = None
            st.session_state.messages = []
            st.rerun()
    with title_col:
        header = "💬 One Shot Chat" if st.session_state.page == "one_shot" else "💬 Planning and Control Chat"
        st.title(header)

        # ── split screen: conversation on left, params on right ─────────────
    main_col, param_col = st.columns([4, 1])
    with param_col:
        st.header("Parameters")
        if st.session_state.page == "one_shot":
            max_rounds = st.slider(
                "Max Rounds", min_value=1, max_value=20, value=10, step=1
            )
            max_n_attempts = st.slider(
                "Max Attempts", min_value=1, max_value=10, value=3, step=1
            )
        else:  # planning_and_control
            max_rounds_control = st.slider(
                "Max Control Rounds", min_value=1, max_value=1000, value=500, step=1
            )
            n_plan_reviews = st.slider(
                "Plan Reviews", min_value=1, max_value=10, value=1, step=1
            )
            max_n_attempts = st.slider(
                "Max Attempts", min_value=1, max_value=10, value=4, step=1
            )
            max_plan_steps = st.slider(
                "Max Plan Steps", min_value=1, max_value=20, value=7, step=1
            )


    # Render conversation history
    # for msg in st.session_state.messages:
    #     with st.chat_message(msg["role"]):
    #         st.markdown(msg["content"])
    # Render conversation history (supports both new dict‐messages and legacy strings/lists)
    for msg in st.session_state.messages:
        if isinstance(msg, dict) and "role" in msg:
            role    = msg.get("role", "assistant")
            content = msg.get("content", "")
        else:
            # fallback for legacy entries (just show as assistant text)
            role    = "assistant"
            content = msg
        with st.chat_message(role):
            render_turn(content)



    # --- Agent picker pinned above the prompt ---------------------------------
    if st.session_state.page == "one_shot":
        with st.container():
            st.radio(
                "Pick the agent for this turn ↘︎",
                ["engineer", "researcher"],
                horizontal=True,
                key="one_shot_selected_agent",
            )
    # --------------------------------------------------------------------------

    user_input = st.chat_input("Type your task or question here…")


    # --- Display Full Chat History ---
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            # Check if this message contains a plot path marker
            if "PLOT_PATH:" in message["content"]:
                # Split content into text and plot path
                parts = message["content"].split("PLOT_PATH:")
                # Display the text part
                st.markdown(parts[0])
                # Display each plot path
                for plot_info in parts[1:]:
                    plot_path = plot_info.split('\n')[0].strip()
                    if os.path.exists(plot_path):
                        st.image(plot_path, width=700)
            else:
                st.markdown(message["content"])
                
     # … everything above stays the same until here …
    if user_input:
        # ── create chat-history file on very first prompt ────────────────────────────
        if st.session_state.cur_hist is None:          # first message this session
            # include the current page ("one_shot" or "planning_and_control") in the filename
            page = st.session_state.page  # "one_shot" or "planning_and_control"
            slug = _slugify(user_input)
            hist_dir = os.path.join(os.path.dirname(__file__), "history")
            fn = f"{page}_{slug}_chat_history.json"

            path    = os.path.join(hist_dir, fn)

            # avoid collisions: add _2, _3 … if necessary
            counter = 2
            base, ext = os.path.splitext(path)
            while os.path.exists(path):
                path = f"{base}_{counter}{ext}"
                counter += 1

            st.session_state.cur_hist = path          # remember it
            save_chat_history(None, [])           # touch the file once


        # 1) Record & echo the user message
        st.session_state.messages.append({"role": "user", "content": user_input})
        save_chat_history(st.session_state.get("username"), st.session_state.messages)

        with st.chat_message("user"):
            st.markdown(user_input)

        # st.session_state.memory.add_user_message(user_input)
        # context = retrieve_context(user_input)

        # 2) Prepare your chosen models
        engineer_model   = st.session_state.selected_model["engineer"]
        researcher_model = st.session_state.selected_model["researcher"]

        # # 3) Stream only internal logs in one assistant bubble
        # with st.chat_message("assistant"):
        #     exp = st.expander("🧠 Internal reasoning (optional)", expanded=False)
        #     handler = StreamHandler(exp)

        # 3) Stream only internal logs in one assistant bubble  ← still true :)
        import time
        with st.chat_message("assistant"):
            start = time.perf_counter()
            status_ph = st.empty()
            status_ph.markdown("**Thinking in progress…**")
            output_ph = st.empty()
            handler   = StreamHandler(output_ph)



            # 1) run the agent
            with redirect_stdout(handler):
                if st.session_state.page == "one_shot":
                    results = one_shot(
                        user_input,
                        max_rounds       = max_rounds,
                        max_n_attempts   = max_n_attempts,
                        initial_agent    = st.session_state.one_shot_selected_agent,
                        engineer_model   = engineer_model,
                    )
                else:
                    results = planning_and_control(
                        user_input,
                        max_rounds_control = max_rounds_control,
                        n_plan_reviews     = n_plan_reviews,
                        max_n_attempts     = max_n_attempts,
                        max_plan_steps     = max_plan_steps,
                        engineer_model     = engineer_model,
                        researcher_model   = researcher_model,
                    )

                save_chat_history(None, st.session_state.messages)

            elapsed = time.perf_counter() - start
            status_ph.markdown(f"**Done in {elapsed:.2f}s**")
            


  # … right after your redirect_stdout(...) block …

        from IPython.display import Image as IPyImage
        from PIL.Image import Image as PILImage

        # history        = results.get("chat_history", [])
        # tool_responses = results.get("tool_responses", [])

  

        def extract_key(name, content):
            # pull out the actual string we care about
            if isinstance(content, IPyMarkdown):
                text = getattr(content, "data", "") or ""
            elif isinstance(content, pd.io.formats.style.Styler):
                df = getattr(content, "data", None)
                text = df.to_csv(index=False) if (df is not None) else ""
            else:
                text = str(content)
            return f"{name}::{text.strip()}"
  

        to_display = []
        seen = set()
        
        # --- NEW: sweep ./output/data for images -------------------------------
        image_dir = os.path.join(os.path.dirname(__file__), "output", "data")
        img_paths = sorted(glob.glob(os.path.join(image_dir, "*")))    # natural order

        img_ext_pattern = re.compile(r"\.(png|jpe?g|gif|bmp|tiff)$", re.I)
        for p in img_paths:
            if not img_ext_pattern.search(p):
                continue                       # skip non-image files

            if p in seen:                      # avoid duplicates in the same run
                continue
            seen.add(p)

            try:
                img = Image.open(p)
                to_display.append(("file", img))
            except Exception as e:
                print(f"⚠️  Could not open {p}: {e}")
        # -----------------------------------------------------------------------

        # ——— also load any .md files from ./output (without clearing to_display) ———
        md_dir   = os.path.join(os.path.dirname(__file__), "output")
        md_paths = sorted(glob.glob(os.path.join(md_dir, "*.md")))
        for md_path in md_paths:
            if md_path in seen:
                continue
            seen.add(md_path)
            try:
                with open(md_path, "r", encoding="utf-8") as f:
                    md_text = f.read()
            except Exception:
                continue
            to_display.append((
                "researcher",
                f"### Contents of `{os.path.basename(md_path)}`\n\n{md_text}"
            ))

        render_turn(to_display)
        reasoning_text = handler.text
    # --- save the chat history to disk -----------------------------------
        st.session_state.messages.append({
            "role": "assistant",
            "content": to_display,
            "reasoning": reasoning_text,       # ← NEW FIELD
            "elapsed":  f"{elapsed:.2f}s",     # (optional) useful in raw JSON
        })

        save_chat_history(None, st.session_state.messages)

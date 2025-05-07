# CMBAGENT_GUI.py

import streamlit as st
import os
import re
import io
from PIL import Image
import pandas as pd
from IPython.display import Markdown as IPyMarkdown, Image as IPyImage
# from PIL.Image import Image as PILImage  # <-- this is the actual class
from pandas.io.formats.style import Styler
os.environ["CMBAGENT_DEBUG"] = "false"
os.environ["ASTROPILOT_DISABLE_DISPLAY"] = "true"
os.environ["STREAMLIT_ON"] = "true"
import json
import time
import copy
# import cmbagent
from cmbagent.cmbagent import CMBAgent, planning_and_control, one_shot, human_in_the_loop
# from .cmbagent import CMBAgent, planning_and_control, one_shot, human_in_the_loop
import requests
import sys
from contextlib import redirect_stdout
from streamlit import components
import glob          # <-- NEW
import traceback
import base64
from pathlib import Path
# import builtins, uuid

# from langchain_community.chat_message_histories import ChatMessageHistory
# from langchain.callbacks.base import BaseCallbackHandler

# ── rolling memory: only used in HUMAN‑IN‑THE‑LOOP mode ───────────────
from collections import deque

def main():
    from PIL.Image import Image as PILImage
    MEMORY_WINDOW = None                     # keep last 12 exchanges
    def ensure_memory() -> None:
        """
        Make sure st.session_state.memory exists.
        If MEMORY_WINDOW is truthy, cap the size; otherwise let it grow forever.
        """
        if "memory" not in st.session_state or st.session_state.memory is None:
            if MEMORY_WINDOW:
                st.session_state.memory = deque(maxlen=MEMORY_WINDOW)
            else:
                st.session_state.memory = deque()        # unlimited

    def add_to_memory(role: str, content: str) -> None:
        """Push one turn into memory – *only* in human‑in‑the‑loop mode."""
        if st.session_state.page == "human_in_the_loop":
            ensure_memory()
            st.session_state.memory.append((role, str(content)))
            # print(f"Memory: {st.session_state.memory}")

    def memory_as_text() -> str:
        """Return the rolling window as '[role]: content' lines."""
        if st.session_state.page != "human_in_the_loop":
            return ""                         # any other mode → no memory
        ensure_memory()
        return "\n".join(f"[{r}]: {c}" for r, c in st.session_state.memory)
    # ───────────────────────────────────────────────────────────────────────

    def _text_from_content(obj) -> str:
        """
        Return the human‑readable text of an Autogen ‘content’ payload.
        • strings  → stripped
        • (role, text) tuples / lists → first text element
        • IPython Markdown → its .data
        • DataFrames / images → ignored (return "")
        """
        if obj is None:
            return ""              # genuinely empty
        
        from IPython.display import Markdown as IPyMarkdown
        if isinstance(obj, str):
            return obj.strip()

        # Autogen’s to_display: list of (who, subcontent) tuples
        if isinstance(obj, list) and obj and isinstance(obj[0], (list, tuple)):
            # find the first element that is text‑like
            for _, sub in obj:
                t = _text_from_content(sub)
                if t:
                    return t
            return ""

        if isinstance(obj, IPyMarkdown):
            return obj.data.strip()

        # fall‑through: not text
        return ""




    # ---------------------------------------------------------------------------

    class StreamHandler:
        def __init__(self, container):
            self.container = container.empty()
            self.text = ""

        def write(self, token: str, **kwargs):
            # ── FILTER noisy debug prefixes (optional) ───────────
            skip_prefixes = ("[user]:", "[assistant]:", "[Human]:")
            if token.lstrip().startswith(skip_prefixes):
                return
            
            self.text += token
            self.container.markdown(self.text + "▌")

        def flush(self):
            pass

        def render_structured(self, results):
            if isinstance(results, dict) and results.get("chat_history"):
                for msg in results["chat_history"]:
                    with st.chat_message(msg["role"]):
                        st.markdown(msg["content"])


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

    # st.set_page_config(
    #     page_title="CMBAgent",
    #     page_icon=":cmbagent:",
    #     layout="wide",
    #     initial_sidebar_state="auto"
    # )

    cmbagent_logo_path = os.path.join(os.path.dirname(__file__), "..", "logo.png")
    st.set_page_config(
        page_title="CMBAGENT",
        page_icon=cmbagent_logo_path,          # or page_icon=logo_img
        layout="wide",
        initial_sidebar_state="auto"
    )


    st.markdown("""
    <link href='https://fonts.googleapis.com/css?family=Jersey+10' rel='stylesheet'>
    <style>
    div[data-testid="stButton"] > button {
        font-family: 'Jersey 10', sans-serif !important;
        font-size: 40px !important;
        padding: 0.5rem 0.5rem !important;
        border-radius: 0rem !important;
    }
    </style>
    """, unsafe_allow_html=True)

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

            # elif isinstance(content, (dict, list)):
            #     pretty = json.dumps(content, indent=2, ensure_ascii=False)
            #     st.code(pretty, language="json")

            # handle Pandas Styler if available
            elif Styler is not None and isinstance(content, Styler):
                st.dataframe(content.data)
                continue

            elif isinstance(content, pd.DataFrame):
                # in case you already unwrapped to a DataFrame
                st.dataframe(content)

            else:
                st.markdown(content)

    def render_content(obj):
        # 1) Turn-list: [(who, subcontent), …]
        if (
            isinstance(obj, list)
            and all(isinstance(item, (list,tuple)) and len(item)==2 and isinstance(item[0], str)
                    for item in obj)
        ):
            for who, sub in obj:
                st.markdown(f"**{who}:**")
                render_content(sub)
            return

        # 2) Plain text
        if isinstance(obj, str):
            st.markdown(obj)
            return

        # 3) IPython-Markdown
        if isinstance(obj, IPyMarkdown):
            st.markdown(obj.data, unsafe_allow_html=True)
            return

        # 4) PIL images
        if isinstance(obj, PILImage):
            st.image(obj, use_container_width=True)
            return

        # 5) DataFrames
        if isinstance(obj, pd.DataFrame):
            st.dataframe(obj)
            return

        # 6) Dicts → pretty-print
        if isinstance(obj, dict):
            pretty = json.dumps(obj, indent=2, ensure_ascii=False)
            st.code(pretty, language="json")
            return

        # 7) “Real” lists → render each item
        if isinstance(obj, list):
            for item in obj:
                render_content(item)
            return

        # 8) Fallback
        st.markdown(str(obj))

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

    # def typewriter(text, placeholder, delay=0.05):
    #     displayed = ""
    #     for char in text:
    #         displayed += char
    #         placeholder.markdown(
    #             f"<h1 style=\"font-family: 'Roboto Mono', monospace; color:#0ff; letter-spacing:1px\">{displayed}</h1>",
    #             unsafe_allow_html=True
    #         )
    #         time.sleep(delay)

    # def typewriter(text, placeholder, delay=0.05):
    #     placeholder.markdown(
    #         f"<h1 style=\"font-family: 'Jersey 10'; letter-spacing:2px; text-align:center\">{text}</h1>",
    #         unsafe_allow_html=True
    #     )

    def typewriter(text, placeholder, delay=0.05):
        # 1) Load the font once
        if not st.session_state.get("jersey10_loaded", False):
            st.markdown(
                "<link href='https://fonts.googleapis.com/css?family=Jersey+10' rel='stylesheet'>",
                unsafe_allow_html=True
            )
            st.session_state.font_loaded = True

        # 2) Render the heading
        placeholder.markdown(
            f"""
            <h6 style="
                font-family: 'Jersey 10', sans-serif;
                font-size: 80px;
                letter-spacing: 2px;
                text-align: center;
            ">
                {text}
            </h6>
            """,
            unsafe_allow_html=True
        )

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
        # stored = load_chat_history(st.session_state.get("username"))
        # if stored and not st.session_state.messages:
        #     st.session_state.messages = stored

        if "debug" not in st.session_state:
            st.session_state.debug = False
        if "llm" not in st.session_state:
            st.session_state.llm = None
        if "llmBG" not in st.session_state:
            st.session_state.llmBG = None
        if "memory" not in st.session_state:
            st.session_state.memory = None
        if "vector_store" not in st.session_state:
            st.session_state.vector_store = None
        if "last_token_count" not in st.session_state:
            st.session_state.last_token_count = 0
        # if "selected_model" not in st.session_state:
        #     st.session_state.selected_model = "gpt-4o-mini"
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
        if "font_loaded" not in st.session_state:
            st.session_state.font_loaded = False
        # 🔸 initialise memory *only* if we start in human‑in‑the‑loop
        if st.session_state.page == "human_in_the_loop":
            ensure_memory()

        # -------------------------------- top of file -------------------------------
        # if "cmb_agent" not in st.session_state:
        #     st.session_state.cmb_agent = None          # nothing yet
        # if "chat_ctx"  not in st.session_state:
        #     st.session_state.chat_ctx  = {}            # empty context


    init_session()
    # 1) Make sure Jersey 10 is loaded once
    st.markdown(
            "<link href='https://fonts.googleapis.com/css?family=Jersey+10&display=swap' rel='stylesheet'>",
            unsafe_allow_html=True
        )
    
    current_path = os.path.dirname(__file__)
    logo_path    = os.path.join(current_path, "..", "Robot-MS-Aqua.png")
    with open(logo_path, "rb") as f:
        logo_b64 = base64.b64encode(f.read()).decode()
    # --- Sidebar: Always visible controls ---
    with st.sidebar:

        # st.header("📂 Agents in charge")
        st.markdown(
        f"""
        <h3 style="
            font-family: 'Jersey 10', sans-serif;
            font-size: 30px;
            margin-top: 1rem;
            display: flex;
            align-items: center;
        ">
          <img src="data:image/png;base64,{logo_b64}" 
               alt="Agents" 
               style="height:1.5em; vertical-align: middle; margin-right:0.5em;" />
          Agents
        </h3>
        """,
        unsafe_allow_html=True)

        st.markdown(
            """
            <span style="font-size:0.9rem;">
            <em>Chose a model for each agent. Click on the dropdown menus.</em>
            </span>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("<br>", unsafe_allow_html=True)
        


        agents = {
            "engineer": {
                "label": "Engineer",
                "models": [
                    "gpt-4o", "gpt-4o-mini","gpt-4.1", "gpt-4.1-mini", "gpt-4.5-preview",  "o3", "o4-mini", "o3-mini",
                    "claude-3-7-sonnet-20250219", "claude-3-5-haiku-20241022", "claude-3-5-sonnet-20241022",
                    "gemini-2.5-flash-preview-04-17", "gemini-2.5-pro-preview-03-25", "gemini-2.0-flash",
                    # "sonar-pro", "sonar"
                ]
            },
            "researcher": {
                "label": "Researcher",
                "models": [
                    "gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4.5-preview", "o3", "o4-mini", "o3-mini",
                    "claude-3-7-sonnet-20250219", "claude-3-5-haiku-20241022", "claude-3-5-sonnet-20241022",
                    "gemini-2.5-flash-preview-04-17", "gemini-2.5-pro-preview-03-25", "gemini-2.0-flash",
                    # "sonar-pro", "sonar"
                ]
            }
        }

        def get_provider_for_model(model):
            oai = {"gpt-4.1", "gpt-4.1-mini", "gpt-4.5-preview", "gpt-4o", "gpt-4o-mini", "o3", "o4-mini", "o3-mini"}
            anth = {"Claude 3.7 Sonnet", "Claude 3.5 Haiku", "Claude 3.5 Sonnet"}
            gem = {"gemini-2.5-flash-preview-04-17", "gemini-2.5-pro-preview-03-25", "gemini-2.0-flash"}
            # per = {"sonar-pro", "sonar"}

            if model in oai:
                return "OpenAI"
            if model in anth:
                return "Anthropic"
            if model in gem:
                return "Gemini"
            # if model in per:
            #     return "Perplexity"
            return None

        # Initialize session_state.agent_models once
        if "agent_models" not in st.session_state:
            st.session_state.agent_models = {
                name: info["models"][0] for name, info in agents.items()
            }

        # for key, info in agents.items():
        #     with st.expander(f"🧑‍💻 {info['label']}"):
        #         choice = st.selectbox(
        #             f"Select LLM model for {info['label']}",
        #             info["models"],
        #             key=f"{key}_model",
        #             index=info["models"].index(st.session_state.agent_models[key])
        #         )
        #         st.session_state.agent_models[key] = choice

        # somewhere near the top of your file, right after you define `agents = {...}`
        current_path = os.path.dirname(__file__)
        # print(current_path)
        logo_path    = os.path.join(current_path, "..", "Robot-MS-Aqua.png")
        with open(logo_path, "rb") as f:
            logo_b64 = base64.b64encode(f.read()).decode()

        # then further down, replace your old expander loop…
        for key, info in agents.items():
            # build a little markdown image tag
            img_md = f"![logo](data:image/png;base64,{logo_b64})"
            # use that instead of the 🧑‍💻 emoji
            with st.expander(f"{img_md}  {info['label']}", expanded=False):
                choice = st.selectbox(
                    f"Select LLM model for {info['label']}",
                    info["models"],
                    key=f"{key}_model",
                    index=info["models"].index(st.session_state.agent_models[key])
                )
                st.session_state.agent_models[key] = choice
        
        # st.header("🔐 API Provider")
        st.markdown(
            """
            <h3 style="
            font-family: 'Jersey 10', sans-serif;
            font-size: 30px;
            margin-top: 1rem;
            ">
            🔐 API Keys
            </h3>
            """,
            unsafe_allow_html=True
        )

        # 📝  Tell users they can skip the boxes if their keys are already exported
        st.markdown(
            """
            <span style="font-size:0.9rem;">
            <em>If you’ve already set your API keys as environment variables
            (e.g., in <code>~/.bashrc</code>), leave these fields blank.</em>
            </span>
            """,
            unsafe_allow_html=True,
        )
        st.markdown("<br>", unsafe_allow_html=True)

        provider_oai        = st.text_input("OpenAI",      type="password", key="api_key_oai")
        provider_anthropic  = st.text_input("Anthropic",    type="password", key="api_key_anthropic")
        provider_gemini     = st.text_input("Gemini",       type="password", key="api_key_gemini")
        # provider_perplexity = st.text_input("Perplexity API Key",   type="password", key="api_key_perplexity")

        username = None
        # username            = st.text_input("2. Username (for saving your files)", placeholder="Enter your username")
        # user_password       = st.text_input("3. Password to encrypt/decrypt API key", type="password")

        # ——— Set environment variables on every run ———
        if provider_oai:
            os.environ["OPENAI_API_KEY"] = provider_oai
        if provider_anthropic:
            os.environ["ANTHROPIC_API_KEY"] = provider_anthropic
        if provider_gemini:
            os.environ["GEMINI_API_KEY"] = provider_gemini
        # if provider_perplexity:
        #     os.environ["PERPLEXITY_API_KEY"] = provider_perplexity

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
        # if provider_perplexity:
        #     if validate_perplexity_key(provider_perplexity):
        #         st.success("Perplexity API Key is valid.")
        #     else:
        #         st.error("Invalid Perplexity API Key.")

        # st.markdown("---")

        

        # ------------------- sidebar : "🗂️ Chat history" -------------------
    #     st.markdown("---")
    #     # st.header("🗂️ Chat history")
        # st.markdown(
        #     """
        #     <h3 style="
        #       font-family: 'Jersey 10', sans-serif;
        #       font-size: 30px;
        #       margin-top: 1rem;
        #     ">
        #       🗂️ Chat history
        #     </h3>
        #     """,
        #     unsafe_allow_html=True
        # )

        
    #     hist_dir = os.path.join(os.path.dirname(__file__), "history")
    #     chat_files = sorted([
    #         f for f in os.listdir(hist_dir) if f.endswith("_chat_history.json")
    #     ])

    #     # Unique key to persist the selection
    #     chat_choice = st.selectbox(
    #         "Open conversation",
    #         ["— choose —"] + chat_files,
    #         label_visibility="collapsed",
    #         key="chat_history_choice"
    #     )

    #     # Load only when a new file is selected to avoid rerun loops
    #     if chat_choice != "— choose —" \
    #     and st.session_state.get("loaded_chat_file") != chat_choice:
    #     # if chat_choice != "— choose —":

    #         file_path = os.path.join(hist_dir, chat_choice)

    #         # 1) infer mode from filename: "<user>_<page>_…"
    #         m = re.match(r'^[^_]+_(one_shot|planning_and_control|human_in_the_loop)_', chat_choice)
    #         if m:
    #             saved_page = m.group(1)
    #             if saved_page == "one_shot":
    #                 saved_chatmode = "one-shot"
    #             elif saved_page == "planning_and_control":
    #                 saved_chatmode = "planning-and-control"
    #             elif saved_page == "human_in_the_loop":
    #                 saved_chatmode = "human-in-the-loop"

    #         else:
    #             # fallback to whatever you have now
    #             saved_page     = st.session_state.page
    #             saved_chatmode = st.session_state.chat_mode

    #         # 2) load and unwrap JSON
    #         with open(file_path, "r", encoding="utf-8") as f:
    #             raw = json.load(f)

    #         if isinstance(raw, dict) and "messages" in raw:
    #             saved_msgs = raw["messages"]
    #         else:
    #             saved_msgs = raw

    #         # 3) restore any images/special objects
    #         def walk(x):
    #             if isinstance(x, list):
    #                 return [walk(i) for i in x]
    #             if isinstance(x, tuple):
    #                 return tuple(walk(i) for i in x)
    #             return _restore_special(x)

    #         st.session_state.messages         = walk(saved_msgs)

    #         st.session_state.cur_hist         = file_path
    #         st.session_state.loaded_chat_file = chat_choice

    #         # 4) **restore the mode you inferred from the filename**
    #         st.session_state.page      = saved_page
    #         st.session_state.chat_mode = saved_chatmode

    #         # 🔸 move memory hydration **here** (uses saved_page which is final)
    #         if saved_page == "human_in_the_loop":
    #             ensure_memory()
    #             st.session_state.memory.clear()
    #             for m in st.session_state.messages:
    #                 role = m.get("role", "assistant")
    #                 content_text = _text_from_content(m.get("content"))   # ← extract real text / md
    #                 if content_text:                                      # skip empty / None
    #                     st.session_state.memory.append((role, content_text))

    #         # … after your selectbox for chat history …
    # # … after your chat_choice selectbox and load‐on‐select block …
    #     # — sidebar‑only CSS reset (leaves big buttons elsewhere intact) —
        st.markdown(
            """
            <style>
            /* -----------------------------
            SIDEBAR‑ONLY BUTTON RESET
            (keeps giant buttons intact on
                the mode‑selection page)
            ----------------------------- */

            /* 1️⃣ Give every sidebar button the same footprint */
            section[data-testid="stSidebar"] .stButton {
                width: 100% !important;          /* rows fill the sidebar */
                margin: 0 0 0.5rem 0 !important; /* tight, even vertical rhythm */
            }

            /* 2️⃣ Normalise the underlying <button> */
            section[data-testid="stSidebar"] .stButton > button,
            section[data-testid="stSidebar"] .stButton > button::first-line {
                /* typography — Streamlit defaults */
                font-size: 0.975rem !important;   /* 14 px */
                font-family: inherit !important;
                font-weight: 400 !important;
                line-height: 1.3 !important;
                letter-spacing: normal !important;
                text-transform: none !important;

                /* layout */
                width: 100% !important;           /* equal width */
                min-width: 0 !important;          /* no intrinsic expansion */
                height: auto !important;          /* equal height (driven by padding) */
                padding: 0.4rem 0.75rem !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

    #     # Single‐file delete
    #     if chat_choice != "— choose —":
    #         delete_path = os.path.join(hist_dir, chat_choice)
    #         if st.button("🗑️ Delete selected chat", key="delete_hist"):
    #             try:
    #                 os.remove(delete_path)
    #                 st.success(f"Deleted `{chat_choice}`")
    #                 # drop the widget state so it goes back to default next render:
    #                 st.session_state.pop("chat_history_choice", None)
    #                 st.session_state.pop("loaded_chat_file",    None)
    #                 st.rerun()
    #             except Exception as e:
    #                 st.error(f"Could not delete `{chat_choice}`: {e}")

    #     # Bulk delete all
    #     if st.button("🗑️ Delete all conversations", key="delete_all_hist"):
    #         removed = 0
    #         for fname in chat_files:
    #             p = os.path.join(hist_dir, fname)
    #             try:
    #                 os.remove(p)
    #                 removed += 1
    #             except OSError:
    #                 pass
    #         if removed:
    #             st.success(f"Deleted all {removed} conversations.")
    #         else:
    #             st.info("No conversation files to delete.")
    #         # drop both keys so selectbox resets
    #         st.session_state.pop("chat_history_choice", None)
    #         st.session_state.pop("loaded_chat_file",    None)
    #         st.rerun()
        # -------------------------------------------------------------------


        # Use a regular Streamlit button
        if st.button("Reset Session", key="reset_session"):
            st.session_state.clear()
            st.rerun()

    # --- Main UI Layout ---
    if st.session_state.page == "mode_select":

        header_placeholder = st.empty()
        # header_text = "🪐 Multi-agent system for Data Driven Discovery"
        header_text = "CMBAGENT"
        typewriter(header_text, header_placeholder)
        # typewriter(header_text)
        # st.title(header_text)

        st.markdown(
        """
        <h3 style="
        text-align: center;
        font-family: 'Jersey 10', sans-serif;
        font-size: 40px;    /* match your design */
        margin-top: 1.5rem; /* optional spacing */
        ">
        Planning and Control System for Data Analysis and Exploration
        </h3>
        """,
        unsafe_allow_html=True
    )
        
        st.markdown(
            """
            <h4 style="
            text-align: center;
            font-family: 'Jersey 10', sans-serif;
            font-size: 28px;
            margin-top: 0.5rem;
            color: #888;
            ">
            Powered by AG2
            </h4>
            """,
            unsafe_allow_html=True
        )

        
        # 1) Inject **scoped** CSS that only hits buttons inside our wrapper
        st.markdown(
            "<link href='https://fonts.googleapis.com/css?family=Jersey+10&display=swap' rel='stylesheet'>",
            unsafe_allow_html=True
        )
        st.markdown("""
        <style>
        /* Base button styling */
        .stButton > button {
            width: 400px !important;
            height: 350px !important;
            font-size: 24px !important; /* Smaller base for second line */
            font-family: 'Roboto', sans-serif !important;
            font-weight: 400 !important;
            white-space: pre-line !important;
            line-height: 1.2 !important;
            border-radius: 20px !important;
            margin: 10px auto !important;
            display: block !important;
            text-align: center !important;
        }

        /* First line styling */
        .stButton > button::first-line {
            font-family: 'Jersey 10', sans-serif !important;
            font-size: 40px !important;
            font-weight: 700 !important;
            letter-spacing: 2px !important;
            text-transform: uppercase !important;
        }

        /* Hover state */
        .stButton > button:hover {
            background-color: #104E8B !important;
            color: #fff !important;
        }
        </style>
        """, unsafe_allow_html=True)

        
        col1, col2, col3 = st.columns([2,2,2], gap="large")

        with col1:
            if st.button("One Shot\n \n ***Instant, single-pass responses***\n " \
            "***Ideal for quick answers***\n ***No follow-up context retained***", key="one_shot_btn", use_container_width=True):
                st.session_state.page        = "one_shot"
                st.session_state.chat_mode   = "one-shot"
                st.session_state.messages    = []
                # inside the mode-select buttons
                st.session_state.cur_hist = None      # ← we’ll create it after the first prompt

                # st.session_state.cur_hist    = _new_history_path(username)   # ← NEW
                # save_chat_history(username, [])                              # ← NEW (touch file)
                st.rerun()
        with col2:
            if st.button("Planning and Control\n\n" \
            "***Automatic multi-step planning***\n ***Iterative review & refinement***\n " \
            "***Deep, structured analysis***", key="planning_btn", use_container_width=True):
                st.session_state.page        = "planning_and_control"
                st.session_state.chat_mode   = "planning-and-control"
                st.session_state.messages    = []
                # inside the mode-select buttons
                st.session_state.cur_hist = None      # ← we’ll create it after the first prompt

                # st.session_state.cur_hist    = _new_history_path(username)   # ← NEW
                # save_chat_history(username, [])                              # ← NEW
                st.rerun()

        with col3:
            if st.button("Human-in-the-loop\n\n ***Single-pass answers with memory***\n" \
            "***Maintain context across turns***\n ***Ask related follow-ups freely***", key="human_loop", use_container_width=True):
                st.session_state.page        = "human_in_the_loop"
                st.session_state.chat_mode   = "human_in_the_loop"
                st.session_state.messages    = []
                st.session_state.cur_hist    = None
                ensure_memory()              # ← add this single line
                st.rerun()

        # st.markdown("---")


    else:



        # Back button + title
        # Back button + centered title
        left_col, title_col, _ = st.columns(3)
        # with left_col:
        #     if st.button("⬅️ Back", key="back_btn", type="secondary"):
        #         st.session_state.clear()
        #         st.session_state.page = "mode_select"
        #         st.session_state.chat_mode = None
        #         st.session_state.messages = []
        #         st.rerun()
        with title_col:
            # pick the right header text
            if st.session_state.page == "one_shot":
                header = "One Shot"
            elif st.session_state.page == "planning_and_control":
                header = "Planning and Control"
            else:
                header = "Human-in-the-loop"
            st.markdown(
                f"<h1 style=\"font-family: 'Jersey 10', sans-serif; "
                "font-size: 50px; text-align:center; letter-spacing:2px; "
                "white-space:nowrap;"          # ← add this
                "margin:0 0 2rem 0; padding:0;\">"
                f"{header}</h1>",
                unsafe_allow_html=True
            )

            # ── split screen: conversation on left, params on right ─────────────
        _, param_col, _ = st.columns([1, 4, 1])
        with param_col:
            # st.header("Parameters")
            if st.session_state.page == "one_shot":
                c1, c2 = st.columns(2)
                with c1:
                    max_rounds = st.slider(
                    "**Max Session Duration** \n\n *The total number of messages exchanged between agents in the session*",
                    min_value=1,
                    max_value=50,
                    value=10,
                    step=1
                )
                with c2:
                    max_n_attempts = st.slider(
                        "**Max Execution Attempts** \n\n *The maximum number of failed code execution before exiting.*", min_value=1, max_value=10, value=3, step=1
                    )
            elif st.session_state.page == "planning_and_control":  # planning_and_control
                col1, col2 = st.columns(2)
                with col1:
                    max_rounds_control = st.slider(
                        "**Max Session Duration** \n\n ***\n\n *The total number of messages exchanged between agents in the session*", min_value=1, max_value=1000, value=500, step=1
                    )
                    n_plan_reviews = st.slider(
                        "**Plan Reviews** \n\n *Number of rounds the plans are reviewed*", min_value=0, max_value=5, value=1, step=1
                    )
                with col2:
                    max_n_attempts = st.slider(
                        "**Max Execution Attempts** \n\n \n\n *The maximum number of failed code execution before exiting.*", min_value=1, max_value=10, value=4, step=1
                    )
                    max_plan_steps = st.slider(
                        "**Max Plan Steps** \n\n *The maximum number of steps for the task to be solved*", min_value=1, max_value=10, value=4, step=1
                    )
                # ← NEW TEXT‐AREA FOR PLAN INSTRUCTIONS
                plan_instructions = st.text_area(
                        "**Plan Instructions**",
                        value=(
                            "Use engineer agent for the whole analysis, "
                            "and researcher at the very end in the last step to comment on results."
                        ),
                        height=120
                )
            elif st.session_state.page == "human_in_the_loop":
                c1, c2 = st.columns(2)
                with c1:
                    max_rounds = st.slider(
                    "**Max Session Duration** \n\n *The total number of messages exchanged between agents in the session*",
                    min_value=1,
                    max_value=100,
                    value=50,
                    step=1
                )
                with c2:
                    max_n_attempts = st.slider(
                        "**Max Execution Attempts** \n\n *The maximum number of failed code execution before exiting*", min_value=1, max_value=10, value=3, step=1
                    )


        # Render conversation history
        # for msg in st.session_state.messages:
        #     with st.chat_message(msg["role"]):
        #         st.markdown(msg["content"])
        # Render conversation history (supports both new dict‐messages and legacy strings/lists)
        
        # for msg in st.session_state.messages:
        #     if isinstance(msg, dict) and "role" in msg:
        #         role    = msg.get("role", "assistant")
        #         content = msg.get("content", "")
        #     else:
        #         # fallback for legacy entries (just show as assistant text)
        #         role    = "assistant"
        #         content = msg
        #     with st.chat_message(role):
        #         render_turn(content)
        # for msg in st.session_state.messages:
        #     role    = msg.get("role", "assistant")
        #     content = msg.get("content", msg)
        #     with st.chat_message(role):
        #         render_turn(content)



        # --- Agent picker pinned above the prompt ---------------------------------
            # if st.session_state.page == "one_shot":
            #     with st.container():
            #         st.radio(
            #             "Pick the agent for this turn",
            #             ["engineer", "researcher"],
            #             horizontal=True,
            #             key="one_shot_selected_agent",
            #         )
            
            # elif st.session_state.page == "human_in_the_loop":
            #     with st.container():
            #         st.radio(
            #             "Pick the agent for this turn",
            #             ["engineer", "researcher"],
            #             horizontal=True,
            #             key="human_in_the_loop_selected_agent",
            #         )
            # --- Agent picker pinned above the prompt ---------------------------------
            if st.session_state.page == "one_shot":


                with st.container():
                    agent_options = {
                        "**Engineer**\n*for code-based tasks (write & debug code)*": "engineer",
                        "**Researcher**\n*for reasoning tasks (interpret & comment)*": "researcher",
                    }


                    st.markdown("""
                    <style>
                    div[data-testid="stRadio"] label {
                        white-space: pre-line;      /* honour \n in the label */
                        margin-bottom: 12px !important;  /* extra gap between items */
                        line-height: 1.25;          /* just a bit tighter */
                    }
                    </style>
                    """, unsafe_allow_html=True)

                    choice_label = st.radio(
                        "Pick the agent for this turn",
                        list(agent_options.keys()),
                        horizontal=False
                    )
                    # store the actual key that your rest of code expects
                    st.session_state.one_shot_selected_agent = agent_options[choice_label]

            elif st.session_state.page == "human_in_the_loop":
                with st.container():
                    agent_options = {
                        "**Engineer**\n*for code-based tasks (write & debug code)*": "engineer",
                        "**Researcher**\n*for reasoning tasks (interpret & comment)*": "researcher",
                    }


                    st.markdown("""
                    <style>
                    div[data-testid="stRadio"] label {
                        white-space: pre-line;      /* honour \n in the label */
                        margin-bottom: 12px !important;  /* extra gap between items */
                        line-height: 1.25;          /* just a bit tighter */
                    }
                    </style>
                    """, unsafe_allow_html=True)

                    choice_label = st.radio(
                        "Pick the agent for this turn",
                        list(agent_options.keys()),
                        horizontal=False
                    )
                    # store the actual key that your rest of code expects
                    st.session_state.one_shot_selected_agent = agent_options[choice_label]

        # --------------------------------------------------------------------------
            

        # --- Display Full Chat History ---
        # for message in st.session_state.messages:
        #     with st.chat_message(message["role"]):
        #         content = message["content"]
        #         if isinstance(content, IPyMarkdown):
        #             st.markdown(content.data, unsafe_allow_html=True)
        #         elif isinstance(content, pd.io.formats.style.Styler):
        #             st.dataframe(content.data)
        #         elif isinstance(content, IPyImage):
        #             img = getattr(content, "data", None) or getattr(content, "_data", None)
        #             st.image(img, use_container_width=True)
        #         elif isinstance(content, PILImage):
        #             st.image(content, use_container_width=True)
        #         elif isinstance(content, (dict, list)):
        #             pretty = json.dumps(content, indent=2, ensure_ascii=False)
        #             st.code(pretty, language="json")

        #         elif isinstance(content, pd.DataFrame):
        #             st.dataframe(content)

        #         else:
        #             st.markdown(content)
        _logo_path = os.path.join(os.path.dirname(__file__), "..", "Robot-MS-Aqua.png")
        with open(_logo_path, "rb") as _f:
            _logo_b64 = base64.b64encode(_f.read()).decode("utf-8")

        # the “avatar” URI we’ll pass to st.chat_message
        ASSISTANT_AVATAR = f"data:image/png;base64,{_logo_b64}"

        for msg in st.session_state.messages:
            role    = msg.get("role", "assistant")
            avatar  = ASSISTANT_AVATAR if role=="assistant" else None
            with st.chat_message(role, avatar=avatar):
                render_content(msg.get("content", msg))



        user_input = st.chat_input("Type your task or question here…")

        # … everything above stays the same until here …
        if user_input:
            # ── create chat-history file on very first prompt ────────────────────────────
            # if st.session_state.cur_hist is None:          # first message this session
            #     # include the current page ("one_shot" or "planning_and_control") in the filename
            #     page = st.session_state.page  # "one_shot" or "planning_and_control"
            #     slug = _slugify(user_input)
            #     hist_dir = os.path.join(os.path.dirname(__file__), "history")
            #     fn = f"{page}_{slug}_chat_history.json"

            #     path    = os.path.join(hist_dir, fn)

            #     # avoid collisions: add _2, _3 … if necessary
            #     counter = 2
            #     base, ext = os.path.splitext(path)
            #     while os.path.exists(path):
            #         path = f"{base}_{counter}{ext}"
            #         counter += 1

                # st.session_state.cur_hist = path          # remember it
                # save_chat_history(username, [])           # touch the file once


            # 1) Record & echo the user message
            st.session_state.messages.append({"role": "user", "content": user_input})
            # save_chat_history(st.session_state.get("username"), st.session_state.messages)

            # NEW – only if we are in human‑in‑the‑loop
            add_to_memory("user", user_input)

            with st.chat_message("user"):
                st.markdown(user_input)

            # st.session_state.memory.add_user_message(user_input)
            # context = retrieve_context(user_input)

            # 2) Prepare your chosen models
            engineer_model   = st.session_state.agent_models["engineer"]
            researcher_model = st.session_state.agent_models["researcher"]

            # --- Model/provider selection logic for engineer and researcher ---

            def get_config_for_model(model, provider_oai, provider_anthropic, provider_gemini):
                model_lower = model.lower()
                # print("MODEL LOWER:", model_lower)
                if any(x in model_lower for x in ["gpt", "o3", "o4"]):
                    # print("OPENAI MODEL SELECTED")
                    # print("OPENAI API KEY:", os.environ["OPENAI_API_KEY"])
                    return {"model": model, "api_key": os.environ["OPENAI_API_KEY"], "api_type": "openai"}
                elif "claude" in model_lower:
                    # print("ANTHROPIC MODEL SELECTED")
                    return {"model": model, "api_key": os.environ["ANTHROPIC_API_KEY"], "api_type": "anthropic"}
                elif "gemini" in model_lower:
                    return {"model": model, "api_key": os.environ["GEMINI_API_KEY"], "api_type": "google"}
                else:
                    return {"model": model, "api_key": "", "api_type": "openai"}

            engineer_config   = get_config_for_model(engineer_model, provider_oai, provider_anthropic, provider_gemini)
            researcher_config = get_config_for_model(researcher_model, provider_oai, provider_anthropic, provider_gemini)

    

            # # 3) Stream only internal logs in one assistant bubble
            # with st.chat_message("assistant"):
            #     exp = st.expander("🧠 Internal reasoning (optional)", expanded=False)
            #     handler = StreamHandler(exp)

            # _logo_path = os.path.join(os.path.dirname(__file__), "cmbagent", "Robot-MS-Aqua.png")
            # with open(_logo_path, "rb") as _f:
            #     _logo_b64 = base64.b64encode(_f.read()).decode("utf-8")

            # # the “avatar” URI we’ll pass to st.chat_message
            # ASSISTANT_AVATAR = f"data:image/png;base64,{_logo_b64}"


            # 3) Stream only internal logs in one assistant bubble  ← still true :)
            # with st.chat_message("assistant") as assistant_message:
            with st.chat_message("assistant", avatar=ASSISTANT_AVATAR) as assistant_message:

                    # ── live status banner ─────────────────────────────────────────────
                import time
                start_ts = time.perf_counter()
                status_placeholder = st.empty()
                status_placeholder.markdown("**Agent running…**")

                output_placeholder = st.empty()             # lives inside the chat bubble
                handler = StreamHandler(output_placeholder) # stream tokens right here

                try:
                    # Everything printed (stdout) goes into the expander
                    with redirect_stdout(handler):
                        if st.session_state.page == "one_shot":
                            results = one_shot(
                                user_input,
                                max_rounds = max_rounds,
                                max_n_attempts = max_n_attempts,
                                engineer_model=engineer_model,
                                researcher_model=researcher_model,
                                initial_agent=st.session_state.one_shot_selected_agent,
                            )

                            # image_dir = results['final_context']['work_dir']+'/data'

                            # # List any image files in the output directory
                            # if os.path.exists(image_dir):
                            #     image_files = glob.glob(os.path.join(image_dir, '*.png')) + \
                            #                 glob.glob(os.path.join(image_dir, '*.jpg')) + \
                            #                 glob.glob(os.path.join(image_dir, '*.jpeg'))
                            #     if image_files:
                            #         st.markdown("### Generated Images:")
                            #         for img_path in image_files:
                            #             st.image(img_path)

                        elif st.session_state.page == "planning_and_control":
                            results = planning_and_control(
                                user_input,
                                max_rounds_control = 500,
                                n_plan_reviews = n_plan_reviews,
                                max_n_attempts = max_n_attempts,
                                max_plan_steps= max_plan_steps,
                                engineer_model=engineer_model,
                                researcher_model=researcher_model,
                                plan_instructions=plan_instructions,
                            )

                        elif st.session_state.page == "human_in_the_loop":
                            context = memory_as_text()                    # may be empty
                            prompt_for_agent = f"{context}\n\n[Human]: {user_input}".lstrip()

                            results = one_shot(
                                prompt_for_agent,
                                max_rounds       = max_rounds,
                                max_n_attempts   = max_n_attempts,
                                engineer_model   = engineer_model,
                                researcher_model = researcher_model,
                                initial_agent    = st.session_state.one_shot_selected_agent,
                            )


                        # print(">>>>> RESULTS KEYS:", results.keys())
                        # print("CHAT_HISTORY CONTENT:", results["chat_history"])

                except Exception as e:
                    # 1) print to Streamlit
                    handler.write("### ❌  An exception occurred:\n")
                    handler.write(f"`{e.__class__.__name__}: {e}`\n\n")
                    handler.write("```text\n" + traceback.format_exc() + "```\n")

                    # 2) still keep the app alive
                    results = {"chat_history": []}


    # … right after your redirect_stdout(...) block …

            from IPython.display import Image as IPyImage
            # from PIL.Image import Image as PILImage


            history        = results.get("chat_history", [])
            tool_responses = results.get("tool_responses", [])

    

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

            for turn in history:
                name    = turn.get("name", "")
                content = turn.get("content")
                # (1) only formatter turns
                # if not name.endswith("_response_formatter") or content is None:
                #     continue
                
                # >>> ADD THIS GUARD <<<
                # if content is None:
                #     continue               # ignore empty turns entirely

                if name == "" or content is None:
                    continue

                # (2) a simple dedupe key
                key = f"{name}::{str(content)[:200]}"
                if key in seen:
                    continue
                seen.add(key)

                # (3) unwrap into "displayable"
                if isinstance(content, IPyMarkdown):
                    disp = content.data or ""
                elif isinstance(content, pd.io.formats.style.Styler):
                    disp = content.data   # a DataFrame
                elif isinstance(content, pd.DataFrame):
                    disp = content
                elif isinstance(content, (IPyImage, PILImage)):
                    # extract the raw image bytes
                    disp = getattr(content, "data", getattr(content, "_data", None))
                else:
                    disp = str(content)

                to_display.append((name, disp))


            # --- after your history‐scan that builds to_display: ---
            # 2) also scan tool_responses (just in case your agent returned an image there)
            for tr in tool_responses:
                c = tr.get("content")
                if isinstance(c, (IPyImage, PILImage)):
                    to_display.append(("control", c))
            
            # --- NEW: sweep ./output/data for images -------------------------------
            # image_dir = os.path.join(os.path.dirname(__file__), "output", "data")
            # image_dir = results['final_context']['work_dir']+'/data'
            image_dir = Path(results['final_context']['work_dir'])/ 'data'   # ← preferred
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

            # ── 1.  clear the cursor that was blinking during streaming
            output_placeholder.markdown(handler.text)      # ← keeps the text!

            # ── 2.  show only the image(s) and markdown you just collected
            for who, content in to_display:
                if who == "file":                 # <- PIL image from ./output/data
                    st.image(content, use_container_width=True)
                else:
                    pass
                # elif who == "researcher":         # <- markdown file you loaded
                #     st.markdown(content, unsafe_allow_html=True)



            # ── finish up: save reasoning + elapsed time ─────────────────────────
            elapsed = time.perf_counter() - start_ts
            status_placeholder.markdown(f"**Complete thinking – {elapsed:.2f}s**")

            reasoning_text = handler.text          # everything StreamHandler captured
            full_content = [("assistant", reasoning_text)] + to_display  # ← NEW

            st.session_state.messages.append({
                "role": "assistant",
                "content": to_display,
                "reasoning": reasoning_text,       # ← NEW FIELD
                "elapsed":  f"{elapsed:.2f}s",     # (optional) useful in raw JSON
            })

            # save_chat_history(username, st.session_state.messages)

            # ── sync rolling memory with *all* assistant replies from this turn ──
            # ── after you built to_display  ────────────────────────────────────────
            if st.session_state.page == "human_in_the_loop":
                ensure_memory()
                for who, disp in to_display:
                    # skip files / tool images – keep every agent’s visible reply
                    if who.lower() not in {"file", "control"}:
                        txt = _text_from_content(disp)
                        # print(f"DISP: {txt}")
                        if txt:
                            add_to_memory("assistant", txt)
    # ───────────────────────────────────────────────────────────────────────

            # ─────────────────────────────────────────────────────────────────────




            # 3) render everything in *the existing* assistant bubble
            # if to_display:
            #         # <-- note the placeholder
            #     for who, content in to_display:
            #         st.markdown(f"**Message from {who}:**")
            #         if isinstance(content, IPyMarkdown):
            #             st.markdown(content.data, unsafe_allow_html=True)
            #         elif isinstance(content, pd.io.formats.style.Styler):
            #             st.dataframe(content.data)
            #         elif isinstance(content, IPyImage):
            #             img = getattr(content, "data", None) or getattr(content, "_data", None)
            #             st.image(img, use_container_width=True)
            #         elif isinstance(content, PILImage):
            #             st.image(content, use_container_width=True)
            #         elif isinstance(content, (dict, list)):
            #             st.json(content)
                    
            #         elif isinstance(content, pd.DataFrame):
            #             st.dataframe(content)

            #         else:
            #             st.markdown(content)
                
                

if __name__ == "__main__":
    main()
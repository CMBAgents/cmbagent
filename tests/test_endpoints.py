"""Offline tests of the OpenAI-compatible endpoint registry in cmbagent_lg.llms (no network, no keys).

    python -m pytest -q tests/test_endpoints.py
"""
import json

import pytest

from cmbagent_lg import llms


@pytest.fixture
def registry(tmp_path, monkeypatch):
    bare = tmp_path / "bare.key"
    bare.write_text("sk-bare\n")
    env = tmp_path / "endpoint.env"
    env.write_text("# a comment\nOTHER=x\nAPI_KEY='sk-env-file'\n")
    single = tmp_path / "single.env"
    single.write_text("TOKEN=sk-single\n")
    reg = {
        "qwen-local": {"base_url": "http://127.0.0.1:30000/v1", "key_file": str(bare), "max_tokens": 4096},
        "kimi-k3": {"base_url": "https://llm.example.org/v1", "model": "moonshotai/Kimi-K3",
                    "key_file": str(env), "key_var": "API_KEY", "structured_output": "function_calling"},
        "single": {"base_url": "https://one.example.org/v1", "key_file": str(single), "temperature": 0.7},
        "from-env": {"base_url": "https://two.example.org/v1", "api_key_env": "MY_ENDPOINT_KEY"},
        "no-key": {"base_url": "http://localhost:8000/v1"},
    }
    path = tmp_path / "endpoints.json"
    path.write_text(json.dumps(reg))
    monkeypatch.setenv("LLM_ENDPOINTS", str(path))
    monkeypatch.setenv("MY_ENDPOINT_KEY", "sk-from-env")
    llms.chat_model.cache_clear()
    yield path
    llms.chat_model.cache_clear()


def test_routing(registry):
    assert llms._provider("qwen-local") == "compat"
    assert llms._provider("kimi-k3") == "compat"
    # names outside the registry keep the name-based routing
    assert llms._provider("gpt-5") == "openai"
    assert llms._provider("claude-sonnet-5-5") == "anthropic"
    assert llms._provider("gemini-3.1-flash-lite") == "google"
    assert llms._provider("qwen-not-listed") == "google"


def test_no_registry(monkeypatch):
    monkeypatch.delenv("LLM_ENDPOINTS", raising=False)
    assert llms.endpoints() == {}
    assert llms._provider("qwen-local") == "google"


def test_compat_model_settings(registry):
    m = llms.chat_model("qwen-local", "coder")
    assert m.model_name == "qwen-local"
    assert m.openai_api_base == "http://127.0.0.1:30000/v1"
    assert m.openai_api_key.get_secret_value() == "sk-bare"
    assert m.max_tokens == 4096
    assert m.temperature == 0.2                      # the coder role's temperature
    k = llms.chat_model("kimi-k3", "generator")
    assert k.model_name == "moonshotai/Kimi-K3"      # the server's own id
    assert k.openai_api_key.get_secret_value() == "sk-env-file"
    s = llms.chat_model("single", "formatter")
    assert s.openai_api_key.get_secret_value() == "sk-single"
    assert s.temperature == 0.7                      # the entry overrides the role
    assert llms.chat_model("from-env").openai_api_key.get_secret_value() == "sk-from-env"
    assert llms.chat_model("no-key").openai_api_key.get_secret_value() == "EMPTY"


def test_structured_output_method(registry):
    from pydantic import BaseModel

    class Verdict(BaseModel):
        ok: bool

    def bound_kwargs(runnable):
        runnable = getattr(runnable, "first_attempt", runnable)    # inside the structured retry wrapper
        first = runnable.first if hasattr(runnable, "first") else runnable.steps[0]
        return getattr(first, "kwargs", {})

    js = bound_kwargs(llms.chat_model("qwen-local").with_structured_output(Verdict))
    assert "response_format" in js and "tools" not in js           # JSON-schema response format
    fc = bound_kwargs(llms.chat_model("kimi-k3").with_structured_output(Verdict))
    assert "tools" in fc and "response_format" not in fc           # forced function call
    # an explicit method still wins
    exp = bound_kwargs(llms.chat_model("kimi-k3").with_structured_output(Verdict, method="json_schema"))
    assert "response_format" in exp and "tools" not in exp


def test_bad_registry(tmp_path, monkeypatch):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"x": {"model": "y"}}))
    monkeypatch.setenv("LLM_ENDPOINTS", str(p))
    with pytest.raises(ValueError, match="base_url"):
        llms.endpoints()


def test_missing_env_key(registry, monkeypatch):
    monkeypatch.delenv("MY_ENDPOINT_KEY")
    llms.chat_model.cache_clear()
    with pytest.raises(RuntimeError, match="MY_ENDPOINT_KEY"):
        llms.chat_model("from-env")

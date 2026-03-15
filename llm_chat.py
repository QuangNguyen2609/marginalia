"""
Multi-provider LLM integration for Reader3 chat sidebar.
Uses LangChain's init_chat_model to support OpenAI, Azure OpenAI, Anthropic, Ollama, and more.
"""

import json
import os
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import List, Dict, Optional

from langchain.chat_models import init_chat_model
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage


CONFIG_PATH = "config.json"
BOOKS_DIR = "library"

# Providers that require an API key entered by the user
_PROVIDERS_REQUIRE_KEY = {
    "openai", "azure_openai", "anthropic", "groq", "mistralai", "deepseek",
    "xai", "cohere", "fireworks", "together", "nvidia", "google_genai",
    "openrouter", "perplexity", "upstage",
}

# Providers that use cloud/IAM credentials from the environment (no API key field)
_CREDENTIAL_PROVIDERS = {
    "google_vertexai", "google_anthropic_vertex",
    "bedrock", "bedrock_converse", "anthropic_bedrock", "ibm",
}

# Providers that use a non-standard kwarg name for the API key
_API_KEY_KWARG = {
    "google_genai": "google_api_key",
}


@dataclass
class LLMConfig:
    provider: str = ""       # e.g. openai, azure_openai, anthropic, ollama, groq ...
    model: str = ""          # model name or Azure deployment name
    api_key: str = ""        # omit for local providers like ollama
    endpoint: str = ""       # azure_openai: Azure endpoint; others: custom base URL
    api_version: str = "2024-02-15-preview"  # for azure_openai

    @property
    def is_configured(self) -> bool:
        if not (self.provider and self.model):
            return False
        if self.provider in _PROVIDERS_REQUIRE_KEY and not self.api_key:
            return False
        if self.provider == "azure_openai" and not self.endpoint:
            return False
        return True

    @property
    def needs_api_key(self) -> bool:
        return self.provider not in _CREDENTIAL_PROVIDERS


def load_config() -> LLMConfig:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
            return LLMConfig(**{k: v for k, v in data.items() if k in LLMConfig.__dataclass_fields__})
        except Exception:
            pass
    return LLMConfig()


def save_config(config: LLMConfig):
    with open(CONFIG_PATH, "w") as f:
        json.dump(asdict(config), f, indent=2)


# ===== Conversations =====

def _conv_dir(book_id: str) -> str:
    return os.path.join(BOOKS_DIR, book_id, "chat_data")


def _conv_path(book_id: str, conv_id: str) -> str:
    safe_id = "".join(c for c in conv_id if c.isalnum() or c in "_-")
    return os.path.join(_conv_dir(book_id), f"{safe_id}.json")


def _derive_title(messages: List[Dict]) -> str:
    for msg in messages:
        if msg.get("role") == "user":
            return msg["content"][:60]
    return "New conversation"


def _migrate_legacy(book_id: str):
    """Migrate old chat_history.json into the chat_data/ folder (runs once)."""
    legacy_path = os.path.join(BOOKS_DIR, book_id, "chat_history.json")
    conv_dir = _conv_dir(book_id)
    if not os.path.exists(legacy_path) or os.path.exists(conv_dir):
        return
    try:
        with open(legacy_path, "r") as f:
            messages = json.load(f)
        if not messages:
            return
        os.makedirs(conv_dir, exist_ok=True)
        ts = os.path.getmtime(legacy_path)
        conv_id = f"conv_{int(ts * 1000)}"
        conv_data = {
            "id": conv_id,
            "created_at": datetime.fromtimestamp(ts).isoformat(),
            "title": _derive_title(messages),
            "messages": messages,
        }
        with open(_conv_path(book_id, conv_id), "w") as f:
            json.dump(conv_data, f, indent=2)
    except Exception:
        pass


def list_conversations(book_id: str) -> List[Dict]:
    _migrate_legacy(book_id)
    conv_dir = _conv_dir(book_id)
    if not os.path.exists(conv_dir):
        return []
    convs = []
    for fname in os.listdir(conv_dir):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(conv_dir, fname)
        try:
            with open(fpath, "r") as f:
                data = json.load(f)
            convs.append({
                "id": data["id"],
                "title": data.get("title", "Conversation"),
                "created_at": data.get("created_at", ""),
                "message_count": len([m for m in data.get("messages", []) if m.get("role") == "user"]),
            })
        except Exception:
            pass
    convs.sort(key=lambda x: x["created_at"], reverse=True)
    return convs


def create_conversation(book_id: str) -> str:
    conv_id = f"conv_{int(time.time() * 1000)}"
    conv_dir = _conv_dir(book_id)
    os.makedirs(conv_dir, exist_ok=True)
    conv_data = {
        "id": conv_id,
        "created_at": datetime.now().isoformat(),
        "title": "New conversation",
        "messages": [],
    }
    with open(_conv_path(book_id, conv_id), "w") as f:
        json.dump(conv_data, f, indent=2)
    return conv_id


def load_chat_history(book_id: str, conv_id: Optional[str] = None) -> List[Dict]:
    if conv_id:
        path = _conv_path(book_id, conv_id)
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                return data.get("messages", [])
            except Exception:
                pass
        return []
    # Legacy fallback
    path = os.path.join(BOOKS_DIR, book_id, "chat_history.json")
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_chat_history(book_id: str, history: List[Dict], conv_id: Optional[str] = None):
    if conv_id:
        path = _conv_path(book_id, conv_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        title = _derive_title(history) if history else "New conversation"
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except Exception:
            data = {"id": conv_id, "created_at": datetime.now().isoformat()}
        data["messages"] = history
        data["title"] = title
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return
    # Legacy
    path = os.path.join(BOOKS_DIR, book_id, "chat_history.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(history, f, indent=2)


def clear_chat_history(book_id: str, conv_id: Optional[str] = None):
    if conv_id:
        path = _conv_path(book_id, conv_id)
        if os.path.exists(path):
            os.remove(path)
        return
    # Legacy
    path = os.path.join(BOOKS_DIR, book_id, "chat_history.json")
    if os.path.exists(path):
        os.remove(path)


def _build_llm(config: LLMConfig):
    kwargs: Dict = {"temperature": 0.7, "max_tokens": 1024}
    if config.api_key and config.provider not in _CREDENTIAL_PROVIDERS:
        kwarg = _API_KEY_KWARG.get(config.provider, "api_key")
        kwargs[kwarg] = config.api_key
    if config.endpoint:
        if config.provider == "azure_openai":
            kwargs["azure_endpoint"] = config.endpoint
            kwargs["api_version"] = config.api_version
        else:
            kwargs["base_url"] = config.endpoint
    return init_chat_model(config.model, model_provider=config.provider, **kwargs)


def _to_lc_messages(messages: List[Dict[str, str]]):
    lc = []
    for msg in messages:
        role, content = msg["role"], msg["content"]
        if role == "system":
            lc.append(SystemMessage(content=content))
        elif role == "user":
            lc.append(HumanMessage(content=content))
        elif role == "assistant":
            lc.append(AIMessage(content=content))
    return lc


async def chat_completion(config: LLMConfig, messages: List[Dict[str, str]]) -> str:
    """Call LLM via LangChain init_chat_model (non-streaming)."""
    if not config.is_configured:
        raise ValueError("LLM is not configured. Click the gear icon to set up.")
    llm = _build_llm(config)
    response = await llm.ainvoke(_to_lc_messages(messages))
    return response.content


async def chat_completion_stream(config: LLMConfig, messages: List[Dict[str, str]]):
    """Stream LLM response token by token via LangChain init_chat_model."""
    if not config.is_configured:
        raise ValueError("LLM is not configured. Click the gear icon to set up.")
    llm = _build_llm(config)
    async for chunk in llm.astream(_to_lc_messages(messages)):
        if chunk.content:
            yield chunk.content

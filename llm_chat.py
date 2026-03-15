"""
Azure OpenAI integration for Reader3 chat sidebar.
Uses httpx directly for lightweight, async HTTP calls.
"""

import json
import os
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional

import httpx


CONFIG_PATH = "config.json"


@dataclass
class AzureConfig:
    endpoint: str = ""
    api_key: str = ""
    deployment_name: str = ""
    api_version: str = "2024-02-15-preview"

    @property
    def is_configured(self) -> bool:
        return bool(self.endpoint and self.api_key and self.deployment_name)

    @property
    def chat_url(self) -> str:
        base = self.endpoint.rstrip("/")
        return f"{base}/openai/deployments/{self.deployment_name}/chat/completions?api-version={self.api_version}"


def load_config() -> AzureConfig:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
            return AzureConfig(**{k: v for k, v in data.items() if k in AzureConfig.__dataclass_fields__})
        except Exception:
            pass
    return AzureConfig()


def save_config(config: AzureConfig):
    with open(CONFIG_PATH, "w") as f:
        json.dump(asdict(config), f, indent=2)


def load_chat_history(book_id: str) -> List[Dict]:
    path = os.path.join(book_id, "chat_history.json")
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_chat_history(book_id: str, history: List[Dict]):
    path = os.path.join(book_id, "chat_history.json")
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(history, f, indent=2)


def clear_chat_history(book_id: str):
    path = os.path.join(book_id, "chat_history.json")
    if os.path.exists(path):
        os.remove(path)


async def chat_completion(config: AzureConfig, messages: List[Dict[str, str]]) -> str:
    """Call Azure OpenAI chat completion API."""
    if not config.is_configured:
        raise ValueError("Azure OpenAI is not configured. Open settings to configure.")

    headers = {
        "Content-Type": "application/json",
        "api-key": config.api_key,
    }

    payload = {
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1024,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(config.chat_url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]

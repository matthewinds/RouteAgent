"""Local API configuration; no prompts, sampling settings or tool logic live here."""
import os
from pathlib import Path

import googlemaps
import httpx
import requests
from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT.parents[1] / ".env", override=False)


def get_llm_client():
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("Set OPENROUTER_API_KEY in the project .env file.")
    # Ignore the Windows Internet Options proxy here. On this machine its HTTPS
    # entry points at a plain HTTP CONNECT port, which makes httpx fail during
    # the TLS handshake before the request reaches OpenRouter.
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=key,
        http_client=httpx.Client(trust_env=False),
    )


def model_id(model):
    # Only translate provider names; never substitute a different model family.
    aliases = {
        "gpt-35-turbo": "openai/gpt-3.5-turbo",
        "gpt-3.5-turbo": "openai/gpt-3.5-turbo",
        "gpt-4o": "openai/gpt-4o",
        "gpt-4": "openai/gpt-4",
    }
    return aliases.get(model, model)


def get_maps_client():
    key = os.getenv("GOOGLE_MAP_API_KEY")
    # Permit --help and offline imports without making network calls.
    if not key:
        return None
    session = requests.Session()
    session.trust_env = False
    return googlemaps.Client(key=key, requests_session=session)


def validate_credentials():
    missing = [name for name in ("OPENROUTER_API_KEY", "GOOGLE_MAP_API_KEY")
               if not os.getenv(name)]
    if missing:
        raise RuntimeError("Set these values in the project .env file: " + ", ".join(missing))


def json_default(value):
    """Serialize SDK objects only at output time, preserving in-memory prompts."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


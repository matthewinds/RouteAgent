"""Verify configured services with a few real requests; never print credentials/URLs."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime_config import get_llm_client, get_maps_client, model_id, validate_credentials


def main():
    validate_credentials()
    import googlemaps.exceptions
    import openai
    client = get_llm_client()
    try:
        models = {m.id for m in client.models.list().data}
    except openai.APIError as error:
        print(f"OpenRouter: {type(error).__name__}; HTTP {getattr(error, 'status_code', 'N/A')}")
        return 1
    wanted = {model_id(os.getenv("MAPAGENT_MODEL", "gpt-3.5-turbo")),
              model_id(os.getenv("MAPAGENT_MAP_MODEL", "gpt-3.5-turbo"))}
    missing = wanted - models
    print("Model catalog: " + json.dumps({m: m in models for m in sorted(wanted)}))
    if missing:
        print("Configured model is unavailable; no alternative model was selected.")
        return 1
    maps = get_maps_client()
    checks = [
        ("Geocoding", lambda: maps.geocode("Eiffel Tower, Paris")),
        ("Places Text Search (Legacy)", lambda: maps.places("Eiffel Tower, Paris")),
        ("Directions (Legacy)", lambda: maps.directions("Eiffel Tower, Paris", "Louvre Museum, Paris", mode="walking")),
    ]
    failed = False
    for name, operation in checks:
        try:
            value = operation()
            if not value or (isinstance(value, dict) and not value.get("results")):
                print(f"{name}: empty result")
                failed = True
                continue
            print(f"{name}: OK")
            if name.startswith("Places"):
                details = maps.place(value["results"][0]["place_id"])
                if not details.get("result"):
                    raise RuntimeError("Empty details")
                print("Place Details (Legacy): OK")
        except googlemaps.exceptions.ApiError as error:
            # Google's message is useful for enablement/billing; redact configured keys.
            message = str(error)
            for key in (os.getenv("GOOGLE_MAP_API_KEY"), os.getenv("OPENROUTER_API_KEY")):
                if key:
                    message = message.replace(key, "[REDACTED]")
            print(f"{name}: {message}")
            failed = True
        except Exception as error:
            print(f"{name}: {type(error).__name__}")
            failed = True
    client.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

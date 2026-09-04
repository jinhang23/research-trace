import hashlib
import json
from typing import Any

def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def fingerprint(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()

def visible_content(value: Any) -> Any:
    """Apply the existing visible-content boundary to structured external traces.

    MLflow also JSON-encodes span attributes, so nested JSON strings need the
    same treatment. Ordinary prose mentioning reasoning is not a hidden block.
    """
    hidden = {"thinking", "redacted_thinking", "reasoning", "reasoning_content",
              "reasoning_details", "chain_of_thought", "encrypted_content"}
    if isinstance(value, dict):
        if str(value.get("type", "")).lower() in hidden:
            return {"type": "omitted", "reason": "hidden_reasoning"}
        return {k: visible_content(v) for k, v in value.items()
                if str(k).lower() not in hidden}
    if isinstance(value, list):
        return [visible_content(item) for item in value]
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            return stable_json(visible_content(json.loads(value)))
        except (ValueError, RecursionError):
            return {"type": "unparsed_json", "sha256": fingerprint(value), "length": len(value)}
    return value

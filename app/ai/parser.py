"""Recover complete JSON actions from local model output."""
import ast
import json
import re


def parse_llm_json(raw):
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.IGNORECASE)
    candidates = [text, re.sub(r",\s*([}\]])", r"\1", text)]
    decoder = json.JSONDecoder()
    for candidate in candidates:
        for index, character in enumerate(candidate):
            if character not in "[{":
                continue
            try:
                value, _ = decoder.raw_decode(candidate[index:])
            except ValueError:
                continue
            if isinstance(value, list):
                return {"actions": value}
            if isinstance(value, dict) and ("actions" in value or "corrections" in value):
                return value
        try:
            value = ast.literal_eval(candidate)
            if isinstance(value, dict):
                return value
            if isinstance(value, list):
                return {"actions": value}
        except (ValueError, SyntaxError, RecursionError):
            pass
    # Keep complete action objects from truncated output; do not invent missing paths.
    actions = []
    for fragment in re.findall(r'\{[^{}]*"src_rel"\s*:[^{}]*\}', text):
        try:
            action = json.loads(re.sub(r",\s*([}\]])", r"\1", fragment))
            if all(key in action for key in ("type", "src_rel", "dst_rel")):
                actions.append(action)
        except ValueError:
            pass
    return {"actions": actions} if actions else None


def validate_plan_dict(value):
    if not isinstance(value, dict) or not isinstance(value.get("actions"), list):
        return False
    return all(
        isinstance(action, dict) and all(
            isinstance(action.get(key), str) for key in ("type", "src_rel", "dst_rel")
        )
        for action in value["actions"]
    )

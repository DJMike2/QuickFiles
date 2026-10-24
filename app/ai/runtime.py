"""Reuse one model server across planning batches and modes."""
from app.ai.llm_server import ContextTooLargeError, LLMServer, LLMServerError
from app.ai.parser import parse_llm_json, validate_plan_dict
from app.ai.prompts import SYSTEM_PROMPT, build_planner_prompt, build_verifier_prompt


class AIRuntime:
    def __init__(self, binary_manager, model_manager):
        self.binary_manager = binary_manager
        self.model_manager = model_manager
        self._server = None
        self._mode = None

    @property
    def available(self):
        return self.binary_manager.is_available and self.model_manager.is_downloaded

    @property
    def backend_label(self):
        if self._server and self._server.is_running:
            return f"Local AI ({self._server.backend.upper()})"
        return "Local AI ready" if self.available else "Local AI setup required"

    def _generate(self, mode, system, user, status_cb=None, cancelled=None, max_tokens=None):
        if self._mode != mode or self._server is None or not self._server.is_running:
            self.shutdown()
            profile = self.model_manager.profile(mode)
            self._server = LLMServer(self.binary_manager, self.model_manager.model_path, profile)
            self._mode = mode
            self._server.start(status_cb, cancelled)
        profile = self.model_manager.profile(mode)
        return self._server.chat(system, user, max_tokens or profile["max_tokens"], profile["temperature"])

    def plan_batch(self, mode, root, batch, folders, context, status_cb=None, cancelled=None):
        if cancelled and cancelled():
            raise InterruptedError("Planning stopped")
        profile = self.model_manager.profile(mode)
        prompt = build_planner_prompt(root, batch, folders, context)
        estimated = len((SYSTEM_PROMPT + prompt).encode("utf-8")) / 3 + profile["max_tokens"]

        def split():
            if len(batch) <= 1:
                raise LLMServerError("This file's planning context is too large")
            middle = len(batch) // 2
            actions = []
            for half in (batch[:middle], batch[middle:]):
                result = self.plan_batch(mode, root, half, folders, context, status_cb, cancelled)
                actions.extend(result["actions"])
            return {"actions": actions}

        if estimated > profile["context_tokens"] * 0.7 and len(batch) > 1:
            return split()
        try:
            raw = self._generate(mode, SYSTEM_PROMPT, prompt, status_cb, cancelled)
        except ContextTooLargeError:
            return split()
        parsed = parse_llm_json(raw)
        if not validate_plan_dict(parsed):
            raise LLMServerError("The local model returned an invalid plan")
        return parsed

    def verify_plan(self, mode, actions, status_cb=None, cancelled=None):
        # Verify in the same bounded batches rather than sending an entire large scan.
        remove = set()
        batch_size = self.model_manager.profile(mode)["batch_size"]
        for offset in range(0, len(actions), batch_size):
            if cancelled and cancelled():
                raise InterruptedError("Verification stopped")
            raw = self._generate(
                mode, "You review file plans. Return JSON only.",
                build_verifier_prompt([a.to_dict() for a in actions[offset:offset + batch_size]]),
                status_cb, cancelled, max_tokens=256,
            )
            parsed = parse_llm_json(raw)
            if not parsed or not isinstance(parsed.get("corrections"), list):
                raise LLMServerError("The local model returned an invalid verification response")
            remove.update(str(item["action_id"]) for item in parsed["corrections"]
                          if isinstance(item, dict) and item.get("remove") is True and "action_id" in item)
        return remove

    def shutdown(self):
        if self._server:
            self._server.stop()
        self._server = None
        self._mode = None

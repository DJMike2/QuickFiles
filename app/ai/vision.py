"""Describe photos, videos and scanned pages with the local vision model, once per file."""
import hashlib
import json
import logging
import re

from app.ai import media
from app.ai.llm_server import LLMServer, LLMServerError
from app.ai.model_runtime import ModelRuntime
from app.ai.prompts import VISION_PROMPTS

log = logging.getLogger(__name__)
# Short descriptions keep indexing fast; scans get more room for their key words.
MAX_TOKENS = {'image': 90, 'video': 90, 'scan': 120}
LABELS = {'image': 'Image', 'video': 'Video', 'scan': 'Scanned page'}


class VisionRuntime(ModelRuntime):
    def __init__(self, binary_manager, model_manager, runtime):
        super().__init__()
        self.binary_manager = binary_manager
        self.model_manager = model_manager
        # The text model shares the GPU; it is unloaded while images are described.
        self.runtime = runtime

    def _new_server(self):
        manager = self.model_manager
        return LLMServer(self.binary_manager, manager.vision_model_path,
                         {**manager.runtime_settings, 'context_tokens': 4096},
                         mmproj=manager.vision_mmproj_path)

    def _before_start(self):
        # Finish/cancel a pending text load before reserving the GPU for vision.
        self.runtime.shutdown()

    def prestart(self, cancelled=None):
        if self.available:
            return self._load_async(cancelled=cancelled)

    @property
    def available(self):
        return self.binary_manager.is_available and self.model_manager.vision_downloaded

    @property
    def fingerprint(self):
        """Changes with the model or the prompts, so stored descriptions are rewritten once."""
        source = json.dumps([self.model_manager.vision_model_info['sha256'], VISION_PROMPTS, MAX_TOKENS], sort_keys=True)
        return hashlib.sha256(source.encode()).hexdigest()[:16]

    def describe(self, path, kind, status_cb=None, cancelled=None):
        """One line about what the file shows, labelled by kind, or "" when it cannot be decoded."""
        frame = media.FRAMES[kind](path)
        if frame is None:
            return ''
        if cancelled and cancelled():
            raise InterruptedError('Search stopped')
        try:
            server = self._ensure_started(status_cb, cancelled)
            raw = server.chat(VISION_PROMPTS[kind], 'Describe it.', MAX_TOKENS[kind], 0,
                                    images=[frame], allow_truncated=True)
        except (LLMServerError, InterruptedError):
            self.shutdown()
            raise
        text = re.sub(r'\s+', ' ', raw).strip()
        return f'{LABELS[kind]}: {text}' if text else ''

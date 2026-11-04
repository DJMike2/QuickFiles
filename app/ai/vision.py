"""Describe photos, videos and scanned pages through the shared local model."""
import hashlib
import json
import logging
import re

from app.ai import media
from app.ai.content import pdf_layout
from app.ai.prompts import PDF_TEXT_PROMPT, VISION_PROMPTS

log = logging.getLogger(__name__)

# Short descriptions keep indexing fast; scans get more room for their key words.
MAX_TOKENS = {'image': 90, 'video': 90, 'scan': 120}
LABELS = {'image': 'Image', 'video': 'Video', 'scan': 'Scanned page'}


class VisionDescriptions:
    def __init__(self, runtime):
        self.runtime = runtime

    @property
    def slots(self):
        return self.runtime.slots

    @property
    def available(self):
        return self.runtime.available

    @property
    def fingerprint(self):
        manager = self.runtime.model_manager
        source = json.dumps([manager.model_info['sha256'], manager.model_info['mmproj']['sha256'],
                             manager.runtime_settings['image_max_tokens'], VISION_PROMPTS, MAX_TOKENS,
                             media.IMAGE_SIDE, media.SCAN_SIDE, media.VIDEO_FRAMES], sort_keys=True)
        return hashlib.sha256(source.encode()).hexdigest()[:16]

    def needs_pdf_image(self, path, text, status_cb=None, cancelled=None):
        if cancelled and cancelled():
            raise InterruptedError('Search stopped')
        try:
            layout = pdf_layout(path, cancelled=cancelled)
            if not text.strip():
                return bool(layout['images'] or layout['shapes'])
            # Short labels can conceal vector charts; keep their visual review.
            if layout['shapes'] and len(text.strip()) < 40:
                return True
            if layout['image_coverage'] < .4:
                return False
            labels = ['details', 'label_only', 'noise', 'uncertain']
            raw = self.runtime._generate(PDF_TEXT_PROMPT, json.dumps({'text': text}, ensure_ascii=False),
                status_cb, cancelled, schema={'type': 'string', 'enum': labels}, max_tokens=16, temperature=0)
            label = json.loads(raw)
            if label not in labels:
                raise ValueError('Invalid PDF text classification')
            return label != 'details'
        except InterruptedError:
            raise
        except (OSError, ValueError, RuntimeError):
            log.warning('PDF routing failed for %s; using visual review', path, exc_info=True)
            return True

    def describe(self, path, kind, status_cb=None, cancelled=None):
        """One line about what the file shows, labelled by kind, or "" when it cannot be decoded."""
        if cancelled and cancelled():
            raise InterruptedError('Search stopped')
        frame = media.FRAMES[kind](path)
        if frame is None:
            return ''
        if cancelled and cancelled():
            raise InterruptedError('Search stopped')
        server = self.runtime._ensure_started(status_cb, cancelled)
        raw = server.chat(VISION_PROMPTS[kind], 'Describe it.', MAX_TOKENS[kind], 0,
                          images=[frame], allow_truncated=True)
        if cancelled and cancelled():
            raise InterruptedError('Search stopped')
        text = re.sub(r'\s+', ' ', raw).strip()
        return f'{LABELS[kind]}: {text}' if text else ''

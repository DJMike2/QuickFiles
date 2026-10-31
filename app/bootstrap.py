"""Construct the services used by the desktop window."""
from contextlib import contextmanager

from app.ai.activity import AIActivity
from app.ai.binary_manager import BinaryManager
from app.ai.hardware_probe import probe
from app.ai.model_manager import ModelManager
from app.ai.runtime import AIRuntime
from app.ai.embeddings import EmbeddingRuntime
from app.ai.vision import VisionRuntime
from app.core.search import FileSearch
from app.storage import Journal


class AppContext:
    def __init__(self, config):
        self.config = config
        self.hardware = probe()
        self.model_manager = ModelManager(config.models_dir)
        self.binary_manager = BinaryManager(config.app_data_dir, self.hardware)
        self.runtime = AIRuntime(self.binary_manager, self.model_manager)
        self.embeddings = EmbeddingRuntime(self.binary_manager, self.model_manager)
        self.vision = VisionRuntime(self.binary_manager, self.model_manager, self.runtime)
        self.ai_activity = AIActivity(self.runtime, self.embeddings, self.vision)
        self.search = FileSearch(config.app_data_dir / "search.sqlite3", self.runtime,
                                 self.embeddings, config.get("exclude_patterns"), vision=self.vision)
        self.journal = Journal(config.db_path)

    @contextmanager
    def ai_operation(self, kind, cancelled):
        with self.ai_activity.operation():
            if kind == 'scan':
                self.runtime.prestart(cancelled)
            try:
                yield
            except BaseException:
                self.cancel_ai()
                self.runtime.shutdown()
                self.embeddings.shutdown()
                self.vision.shutdown()
                raise

    def prepare_search(self):
        """Load the search models while a request is typed; the idle timer still releases them."""
        with self.ai_activity.operation():
            self.embeddings.prestart()
            self.runtime.prestart()

    def cancel_ai(self):
        self.runtime.interrupt()
        self.embeddings.interrupt()
        self.vision.interrupt()

    def shutdown(self):
        activity = getattr(self, 'ai_activity', None)
        if activity is not None:
            activity.close()
        else:
            self.runtime.shutdown()
            self.embeddings.shutdown()
            self.vision.shutdown()
        self.journal.close()

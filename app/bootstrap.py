"""Construct the services used by the desktop window."""
from app.ai.binary_manager import BinaryManager
from app.ai.hardware_probe import probe
from app.ai.model_manager import ModelManager
from app.ai.runtime import AIRuntime
from app.ai.embeddings import EmbeddingRuntime
from app.ai.vision import VisionRuntime
from app.core.search import FileSearch
from app.logging_setup import setup_logging
from app.storage import Journal


class AppContext:
    def __init__(self, config):
        self.config = config
        setup_logging(config.app_data_dir / "logs")
        self.hardware = probe()
        self.model_manager = ModelManager(config.models_dir)
        self.binary_manager = BinaryManager(config.app_data_dir, self.hardware)
        self.runtime = AIRuntime(self.binary_manager, self.model_manager)
        self.embeddings = EmbeddingRuntime(self.binary_manager, self.model_manager)
        self.vision = VisionRuntime(self.binary_manager, self.model_manager, self.runtime)
        self.search = FileSearch(config.app_data_dir / "search.sqlite3", self.runtime,
                                 self.embeddings, config.get("exclude_patterns"), vision=self.vision)
        self.journal = Journal(config.db_path)

    def cancel_ai(self):
        self.runtime.interrupt()
        self.embeddings.interrupt()
        self.vision.interrupt()

    def shutdown(self):
        self.runtime.shutdown()
        self.embeddings.shutdown()
        self.vision.shutdown()
        self.journal.close()

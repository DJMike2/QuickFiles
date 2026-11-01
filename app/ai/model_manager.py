"""Model metadata and the single internal runtime configuration."""
import json
from pathlib import Path

from app.ai.model_downloader import download_file


class ModelManager:
    def __init__(self, models_dir: Path):
        data_path = Path(__file__).resolve().parents[2] / "models/settings.json"
        data = json.loads(data_path.read_text(encoding="utf-8"))
        self.runtime_settings = data["runtime"]
        # Search shares the organizer's model file but has its own generation settings.
        self.search_settings = data["search"]
        self.model_info = data["model"]
        self.model_path = models_dir / self.model_info["filename"]
        self.mmproj_path = models_dir / self.model_info["mmproj"]["filename"]
        self.embedding_model_info = data["embedding_model"]
        self.embedding_model_path = models_dir / self.embedding_model_info["filename"]

    @staticmethod
    def _complete(path, info):
        try:
            with path.open("rb") as stream:
                return path.stat().st_size == info["size_bytes"] and stream.read(4) == b"GGUF"
        except OSError:
            return False

    @property
    def embedding_downloaded(self):
        return self._complete(self.embedding_model_path, self.embedding_model_info)

    def download_embedding(self, progress_cb=None, cancelled=None):
        if not self.embedding_downloaded:
            info = self.embedding_model_info
            download_file(info["url"], self.embedding_model_path, progress_cb, cancelled,
                          expected_size=info["size_bytes"], expected_sha256=info["sha256"])
        return self.embedding_model_path

    @property
    def is_downloaded(self):
        return (self._complete(self.model_path, self.model_info)
                and self._complete(self.mmproj_path, self.model_info["mmproj"]))

    @property
    def disk_usage_mb(self):
        return sum(path.stat().st_size for path in (self.model_path, self.mmproj_path)
                   if path.is_file()) / 1048576

    def download(self, progress_cb=None, cancelled=None):
        for path, info in ((self.model_path, self.model_info), (self.mmproj_path, self.model_info["mmproj"])):
            if cancelled and cancelled():
                raise InterruptedError("Installation cancelled")
            if not self._complete(path, info):
                download_file(info["url"], path, progress_cb, cancelled,
                              expected_size=info["size_bytes"], expected_sha256=info["sha256"])
                if not self._complete(path, info):
                    path.unlink(missing_ok=True)
                    raise ValueError("The downloaded file is not a GGUF model")
        return self.model_path

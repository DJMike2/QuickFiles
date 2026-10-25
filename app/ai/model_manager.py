"""Model metadata and the single internal runtime configuration."""
import json
from pathlib import Path

from app.ai.model_downloader import download_file


class ModelManager:
    def __init__(self, models_dir: Path):
        data_path = Path(__file__).resolve().parents[2] / "models/settings.json"
        data = json.loads(data_path.read_text(encoding="utf-8"))
        self.runtime_settings = data["runtime"]
        self.model_info = data["model"]
        self.model_path = models_dir / self.model_info["filename"]
        self.testing_model_info = data["testing_model"]
        self.testing_model_path = models_dir / self.testing_model_info["filename"]
        self.embedding_model_info = data["embedding_model"]
        self.embedding_model_path = models_dir / self.embedding_model_info["filename"]

    @property
    def embedding_downloaded(self):
        try:
            with self.embedding_model_path.open("rb") as stream:
                return (self.embedding_model_path.stat().st_size == self.embedding_model_info["size_bytes"]
                        and stream.read(4) == b"GGUF")
        except OSError:
            return False

    def download_embedding(self, progress_cb=None, cancelled=None):
        if not self.embedding_downloaded:
            info = self.embedding_model_info
            download_file(info["url"], self.embedding_model_path, progress_cb, cancelled,
                          expected_size=info["size_bytes"], expected_sha256=info["sha256"])
        return self.embedding_model_path

    @property
    def is_downloaded(self):
        try:
            with self.model_path.open("rb") as stream:
                return (self.model_path.stat().st_size == self.model_info["size_bytes"]
                        and stream.read(4) == b"GGUF")
        except OSError:
            return False

    @property
    def disk_usage_mb(self):
        return self.model_path.stat().st_size / 1048576 if self.model_path.is_file() else 0

    def download(self, progress_cb=None, cancelled=None):
        if self.is_downloaded:
            return self.model_path
        download_file(self.model_info["url"], self.model_path, progress_cb, cancelled,
                      expected_size=self.model_info["size_bytes"],
                      expected_sha256=self.model_info["sha256"])
        if not self.is_downloaded:
            self.model_path.unlink(missing_ok=True)
            raise ValueError("The downloaded file is not a GGUF model")
        return self.model_path

    def delete(self):
        self.model_path.unlink(missing_ok=True)

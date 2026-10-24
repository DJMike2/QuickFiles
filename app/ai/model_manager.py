"""The single model and its two planning profiles."""
import json
from pathlib import Path

from app.ai.model_downloader import download_file


class ModelManager:
    def __init__(self, models_dir: Path):
        data_path = Path(__file__).resolve().parents[2] / "models/profiles.json"
        data = json.loads(data_path.read_text(encoding="utf-8"))
        self.profiles = data["profiles"]
        self.model_info = data["model"]
        self.model_path = models_dir / self.model_info["filename"]

    def profile(self, mode):
        return self.profiles.get(mode, self.profiles["fast"])

    @property
    def is_downloaded(self):
        try:
            with self.model_path.open("rb") as stream:
                return stream.read(4) == b"GGUF"
        except OSError:
            return False

    @property
    def disk_usage_mb(self):
        return self.model_path.stat().st_size / 1048576 if self.model_path.is_file() else 0

    def download(self, progress_cb=None, cancelled=None):
        if self.is_downloaded:
            return self.model_path
        download_file(self.model_info["url"], self.model_path, progress_cb, cancelled)
        if not self.is_downloaded:
            self.model_path.unlink(missing_ok=True)
            raise ValueError("The downloaded file is not a GGUF model")
        return self.model_path

    def delete(self):
        self.model_path.unlink(missing_ok=True)

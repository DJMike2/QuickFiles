"""Small CPU embeddings through the installed llama.cpp engine."""
import numpy as np

from app.ai.llm_server import ContextTooLargeError, LLMServer, LLMServerError


class EmbeddingRuntime:
    dimensions = 384

    def __init__(self, binary_manager, model_manager):
        self.binary_manager = binary_manager
        self.model_manager = model_manager
        self._server = None

    @property
    def fingerprint(self):
        return self.model_manager.embedding_model_info['sha256']

    def embed(self, texts, status_cb=None, progress_cb=None, cancelled=None):
        cancelled = cancelled or (lambda: False)
        if cancelled():
            raise InterruptedError('Search stopped')
        if not texts:
            return np.empty((0, self.dimensions), dtype=np.float32)
        if not self.model_manager.embedding_downloaded and status_cb:
            status_cb('Downloading the small local search model…')
        self.model_manager.download_embedding(progress_cb, cancelled)
        if self._server is None or not self._server.is_running:
            self.shutdown()
            self._server = LLMServer(self.binary_manager, self.model_manager.embedding_model_path,
                                     {'n_gpu_layers': 0, 'context_tokens': 2048, 'threads': 2},
                                     embedding=True)
            self._server.start(status_cb, cancelled)

        def encode(batch):
            if cancelled():
                raise InterruptedError('Search stopped')
            try:
                return self._server.embed(batch)
            except ContextTooLargeError:
                if len(batch) > 1:
                    middle = len(batch) // 2
                    return encode(batch[:middle]) + encode(batch[middle:])
                if len(batch[0]) > 128:
                    return encode([batch[0][:len(batch[0]) // 2]])
                raise

        rows = []
        for offset in range(0, len(texts), 4):
            rows.extend(encode([text[:1000] for text in texts[offset:offset+4]]))
        if cancelled():
            raise InterruptedError('Search stopped')
        vectors = np.asarray(rows, dtype=np.float32)
        if vectors.shape != (len(texts), self.dimensions) or not np.isfinite(vectors).all():
            raise LLMServerError('The search model returned invalid vectors')
        lengths = np.linalg.norm(vectors, axis=1, keepdims=True)
        if (lengths <= 0).any():
            raise LLMServerError('The search model returned an empty vector')
        return vectors / lengths

    def shutdown(self):
        if self._server:
            self._server.stop()
        self._server = None

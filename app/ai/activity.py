"""Keep models alive during AI jobs, then release them after five idle minutes."""
from contextlib import contextmanager
import logging
import threading

log = logging.getLogger(__name__)
IDLE_SECONDS = 5 * 60


class AIActivity:
    def __init__(self, *runtimes, idle_seconds=IDLE_SECONDS):
        self.runtimes = runtimes
        self.idle_seconds = idle_seconds
        self._lock = threading.RLock()
        self._timer = None
        self._generation = 0
        self._active = 0
        self._closed = False

    def _cancel_timer(self):
        self._generation += 1
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    @contextmanager
    def operation(self):
        with self._lock:
            if self._closed:
                raise InterruptedError('Application is closing')
            self._cancel_timer()
            self._active += 1
        try:
            yield
        finally:
            with self._lock:
                self._active -= 1
                if not self._active and not self._closed:
                    generation = self._generation
                    self._timer = threading.Timer(self.idle_seconds, self._expire, (generation,))
                    self._timer.daemon = True
                    self._timer.start()

    def _expire(self, generation):
        # Serialize teardown with the next job, so it cannot acquire a model that
        # an expired timer is still stopping. Never stop a long-running AI job.
        with self._lock:
            if self._closed or self._active or generation != self._generation:
                return
            self._timer = None
            self._release_models()
            log.debug('Released local AI models after five idle minutes')

    def _release_models(self):
        for runtime in self.runtimes:
            try:
                runtime.shutdown()
            except Exception:
                log.exception('Could not unload a local AI model')

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cancel_timer()
            self._release_models()

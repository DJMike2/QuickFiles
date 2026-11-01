"""Serialize background model loading, use, cancellation, and shutdown."""
from concurrent.futures import Future
from contextvars import copy_context
import threading


class ModelRuntime:
    def __init__(self):
        self._server = None
        self._startup_lock = threading.RLock()
        self._startup = None
        self._loader = None
        self._load_cancel = threading.Event()

    def _new_server(self):
        raise NotImplementedError

    def _load_async(self, status_cb=None, cancelled=None):
        cancelled = cancelled or (lambda: False)
        if cancelled():
            raise InterruptedError('AI request stopped')
        with self._startup_lock:
            if self._startup is not None:
                # A running process is not necessarily healthy yet. Requests must
                # wait for the same startup future, including its failure.
                if not self._startup.done() or self._startup.exception() is not None:
                    return self._startup
                if self._server is not None and self._server.is_running:
                    return self._startup
            if self._server is not None and self._server.is_running:
                ready = Future()
                ready.set_result(self._server)
                return ready
            if self._server is not None:
                self._server.stop()
            stop = self._load_cancel = threading.Event()
            server = self._server = self._new_server()
            future = self._startup = Future()

            def load():
                try:
                    if stop.is_set() or cancelled():
                        raise InterruptedError('Model loading stopped')
                    server.start(status_cb, lambda: stop.is_set() or cancelled())
                    if stop.is_set() or cancelled():
                        raise InterruptedError('Model loading stopped')
                except BaseException as exc:
                    try:
                        server.stop()
                    finally:
                        future.set_exception(exc)
                else:
                    future.set_result(server)

            self._loader = threading.Thread(target=copy_context().run, args=(load,),
                                            name='local-model-loader', daemon=True)
            self._loader.start()
            return future

    def _ensure_started(self, status_cb=None, cancelled=None):
        future = self._load_async(status_cb, cancelled)
        if not future.done() and status_cb:
            status_cb('Starting local AI…')
        server = future.result()
        if cancelled and cancelled():
            raise InterruptedError('AI request stopped')
        return server

    def interrupt(self):
        # Do not wait for a loader or an HTTP request on the GUI thread.
        self._load_cancel.set()
        server = self._server
        if server is not None:
            server.interrupt()

    def shutdown(self):
        with self._startup_lock:
            self._load_cancel.set()
            if self._startup is not None and not self._startup.done() and self._server is not None:
                self._server.interrupt()
            if self._loader is not None and self._loader is not threading.current_thread():
                self._loader.join()
            if self._server is not None:
                self._server.stop()
            self._server = self._startup = self._loader = None

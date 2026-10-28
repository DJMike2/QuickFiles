"""Bounded reuse of fully supported organizer decisions within one model session."""
from collections import OrderedDict
from copy import deepcopy
import functools
import hashlib
import inspect
import json
import threading


class DecisionCache:
    """Exact inputs only; preserve JSON field/list order, which affects prompts.

    Keys and values are retained only in RAM. Do not cache failures, uncertain
    matches or review holds: those should get another opportunity on a later scan.
    Callers remain responsible for fresh filesystem and evidence checks.
    """
    def __init__(self, max_entries=128, max_bytes=4 * 1024 * 1024):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._values = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self._generator = None
        self._generator_owner = None
        self.hits = 0
        self.misses = 0

    def bind(self, generator):
        """An injected/replaced generation backend starts a new cache session."""
        provider = getattr(generator, '__func__', generator)
        owner = getattr(generator, '__self__', None)
        with self._lock:
            if self._generator is not provider or self._generator_owner is not owner:
                self._values.clear()
                self._bytes = 0
                self._generator, self._generator_owner = provider, owner

    def get(self, key):
        with self._lock:
            if key not in self._values:
                self.misses += 1
                return None
            self.hits += 1
            self._values.move_to_end(key)
            return deepcopy(self._values[key][0])

    def put(self, key, value):
        size = len(key) + len(json.dumps(value, ensure_ascii=True).encode('utf-8'))
        if size > self.max_bytes or self.max_entries < 1:
            return
        with self._lock:
            if key in self._values:
                self._bytes -= self._values.pop(key)[1]
            self._values[key] = (deepcopy(value), size)
            self._bytes += size
            while len(self._values) > self.max_entries or self._bytes > self.max_bytes:
                self._bytes -= self._values.popitem(last=False)[1][1]

    def clear(self):
        with self._lock:
            self._values.clear()
            self._bytes = 0


def reuse_decision(prompt, accepted):
    """Cache a public runtime result only after its normal validation succeeds."""
    def decorate(method):
        signature = inspect.signature(method)
        @functools.wraps(method)
        def call(self, *args, **kwargs):
            bound = signature.bind(self, *args, **kwargs)
            bound.apply_defaults()
            cancelled = bound.arguments.get('cancelled')
            if cancelled and cancelled():
                raise InterruptedError('Planning stopped')
            inputs = {k: v for k, v in bound.arguments.items()
                      if k not in {'self', 'status_cb', 'cancelled'}}
            # Do not sort nested dictionaries: the model sees their original order.
            key = hashlib.sha256(json.dumps(
                [method.__name__, prompt, self.summary_cache_key, inputs],
                ensure_ascii=True).encode('utf-8')).hexdigest()
            cache = self._organizer_decisions
            cache.bind(self._generate)
            cached = cache.get(key)
            if cached is not None:
                if cancelled and cancelled():
                    raise InterruptedError('Planning stopped')
                return cached
            result = method(self, *args, **kwargs)
            if cancelled and cancelled():
                raise InterruptedError('Planning stopped')
            if accepted(result):
                cache.put(key, result)
            return result
        return call
    return decorate

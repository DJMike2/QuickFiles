from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import logging
import threading

from app.ai.llm_server import ContextTooLargeError
from app.ai.prompts import SEARCH_SCOPE_PROMPT, SEARCH_SCOPE_ROLES
from app.core.folder_policy import folder_evidence
from app.core.organization import EvidenceCache, cache_key

log = logging.getLogger(__name__)


class SearchScope:
    batch_size = 4
    max_calls = 8

    def __init__(self, runtime, cache_path, status, cancelled):
        self.runtime = runtime
        self.cache = EvidenceCache(cache_path)
        self.status = status
        self.cancelled = cancelled
        self.version = cache_key('search-scope-v1', SEARCH_SCOPE_PROMPT, runtime.model_cache_key)
        self.calls = self.cached = self.deferred = 0
        self._budget_lock = threading.Lock()

    def _check(self):
        if self.cancelled():
            raise InterruptedError('Search stopped')

    def _evidence(self, path, include):
        return folder_evidence(path, self.cancelled, include, entry_limit=32)

    def allowed(self, paths, include):
        # The selected root is entered by the caller; only its descendants are classified.
        keep = dict.fromkeys(paths, True)
        pending = []
        for path in paths:
            self._check()
            try:
                evidence, _, signature = self._evidence(path, include)
                key = cache_key(self.version, str(path), signature)
                role = self.cache.get(key)
                if role in SEARCH_SCOPE_ROLES:
                    self.cached += 1
                    if role == 'generated' and self._evidence(path, include)[2] == signature:
                        keep[path] = False
                else:
                    pending.append((path, evidence, signature, key))
            except InterruptedError:
                raise
            except (OSError, ValueError, RuntimeError):
                log.warning('Search scope could not inspect %s; retaining folder', path, exc_info=True)
        # Do not queue model work beyond the remaining request budget.
        available = (self.max_calls - self.calls) * self.batch_size
        self.deferred += max(0, len(pending) - available)
        pending = pending[:available]
        slots = min(self.runtime.slots, 4)
        batches = [pending[offset:offset + self.batch_size] for offset in range(0, len(pending), self.batch_size)]
        if slots == 1 or len(batches) <= 1:
            for batch in batches:
                self._classify(batch, keep, include)
        else:
            with ThreadPoolExecutor(max_workers=min(slots, len(batches)), thread_name_prefix='search-scope') as pool:
                jobs = [pool.submit(copy_context().run, self._classify, batch, keep, include) for batch in batches]
                for job in jobs:
                    job.result()
        return [path for path in paths if keep[path]]

    def _classify(self, batch, keep, include):
        self._check()
        with self._budget_lock:
            if self.calls >= self.max_calls:
                self.deferred += len(batch)
                return  # An unclassified folder remains searchable.
            self.calls += 1
        self.status('Checking folder contents for search…')
        try:
            judgments = self.runtime.describe_search_folders([item[1] for item in batch],
                                                             self.status, self.cancelled)
        except InterruptedError:
            raise
        except ContextTooLargeError:
            if len(batch) > 1:
                middle = len(batch) // 2
                self._classify(batch[:middle], keep, include)
                self._classify(batch[middle:], keep, include)
            else:
                log.warning('Folder evidence exceeded search context; retaining %s', batch[0][0])
            return
        except (ValueError, RuntimeError):
            log.warning('Search scope classification failed; retaining folders', exc_info=True)
            return
        self._check()
        for (path, _, signature, key), value in zip(batch, judgments):
            try:
                if self._evidence(path, include)[2] != signature:
                    continue
                role = value['role']
                self.cache.put(key, role)
                keep[path] = role != 'generated'
            except InterruptedError:
                raise
            except (OSError, ValueError, RuntimeError):
                log.warning('Search folder changed during classification: %s', path, exc_info=True)

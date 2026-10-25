"""One local model server for folder planning and constrained file assignments."""
import json
import re

from app.ai.llm_server import ContextTooLargeError, LLMServer, LLMServerError
from app.ai.prompts import ASSIGN_PROMPT, FOLDER_PLAN_PROMPT, LEFTOVER_FOLDERS_PROMPT, SEARCH_QUERY_PROMPT


class AIRuntime:
    def __init__(self, binary_manager, model_manager):
        self.binary_manager = binary_manager
        self.model_manager = model_manager
        self._server = None

    @property
    def available(self):
        return self.binary_manager.is_available and self.model_manager.is_downloaded

    @property
    def backend_label(self):
        if self._server and self._server.is_running:
            return f"Local AI ({self._server.backend.upper()})"
        return "Local AI ready" if self.available else "Local AI setup required"

    def _generate(self, system, user, status_cb=None, cancelled=None, *, max_tokens=None,
                  schema=None, grammar=None):
        if cancelled and cancelled():
            raise InterruptedError("Planning stopped")
        settings = self.model_manager.runtime_settings
        if self._server is None or not self._server.is_running:
            self.shutdown()
            self._server = LLMServer(self.binary_manager, self.model_manager.model_path, settings)
            self._server.start(status_cb, cancelled)
        raw = self._server.chat(system, user, max_tokens or settings["max_tokens"],
                                settings["temperature"], schema=schema, grammar=grammar)
        if cancelled and cancelled():
            raise InterruptedError("Planning stopped")
        return raw

    def plan_folders(self, files, existing_folders, protected_projects, already_planned, *,
                     leftovers=False, status_cb=None, cancelled=None):
        schema = {'type': 'object', 'additionalProperties': False, 'properties': {'folders': {
            'type': 'array', 'maxItems': 6 if leftovers else 18,
            'items': {'type': 'object', 'additionalProperties': False,
                      'properties': {'path': {'type': 'string', 'maxLength': 64},
                                     'holds': {'type': 'string', 'maxLength': 96}},
                      'required': ['path', 'holds']}}}, 'required': ['folders']}
        rows = [{'id': row['id'], 'name': row['name'], 'hint': row['hint'][:85]} for row in files]
        while True:
            user = json.dumps({'existing_folders': existing_folders,
                               'protected_projects': protected_projects,
                               'already_planned': already_planned, 'loose_files': rows},
                              ensure_ascii=False, separators=(',', ':'))
            try:
                raw = self._generate(LEFTOVER_FOLDERS_PROMPT if leftovers else FOLDER_PLAN_PROMPT,
                                     user, status_cb, cancelled, schema=schema)
                break
            except ContextTooLargeError:
                if len(rows) <= 1:
                    raise LLMServerError("Too much folder context. Choose a smaller folder to organize.")
                # Large directories still receive one shared plan, based on a bounded sample.
                rows = rows[::2]
                if status_cb:
                    status_cb("Using a sample of filenames to plan this large folder…")
        try:
            folders = json.loads(raw)['folders']
            if not isinstance(folders, list) or any(
                not isinstance(item, dict) or not isinstance(item.get('path'), str)
                or not isinstance(item.get('holds'), str) for item in folders
            ):
                raise ValueError("Invalid folder entries")
            return folders
        except (ValueError, TypeError, KeyError) as exc:
            raise LLMServerError("The local model returned an invalid folder plan") from exc

    def assign_files(self, files, folders, status_cb=None, cancelled=None):
        if not files or not folders:
            return {}
        choices = list(folders)
        mapping = [{'id': index, 'path': path, 'holds': folders[path]}
                   for index, path in enumerate(choices, 1)]
        system = ASSIGN_PROMPT + '\nDestinations: ' + json.dumps(
            mapping, ensure_ascii=False, separators=(',', ':'))
        literals = ' | '.join(json.dumps(str(number)) for number in range(len(choices) + 1))
        batch_size = self.model_manager.runtime_settings['batch_size']

        def assign(batch):
            grammar = 'root ::= ' + ' '.join(
                json.dumps(f"{row['id']}:") + ' folder "\\n"' for row in batch)
            grammar += '\nfolder ::= ' + literals
            user = json.dumps([{'id': row['id'], 'name': row['name'], 'hint': row['hint']}
                               for row in batch], ensure_ascii=False, separators=(',', ':'))
            try:
                raw = self._generate(system, user, status_cb, cancelled,
                                     max_tokens=12 * len(batch) + 32, grammar=grammar)
            except ContextTooLargeError:
                if len(batch) <= 1:
                    raise LLMServerError("Too many destination folders. Choose a smaller folder to organize.")
                middle = len(batch) // 2
                return {**assign(batch[:middle]), **assign(batch[middle:])}
            lines = raw.strip().splitlines()
            if len(lines) != len(batch):
                raise LLMServerError("The local model omitted files from its assignment response")
            output = {}
            for line, row in zip(lines, batch):
                match = re.fullmatch(r'(\d+):(\d+)', line.strip())
                if not match or int(match[1]) != row['id'] or not 0 <= int(match[2]) <= len(choices):
                    raise LLMServerError("The local model returned an invalid file assignment")
                if int(match[2]):
                    output[row['rel_path']] = choices[int(match[2]) - 1]
            return output

        found = {}
        for offset in range(0, len(files), batch_size):
            if status_cb:
                status_cb(f"Assigning files… {min(offset + batch_size, len(files)):,}/{len(files):,}")
            found.update(assign(files[offset:offset + batch_size]))
        return found

    def rewrite_query(self, query, status_cb=None, cancelled=None):
        schema = {'type': 'object', 'additionalProperties': False, 'properties': {
            'query': {'type': 'string', 'minLength': 1, 'maxLength': 240},
            'extensions': {'type': 'array', 'maxItems': 4, 'items': {'type': 'string', 'maxLength': 12}},
            'exclude_extensions': {'type': 'array', 'maxItems': 4, 'items': {'type': 'string', 'maxLength': 12}},
            'exclude_terms': {'type': 'array', 'maxItems': 4, 'items': {'type': 'string', 'maxLength': 48}},
        }, 'required': ['query', 'extensions', 'exclude_extensions', 'exclude_terms']}
        try:
            raw = self._generate(SEARCH_QUERY_PROMPT, json.dumps({'request': query}, ensure_ascii=False),
                                 status_cb, cancelled, max_tokens=192, schema=schema)
            value = json.loads(raw)
            if not isinstance(value, dict) or not isinstance(value.get('query'), str) or not value['query'].strip():
                raise ValueError('Empty rewritten query')
            for key in ('extensions', 'exclude_extensions', 'exclude_terms'):
                if not isinstance(value.get(key), list) or any(not isinstance(x, str) for x in value[key]):
                    raise ValueError('Invalid query filters')
            for key in ('extensions', 'exclude_extensions'):
                value[key] = [('.' + x.lstrip('.')).lower() for x in value[key]
                              if re.fullmatch(r'\.?[a-zA-Z0-9]{1,10}', x)]
            value['query'] = value['query'].strip()[:240]
            value['exclude_terms'] = [x.casefold().strip() for x in value['exclude_terms'] if x.strip()]
            return value
        except (ValueError, KeyError, TypeError) as exc:
            self.shutdown()
            raise LLMServerError('The local model returned an invalid search request') from exc
        except (LLMServerError, InterruptedError):
            self.shutdown()
            raise

    def shutdown(self):
        if self._server:
            self._server.stop()
        self._server = None

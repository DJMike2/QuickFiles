"""One local model server for grounded planning, independent review and semantic search."""
from datetime import date
import json
import logging
import re

from app.ai.llm_server import ContextTooLargeError, IncompleteResponseError, LLMServer, LLMServerError
from app.ai.prompts import (ASSIGN_PROMPT, FILE_SUMMARY_PROMPT, FOLDER_PLAN_PROMPT,
                            LEFTOVER_FOLDERS_PROMPT, SEARCH_QUERY_PROMPT, SEARCH_REVIEW_PROMPT,
                            CATALOG_PROMPT, GROUPS_PROMPT, MATCH_PROMPT, REVIEW_PROMPT)
from app.constants import SEARCH_KIND_WORDS, SEARCH_KINDS

SEARCH_SORTS = ['relevance', 'newest', 'oldest', 'largest', 'smallest']
log = logging.getLogger(__name__)


class SummaryGroundingError(LLMServerError):
    """A completed summary failed evidence checks; the file must remain unassigned."""

# A small model fills optional fields eagerly, so each filter is kept only when the
# request itself contains words that could have asked for it.
ASKS_FOR_TIME = re.compile(
    r'\b(today|yesterday|recent\w*|latest|newest|oldest|ago|since|before|after|until|last|this|past|'
    r'days?|weeks?|weekend|months?|years?|jan\w*|feb\w*|mar\w*|apr\w*|may|june?|july?|aug\w*|'
    r'sep\w*|oct\w*|nov\w*|dec\w*|spring|summer|fall|autumn|winter|(19|20)\d\d)\b', re.I)
ASKS_TO_EXCLUDE = re.compile(r"\b(not|no|without|except|excluding|exclude|other than|minus|skip|ignore|"
                             r"don'?t|non)\b", re.I)
ASKS_FOR_SIZE = re.compile(r'\d\s*(k|m|g|t)i?b?\b|\b(bytes?|kb|mb|gb|tb)\b', re.I)
ASKS_FOR_ORDER = re.compile(r'\b(latest|newest|recent\w*|oldest|earliest|first|last|largest|biggest|'
                            r'smallest|tiniest|big+\w*|large\w*|huge|small\w*|order|sort\w*)\b', re.I)


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
                  temperature=None, schema=None, grammar=None):
        if cancelled and cancelled():
            raise InterruptedError("Planning stopped")
        settings = self.model_manager.runtime_settings
        if self._server is None or not self._server.is_running:
            self.shutdown()
            self._server = LLMServer(self.binary_manager, self.model_manager.model_path, settings)
            self._server.start(status_cb, cancelled)
        raw = self._server.chat(system, user, max_tokens or settings["max_tokens"],
                                settings["temperature"] if temperature is None else temperature,
                                schema=schema, grammar=grammar)
        if cancelled and cancelled():
            raise InterruptedError("Planning stopped")
        return raw

    @property
    def summary_cache_key(self):
        manager = self.model_manager
        return json.dumps({'model': getattr(manager, 'model_info', {}),
                           'settings': manager.runtime_settings, 'prompt': FILE_SUMMARY_PROMPT},
                          sort_keys=True)

    def summarize_file(self, source, *, basis, status_cb=None, cancelled=None):
        """One constrained call per file; exact evidence spans are verified outside the LLM."""
        if basis not in {'content', 'filename', 'archive_listing'}:
            raise ValueError('Unknown summary evidence basis')
        def strings(count, length):
            return {'type': 'array', 'maxItems': count,
                    'items': {'type': 'string', 'maxLength': length}}
        schema = {'type': 'object', 'additionalProperties': False, 'properties': {
            'subject': {'type': 'string', 'maxLength': 96},
            'identifiers': strings(6, 64), 'quotes': strings(3, 140)},
            'required': ['subject', 'identifiers', 'quotes']}
        # On context overflow shorten evidence, never fall back silently to a misleading name.
        sample = source
        feedback = ''
        for attempt in range(2):
            value, sample, feedback = self._summarize_attempt(
                sample, basis, schema, feedback, status_cb, cancelled)
            if value is not None:
                return value
            log.debug('Retrying file summary after evidence validation: %s', feedback)
        raise SummaryGroundingError(f'The local model returned an ungrounded file summary: {feedback}')

    def _summarize_attempt(self, sample, basis, schema, feedback, status_cb, cancelled):
        if feedback:
            # A correction should be simpler, especially for formula-heavy text.
            schema = json.loads(json.dumps(schema))
            schema['properties']['quotes']['maxItems'] = 1
            schema['properties']['quotes']['items']['maxLength'] = 96
            schema['properties']['identifiers']['maxItems'] = 3
        while True:
            try:
                raw = self._generate(FILE_SUMMARY_PROMPT, json.dumps(
                    {'basis': basis, 'evidence': sample, **({'validation_error': feedback,
                      'retry_instruction': 'Correct the error. Copy one short exact evidence quote; '
                                           'do not paraphrase quotes or invent identifiers.'} if feedback else {})},
                    ensure_ascii=False), status_cb, cancelled,
                    schema=schema, max_tokens=1024, temperature=0)
                break
            except IncompleteResponseError:
                if len(sample) > 800:
                    sample = sample[:len(sample) // 2]
                return None, sample, 'Response was incomplete. Select one short heading and only essential identifiers.'
            except ContextTooLargeError:
                if len(sample) <= 800:
                    raise LLMServerError('The file evidence does not fit the local model context')
                sample = sample[:len(sample) // 2]
        try:
            value = json.loads(raw)
            if not isinstance(value, dict) or not isinstance(value.get('subject'), str):
                raise ValueError('Missing subject')
            for key in ('quotes', 'identifiers'):
                if not isinstance(value.get(key), list) or any(not isinstance(x, str) for x in value[key]):
                    raise ValueError('Invalid summary evidence')
            from app.ai.evidence import compact
            normalized = compact(sample)
            value['subject'] = compact(value['subject'])
            if len(value['subject']) > 96 or len(value['quotes']) > 3 or len(value['identifiers']) > 6:
                raise ValueError('Invalid summary size')
            if any(not compact(quote) or len(compact(quote)) > 140 or compact(quote) not in normalized
                   for quote in value['quotes']):
                raise ValueError('Summary quoted text absent from the file evidence')
            if value['subject'] and value['subject'] not in normalized:
                raise ValueError('Subject must be copied exactly from the evidence')
            if any(not item.strip() or item.casefold() not in normalized.casefold()
                   for item in value['identifiers']):
                raise ValueError('Summary invented an identifier')
            if (value['subject'] or value['identifiers']) and not value['quotes']:
                raise ValueError('Summary subject has no supporting evidence')
            value['quotes'] = [compact(quote) for quote in value['quotes']]
            value['summary'] = ' … '.join(value['quotes']) if value['quotes'] else 'Insufficient subject evidence'
            return value, sample, ''
        except (TypeError, ValueError, KeyError) as exc:
            return None, sample, str(exc)

    def plan_folders(self, files, existing_folders, protected_projects, already_planned, *,
                     leftovers=False, status_cb=None, cancelled=None):
        schema = {'type': 'object', 'additionalProperties': False, 'properties': {'folders': {
            'type': 'array', 'maxItems': 6 if leftovers else 18,
            'items': {'type': 'object', 'additionalProperties': False,
                      'properties': {'path': {'type': 'string', 'maxLength': 64},
                                     'holds': {'type': 'string', 'maxLength': 96}},
                      'required': ['path', 'holds']}}}, 'required': ['folders']}
        rows = [{'id': row['id'], 'name': row['name'], 'hint': row['hint']} for row in files]
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

    def profile_items(self, rows, status_cb=None, cancelled=None):
        """Small batches retain every file; context overflow splits instead of sampling away files."""
        if not rows:
            return []
        from app.core.organization import SCOPES
        properties = {'description': {'type': 'string', 'maxLength': 200},
            'scope_kind': {'enum': list(SCOPES)},
            'scope': {'type': 'array', 'maxItems': 4, 'items': {'type': 'string', 'maxLength': 80}},
            'topic': {'type': 'string', 'maxLength': 96},
            'quote': {'type': 'string', 'maxLength': 140}}
        from app.ai.evidence import literal_passages
        local_rows = [{**row, 'id': i} for i, row in enumerate(rows, 1)]
        keys = [str(i) for i in range(1, len(rows)+1)]
        schema = {'type': 'object', 'additionalProperties': False, 'properties': {'profiles': {
            'type': 'object', 'additionalProperties': False,
            'properties': {key: {'type':'object', 'additionalProperties':False,
                'properties': {**properties, 'quote': {'enum':['', *literal_passages(row['evidence'])]}},
                'required':list(properties)} for key,row in zip(keys, rows)}, 'required': keys}},
            'required': ['profiles']}
        try:
            raw = self._generate(CATALOG_PROMPT, json.dumps({'items': local_rows}, ensure_ascii=False),
                status_cb, cancelled, schema=schema, max_tokens=320 * len(rows) + 64, temperature=0)
        except (ContextTooLargeError, IncompleteResponseError):
            if len(rows) <= 1:
                raise LLMServerError('The local model could not describe this item completely')
            middle = len(rows) // 2
            return (self.profile_items(rows[:middle], status_cb, cancelled)
                    + self.profile_items(rows[middle:], status_cb, cancelled))
        try:
            values = json.loads(raw)['profiles']
            if (not isinstance(values, dict) or set(values) != set(keys)
                    or any(not isinstance(v, dict) for v in values.values())):
                raise ValueError('Missing or invalid profile keys')
            return [{**values[key], 'id': row['id']} for key, row in zip(keys, rows)]
        except (ValueError, TypeError, KeyError) as exc:
            raise LLMServerError('The local model returned an invalid evidence catalog') from exc

    def propose_groups(self, rows, existing, status_cb=None, cancelled=None):
        local_rows = [{**row, 'id': i} for i, row in enumerate(rows, 1)]
        max_groups = max(1, min(18, len(rows)//2))
        schema = {'type': 'object', 'additionalProperties': False, 'properties': {'groups': {
            'type': 'array', 'maxItems': max_groups, 'items': {'type': 'object', 'additionalProperties': False,
                'properties': {'path': {'type': 'string', 'maxLength': 80},
                    'purpose': {'type': 'string', 'maxLength': 160}, 'members': {'type': 'array',
                        'minItems': 2, 'items': {'enum': list(range(1, len(rows)+1))}}},
                'required': ['path', 'purpose', 'members']}}}, 'required': ['groups']}
        try:
            raw = self._generate(GROUPS_PROMPT, json.dumps({'files': local_rows, 'existing': existing},
                ensure_ascii=False), status_cb, cancelled, schema=schema,
                max_tokens=max(512, 192*max_groups+128), temperature=0)
        except (ContextTooLargeError, IncompleteResponseError):
            if len(rows) <= 2:
                raise LLMServerError('The local model could not finish grouping these files')
            middle = len(rows) // 2
            # Deterministic overlapping boundary retains the possibility of a two-file group.
            return (self.propose_groups(rows[:middle + 1], existing, status_cb, cancelled)
                    + self.propose_groups(rows[middle:], existing, status_cb, cancelled))
        try:
            values = json.loads(raw)['groups']
            if not isinstance(values, list) or any(not isinstance(v.get('path'), str)
                    or not isinstance(v.get('purpose'), str) or not isinstance(v.get('members'), list)
                    or any(type(i) is not int for i in v['members']) for v in values):
                raise ValueError('Invalid group membership')
            for value in values:
                if any(not 1 <= i <= len(rows) for i in value['members']):
                    raise ValueError('Unknown supporting member')
                value['members'] = [rows[i-1]['id'] for i in value['members']]
            return values
        except (ValueError, TypeError, KeyError) as exc:
            raise LLMServerError('The local model returned invalid shared groups') from exc

    def choose_destination(self, file, destinations, status_cb=None, cancelled=None):
        from app.ai.evidence import compact, literal_passages
        paths = [row['path'] for row in destinations]
        schema = {'type': 'object', 'additionalProperties': False, 'properties': {
            'folder': {'enum': ['', *paths]}, 'match': {'enum': ['clear', 'uncertain']},
            'reason': {'type': 'string', 'maxLength': 240},
            'evidence': {'enum': ['', *literal_passages(file['evidence'])]}},
            'required': ['folder', 'match', 'reason', 'evidence']}
        try:
            raw = self._generate(MATCH_PROMPT, json.dumps({'file': file, 'destinations': destinations},
                ensure_ascii=False), status_cb, cancelled, schema=schema, max_tokens=512, temperature=0)
        except (ContextTooLargeError, IncompleteResponseError):
            if len(destinations) <= 1:
                return {'folder': '', 'match': 'uncertain', 'reason': 'Evidence comparison did not complete', 'evidence': ''}
            middle = len(destinations) // 2
            values = [self.choose_destination(file, part, status_cb, cancelled)
                      for part in (destinations[:middle], destinations[middle:])]
            matches = [v for v in values if v['folder']]
            if len(matches) == 1:
                return matches[0]
            return {'folder': '', 'match': 'uncertain', 'reason': 'Multiple plausible destinations', 'evidence': ''}
        try:
            value = json.loads(raw)
            if (value.get('folder') not in ['', *paths] or value.get('match') not in ['clear', 'uncertain']
                    or any(not isinstance(value.get(k), str) for k in ['reason', 'evidence'])):
                raise ValueError('Invalid destination decision')
            if value['folder'] and (value['match'] != 'clear' or not value['reason'].strip()
                    or not compact(value['evidence']) or compact(value['evidence']) not in compact(file['evidence'])):
                return {'folder': '', 'match': 'uncertain', 'reason': 'Match lacked supporting evidence', 'evidence': ''}
            return value
        except (ValueError, TypeError, KeyError) as exc:
            raise LLMServerError('The local model returned an invalid destination decision') from exc

    def review_destination(self, files, destination, existing, status_cb=None, cancelled=None, *, _retry=True):
        """A separate judgment receives evidence, never the assignment's original rationale."""
        from app.ai.evidence import compact
        if not files:
            return {}
        keys = [str(i) for i in range(1, len(files) + 1)]
        from app.ai.evidence import literal_passages
        paths = [row['path'] for row in existing]
        item = {'type': 'object', 'additionalProperties': False, 'properties': {
            'verdict': {'enum': ['keep', 'hold']}, 'reason': {'type': 'string', 'maxLength': 200},
            'evidence': {'type': 'string', 'maxLength': 140}},
            'required': ['verdict', 'reason', 'evidence']}
        schema = {'type': 'object', 'additionalProperties': False, 'properties': {
            'coherent': {'type': 'boolean'}, 'reuse_existing': {'enum': ['', *paths]},
            'decisions': {'type': 'object', 'additionalProperties': False,
                          'properties': {key: {**item, 'properties':{**item['properties'],
                              'evidence':{'enum':['', *literal_passages(file['evidence'])]}}}
                              for key,file in zip(keys, files)}, 'required': keys}},
            'required': ['coherent', 'reuse_existing', 'decisions']}
        held = lambda reason: {f['id']: {'verdict': 'hold', 'reason': reason, 'evidence': ''} for f in files}
        try:
            raw = self._generate(REVIEW_PROMPT, json.dumps({'destination': destination,
                'existing_destinations': existing,
                'files': [{**row, 'id': i} for i, row in enumerate(files, 1)],
                **({'feedback': 'A previous keep decision could not copy its supporting passage. '
                    'For each keep, copy just ONE SHORT heading or phrase exactly from that file. '
                    'Do not paraphrase or join passages.'} if not _retry else {})}, ensure_ascii=False),
                status_cb, cancelled, schema=schema, max_tokens=230 * len(files) + 128, temperature=0)
        except (ContextTooLargeError, IncompleteResponseError):
            if len(files) <= 2:
                return held('Independent review did not complete')
            middle = len(files) // 2
            # An overlapping member avoids manufacturing a one-file group at a split.
            left = self.review_destination(files[:middle+1], destination, existing, status_cb, cancelled, _retry=_retry)
            right = self.review_destination(files[middle:], destination, existing, status_cb, cancelled, _retry=_retry)
            result = {**left, **right}
            for identifier in left.keys() & right.keys():
                if left[identifier]['verdict'] != 'keep':
                    result[identifier] = left[identifier]
            return result
        try:
            value = json.loads(raw)
            decisions = value['decisions']
            if (type(value['coherent']) is not bool or value['reuse_existing'] not in ['', *paths]
                    or not isinstance(decisions, dict) or set(decisions) != set(keys)):
                raise ValueError('Incomplete independent review')
            result = {}
            unsupported = []
            for key, file in zip(keys, files):
                decision = decisions[key]
                if (not isinstance(decision, dict) or decision.get('verdict') not in {'keep', 'hold'}
                        or any(not isinstance(decision.get(k), str) for k in ('reason', 'evidence'))):
                    raise ValueError('Invalid review decision')
                if not value['coherent'] or value['reuse_existing']:
                    decision = {'verdict': 'hold', 'reason': ('Prefer the existing destination: ' + value['reuse_existing']
                        if value['reuse_existing'] else 'Proposed destination lacks a coherent shared purpose'), 'evidence': ''}
                elif decision['verdict'] == 'keep':
                    quote = decision['evidence']
                    candidates = [compact(quote)]
                    # Some small-model responses JSON-quote the passage a second time.
                    # Remove only transport decoration, then still require a literal source match.
                    try:
                        decoded = json.loads(quote)
                        if isinstance(decoded, str):
                            candidates.extend([compact(decoded), compact(decoded.replace('\\n', '\n'))])
                    except (ValueError, TypeError):
                        pass
                    grounded = next((q for q in candidates if q and q in compact(file['evidence'])), '')
                    if decision['reason'].strip() and grounded:
                        decision = {**decision, 'evidence': grounded}
                    else:
                        decision = {'verdict': 'hold', 'reason': 'Independent review lacked grounded support', 'evidence': ''}
                        unsupported.append(file['id'])
                result[file['id']] = decision
            if unsupported and _retry:
                log.debug('Retrying independent review for unsupported file IDs: %s', unsupported)
                corrected = self.review_destination(files, destination, existing, status_cb, cancelled, _retry=False)
                for identifier in unsupported:
                    result[identifier] = corrected[identifier]
            return result
        except (ValueError, TypeError, KeyError, AttributeError):
            return held('Independent review returned an incomplete or invalid judgment')

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

    def rewrite_query(self, query, status_cb=None, cancelled=None, today=None):
        """Turn a request into a semantic query, keywords and explicit metadata filters."""
        def strings(items, length):
            return {'type': 'array', 'maxItems': items, 'items': {'type': 'string', 'maxLength': length}}
        schema = {'type': 'object', 'additionalProperties': False, 'properties': {
            'query': {'type': 'string', 'maxLength': 240},
            'keywords': strings(8, 32),
            'kinds': {'type': 'array', 'maxItems': 4, 'items': {'enum': sorted(SEARCH_KINDS)}},
            'extensions': strings(4, 12), 'exclude_extensions': strings(4, 12), 'exclude_terms': strings(4, 48),
            'min_size_mb': {'type': 'number'}, 'max_size_mb': {'type': 'number'},
            'modified_after': {'type': 'string', 'maxLength': 10},
            'modified_before': {'type': 'string', 'maxLength': 10},
            'sort': {'enum': SEARCH_SORTS},
        }}
        # Filters are optional so a plain topic search does not spend tokens writing empty fields.
        schema['required'] = ['query', 'keywords']
        defaults = {'kinds': [], 'extensions': [], 'exclude_extensions': [], 'exclude_terms': [],
                    'min_size_mb': 0., 'max_size_mb': 0., 'modified_after': '', 'modified_before': '',
                    'sort': 'relevance'}
        today = today or date.today()
        settings = self.model_manager.search_settings
        try:
            user = json.dumps({'today': f'{today:%Y-%m-%d (%A)}', 'request': query}, ensure_ascii=False)
            raw = self._generate(SEARCH_QUERY_PROMPT, user, status_cb, cancelled, schema=schema,
                                 max_tokens=settings['max_tokens'], temperature=settings['temperature'])
            value = json.loads(raw)
            if not isinstance(value, dict) or not isinstance(value.get('query'), str):
                raise ValueError('Missing rewritten query')
            value = {**defaults, **value}
            for key in ('keywords', 'kinds', 'extensions', 'exclude_extensions', 'exclude_terms'):
                if not isinstance(value.get(key), list) or any(not isinstance(x, str) for x in value[key]):
                    raise ValueError('Invalid query filters')
            for key in ('extensions', 'exclude_extensions'):
                value[key] = [('.' + x.lstrip('.')).lower() for x in value[key]
                              if re.fullmatch(r'\.?[a-zA-Z0-9]{1,10}', x)]
            value['query'] = value['query'].strip()[:240]
            value['keywords'] = list(dict.fromkeys(x.casefold().strip() for x in value['keywords'] if x.strip()))
            value['kinds'] = [x for x in value['kinds'] if x in SEARCH_KINDS]
            value['exclude_terms'] = [x.casefold().strip() for x in value['exclude_terms'] if x.strip()]
            for key in ('min_size_mb', 'max_size_mb'):
                number = value.get(key)
                value[key] = float(number) if isinstance(number, (int, float)) and number > 0 else 0.
            for key in ('modified_after', 'modified_before'):
                try:
                    value[key] = date.fromisoformat(value.get(key) or '').isoformat()
                except (TypeError, ValueError):
                    value[key] = ''  # Unparseable dates are dropped rather than guessed.
            if value.get('sort') not in SEARCH_SORTS or not ASKS_FOR_ORDER.search(query):
                value['sort'] = 'relevance'
            if not ASKS_FOR_TIME.search(query):
                value['modified_after'] = value['modified_before'] = ''
            if not ASKS_FOR_SIZE.search(query):
                value['min_size_mb'] = value['max_size_mb'] = 0.
            wanted = set(value['keywords']) | set(re.findall(r'\w+', value['query'].casefold()))
            value['exclude_terms'] = [x for x in value['exclude_terms'] if x not in wanted]
            if not ASKS_TO_EXCLUDE.search(query):
                value['exclude_terms'], value['exclude_extensions'] = [], []
            # A kind or format becomes a hard filter only when the request names it;
            # "lecture slides" must not lose its PDFs to a guessed "presentation" kind.
            said = {word[:-1] if len(word) > 3 and word.endswith('s') else word
                    for word in re.findall(r'[\w+]+', query.casefold())}
            value['kinds'] = [kind for kind in value['kinds'] if SEARCH_KIND_WORDS[kind] & said]
            value['extensions'] = [extension for extension in value['extensions'] if extension[1:] in said
                                   or any(SEARCH_KIND_WORDS[kind] & said for kind, extensions
                                          in SEARCH_KINDS.items() if extension in extensions)]
            return value
        except (ValueError, KeyError, TypeError) as exc:
            self.shutdown()
            raise LLMServerError('The local model returned an invalid search request') from exc
        except (LLMServerError, InterruptedError):
            self.shutdown()
            raise

    def review_results(self, query, files, status_cb=None, cancelled=None):
        """Final check of retrieved files against the request as typed; returns the keys to show."""
        if not files:
            return set()
        try:
            return self._review_results(query, files, status_cb, cancelled)
        except (LLMServerError, InterruptedError):
            self.shutdown()
            raise

    def _review_results(self, query, files, status_cb, cancelled):
        ids = ' | '.join(json.dumps(str(index)) for index in range(1, len(files) + 1))
        # Only candidate ids can be written, so the answer is short and always parseable.
        grammar = 'root ::= "[" ( id ( "," id )* )? "]"\nid ::= ' + ids
        rows = [{'id': index, **{k: v for k, v in file.items() if k != 'key'}}
                for index, file in enumerate(files, 1)]
        user = json.dumps({'request': query[:1000], 'candidates': rows}, ensure_ascii=False, separators=(',', ':'))
        try:
            raw = self._generate(SEARCH_REVIEW_PROMPT, user, status_cb, cancelled, grammar=grammar,
                                 max_tokens=3 * len(files) + 8,
                                 temperature=self.model_manager.search_settings['temperature'])
        except (ContextTooLargeError, IncompleteResponseError):
            if len(files) <= 1:
                return {file['key'] for file in files}  # Retrieval already vouched for it.
            middle = len(files) // 2
            return (self._review_results(query, files[:middle], status_cb, cancelled)
                    | self._review_results(query, files[middle:], status_cb, cancelled))
        try:
            listed = {int(index) for index in json.loads(raw)}
        except (ValueError, TypeError) as exc:
            raise LLMServerError('The local model returned an invalid search review') from exc
        return {file['key'] for index, file in enumerate(files, 1) if index in listed}

    def interrupt(self):
        server = self._server
        if server is not None:
            server.interrupt()

    def shutdown(self):
        if self._server:
            self._server.stop()
        self._server = None

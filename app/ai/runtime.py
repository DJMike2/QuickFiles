"""One local model server for organization, search and image descriptions."""
from datetime import date
import json
import logging
import re

from app.ai.llm_server import ContextTooLargeError, IncompleteResponseError, LLMServer, LLMServerError
from app.ai.model_runtime import ModelRuntime
from app.ai.decision_cache import DecisionCache, reuse_decision
from app.ai.evidence import compact, literal_passages
from app.ai.quotes import choices as quote_choices, numbered as numbered_quotes, restore as restore_quote
from app.ai.prompts import (FOLDER_ROLE_PROMPT, SEARCH_QUERY_PROMPT, SEARCH_REVIEW_PROMPT,
                            CATALOG_PROMPT, CATALOG_LITERAL_PROMPT, GROUPS_PROMPT,
                            MATCH_PROMPT, REVIEW_PROMPT)
from app.constants import SEARCH_KIND_WORDS, SEARCH_KINDS

SEARCH_SORTS = ['relevance', 'newest', 'oldest', 'largest', 'smallest']
PARALLEL_VRAM_GB = 7.5
log = logging.getLogger(__name__)


def _split_existing(rows, existing):
    # Split the larger input, including catalogs that cannot fit beside two files.
    return len(existing) > 1 and (len(rows) <= 2 or
        len(json.dumps(existing, ensure_ascii=False).encode('utf-8')) >
        len(json.dumps(rows, ensure_ascii=False).encode('utf-8')))


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


class AIRuntime(ModelRuntime):
    def __init__(self, binary_manager, model_manager):
        super().__init__()
        self.binary_manager = binary_manager
        self.model_manager = model_manager
        self._organizer_decisions = DecisionCache()

    def _new_server(self):
        self._organizer_decisions.clear()
        return LLMServer(self.binary_manager, self.model_manager.model_path,
                         {**self.model_manager.runtime_settings, 'parallel': self.slots},
                         mmproj=self.model_manager.mmproj_path)

    @property
    def slots(self):
        if not getattr(self.binary_manager, 'hardware', None) or self.binary_manager.hardware.vram_gb < PARALLEL_VRAM_GB:
            return 1
        return self.model_manager.runtime_settings['parallel']

    def shutdown(self):
        try:
            super().shutdown()
        finally:
            self._organizer_decisions.clear()

    def prestart(self, cancelled=None):
        if self.available:
            return self._load_async(cancelled=cancelled)

    @property
    def available(self):
        return self.binary_manager.is_available and self.model_manager.is_downloaded

    @property
    def backend_label(self):
        server = self._server
        if server and server.is_running:
            return f"Local AI ({server.backend.upper()})"
        return "Local AI ready" if self.available else "Local AI setup required"

    def _generate(self, system, user, status_cb=None, cancelled=None, *, max_tokens=None,
                  temperature=None, schema=None, grammar=None):
        if cancelled and cancelled():
            raise InterruptedError("Planning stopped")
        settings = self.model_manager.runtime_settings
        server = self._ensure_started(status_cb, cancelled)
        raw = server.chat(system, user, max_tokens or settings["max_tokens"],
                                settings["temperature"] if temperature is None else temperature,
                                schema=schema, grammar=grammar,
                                check_context=system in (GROUPS_PROMPT, REVIEW_PROMPT))
        if cancelled and cancelled():
            raise InterruptedError("Planning stopped")
        return raw

    @property
    def model_cache_key(self):
        return json.dumps({'model': self.model_manager.model_info,
                           'settings': self.model_manager.runtime_settings}, sort_keys=True)

    def describe_folder(self, evidence, status_cb=None, cancelled=None):
        schema = {'type': 'object', 'additionalProperties': False,
            'properties': {'observation': {'type': 'string', 'maxLength': 120},
                'roles': {'type': 'array', 'minItems': 1, 'maxItems': 1,
                          'items': {'enum': ['managed', 'filing', 'uncertain']}}},
            'required': ['observation', 'roles']}
        raw = self._generate(FOLDER_ROLE_PROMPT, json.dumps({'folders': [
            {'id': 1, 'evidence': evidence}]}, ensure_ascii=False), status_cb, cancelled,
            schema=schema, max_tokens=96, temperature=0)
        try:
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {'observation', 'roles'} or len(value['roles']) != 1:
                raise ValueError('Incomplete folder classification')
            role, observation = value['roles'][0], value['observation']
            if (role not in {'managed', 'filing', 'uncertain'} or not isinstance(observation, str)
                    or not observation.strip() or len(observation) > 120):
                raise ValueError('Invalid folder classification')
            return {'role': role, 'observation': observation}
        except (ValueError, TypeError, KeyError, IndexError) as error:
            raise LLMServerError('The local model returned an invalid folder classification') from error

    def profile_items(self, rows, status_cb=None, cancelled=None):
        """Small batches retain every file; context overflow splits instead of sampling away files."""
        if not rows:
            return []
        scopes = ('course', 'client', 'project', 'topic', 'kind', 'unknown', 'mixed')
        properties = {'description': {'type': 'string', 'maxLength': 200},
            'scope_kind': {'enum': list(scopes)},
            'scope': {'type': 'array', 'maxItems': 4, 'items': {'type': 'string', 'maxLength': 80}},
            'topic': {'type': 'string', 'maxLength': 96},
            'quote': {'type': 'string', 'maxLength': 140}}
        passages = [['', *literal_passages(row['evidence'])] for row in rows]
        local_rows = [{**row, 'id': i, 'support': numbered_quotes(options)}
                      for i, (row, options) in enumerate(zip(rows, passages), 1)]
        user = json.dumps({'items': local_rows}, ensure_ascii=False)
        max_tokens = 320 * len(rows) + 64
        # UTF-8 bytes conservatively bound tokens; reserve room for output and chat framing.
        indexed = (len(user.encode('utf-8')) + len(CATALOG_PROMPT.encode('utf-8')) + max_tokens + 256
                   <= self.model_manager.runtime_settings.get('context_tokens', 8192))
        if not indexed:
            local_rows = [{**row, 'id': i} for i, row in enumerate(rows, 1)]
            user = json.dumps({'items': local_rows}, ensure_ascii=False)
        keys = [str(i) for i in range(1, len(rows)+1)]
        schema = {'type': 'object', 'additionalProperties': False, 'properties': {'profiles': {
            'type': 'object', 'additionalProperties': False,
            'properties': {key: {'type':'object', 'additionalProperties':False,
                'properties': {**properties, 'quote': {'enum':list(range(len(options))) if indexed else options}},
                'required':list(properties)} for key,options in zip(keys, passages)}, 'required': keys}},
            'required': ['profiles']}
        try:
            raw = self._generate(CATALOG_PROMPT if indexed else CATALOG_LITERAL_PROMPT, user,
                status_cb, cancelled, schema=schema, max_tokens=max_tokens, temperature=0)
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
            return [{**values[key], 'quote': restore_quote(values[key]['quote'], options), 'id': row['id']}
                    for key, row, options in zip(keys, rows, passages)]
        except (ValueError, TypeError, KeyError) as exc:
            raise LLMServerError('The local model returned an invalid evidence catalog') from exc

    @reuse_decision(GROUPS_PROMPT, lambda groups: bool(groups))
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
            if _split_existing(rows, existing):
                middle = len(existing) // 2
                log.info('Splitting shared-subject catalog: files=%d folders=%d', len(rows), len(existing))
                groups = (self.propose_groups(rows, existing[:middle], status_cb, cancelled)
                          + self.propose_groups(rows, existing[middle:], status_cb, cancelled))
                unique = {(g['path'], tuple(sorted(set(g['members'])))): g for g in groups}
                return list(unique.values())
            if len(rows) <= 2:
                log.warning('Shared-subject comparison could not complete: files=%d folders=%d; leaving files ungrouped',
                            len(rows), len(existing))
                return []
            middle = len(rows) // 2
            log.info('Splitting shared-subject files: files=%d folders=%d', len(rows), len(existing))
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

    @reuse_decision(MATCH_PROMPT, lambda decision: bool(decision['folder']))
    def choose_destination(self, file, destinations, status_cb=None, cancelled=None):
        passages = quote_choices(file['evidence'])
        paths = [row['path'] for row in destinations]
        schema = {'type': 'object', 'additionalProperties': False, 'properties': {
            'folder': {'enum': ['', *paths]}, 'match': {'enum': ['clear', 'uncertain']},
            'reason': {'type': 'string', 'maxLength': 80},
            'evidence': {'enum': list(range(len(passages)))}},
            'required': ['folder', 'match', 'reason', 'evidence']}
        try:
            raw = self._generate(MATCH_PROMPT, json.dumps({'file': {**{k: v for k, v in file.items() if k != 'evidence'},
                'evidence': numbered_quotes(passages)}, 'destinations': destinations},
                ensure_ascii=False, separators=(',', ':')), status_cb, cancelled,
                schema=schema, max_tokens=512, temperature=0)
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
            if not isinstance(value, dict):
                raise ValueError('Invalid destination decision')
            value['evidence'] = restore_quote(value.get('evidence'), passages)
            if (value.get('folder') not in ['', *paths] or value.get('match') not in ['clear', 'uncertain']
                    or any(not isinstance(value.get(k), str) for k in ['reason', 'evidence'])):
                raise ValueError('Invalid destination decision')
            if value['folder'] and (value['match'] != 'clear' or not value['reason'].strip()
                    or not compact(value['evidence']) or compact(value['evidence']) not in compact(file['evidence'])):
                return {'folder': '', 'match': 'uncertain', 'reason': 'Match lacked supporting evidence', 'evidence': ''}
            return value
        except (ValueError, TypeError, KeyError) as exc:
            raise LLMServerError('The local model returned an invalid destination decision') from exc

    @staticmethod
    def _grounded_quote(quote, evidence):
        candidates = [compact(quote)]
        # Decode extra JSON quoting, then require an exact source passage.
        try:
            decoded = json.loads(quote)
            if isinstance(decoded, str):
                candidates.extend([compact(decoded), compact(decoded.replace('\\n', '\n'))])
        except (ValueError, TypeError):
            pass
        return next((quote for quote in candidates if quote and quote in compact(evidence)), '')

    @reuse_decision(REVIEW_PROMPT, lambda decisions: bool(decisions) and all(
        decision['verdict'] == 'keep' for decision in decisions.values()))
    def review_destination(self, files, destination, existing, status_cb=None, cancelled=None, *, _retry=True):
        # Review evidence independently of the assignment rationale.
        if not files:
            return {}
        keys = [str(i) for i in range(1, len(files) + 1)]
        passages = [quote_choices(file['evidence']) for file in files]
        paths = [row['path'] for row in existing]
        item = {'type': 'object', 'additionalProperties': False, 'properties': {
            'verdict': {'enum': ['keep', 'hold']}, 'reason': {'type': 'string', 'maxLength': 80},
            'evidence': {'type': 'string', 'maxLength': 140}},
            'required': ['verdict', 'reason', 'evidence']}
        schema = {'type': 'object', 'additionalProperties': False, 'properties': {
            'coherent': {'type': 'boolean'}, 'reuse_existing': {'enum': ['', *paths]},
            'decisions': {'type': 'object', 'additionalProperties': False,
                          'properties': {key: {**item, 'properties':{**item['properties'],
                              'evidence':{'enum':list(range(len(options)))}}}
                              for key,options in zip(keys, passages)}, 'required': keys}},
            'required': ['coherent', 'reuse_existing', 'decisions']}
        def held(reason):
            return {file['id']: {'verdict': 'hold', 'reason': reason, 'evidence': ''} for file in files}
        try:
            raw = self._generate(REVIEW_PROMPT, json.dumps({'destination': destination,
                'existing_destinations': existing,
                'files': [{**{k: v for k, v in row.items() if k != 'evidence'},
                           'id': i, 'evidence': numbered_quotes(options)}
                          for i, (row, options) in enumerate(zip(files, passages), 1)],
                **({'feedback': 'A previous keep lacked a valid supporting passage. '
                    'For each keep, select one positive integer from THAT file numbered evidence. '
                    'Use 0 for holds; never invent an index.'} if not _retry else {})},
                ensure_ascii=False, separators=(',', ':')),
                status_cb, cancelled, schema=schema, max_tokens=230 * len(files) + 128, temperature=0)
        except (ContextTooLargeError, IncompleteResponseError):
            if _split_existing(files, existing):
                middle = len(existing) // 2
                log.info('Splitting independent-review catalog: files=%d folders=%d', len(files), len(existing))
                left = self.review_destination(files, destination, existing[:middle], status_cb, cancelled, _retry=_retry)
                right = self.review_destination(files, destination, existing[middle:], status_cb, cancelled, _retry=_retry)
            else:
                if len(files) <= 2:
                    log.warning('Independent review could not complete: files=%d folders=%d; holding files',
                                len(files), len(existing))
                    return held('Independent review did not complete')
                middle = len(files) // 2
                log.info('Splitting independent-review files: files=%d folders=%d', len(files), len(existing))
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
                if isinstance(decision, dict):
                    decision = {**decision, 'evidence': restore_quote(
                        decision.get('evidence'), passages[int(key)-1])}
                if (not isinstance(decision, dict) or decision.get('verdict') not in {'keep', 'hold'}
                        or any(not isinstance(decision.get(k), str) for k in ('reason', 'evidence'))):
                    raise ValueError('Invalid review decision')
                if not value['coherent'] or value['reuse_existing']:
                    decision = {'verdict': 'hold', 'reason': ('Prefer the existing destination: ' + value['reuse_existing']
                        if value['reuse_existing'] else 'Proposed destination lacks a coherent shared purpose'), 'evidence': ''}
                elif decision['verdict'] == 'keep':
                    grounded = self._grounded_quote(decision['evidence'], file['evidence'])
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

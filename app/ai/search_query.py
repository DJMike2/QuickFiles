import calendar
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, timedelta
import json
import math
import re

from app.constants import SEARCH_KINDS
from app.ai.prompts import (SEARCH_QUERY_PROMPT, SEARCH_COMPACT_PROMPT, SEARCH_CALENDAR_PROMPT,
                            SEARCH_SIZE_PROMPT, SEARCH_SUBJECT_PROMPT, SEARCH_ORDER_PROMPT,
                            SEARCH_PHRASE_PROMPT, SEARCH_CONTENT_CHECK)

SORTS = ['relevance', 'newest', 'oldest', 'largest', 'smallest']
DEFAULTS = dict(query='', keywords=[], kinds=[], extensions=[], exclude_terms=[], exclude_extensions=[],
                min_size_mb=0, max_size_mb=0, modified_after='', modified_before='', sort='relevance')
UNITS = {'B': 1 / 1024**2, 'KB': 1 / 1024, 'MB': 1, 'GB': 1024, 'TB': 1024**2}
DATE = {'type': 'string', 'pattern': r'^[0-9]{4}-[0-9]{2}-[0-9]{2}$'}


def object_schema(properties):
    return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)


def array_schema(items):
    return dict(type='array', items=items)


STRINGS = array_schema({'type': 'string'})
FLAT_FIELDS = {'query': {'type': 'string'}, 'keywords': STRINGS,
               'kinds': array_schema({'enum': list(SEARCH_KINDS)}),
               'extensions': STRINGS, 'exclude_extensions': STRINGS, 'exclude_terms': STRINGS,
               'min_size_mb': {'type': 'number'}, 'max_size_mb': {'type': 'number'},
               'modified_after': {'type': 'string'}, 'modified_before': {'type': 'string'},
               'sort': {'enum': SORTS}}
FLAT_SCHEMA = dict(type='object', properties=FLAT_FIELDS, additionalProperties=False,
                   required=['query', 'keywords'])
COMPACT_SCHEMA = object_schema({
    'subject': {'type': 'string'}, 'types': STRINGS, 'omit': STRINGS,
    'size_mb': {'anyOf': [{'type': 'null'}, dict(type='array', minItems=2, maxItems=2,
                                               items={'type': 'number'})]},
    'modified': {'anyOf': [{'type': 'null'}, dict(type='array', minItems=2, maxItems=2,
                                                items={'type': 'string'})]},
    'order': {'enum': [None, 'newest', 'oldest', 'largest', 'smallest']}})
CALENDAR_SCHEMA = object_schema({
    'types': dict(type='array', maxItems=4, items={'anyOf': [
        {'enum': list(SEARCH_KINDS)}, {'type': 'string', 'pattern': r'^\.[a-zA-Z0-9]{1,10}$'}]}),
    'omit': dict(type='array', maxItems=4, items={'type': 'string', 'maxLength': 48}),
    'size_mb': {'anyOf': [{'type': 'null'}, dict(type='array', minItems=2, maxItems=2,
                                               items={'type': 'number', 'minimum': 0})]},
    'modified': {'anyOf': [{'type': 'null'},
        object_schema({'relation': {'enum': ['before', 'after', 'since', 'until', 'on']}, 'date': DATE}),
        object_schema({'relation': {'const': 'period'},
                       'unit': {'enum': ['day', 'week', 'month', 'quarter', 'year']}, 'anchor': DATE}),
        object_schema({'relation': {'const': 'between'}, 'first': DATE, 'last': DATE})]},
    'order': {'anyOf': [{'type': 'null'}, object_schema({
        'field': {'enum': ['size', 'modified']}, 'direction': {'enum': ['ascending', 'descending']}})]},
    'content_about': {'anyOf': [{'type': 'null'}, {'type': 'string', 'maxLength': 160}]}})
BOUND_SCHEMA = {'anyOf': [{'type': 'null'}, object_schema({
    'value': {'type': 'number', 'minimum': 0}, 'unit': {'enum': list(UNITS)}})]}
SIZE_SCHEMA = object_schema({'minimum': BOUND_SCHEMA, 'maximum': BOUND_SCHEMA})


def date_bounds(modified):
    if not modified:
        return '', ''
    first = last = None
    relation = modified['relation']
    if relation == 'between':
        first, last = date.fromisoformat(modified['first']), date.fromisoformat(modified['last'])
    elif relation == 'period':
        anchor, unit = date.fromisoformat(modified['anchor']), modified['unit']
        if unit == 'day':
            first = last = anchor
        elif unit == 'week':
            first = anchor - timedelta(days=anchor.weekday())
            last = first + timedelta(days=6)
        else:
            month = 1 if unit == 'year' else (anchor.month - 1) // 3 * 3 + 1 if unit == 'quarter' else anchor.month
            count = 12 if unit == 'year' else 3 if unit == 'quarter' else 1
            first = date(anchor.year, month, 1)
            end_month = month + count - 1
            last = date(anchor.year, end_month, calendar.monthrange(anchor.year, end_month)[1])
    else:
        point = date.fromisoformat(modified['date'])
        if relation == 'on':
            first = last = point
        elif relation == 'before':
            last = point - timedelta(days=1)
        elif relation == 'after':
            first = point + timedelta(days=1)
        elif relation == 'since':
            first = point
        elif relation == 'until':
            last = point
        else:
            raise ValueError('Invalid date relation')
    if first and last and first > last:
        raise ValueError('Invalid date range')
    return first.isoformat() if first else '', last.isoformat() if last else ''


def example_plans(today, structured):
    rows = [
        ('please find the tunes', '', ['audio'], [], None, None, None),
        ('spreadsheets about inventory', 'inventory', ['spreadsheet'], [], None, None, None),
        ('family pictures', 'family', ['image'], [], None, None, None),
        ('presentation decks for the ecology meeting', 'ecology meeting', ['presentation'], [], None, None, None),
        ('disk images', '', ['disk image'], [], None, None, None),
        ('packed archives', '', ['archive'], [], None, None, None),
        ('JPEG diagrams', 'diagrams', ['.jpeg'], [], None, None, None),
        ('files edited today', '', [], [], None, dict(relation='on', date=today.isoformat()), None),
        ('updated in 2022', '', [], [], None, dict(relation='period', unit='year', anchor='2022-01-01'), None),
        ('edited before 2026-04-10', '', [], [], None, dict(relation='before', date='2026-04-10'), None),
        ('edited after 2026-04-10', '', [], [], None, dict(relation='after', date='2026-04-10'), None),
        ("notes about yesterday's storm", "yesterday's storm", [], [], None, None, None),
        ('larger than seven KB', '', [], [], [7 / 1024, 0], None, None),
        ('files above 3 gigabytes', '', [], [], [3072, 0], None, None),
        ('files at most 8 megabytes', '', [], [], [0, 8], None, None),
        ('the most recent build logs', 'build logs', [], [], None, None, 'newest'),
        ('ignore rough copies when finding meal plans', 'meal plans', [], ['rough copy'], None, None, None),
        ('mes factures sans copies', 'factures', [], ['copie'], None, None, None),
        ('small dog care notes', 'small dog care', [], [], None, None, None)]
    if structured:
        rows += [
            ('pictures ordered by descending modification date', '', ['image'], [], None, None, 'newest'),
            ('old compressed files above 4MB', '', ['archive'], [], [4, 0], None, 'oldest'),
            ('JSON files changed since 2026-02-03', '', ['.json'], [], None,
             dict(relation='since', date='2026-02-03'), None)]
    examples = []
    for request, subject, types, omit, size, modified, order in rows:
        if structured:
            sorting = (dict(field='size' if order in ('largest', 'smallest') else 'modified',
                            direction='ascending' if order in ('oldest', 'smallest') else 'descending')
                       if order else None)
            plan = dict(types=types, omit=omit, size_mb=size, modified=modified, order=sorting,
                        content_about=subject or None)
        else:
            plan = dict(subject=subject, types=types, omit=omit, size_mb=size,
                        modified=list(date_bounds(modified)) if modified else None, order=order)
        examples.append(dict(request=request, plan=plan))
    if structured:
        return '\nExamples:\n' + '\n'.join(json.dumps(row, ensure_ascii=False, separators=(',', ':')) for row in examples)
    return f'\n\nMore examples (calendar {today.isoformat()}):\n' + '\n'.join(
        json.dumps(row, ensure_ascii=False) for row in examples)


def parse_plan(value, structured):
    types, omitted = value['types'], value['omit']
    low, high = value['size_mb'] or [0, 0]
    if low < 0 or high < 0 or low and high and low > high:
        raise ValueError('Invalid size range')
    if structured:
        first, last = date_bounds(value['modified'])
        order = value['order']
        sort = ({('size', 'ascending'): 'smallest', ('size', 'descending'): 'largest',
                 ('modified', 'ascending'): 'oldest', ('modified', 'descending'): 'newest'}[
                    order['field'], order['direction']] if order else 'relevance')
        query = value['content_about'] or ''
    else:
        first, last = value['modified'] or ['', '']
        sort, query = value['order'] or 'relevance', value['subject']
    return dict(query=query, keywords=[], kinds=[kind for kind in types if kind in SEARCH_KINDS],
                extensions=[name.lower() for name in types if name.startswith('.')],
                exclude_terms=[name.casefold() for name in omitted if not name.startswith('.')],
                exclude_extensions=[name.lower() for name in omitted if name.startswith('.')],
                min_size_mb=low, max_size_mb=high, modified_after=first, modified_before=last, sort=sort)


def normalize(plan):
    plan = {**DEFAULTS, **plan}
    if not isinstance(plan['query'], str):
        raise ValueError('Invalid subject')
    for key in ('keywords', 'kinds', 'extensions', 'exclude_extensions', 'exclude_terms'):
        if not isinstance(plan[key], list) or any(not isinstance(item, str) for item in plan[key]):
            raise ValueError('Invalid search filters')
    for key in ('extensions', 'exclude_extensions'):
        plan[key] = list(dict.fromkeys('.' + item.lstrip('.').lower() for item in plan[key]
                                     if re.fullmatch(r'\.?[a-zA-Z0-9]{1,10}', item)))
    plan['kinds'] = [kind for kind in plan['kinds'] if kind in SEARCH_KINDS
                     and not set(plan['extensions']).intersection(SEARCH_KINDS[kind])]
    for key in ('keywords', 'exclude_terms'):
        plan[key] = list(dict.fromkeys(item.casefold().strip() for item in plan[key] if item.strip()))
    for key in ('min_size_mb', 'max_size_mb'):
        if not isinstance(plan[key], (int, float)) or not math.isfinite(plan[key]) or plan[key] < 0:
            raise ValueError('Invalid search size')
    if plan['sort'] not in SORTS:
        raise ValueError('Invalid search order')
    return plan


def combine(plans):
    result = dict(DEFAULTS)
    groups = [('kinds', 'extensions'), ('exclude_terms', 'exclude_extensions'),
              ('min_size_mb', 'max_size_mb'), ('sort',)]
    date_fields = ('modified_after', 'modified_before')
    same_dates = all(plans[1][key] == plans[2][key] for key in date_fields)
    if (all(plan['modified_after'] or plan['modified_before'] for plan in plans)
        or same_dates and any(plans[2][key] for key in date_fields)):
        groups.append(('modified_after', 'modified_before'))
    for fields in groups:
        values = []
        for plan in plans:
            value = tuple(tuple(sorted(plan[key])) if isinstance(DEFAULTS[key], list) else plan[key] for key in fields)
            if fields == ('modified_after', 'modified_before'):
                try:
                    for point in value:
                        if point:
                            date.fromisoformat(point)
                except (TypeError, ValueError):
                    value = None
            values.append(value)
        counts = Counter(value for value in values if value is not None)
        if not counts:
            raise ValueError('Invalid date votes')
        chosen = max((index for index in reversed(range(len(plans))) if values[index] is not None),
                     key=lambda index: counts[values[index]])
        result.update({key: plans[chosen][key] for key in fields})
    return result


def rewrite_search(generate, request, today=None, workers=1, max_tokens=384, temperature=0):
    today = today or date.today()
    week_start = today - timedelta(days=today.weekday())
    user = json.dumps({'calendar': dict(today=f'{today:%Y-%m-%d %A}',
        yesterday=(today - timedelta(days=1)).isoformat(),
        last_week=[(week_start - timedelta(days=7)).isoformat(), (week_start - timedelta(days=1)).isoformat()],
        this_month=[today.replace(day=1).isoformat(), today.replace(day=calendar.monthrange(today.year, today.month)[1]).isoformat()]),
        'request': request}, ensure_ascii=False)

    def original():
        raw = generate(SEARCH_QUERY_PROMPT, json.dumps({'today': f'{today:%Y-%m-%d (%A)}', 'request': request},
                       ensure_ascii=False), schema=FLAT_SCHEMA, max_tokens=max_tokens, temperature=temperature)
        return normalize(json.loads(raw))

    def draft(structured):
        schema = deepcopy(CALENDAR_SCHEMA if structured else COMPACT_SCHEMA)
        if structured:
            points = []
            for point in re.findall(r'\b[0-9]{4}-[0-9]{2}-[0-9]{2}\b', request):
                try:
                    date.fromisoformat(point)
                except ValueError:
                    continue
                points.append(point)
            if points:
                for option in schema['properties']['modified']['anyOf']:
                    if 'date' in option.get('properties', {}):
                        option['properties']['date'] = {'enum': list(dict.fromkeys(points))}
        prompt = (SEARCH_CALENDAR_PROMPT if structured else SEARCH_COMPACT_PROMPT) + example_plans(today, structured) + SEARCH_CONTENT_CHECK
        raw = generate(prompt, user, schema=schema, max_tokens=384 if structured else 256, temperature=0)
        return normalize(parse_plan(json.loads(raw), structured))

    def classify(prompt, field, text, labels):
        raw = generate(prompt, json.dumps({field: text}, ensure_ascii=False),
                       schema={'enum': labels}, max_tokens=16, temperature=0)
        value = json.loads(raw)
        if value not in labels:
            raise ValueError('Invalid search classification')
        return value

    with ThreadPoolExecutor(max_workers=min(workers, 5)) as pool:
        work = [pool.submit(original), pool.submit(draft, False), pool.submit(draft, True),
                pool.submit(classify, SEARCH_SUBJECT_PROMPT, 'request', request, ['topic', 'metadata']),
                pool.submit(classify, SEARCH_ORDER_PROMPT, 'request', request, SORTS)]
        plans = [future.result() for future in work[:3]]
        has_subject = work[3].result() == 'topic'
        requested_order = work[4].result()
    result = combine(plans)
    positive = next((plan['query'] for plan in (plans[2], plans[0], plans[1]) if plan['query'].strip()), '')
    if (not has_subject and positive and plans[2]['query'].strip()
        and sum(bool(plan['query'].strip()) for plan in plans) >= 2):
        has_subject = classify(SEARCH_PHRASE_PROMPT, 'positive_phrase', positive, ['content', 'attributes']) == 'content'
    if has_subject:
        result.update(query=positive or request, keywords=plans[0]['keywords'])
    else:
        result.update(query='', keywords=[])
        if not any(result[key] for key in ('kinds', 'extensions', 'exclude_terms', 'exclude_extensions',
            'min_size_mb', 'max_size_mb', 'modified_after', 'modified_before')) and result['sort'] == 'relevance':
            proposed = [tuple(plan['kinds']) for plan in plans if plan['kinds']]
            if proposed:
                result['kinds'] = list(max(reversed(proposed), key=Counter(proposed).get))
        # Resolve a literal physical format within the kind already selected by the model.
        literals = set(re.findall(r'[\w+-]+', request.casefold()))
        formats = set().union(*(SEARCH_KINDS[kind] for kind in result['kinds'])) if result['kinds'] else set()
        named = sorted(extension for extension in formats if extension[1:] in literals)
        if named:
            result['kinds'] = [kind for kind in result['kinds'] if not SEARCH_KINDS[kind].intersection(named)]
            result['extensions'] = list(dict.fromkeys(result['extensions'] + named))
    result['sort'] = requested_order
    if result['min_size_mb'] or result['max_size_mb']:
        raw = generate(SEARCH_SIZE_PROMPT, json.dumps({'request': request}, ensure_ascii=False),
                       schema=SIZE_SCHEMA, max_tokens=128, temperature=0)
        size = json.loads(raw)
        limits = []
        for key in ('minimum', 'maximum'):
            bound = size[key]
            number = bound['value'] * UNITS[bound['unit']] if bound else 0
            if not math.isfinite(number) or number < 0:
                raise ValueError('Invalid size bound')
            limits.append(number)
        low, high = limits
        if low and high and low > high:
            raise ValueError('Invalid size range')
        result.update(min_size_mb=low, max_size_mb=high)
    return result

"""Literal identity matching shared by preparation and organization."""
import re
from app.ai.evidence import compact

ACADEMIC = re.compile(r'\b(homework|lecture|course|exam|syllabus|laboratory|quiz|midterm)\b', re.I)
COURSE = re.compile(r'\b([A-Z]{2,8})(?:\s*/\s*([A-Z]{2,8}))?[ ._-]*(\d{2,4})(?!\d)', re.I)
COURSE_PREFIXES = set('MATH STAT CMPSC CMSPC CS CSE CHEM PHYS BIO BIOL ECON HIST ENG ENGL SOC PSYCH PSYC'.split())
NOT_COURSES = set('HOMEWORK HW ASSIGNMENT CHAPTER PROJECT EXERCISE QUIZ MIDTERM LECTURE FALL SPRING SUMMER '
                  'WINTER VERSION LAB PAGE PAGES CLASS TERM PART YEAR FIGURE TABLE RGB HSV ISBN HTTP HTTPS'.split())


def normalized(value):
    return re.sub(r'[^\w]+', '', value.casefold())


def course_codes(text):
    found = set()
    for match in COURSE.finditer(text):
        prefix, alias, number = match.groups()
        if prefix.upper() in NOT_COURSES:
            continue
        if prefix.upper() in COURSE_PREFIXES or (prefix.isupper() and len(prefix) <= 6 and ACADEMIC.search(text)):
            found.add(prefix.upper() + number)
            if alias:
                found.add(alias.upper() + number)
    return sorted(found)


def same_course(left, right):
    if normalized(left) == normalized(right):
        return True
    a, b = re.fullmatch(r'([A-Z]+)(\d+)', left), re.fullmatch(r'([A-Z]+)(\d+)', right)
    if not a or not b or a[2] != b[2] or len(a[1]) < 4 or len(a[1]) != len(b[1]):
        return False
    # Adjacent-letter transpositions in otherwise identical course codes are
    # spelling variants, not permission to infer a course from its topic.
    return any(a[1][:i] + a[1][i+1] + a[1][i] + a[1][i+2:] == b[1]
               for i in range(len(a[1])-1))


def same_scope(kind, left, right):
    if kind == 'course':
        return same_course(left, right)
    # A shared first word is not a shared identity (Acme Dental vs Acme Manufacturing).
    return normalized(left) == normalized(right)


def explicit_clients(text):
    """Recipient fields take precedence over ambiguous descriptions of billed work.

    ``Client: Example Studio. Invoice for translation services`` names one client.
    A heading ``Invoice for Example Studio`` remains supported when no recipient
    field exists, but lowercase service descriptions are not organization names.
    """
    fields = re.findall(r'\b(?:client|customer|bill(?:ed)? to)\s*:\s*([^.;…\n]{3,70})', text, re.I)
    if not fields:
        fields = [match[1] for match in re.finditer(
            r'\binvoice for\s+([^.;…\n]{3,70})', text, re.I)
            if match[1].strip()[:1].isupper()]
    return list(dict.fromkeys(compact(value) for value in fields))


def explicit_client(text):
    clients = explicit_clients(text)
    return clients[0] if clients else ''


def literal_header_identity(text):
    """Only an explicit identity in a heading, not a course mentioned later in prose."""
    heading = compact(text)[:220]
    if re.match(r'(?:Client\s*:|Customer\s*:|Bill(?:ed)? to\s*:)\s*', heading, re.I):
        clients = explicit_clients(text)
        if len(clients) == 1:
            return clients[0]
    for match in COURSE.finditer(heading):
        if match[1].upper() not in COURSE_PREFIXES:
            continue
        prefix = heading[:match.start()].strip()
        if match.start() <= 3 or (len(prefix) <= 90 and re.match(
                r'^(?:homework|solutions?|practice|lecture|course|exam|quiz|study guide|notes)', prefix, re.I)):
            return match[0]
    return ''


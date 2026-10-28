"""Differential checks against the original scope-partitioning algorithm."""
import random
import unittest

from app.core.organization import Profile, scope_compatible
from app.core.planner import KIND_FOLDERS, Planner


def original_partitions(rows):
    partitions = []
    for row in rows:
        p = row['profile']
        if p.scope_kind in {'unknown', 'mixed'}:
            continue
        scoped = p.scope_kind in {'course', 'client', 'project'}
        family = ('scoped' if scoped else 'unreadable_documents'
                  if row['basis'] != 'content' and row['kind'] in {'document', 'spreadsheet', 'presentation'}
                  else row['kind'] if row['basis'] != 'content' and row['kind'] in KIND_FOLDERS else 'documents')
        for group in partitions:
            if group['family'] == family and scope_compatible([r['profile'] for r in group['rows']] + [p]):
                group['rows'].append(row)
                break
        else:
            partitions.append({'family': family, 'rows': [row]})
    return partitions


def row(identifier, kind, scope=(), *, file_kind='document', basis='content'):
    return {'id': identifier, 'kind': file_kind, 'basis': basis,
            'profile': Profile('Subject '+str(identifier), kind, list(scope), 'Topic '+str(identifier), 'quote')}


class GroupingOptimizationTests(unittest.TestCase):
    def test_overlapping_scopes_do_not_become_transitive(self):
        rows = [row(1, 'course', ['CS101', 'CS102']), row(2, 'course', ['CS101']),
                row(3, 'course', ['CS101']), row(4, 'course', ['CS102'])]
        groups = Planner._partition_rows(rows)
        self.assertEqual([[r['id'] for r in group['rows']] for group in groups], [[1, 2, 3], [4]])
        self.assertEqual(groups, original_partitions(rows))

    def test_identical_scopes_with_different_subjects_keep_every_original_row_and_order(self):
        rows = [row(n, 'course', ['MATH415']) for n in range(60)]
        actual = Planner._partition_rows(rows)
        self.assertEqual(actual, original_partitions(rows))
        self.assertEqual(len(actual[0]['rows']), 60)
        self.assertTrue(all(left is right for left, right in zip(actual[0]['rows'], rows)))

    def test_seeded_mixed_inputs_match_original_partitions_exactly(self):
        rng = random.Random(42)
        scopes = [('topic', []), ('kind', []), ('unknown', []), ('mixed', []), ('course', []),
            ('course', ['CS101']), ('course', ['CS102']), ('course', ['CS101', 'CS102']),
            ('course', ['MATH415']), ('course', ['STAT415']), ('course', ['MATH415', 'STAT415']),
            ('course', ['CMPSC473']), ('course', ['CMSPC473']),
            ('client', ['Acme Manufacturing']), ('client', ['Acme Dental']), ('client', ['Acme']),
            ('project', ['Color tracking']), ('project', ['Color-tracking'])]
        for case in range(500):
            rows = []
            for n in range(rng.randrange(41)):
                kind, scope = rng.choice(scopes)
                rows.append(row(n, kind, scope,
                    file_kind=rng.choice(['document', 'spreadsheet', 'presentation', 'audio', 'image', 'archive', 'code']),
                    basis=rng.choice(['content', 'filename', 'archive_listing'])))
            with self.subTest(case=case):
                self.assertEqual(Planner._partition_rows(rows), original_partitions(rows))


if __name__ == '__main__':
    unittest.main()

"""Transport changes preserve full evidence, grounding and fail-closed decisions."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.ai.evidence import compact
from app.ai.quotes import choices, numbered, restore
from app.ai.runtime import AIRuntime


class IndexedEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.runtime = AIRuntime(None, SimpleNamespace(runtime_settings={}))
        self.files = [{'id': 31, 'kind': 'document', 'basis': 'content',
                       'evidence': 'Telescope calibration measures star positions and instrument alignment.'},
                      {'id': 76, 'kind': 'document', 'basis': 'content',
                       'evidence': 'Telescope alignment notes compare calibration against reference stars.'}]
        self.destination = {'path': 'Astronomy', 'existing': False}

    def response(self, evidence=1):
        return json.dumps({'coherent': True, 'reuse_existing': '', 'decisions': {
            str(i): {'verdict': 'keep', 'reason': 'Telescope calibration fits astronomy', 'evidence': evidence}
            for i in (1, 2)}})

    def test_restore_exact_file_specific_passages_and_original_ids(self):
        with patch.object(self.runtime, '_generate', return_value=self.response()) as generate:
            result = self.runtime.review_destination(self.files, self.destination, [])
        for file in self.files:
            self.assertEqual(result[file['id']]['evidence'], file['evidence'])
            self.assertEqual(result[file['id']]['verdict'], 'keep')
        payload = json.loads(generate.call_args.args[1])
        self.assertEqual([row['id'] for row in payload['files']], [1, 2])
        for original, wire in zip(self.files, payload['files']):
            self.assertEqual(' '.join(wire['evidence'].values()), compact(original['evidence']))
            self.assertNotIn('quote_choices', wire)
        properties = generate.call_args.kwargs['schema']['properties']['decisions']['properties']
        for key in ('1', '2'):
            self.assertEqual(properties[key]['properties']['evidence']['enum'], [0, 1])

    def test_all_long_unicode_evidence_is_present_once_without_sampling(self):
        source = ('écho ギター “quoted” — ﬁlm ' * 500) + ' UNIQUE_END_MARKER'
        options = choices(source)
        self.assertGreater(len(options), 16)
        self.assertEqual(' '.join(options[1:]), compact(source))
        self.assertEqual(' '.join(numbered(options).values()).count('UNIQUE_END_MARKER'), 1)
        self.assertTrue(all(len(p) <= 140 for p in options))

    def test_negative_and_out_of_range_indices_cannot_select_a_passage(self):
        options = choices('Actual source support')
        for value in (-1, 2, 999):
            self.assertEqual(restore(value, options), '')
        for value in (True, False, 1.0, None, [], {}):
            with self.assertRaises(ValueError):
                restore(value, options)

    def test_bad_positive_quote_retries_then_holds(self):
        for index in (0, -1, 500):
            with self.subTest(index=index), patch.object(self.runtime, '_generate', return_value=self.response(index)) as generate:
                result = self.runtime.review_destination(self.files, self.destination, [])
                self.assertEqual(generate.call_count, 2)
                self.assertTrue(all(row['verdict'] == 'hold' for row in result.values()))

    def test_literal_adapter_compatibility_still_requires_source_grounding(self):
        good = json.loads(self.response())
        for key, file in zip(('1', '2'), self.files):
            good['decisions'][key]['evidence'] = file['evidence']
        with patch.object(self.runtime, '_generate', return_value=json.dumps(good)):
            self.assertTrue(all(v['verdict'] == 'keep' for v in
                self.runtime.review_destination(self.files, self.destination, []).values()))
        good['decisions']['1']['evidence'] = 'Invented source quote'
        with patch.object(self.runtime, '_generate', return_value=json.dumps(good)):
            result = self.runtime.review_destination(self.files, self.destination, [])
        self.assertEqual(result[31]['verdict'], 'hold')

    def test_matching_restores_quote_and_rejects_an_unsupported_integer(self):
        good = {'folder': 'Astronomy', 'match': 'clear', 'reason': 'Telescope calibration topic', 'evidence': 1}
        with patch.object(self.runtime, '_generate', return_value=json.dumps(good)) as generate:
            result = self.runtime.choose_destination(self.files[0], [self.destination])
        self.assertEqual(result['evidence'], self.files[0]['evidence'])
        self.assertEqual(result['folder'], 'Astronomy')
        wire = json.loads(generate.call_args.args[1])['file']
        self.assertEqual(wire['evidence'], {'1': self.files[0]['evidence']})
        with patch.object(self.runtime, '_generate', return_value=json.dumps({**good, 'evidence': -1})):
            self.assertFalse(self.runtime.choose_destination(self.files[0], [self.destination])['folder'])

    def test_restored_evidence_cannot_supply_an_unlisted_destination(self):
        bad = {'folder': '../Outside', 'match': 'clear', 'reason': 'No', 'evidence': 1}
        with patch.object(self.runtime, '_generate', return_value=json.dumps(bad)):
            from app.ai.llm_server import LLMServerError
            with self.assertRaises(LLMServerError):
                self.runtime.choose_destination(self.files[0], [self.destination])

    def test_profiling_retains_original_text_and_literal_quote_contract(self):
        profiles = {str(i): {'description': 'Telescope calibration', 'scope_kind': 'topic',
                            'scope': [], 'topic': 'Astronomy', 'quote': file['evidence']}
                    for i, file in enumerate(self.files, 1)}
        with patch.object(self.runtime, '_generate', return_value=json.dumps({'profiles': profiles})) as generate:
            result = self.runtime.profile_items(self.files)
        self.assertEqual([r['id'] for r in result], [31, 76])
        wire = json.loads(generate.call_args.args[1])['items']
        schema = generate.call_args.kwargs['schema']['properties']['profiles']['properties']
        for i, (row, file) in enumerate(zip(wire, self.files), 1):
            self.assertEqual(row['evidence'], file['evidence'])
            self.assertIn(file['evidence'], schema[str(i)]['properties']['quote']['enum'])
            self.assertEqual(result[i-1]['quote'], file['evidence'])


if __name__ == '__main__':
    unittest.main()

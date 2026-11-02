"""Parser observability must not alter acceptance or API request behavior."""
import copy
import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import collect


class ReviewParseDiagnosticsTests(unittest.TestCase):
    def valid(self):
        return dict(id='requested', in_scope=True, topics=['Artificial Intelligence'],
                    is_innovation_policy=False, policy_areas=[], policy_relevance=2,
                    reason='Evidence', content_type='research_breakthrough',
                    technical_focus='Model architecture', scope_evidence='Research result',
                    title_ja='研究成果', summary_ja='新しいモデルを開発した。')

    def parse(self, rows):
        diagnostics = {}
        raw = json.dumps({'items': rows})
        result = collect.parse_japanese_summary_response(raw, {'requested'}, diagnostics)
        self.assertEqual(result, collect.parse_japanese_summary_response(raw, {'requested'}))
        self.assertTrue(all(type(v) is int for v in diagnostics.values()))
        return result, diagnostics

    def test_valid_and_exclusion_and_duplicate_counts(self):
        row = self.valid()
        result, counts = self.parse([row, row])
        self.assertTrue(result['requested']['in_scope'])
        self.assertEqual(counts['rows_accepted'], 2)
        self.assertEqual(counts['accepted_ids'], 1)
        self.assertEqual(counts['unresolved_ids'], 0)
        result, counts = self.parse([dict(id='requested', in_scope=False, reason='No evidence')])
        self.assertFalse(result['requested']['in_scope'])
        self.assertEqual(counts['accepted_ids'], 1)

    def test_each_rejection_path(self):
        cases = [
            ('row_not_object', None),
            ('id_not_requested', dict(self.valid(), id='secret-id')),
            ('missing_reason', dict(self.valid(), reason='')),
            ('missing_title_ja', dict(self.valid(), title_ja='')),
            ('missing_summary_ja', dict(self.valid(), summary_ja='')),
            ('invalid_content_type', dict(self.valid(), content_type='secret-type')),
            ('missing_technical_focus', dict(self.valid(), technical_focus='')),
            ('missing_scope_evidence', dict(self.valid(), scope_evidence='')),
            ('technology_policy_inconsistent', dict(self.valid(), content_type='technology_policy')),
            ('missing_allowed_topics', dict(self.valid(), topics=['secret-topic'])),
            ('missing_allowed_policy_areas', dict(self.valid(), is_innovation_policy=True)),
        ]
        for code, row in cases:
            with self.subTest(code=code):
                result, counts = self.parse([row])
                self.assertEqual(result, {})
                self.assertEqual(counts[code], 1)
                self.assertEqual(counts['unresolved_ids'], 1)
                self.assertNotIn('secret', json.dumps(counts))

    def test_empty_or_non_object_payload(self):
        for raw in ('{}', '{"items": []}', '[]'):
            counts = {}
            self.assertEqual(collect.parse_japanese_summary_response(raw, {'requested'}, counts), {})
            self.assertEqual(counts['rows_seen'], 0)
            self.assertEqual(counts['unresolved_ids'], 1)

    def test_invalid_json_and_invalid_items_preserve_exceptions(self):
        for raw, exception in [('not json', json.JSONDecodeError), ('{"items": null}', TypeError)]:
            with self.subTest(raw=raw):
                with self.assertRaises(exception):
                    collect.parse_japanese_summary_response(raw, {'requested'}, {})

    def test_request_logs_only_counters_and_calls_api_once(self):
        row = dict(self.valid(), reason='sk-proj-secret-raw-model-text')
        response = collect.requests.Response()
        response.status_code = 200
        response._content = json.dumps({'output_text': json.dumps({'items': [row]})}).encode()
        out = io.StringIO()
        stats = {'requests': 0}
        with mock.patch.object(collect.requests, 'post', return_value=response) as post, \
             contextlib.redirect_stdout(out), mock.patch.dict(os.environ, {'JAPANESE_SUMMARY_REQUEST_BUDGET': '2'}):
            result = collect.japanese_summary_request([{'canonical_id': 'requested', 'title': 'private input'}],
                                                       'fake-test-key', collect.DEFAULT_JAPANESE_SUMMARY_MODEL, stats)
        self.assertEqual(post.call_count, 1)
        self.assertEqual(stats['requests'], 1)
        self.assertIn('requested', result)
        log = out.getvalue()
        self.assertTrue(log.startswith('[REVIEW_PARSE] '))
        for secret in ('sk-proj', 'private input', 'fake-test-key', '研究成果', '"requested"'):
            self.assertNotIn(secret, log)
        counters = json.loads(log.removeprefix('[REVIEW_PARSE] '))
        self.assertEqual(counters['accepted_ids'], 1)


if __name__ == '__main__':
    unittest.main()

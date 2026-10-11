"""Offline regression tests for the observed OECD transport/parser failures."""
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('oecd_collector', ROOT / 'scripts/collect.py')
collector = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collector
SPEC.loader.exec_module(collector)
FIXTURES = ROOT / 'tests/fixtures/oecd'
NOW = datetime(2026, 10, 11, 0, tzinfo=timezone.utc)
POLICY_URL = 'https://www.oecd.org/en/blogs/2026/09/how-economic-security-is-shaping-science-technology-and-industrial-policy.html'
SPACE_URL = 'https://www.oecd.org/en/blogs/2026/10/what-is-the-space-economy-and-why-does-it-matter.html'


def fixture(name):
    return (FIXTURES / name).read_text()


def sitemap(*urls):
    return '\n'.join(f'[{url}]({url})\n2026-10-10T00:00:00Z' for url in urls)


def source(**overrides):
    config = json.loads((ROOT / 'config/sources.json').read_text())
    row = next(row for row in config['sources'] if row['name'] == 'OECD Newsroom')
    row['proxy_sitemap_urls'] = ['https://r.jina.ai/http://www.oecd.org/en-blogs-sitemap.xml']
    return {**row, **overrides}


class Response:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class Session:
    """Any unexpected request fails locally; there is no network fallback."""
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return Response(response)


class OECDRecoveryTests(unittest.TestCase):
    def collect(self, body, details=None, **overrides):
        src = source(**overrides)
        responses = {src['proxy_sitemap_urls'][0]: body}
        responses.update(details or {})
        session = Session(responses)
        items, result = collector.fetch_jina_sitemap_source(session, src, NOW - timedelta(days=14), NOW)
        return items, result, session

    def test_observed_xml_sitemap_is_parsed(self):
        rows, representation = collector.parse_jina_sitemap(fixture('data-sitemap-xml.txt'))
        self.assertEqual(representation, 'xml')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0], ('https://www.oecd.org/en/data.html',) * 2 + ('2026-07-08T14:21:41.345Z',))

    def test_markdown_and_xml_known_articles_keep_scope_and_real_dates(self):
        details = {
            collector.jina_reader_url(POLICY_URL): fixture('policy-metadata.txt'),
            collector.jina_reader_url(SPACE_URL): fixture('space-metadata.txt'),
        }
        xml = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + ''.join(
            f'<url><loc>{url}</loc><lastmod>2026-10-10T00:00:00Z</lastmod></url>'
            for url in [POLICY_URL, SPACE_URL]
        ) + '</urlset>'
        for body in [sitemap(POLICY_URL, SPACE_URL), xml]:
            with self.subTest(format=body[:10]):
                items, result, session = self.collect(body, details)
                self.assertEqual(result.status, 'ok')
                self.assertEqual({item['url'] for item in items}, {POLICY_URL, SPACE_URL})
                by_url = {item['url']: item for item in items}
                self.assertTrue(by_url[POLICY_URL]['published_at'].startswith('2026-09-29'))
                self.assertTrue(by_url[SPACE_URL]['published_at'].startswith('2026-10-06'))
                self.assertTrue(by_url[POLICY_URL]['policy_areas'])
                self.assertIn('Space', by_url[SPACE_URL]['topics'])
                self.assertEqual(result.diagnostics['detail_pages'], 2)
                for candidate in result.diagnostics['candidates']:
                    self.assertEqual(candidate['sitemap_lastmod'], '2026-10-10T00:00:00Z')
                    self.assertEqual(candidate['discovery_date'], '2026-10-10T00:00:00+00:00')
                    self.assertTrue(candidate['final_title'])
                    self.assertLessEqual(len(candidate['final_title']), 200)
                self.assertEqual(len(session.calls), 3)

    def test_http_200_sitemap_challenge_is_error_not_healthy_empty(self):
        items, result, session = self.collect(fixture('challenge.txt'))
        self.assertEqual(items, [])
        self.assertEqual(result.status, 'error')
        self.assertEqual(result.diagnostics['sitemap_errors'], 1)
        self.assertEqual(result.diagnostics['sitemaps'][0]['reason'], 'challenge_or_error_page')
        self.assertEqual(len(session.calls), 1)

    def test_partial_sitemap_failure_retains_valid_items_and_reports_error(self):
        src = source(proxy_sitemap_urls=['https://reader/one', 'https://reader/two'])
        session = Session({
            'https://reader/one': fixture('challenge.txt'),
            'https://reader/two': sitemap(SPACE_URL),
            collector.jina_reader_url(SPACE_URL): fixture('space-metadata.txt'),
        })
        items, result = collector.fetch_jina_sitemap_source(session, src, NOW - timedelta(days=14), NOW)
        self.assertEqual(len(items), 1)
        self.assertEqual(result.status, 'error')
        self.assertEqual([row['status'] for row in result.diagnostics['sitemaps']], ['error', 'ok'])

    def test_challenge_does_not_overwrite_verified_slug_with_error_title(self):
        items, result, _ = self.collect(sitemap(POLICY_URL), {
            collector.jina_reader_url(POLICY_URL): fixture('challenge.txt'),
        })
        self.assertEqual(len(items), 1)
        self.assertIn('industrial policy', items[0]['title'])
        self.assertEqual(items[0]['summary'], '')
        self.assertEqual(result.diagnostics['candidates'][0]['metadata'], 'challenge_or_error_page')
        self.assertEqual(result.diagnostics['detail_fallbacks'], 1)

    def test_same_domain_wrong_page_and_error_warning_revert_to_sitemap(self):
        correct = fixture('policy-metadata.txt')
        cases = [
            (correct.replace(POLICY_URL.replace('https:', 'http:'), SPACE_URL), 'source_identity_mismatch'),
            (correct.replace('Markdown Content:', 'Warning: Target URL returned error 403: Forbidden\nMarkdown Content:'), 'challenge_or_error_page'),
        ]
        for body, reason in cases:
            with self.subTest(reason=reason):
                diagnostics = {}
                actual = collector.fetch_jina_page_metadata(
                    Session({collector.jina_reader_url(POLICY_URL): body}), POLICY_URL, 'oecd.org',
                    'verified industrial policy', '', NOW, diagnostics,
                )
                self.assertEqual(actual, ('verified industrial policy', '', NOW, False))
                self.assertEqual(diagnostics['metadata'], reason)

    def test_probe_writes_diagnostics_before_returning_unhealthy(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        try:
            import probe_source
        finally:
            sys.path.pop(0)
        _, result, _ = self.collect(fixture('challenge.txt'))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'probe.json'
            args = ['probe_source.py', '--source', 'OECD Newsroom', '--output', str(output)]
            with mock.patch.object(sys, 'argv', args), mock.patch.object(
                probe_source.collect, 'fetch_source', return_value=([], result)
            ), mock.patch('builtins.print'):
                exit_code = probe_source.main()
            payload = json.loads(output.read_text())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload['diagnostics'], result.diagnostics)
        self.assertEqual(payload['status'], 'error')

    def test_blank_title_does_not_consume_the_next_header(self):
        body = fixture('policy-metadata.txt')
        body = body.replace('Title: How economic security is shaping science, technology and industrial policy', 'Title: ')
        actual = collector.fetch_jina_page_metadata(
            Session({collector.jina_reader_url(POLICY_URL): body}), POLICY_URL, 'oecd.org',
            'verified industrial policy', '', NOW,
        )
        self.assertEqual(actual[0], 'verified industrial policy')
        self.assertNotIn('URL Source:', actual[0])

    def test_parser_rejects_malformed_or_unrecognized_payloads(self):
        for body in ['', 'Title: Unexpected response', '<urlset><url></urlset>']:
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    collector.parse_jina_sitemap(body)
        self.assertEqual(collector.parse_jina_sitemap('<urlset></urlset>'), ([], 'xml'))
        self.assertEqual(collector.parse_jina_sitemap('<urlset/>'), ([], 'xml'))

    def test_date_only_markdown_and_missing_xml_date_are_visible(self):
        rows, _ = collector.parse_jina_sitemap(f'[{POLICY_URL}]({POLICY_URL})\n2026-10-10')
        self.assertEqual(rows[0][2], '2026-10-10')
        items, result, _ = self.collect(f'<urlset><url><loc>{POLICY_URL}</loc></url></urlset>')
        self.assertEqual(items, [])
        self.assertEqual(result.diagnostics['rejections']['sitemap_date_missing'], 1)

    def test_rejection_diagnostics_are_bounded_and_do_not_relax_scope(self):
        urls = [f'https://www.oecd.org/en/blogs/2026/10/tourism-statistics-{i}.html' for i in range(75)]
        urls += ['https://other.example/industrial-policy.html', 'https://www.oecd.org/en/blogs/2025/01/industrial-policy.html']
        items, result, session = self.collect(sitemap(*urls), jina_detail_daily_limit=0)
        self.assertEqual(items, [])
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(result.diagnostics['candidates_total'], 75)
        self.assertEqual(result.diagnostics['candidates_evaluated'], 75)
        self.assertEqual(result.diagnostics['rejections']['no_scope_evidence'], 75)
        self.assertEqual(result.diagnostics['rejections']['publisher_domain'], 1)
        self.assertEqual(result.diagnostics['rejections']['url_month_before_cutoff'], 1)
        self.assertEqual(len(result.diagnostics['candidates']), 20)
        self.assertTrue(all(len(rows) <= 3 for rows in result.diagnostics['rejection_samples'].values()))

    def test_detail_budget_is_not_increased(self):
        urls = [f'https://www.oecd.org/en/blogs/2026/10/industrial-policy-{i}.html' for i in range(30)]
        details = {collector.jina_reader_url(url): fixture('challenge.txt') for url in urls}
        items, result, session = self.collect(sitemap(*urls), details)
        self.assertEqual(result.diagnostics['detail_limit'], 24)
        self.assertEqual(result.diagnostics['detail_pages'], 24)
        self.assertEqual(len(session.calls), 25)
        self.assertEqual(len(items), 30)
        self.assertEqual(result.diagnostics['detail_fallbacks'], 24)

    def test_detail_date_rejection_is_traced_and_status_payload_keeps_diagnostics(self):
        old = fixture('policy-metadata.txt').replace('2026-09-29T00:00:00Z', '2025-09-29T00:00:00Z')
        items, result, _ = self.collect(sitemap(POLICY_URL), {collector.jina_reader_url(POLICY_URL): old})
        self.assertEqual(items, [])
        self.assertEqual(result.diagnostics['rejections']['detail_date_outside_window'], 1)
        payload = collector.source_status_payload([result], NOW)
        self.assertEqual(payload['sources'][0]['diagnostics'], result.diagnostics)


if __name__ == '__main__':
    unittest.main()

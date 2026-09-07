"""Offline regressions for the boundary between empty schedules and failed crawls."""

import json
import logging
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from crawler import scraper


URL = scraper.THEATERS[0]['url'] + '2026-09-12'
OLD_TIME = '2026-09-01T09:00:00-07:00'
MOVIE = {'title': 'Example Movie', 'slug': 'example-movie-12345', 'showtimes': ['7:00pm']}
DATES = [datetime(2026, 9, 12, tzinfo=scraper.LA_TZ), datetime(2026, 9, 13, tzinfo=scraper.LA_TZ)]


def amc_html(payload=None, main='AMC Showtimes', footer=''):
    if payload is None:
        payload = {
            'aria-describedby': 'example-movie-12345',
            'format': 'imaxwithlaseratamc-1',
            'status': 'Available',
            'display': {'time': '7:00', 'amPm': 'pm'},
        }
    return ('<html><head><title>AMC Showtimes</title></head><body>'
            f'<main>{main}</main><footer>{footer}</footer>'
            f'<script>self.__next_f.push([1,{json.dumps(json.dumps(payload))}])</script>'
            '</body></html>')


BLOCK = ('<html><head><title>Attention Required! | Cloudflare</title></head>'
         '<body><h1>Sorry, you have been blocked</h1>amctheatres.com</body></html>')


class ResponseTests(unittest.TestCase):
    def test_cloudflare_footer_on_real_page_is_not_a_block(self):
        scraper.validate_response(amc_html(footer='Performance & security by Cloudflare'), 200, URL, URL)

    def test_access_denials_and_200_challenge_pages_are_errors(self):
        for html, status in ((amc_html(), 403), (amc_html(), 429), (BLOCK, 200),
                             ('<html><body>queueViewModel</body></html>', 200)):
            with self.subTest(status=status, html=html[:20]):
                with self.assertRaises(scraper.AccessBlockedError):
                    scraper.validate_response(html, status, URL, URL)

    def test_wrong_origin_path_date_and_http_errors_are_not_empty(self):
        for final_url, status in (
            ('https://example.com/showtimes', 200), ('https://www.amctheatres.com/', 200),
            (URL.replace('2026-09-12', '2026-09-13'), 200), (URL, 500), (URL, None),
        ):
            with self.subTest(final_url=final_url, status=status):
                with self.assertRaises(scraper.FetchError):
                    scraper.validate_response(amc_html(), status, final_url, URL)

    def test_empty_or_non_amc_document_is_not_empty_schedule(self):
        for html in ('', '<html><body>Something went wrong</body></html>'):
            with self.assertRaises(scraper.ParseError):
                scraper.validate_response(html, 200, URL, URL)

    def test_navigation_timeout_is_typed_and_page_is_closed(self):
        page = Mock(url=URL)
        page.goto.side_effect = TimeoutError('timed out')
        page.content.return_value = amc_html()
        context = Mock()
        context.new_page.return_value = page
        with patch.object(scraper, '_context', context):
            with self.assertRaises(scraper.FetchError):
                scraper.fetch_html_with_playwright(URL)
        page.close.assert_called_once()
        context.new_page.assert_called_once()

    def test_timeout_with_visible_block_stops_without_retry(self):
        page = Mock(url=URL)
        page.goto.side_effect = TimeoutError('timed out')
        page.content.return_value = BLOCK
        context = Mock()
        context.new_page.return_value = page
        with patch.object(scraper, '_context', context):
            with self.assertRaises(scraper.AccessBlockedError):
                scraper.fetch_html_with_playwright(URL)
        page.close.assert_called_once()
        context.new_page.assert_called_once()


class ScheduleTests(unittest.TestCase):
    def request(self, html):
        with patch.object(scraper, 'fetch_html_with_playwright', return_value=html), patch.object(scraper.time, 'sleep'):
            return scraper.fetch_showtimes(scraper.THEATERS[0], '2026-09-12')

    def test_valid_sessions_still_parse(self):
        result = self.request(amc_html())
        self.assertEqual(result.movies[0]['slug'], MOVIE['slug'])
        self.assertEqual(result.movies[0]['showtimes'], MOVIE['showtimes'])
        self.assertIsNone(result.empty_reason)

    def test_missing_format_does_not_prove_no_schedule(self):
        with self.assertRaises(scraper.ParseError):
            self.request(amc_html(payload={'message': 'unrecognized AMC payload'}))

    def test_no_matching_sessions_is_parse_failure(self):
        with self.assertRaises(scraper.ParseError):
            self.request(amc_html(payload={'format': 'imaxwithlaseratamc-1'}))

    def test_explicit_schedule_empty_is_validated_separately(self):
        result = self.request(amc_html(payload={}, main='AMC Showtimes. No showtimes available for this date.'))
        self.assertEqual(result.movies, [])
        self.assertEqual(result.empty_reason, 'schedule_not_released')

    def test_real_sessions_win_over_unrelated_empty_message(self):
        result = self.request(amc_html(main='AMC Showtimes. No showtimes available for another filter.'))
        self.assertEqual(len(result.movies), 1)
        self.assertIsNone(result.empty_reason)

    def test_hidden_empty_message_is_not_a_valid_empty_schedule(self):
        html = amc_html(payload={}, main='AMC Showtimes <div hidden>No showtimes available</div>')
        with self.assertRaises(scraper.ParseError):
            self.request(html)

    def test_explicit_format_empty_has_its_own_reason(self):
        result = self.request(amc_html(payload={}, main='AMC Showtimes. No showtimes found for the selected filters.'))
        self.assertEqual(result.movies, [])
        self.assertEqual(result.empty_reason, 'format_not_scheduled')

    def test_missing_payload_is_not_a_valid_empty_schedule(self):
        html = '<html><body><main>AMC: No showtimes available</main></body></html>'
        with self.assertRaises(scraper.ParseError):
            self.request(html)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.saved = {
            'Century City IMAX': {
                'name': 'Century City IMAX', 'url': scraper.THEATERS[0]['url'],
                'dates': {'2026-09-12': {'day': 'Saturday', 'movies': [MOVIE]}},
            },
        }
        self.seed()

    def seed(self):
        (self.directory / 'showtimes.json').write_text(json.dumps(self.saved))
        (self.directory / 'last_updated.json').write_text(json.dumps({'timestamp': OLD_TIME, 'timezone': 'America/Los_Angeles'}))
        self.before = {name: (self.directory / name).read_bytes() for name in ('showtimes.json', 'last_updated.json')}

    def crawl(self, side_effect, init_error=None):
        with patch.object(scraper, 'THEATERS', scraper.THEATERS[:1]), \
                patch.object(scraper, 'get_weekend_dates', return_value=DATES), \
                patch.object(scraper, 'init_playwright', side_effect=init_error), \
                patch.object(scraper, 'close_playwright') as close, \
                patch.object(scraper, 'fetch_showtimes', side_effect=side_effect) as fetch:
            exit_code = scraper.main(self.directory, source='amc')
        close.assert_called_once()
        return exit_code, json.loads((self.directory / 'crawl_status.json').read_text()), fetch

    def assert_preserved(self):
        for name, data in self.before.items():
            self.assertEqual((self.directory / name).read_bytes(), data)

    def test_access_block_aborts_remaining_requests_and_preserves_data(self):
        code, status, fetch = self.crawl([scraper.AccessBlockedError('HTTP 403')])
        self.assertEqual(code, 1)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(status['status'], 'error')
        self.assertEqual(status['failed_requests'], 1)
        self.assertEqual(status['successful_requests'], 0)
        self.assertEqual(status['last_successful_at'], OLD_TIME)
        self.assertTrue(status['stale'])
        self.assert_preserved()

    def test_partial_run_preserves_the_entire_previous_snapshot(self):
        code, status, fetch = self.crawl([scraper.ShowtimesResult([MOVIE]), scraper.ParseError('payload changed')])
        self.assertEqual(code, 1)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(status['status'], 'partial')
        self.assertEqual((status['successful_requests'], status['failed_requests']), (1, 1))
        self.assertEqual(status['last_successful_at'], OLD_TIME)
        self.assert_preserved()

    def test_total_navigation_failure_does_not_report_a_successful_sync(self):
        code, status, _ = self.crawl(scraper.FetchError('navigation failed'))
        self.assertEqual(code, 1)
        self.assertEqual(status['status'], 'error')
        self.assertEqual(status['failed_requests'], 2)
        self.assert_preserved()

    def test_legacy_empty_data_does_not_claim_an_old_false_success_time(self):
        self.saved['Century City IMAX']['dates']['2026-09-12']['movies'] = []
        self.seed()
        code, status, _ = self.crawl([scraper.AccessBlockedError('HTTP 403')])
        self.assertEqual(code, 1)
        self.assertIsNone(status['last_successful_at'])
        self.assert_preserved()

    def test_initialization_failure_still_cleans_up_and_publishes_error(self):
        code, status, fetch = self.crawl([], init_error=RuntimeError('browser unavailable'))
        self.assertEqual(code, 1)
        fetch.assert_not_called()
        self.assertEqual(status['status'], 'error')
        self.assert_preserved()

    def test_full_success_keeps_schedule_schema_and_advances_success_time(self):
        code, status, _ = self.crawl([
            scraper.ShowtimesResult([MOVIE]), scraper.ShowtimesResult([], 'schedule_not_released'),
        ])
        self.assertEqual(code, 0)
        self.assertEqual(status['status'], 'ok')
        self.assertFalse(status['stale'])
        self.assertEqual(status['successful_requests'], 2)
        self.assertEqual(status['failed_requests'], 0)
        updated = json.loads((self.directory / 'last_updated.json').read_text())
        self.assertEqual(status['last_successful_at'], updated['timestamp'])
        self.assertNotEqual(updated['timestamp'], OLD_TIME)
        data = json.loads((self.directory / 'showtimes.json').read_text())
        self.assertEqual(set(data), {'Century City IMAX'})
        self.assertEqual(data['Century City IMAX']['dates']['2026-09-13'], {'day': 'Sunday', 'movies': []})
        self.assertEqual(set(status), {'status', 'attempted_at', 'last_successful_at', 'message',
                                       'failed_requests', 'successful_requests', 'stale', 'source'})

    def test_default_source_maps_fandango_formats_and_keeps_amc_ticket_links(self):
        dolby_movie = dict(MOVIE, title='Dolby Movie', slug='fandango-20')
        source = Mock()
        source.fetch_day.return_value = {'IMAX': [MOVIE], 'Dolby': [dolby_movie]}
        with patch.object(scraper, 'get_weekend_dates', return_value=DATES[:1]), \
                patch.object(scraper, 'init_playwright'), patch.object(scraper, 'close_playwright'), \
                patch.object(scraper, 'FandangoSource', return_value=source), \
                patch.object(scraper, 'fetch_showtimes') as legacy_fetch:
            code = scraper.main(self.directory)
        self.assertEqual(code, 0)
        legacy_fetch.assert_not_called()
        result = json.loads((self.directory / 'showtimes.json').read_text())
        self.assertEqual(len(result), 3)
        for theater in scraper.THEATERS:
            entry = result[theater['name']]
            self.assertEqual(entry['url'], theater['url'])
            self.assertEqual(entry['source_url'], theater['source_url'])
            expected = [dolby_movie] if 'Dolby' in theater['name'] else [MOVIE]
            self.assertEqual(entry['dates']['2026-09-12']['movies'], expected)
        status = json.loads((self.directory / 'crawl_status.json').read_text())
        self.assertEqual(status['source'], 'Fandango')
        self.assertEqual(status['status'], 'ok')

    def test_fandango_access_denial_preserves_data_and_stops(self):
        source = Mock()
        source.fetch_day.side_effect = scraper.FandangoBlocked('access denied')
        with patch.object(scraper, 'get_weekend_dates', return_value=DATES), \
                patch.object(scraper, 'init_playwright'), patch.object(scraper, 'close_playwright'), \
                patch.object(scraper, 'FandangoSource', return_value=source):
            code = scraper.main(self.directory)
        self.assertEqual(code, 1)
        self.assertEqual(source.fetch_day.call_count, 1)
        self.assert_preserved()

    def test_proven_empty_success_remains_trustworthy_on_later_failure(self):
        code, first_status, _ = self.crawl([scraper.ShowtimesResult([], 'schedule_not_released')] * 2)
        self.assertEqual(code, 0)
        _, status, _ = self.crawl([scraper.AccessBlockedError('HTTP 403')])
        self.assertEqual(status['last_successful_at'], first_status['last_successful_at'])

    def test_failed_atomic_replace_keeps_original_and_removes_tempfile(self):
        target = self.directory / 'showtimes.json'
        with patch.object(scraper.os, 'replace', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                scraper.atomic_write_json(target, {})
        self.assertEqual(target.read_bytes(), self.before['showtimes.json'])
        self.assertEqual(list(self.directory.glob('.*.tmp')), [])


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    unittest.main()

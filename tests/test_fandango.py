"""Offline checks for public-page response provenance and format/date isolation."""

import copy
import unittest
from unittest.mock import Mock

from crawler import fandango


DAY = '2026-09-12'
THEATER_URL = 'https://www.fandango.com/amc-century-city-15-aaaoz/theater-page'
URL = THEATER_URL + '?format=all&date=' + DAY
RESPONSE_URL = ('https://www.fandango.com/napi/theaterMovieShowtimes/AAAOZ'
                '?chainCode=AMC&startDate=' + DAY + '&isdesktop=true')
HTML = '<html><body><h1>AMC Century City 15</h1></body></html>'


def show(hour='16:00', formats=('IMAX',), **changes):
    # Fields/structure observed in the public page's data-amenity-group DOM.
    value = {
        'date': '4:00p', 'ticketingDate': DAY + '+' + hour, 'type': 'available',
        'expired': False, 'isSoldOut': False,
        'filmFormat': [{'filterName': name, 'order': i} for i, name in enumerate(formats)],
    }
    value.update(changes)
    return value


def group(shows=None, amenities=None):
    return {'amenities': amenities or [], 'amenitiesWithImage': [],
            'showtimes': [show()] if shows is None else shows}


def fixture(groups=None):
    return {'viewModel': {'date': DAY,
            'theater': {'details': {'id': 'AAAOZ', 'name': 'AMC Century City 15'}},
            'movies': [{'id': 246559, 'title': "Oasis: Don't Look Back in Anger",
                        'variants': [{'amenityGroups': [group()] if groups is None else groups}]}],
            'formats': ['ALL', 'IMAX', 'Dolby Cinema']}}


class PayloadTests(unittest.TestCase):
    def test_formats_are_selected_per_showtime_and_deduplicated(self):
        value = fixture([group([
            show('16:00'), show('16:00'), show('19:15'),
            show('20:00', ('Dolby Cinema',)), show('21:00', ('Digital',)),
        ])])
        result = fandango.parse_payload(value, DAY, 'aaaoz')
        self.assertEqual(result['IMAX'][0]['showtimes'], ['4:00pm', '7:15pm'])
        self.assertEqual(result['Dolby'][0]['showtimes'], ['8:00pm'])
        self.assertEqual(result['IMAX'][0]['slug'], 'fandango-246559')

    def test_70mm_does_not_include_regular_70mm_in_imax(self):
        value = fixture([
            group([show('10:00')], [{'id': '70MM', 'name': 'IMAX 70mm'}]),
            group([show('11:00', ('70mm',))], [{'id': '70MM', 'name': '70mm'}]),
            group([show('12:00')]),
        ])
        result = fandango.parse_payload(value, DAY, 'aaaoz')['IMAX']
        self.assertEqual([(m['is_70mm'], m['showtimes']) for m in result],
                         [(True, ['10:00am']), (False, ['12:00pm'])])

    def test_image_amenities_can_identify_imax_70mm(self):
        value = fixture()
        value['viewModel']['movies'][0]['variants'][0]['amenityGroups'][0]['amenitiesWithImage'] = [
            {'id': '70mm', 'name': 'IMAX 70 MM'}]
        self.assertTrue(fandango.parse_payload(value, DAY, 'aaaoz')['IMAX'][0]['is_70mm'])

    def test_sold_out_expired_and_unavailable_are_not_coming_soon(self):
        value = fixture([group([
            show('16:00', isSoldOut=True), show('17:00', expired=True),
            show('18:00', type='unavailable'), show('19:00', type='comingSoon'),
            show('20:00'),
        ])])
        movie = fandango.parse_payload(value, DAY, 'aaaoz')['IMAX'][0]
        self.assertEqual(movie['showtimes'], ['8:00pm'])
        self.assertFalse(movie['is_coming_soon'])

    def test_observed_restricted_sessions_are_unavailable_and_skipped(self):
        value = fixture([group([
            show('16:00'),
            show('18:00', type='restricted', message='Tickets coming soon. Be sure to check back later!'),
            show('19:00', type='restricted', message='Another purchase restriction'),
            show('20:00', type='restricted', isSoldOut=True, message='Tickets coming soon.'),
        ])])
        movies = fandango.parse_payload(value, DAY, 'aaaoz')['IMAX']
        self.assertEqual([(m['is_coming_soon'], m['showtimes']) for m in movies],
                         [(False, ['4:00pm'])])

    def test_observed_pastshowtime_is_skipped_without_losing_current_sessions(self):
        # Real AAAWX Sep 7 response contained available and pastshowtime types.
        value = fixture([group([
            show('15:00', type='pastshowtime', expired=True), show('18:00'),
        ])])
        movie = fandango.parse_payload(value, DAY, 'aaaoz')['IMAX'][0]
        self.assertEqual(movie['showtimes'], ['6:00pm'])
        self.assertFalse(movie['is_coming_soon'])

    def test_unknown_state_error_identifies_the_public_enum_only(self):
        with self.assertRaisesRegex(fandango.FandangoError, "availability: 'new-status'"):
            fandango.parse_payload(fixture([group([show(type='new-status')])]), DAY, 'aaaoz')

    def test_explicit_matching_empty_movies_is_valid(self):
        value = fixture()
        value['viewModel']['movies'] = []
        self.assertEqual(fandango.parse_payload(value, DAY, 'aaaoz'), {'IMAX': [], 'Dolby': []})

    def test_observed_null_date_empty_shape_requires_exact_response_scope(self):
        # Real public-page response for AAAOZ / 2026-10-24: HTTP 200,
        # viewModel contains the correct theater but null date and no formats.
        value = fixture()
        value['viewModel'].update(date=None, movies=[], formats=[])
        self.assertEqual(fandango.parse_payload(value, DAY, 'aaaoz', response_url=RESPONSE_URL),
                         {'IMAX': [], 'Dolby': []})
        for response_url in (None, RESPONSE_URL.replace(DAY, '2026-10-24'),
                             RESPONSE_URL.replace('AAAOZ', 'AAAWX'),
                             RESPONSE_URL.replace('fandango.com', 'example.com')):
            with self.subTest(response_url=response_url):
                with self.assertRaises(fandango.FandangoError):
                    fandango.parse_payload(value, DAY, 'aaaoz', response_url=response_url)

    def test_null_date_with_movies_or_unknown_empty_shape_still_fails(self):
        nonempty = fixture()
        nonempty['viewModel']['date'] = None
        no_date = fixture()
        no_date['viewModel'].update(movies=[], formats=[])
        del no_date['viewModel']['date']
        wrong_formats = fixture()
        wrong_formats['viewModel'].update(date=None, movies=[], formats=['IMAX'])
        for value in (nonempty, no_date, wrong_formats):
            with self.subTest(value=value):
                with self.assertRaises(fandango.FandangoError):
                    fandango.parse_payload(value, DAY, 'aaaoz', response_url=RESPONSE_URL)

    def test_wrong_day_theater_and_mixed_session_dates_fail(self):
        wrong_day, wrong_theater, mixed = [fixture() for _ in range(3)]
        wrong_day['viewModel']['date'] = '2026-09-13'
        wrong_theater['viewModel']['theater']['details']['id'] = 'AAAWX'
        mixed['viewModel']['movies'][0]['variants'][0]['amenityGroups'][0]['showtimes'][0][
            'ticketingDate'] = '2026-09-13+16:00'
        for value in (wrong_day, wrong_theater, mixed):
            with self.subTest(value=value):
                with self.assertRaises(fandango.FandangoError):
                    fandango.parse_payload(value, DAY, 'aaaoz')

    def test_observed_overnight_session_preserves_calendar_day_label(self):
        late = show(ticketingDate='2026-09-13+00:15', date='12:15a')
        value = fixture([group([show('22:00'), late])])
        result = fandango.parse_payload(value, DAY, 'aaaoz', response_url=RESPONSE_URL)
        self.assertEqual(result['IMAX'][0]['showtimes'], ['10:00pm', '12:15am (+1 day)'])

    def test_overnight_session_needs_scoped_response_and_matching_early_time(self):
        for ticketing_date, display, response_url in (
            ('2026-09-13+00:15', '12:15a', None),
            ('2026-09-13+00:15', '12:15a', RESPONSE_URL.replace(DAY, '2026-09-13')),
            ('2026-09-13+00:15', '4:00p', RESPONSE_URL),
            ('2026-09-13+16:00', '4:00p', RESPONSE_URL),
            ('2026-09-11+00:15', '12:15a', RESPONSE_URL),
            ('2026-09-14+00:15', '12:15a', RESPONSE_URL),
        ):
            with self.subTest(ticketing_date=ticketing_date, response_url=response_url):
                value = fixture([group([show(ticketingDate=ticketing_date, date=display)])])
                with self.assertRaises(fandango.FandangoError):
                    fandango.parse_payload(value, DAY, 'aaaoz', response_url=response_url)

    def test_non_target_overnight_show_does_not_break_premium_day(self):
        # Real AAAWX Sep 26 error was a next-day 00:15 Infinity Vision session.
        late = show(ticketingDate='2026-09-13+00:15', date='12:15a', formats=('Infinity Vision',))
        result = fandango.parse_payload(fixture([group([show(), late])]), DAY, 'aaaoz',
                                       response_url=RESPONSE_URL)
        self.assertEqual(result['IMAX'][0]['showtimes'], ['4:00pm'])

    def test_unrecognized_empty_or_malformed_response_is_failure(self):
        malformed = fixture()
        malformed['viewModel']['movies'][0]['variants'] = None
        for value in ({}, {'movies': []}, {'viewModel': {}}, {'viewModel': {'movies': []}}, malformed):
            with self.subTest(value=value):
                with self.assertRaises(fandango.FandangoError):
                    fandango.parse_payload(value, DAY, 'aaaoz')

    def test_invalid_time_or_missing_format_fails_instead_of_silent_drop(self):
        for change in ({'ticketingDate': DAY + '+25:00'}, {'filmFormat': None}):
            with self.subTest(change=change):
                with self.assertRaises(fandango.FandangoError):
                    fandango.parse_payload(fixture([group([show(**change)])]), DAY, 'aaaoz')

    def test_missing_or_unknown_availability_cannot_become_empty_success(self):
        for change in ({'type': None}, {'type': []}, {'type': 'new-status'},
                       {'expired': 'false'}, {'isSoldOut': None}):
            with self.subTest(change=change):
                with self.assertRaises(fandango.FandangoError):
                    fandango.parse_payload(fixture([group([show(**change)])]), DAY, 'aaaoz')
        value = fixture()
        del value['viewModel']['movies'][0]['variants'][0]['amenityGroups'][0]['showtimes'][0]['type']
        with self.assertRaises(fandango.FandangoError):
            fandango.parse_payload(value, DAY, 'aaaoz')


class ProvenanceTests(unittest.TestCase):
    def test_response_match_requires_origin_theater_and_requested_day(self):
        self.assertTrue(fandango.response_matches(RESPONSE_URL, 'aaaoz', DAY))
        for url in (
            RESPONSE_URL.replace('fandango.com', 'example.com'),
            RESPONSE_URL.replace('AAAOZ', 'AAAWX'),
            RESPONSE_URL.replace(DAY, '2026-09-13'),
            RESPONSE_URL.replace('https:', 'http:'),
            RESPONSE_URL.replace('theaterMovieShowtimes', 'theaterCalendar'),
        ):
            self.assertFalse(fandango.response_matches(url, 'aaaoz', DAY), url)

    def test_navigation_rejects_wrong_theater_date_origin_and_challenge(self):
        for html, status, url in (
            (HTML.replace('AMC Century City 15', 'Another Cinema'), 200, URL),
            (HTML, 200, URL.replace(DAY, '2026-09-13')),
            (HTML, 200, URL.replace('fandango.com', 'example.com')),
            (HTML, 200, URL.replace('aaaoz', 'aaawx')),
            (HTML, 403, URL),
            ('<html><title>Just a moment...</title><body>Verify you are human</body></html>', 200, URL),
        ):
            with self.subTest(status=status, url=url, html=html):
                with self.assertRaises(fandango.FandangoError):
                    fandango.validate_navigation(html, status, url, URL, 'AMC Century City 15')

    def make_source(self, response_status=200, payload=None):
        page = Mock(url=URL)
        page.content.return_value = HTML
        response = Mock(url=RESPONSE_URL, status=response_status)
        response.json.return_value = fixture() if payload is None else payload
        callbacks = {}
        page.on.side_effect = lambda event, callback: callbacks.update({event: callback})
        def navigate(*args, **kwargs):
            callbacks['response'](response)
            return Mock(status=200)
        page.goto.side_effect = navigate
        context = Mock()
        context.new_page.return_value = page
        return fandango.FandangoSource(context, timeout_ms=0), context, page

    def test_source_only_navigates_public_page_and_reuses_validated_day(self):
        source, context, page = self.make_source()
        first = source.fetch_day(THEATER_URL, DAY)
        first['IMAX'][0]['showtimes'].append('tampered')
        second = source.fetch_day(THEATER_URL, DAY)
        self.assertEqual(second['IMAX'][0]['showtimes'], ['4:00pm'])
        context.new_page.assert_called_once()
        page.goto.assert_called_once_with(URL, wait_until='domcontentloaded', timeout=0)
        page.close.assert_called_once()
        context.request.get.assert_not_called()

    def test_browser_schedule_403_is_blocked_even_when_document_is_200(self):
        source, _, page = self.make_source(response_status=403)
        with self.assertRaises(fandango.FandangoBlocked):
            source.fetch_day(THEATER_URL, DAY)
        self.assertEqual(source._cache, {})
        page.close.assert_called_once()

    def test_no_showtimes_error_text_cannot_prove_empty_without_successful_payload(self):
        source, _, page = self.make_source(payload={'message': 'Session expired or invalid token'})
        page.content.return_value = HTML.replace('</body>', 'No showtimes available for this day.</body>')
        with self.assertRaises(fandango.FandangoError):
            source.fetch_day(THEATER_URL, DAY)
        self.assertEqual(source._cache, {})

    def test_missing_matching_schedule_response_is_error_and_page_closes(self):
        source, _, page = self.make_source()
        page.goto.side_effect = lambda *a, **kw: Mock(status=200)
        with self.assertRaisesRegex(fandango.FandangoError, 'did not return'):
            source.fetch_day(THEATER_URL, DAY)
        page.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()

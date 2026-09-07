"""Read Fandango's public theater page through an ordinary browser.

The page itself requests its schedule; we observe that response. We never call
private endpoints ourselves, extract session tokens, or work around challenges.
A page's "No showtimes" message is not sufficient evidence of an empty day:
Fandango also displays it when its schedule request fails.
"""

import json
import re
import time
from datetime import date, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup


THEATER_NAMES = {
    'aaaoz': 'AMC Century City 15',
    'aaawx': 'Universal Cinema, an AMC Theatre',
}
HOSTS = {'www.fandango.com', 'fandango.com'}
KNOWN_AVAILABILITY_TYPES = {
    'available', 'unavailable', 'soldout', 'comingSoon', 'restricted', 'pastshowtime',
}


class FandangoError(Exception):
    """A failed request or unrecognized schedule, never an empty result."""


class FandangoBlocked(FandangoError):
    """Access denied, rate limited, or challenged; do not retry."""


def _text_key(value):
    return re.sub(r'[^a-z0-9]+', '', value.lower())


def _requested_day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise FandangoError('A YYYY-MM-DD requested date is required.')
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise FandangoError('Invalid requested date.') from exc
    return value


def theater_identity(theater_url):
    parsed = urlsplit(theater_url)
    match = re.fullmatch(r'/[^/]+-([a-zA-Z0-9]{5})/theater-page/?', parsed.path)
    if parsed.scheme != 'https' or parsed.hostname not in HOSTS or not match:
        raise FandangoError('Expected a supported public Fandango theater page.')
    theater_id = match.group(1).lower()
    if theater_id not in THEATER_NAMES:
        raise FandangoError(f'No verified theater name configured for {theater_id}.')
    return theater_id, THEATER_NAMES[theater_id]


def navigation_url(theater_url, requested_date):
    _requested_day(requested_date)
    theater_identity(theater_url)
    parsed = urlsplit(theater_url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path,
                       urlencode({'format': 'all', 'date': requested_date}), ''))


def validate_navigation(html, status, final_url, requested_url, expected_name):
    if status in {401, 403, 429}:
        raise FandangoBlocked(f'Fandango refused access (HTTP {status}).')
    soup = BeautifulSoup(html or '', 'html.parser')
    for node in soup(['script', 'style', 'noscript']):
        node.decompose()
    visible = ' '.join(soup.stripped_strings).lower()
    title = soup.title.get_text(' ', strip=True).lower() if soup.title else ''
    if (title.strip(' .') in {'just a moment', 'access denied'}
            or 'attention required' in title
            or any(phrase in visible for phrase in (
                'sorry, you have been blocked', 'verify you are human',
                'enable javascript and cookies to continue', 'checking your browser'))):
        raise FandangoBlocked('Fandango returned an access challenge.')
    if status is None or not 200 <= status < 300:
        raise FandangoError(f'Unexpected Fandango page HTTP status {status}.')
    actual, expected = urlsplit(final_url), urlsplit(requested_url)
    if (actual.scheme != 'https' or actual.hostname not in HOSTS
            or actual.path.rstrip('/') != expected.path.rstrip('/')
            or parse_qs(actual.query).get('date') != parse_qs(expected.query).get('date')
            or parse_qs(actual.query).get('format') != ['all']):
        raise FandangoError('Fandango navigation changed the requested theater, date, or format.')
    headings = [node.get_text(' ', strip=True) for node in soup.select('h1')]
    if not any(_text_key(name) == _text_key(expected_name) for name in headings):
        raise FandangoError('Fandango page does not identify the requested theater.')


def response_matches(response_url, theater_id, requested_date):
    parsed = urlsplit(response_url)
    return (parsed.scheme == 'https' and parsed.hostname in HOSTS
            and parsed.path.rstrip('/').lower() == f'/napi/theatermovieshowtimes/{theater_id.lower()}'
            and parse_qs(parsed.query).get('startDate') == [requested_date])


def _list(value, label):
    if not isinstance(value, list):
        raise FandangoError(f'Unrecognized Fandango {label}.')
    return value


def _day_from_timestamp(value):
    if not isinstance(value, str) or not re.match(r'^\d{4}-\d{2}-\d{2}(?:$|[T+ ])', value):
        raise FandangoError('Unrecognized Fandango schedule date.')
    return value[:10]


def _showtime_label(ticketing_date):
    # This is the theater's local ticketing time, not UTC or the runner timezone.
    if not isinstance(ticketing_date, str):
        raise FandangoError('Unrecognized Fandango local showtime.')
    match = re.fullmatch(r'\d{4}-\d{2}-\d{2}[+T ](\d{2}):(\d{2})(?::\d{2})?', ticketing_date)
    if not match:
        raise FandangoError('Unrecognized Fandango local showtime.')
    hour, minute = map(int, match.groups())
    if hour > 23 or minute > 59:
        raise FandangoError('Invalid Fandango local showtime.')
    return f'{hour % 12 or 12}:{minute:02d}{"pm" if hour >= 12 else "am"}'


def _group_70mm(group):
    amenities = []
    for key in ('amenities', 'amenitiesWithImage'):
        value = group.get(key, [])
        amenities.extend(_list(value, key))
    # Format inclusion is checked separately on EACH showtime. A regular 70 mm
    # group must never make its sessions appear in the IMAX schedule.
    return any(isinstance(item, dict) and re.search(r'70\s*mm', str(item.get('name', '')), re.I)
               for item in amenities)


def parse_payload(payload, requested_date, theater_id, response_url=None):
    """Validate the browser's successful schedule response and extract formats.

    Fandango's public page uses data.viewModel.{date,theater,movies}. Only an
    explicit movies array in a matching model proves an empty schedule.
    Expired, sold-out and otherwise unavailable sessions are omitted; they are
    never relabeled as "coming soon".
    """
    _requested_day(requested_date)
    if not isinstance(payload, dict) or not isinstance(payload.get('viewModel'), dict):
        raise FandangoError('Fandango response has no schedule viewModel.')
    model = payload['viewModel']
    theater = model.get('theater')
    details = theater.get('details') if isinstance(theater, dict) else None
    if not isinstance(details, dict) or str(details.get('id', '')).lower() != theater_id.lower():
        raise FandangoError('Fandango returned a different theater.')
    movies = _list(model.get('movies'), 'movies array')
    # Observed on a successful far-future public-page request: no scheduled
    # films yields {date: null, movies: [], formats: []}. The response's exact
    # request URL supplies the date in this one known shape. A missing date,
    # any movie entries, or an unscoped response cannot prove an empty day.
    known_empty_without_date = (
        'date' in model and model['date'] is None
        and movies == [] and model.get('formats') == []
        and isinstance(response_url, str)
        and response_matches(response_url, theater_id, requested_date)
    )
    if not known_empty_without_date and _day_from_timestamp(model.get('date')) != requested_date:
        raise FandangoError('Fandango returned a different schedule date.')
    output = {'IMAX': {}, 'Dolby': {}}
    for movie in movies:
        if not isinstance(movie, dict):
            raise FandangoError('Unrecognized Fandango movie.')
        movie_id = str(movie.get('id', ''))
        title = movie.get('title') or movie.get('name')
        if not movie_id.isdigit() or not isinstance(title, str) or not title.strip():
            raise FandangoError('Fandango movie lacks its ID or title.')
        variants = _list(movie.get('variants'), 'movie variants')
        for variant in variants:
            if not isinstance(variant, dict):
                raise FandangoError('Unrecognized Fandango movie variant.')
            for group in _list(variant.get('amenityGroups'), 'amenity groups'):
                if not isinstance(group, dict):
                    raise FandangoError('Unrecognized Fandango amenity group.')
                is_70mm = _group_70mm(group)
                for show in _list(group.get('showtimes'), 'showtimes'):
                    if not isinstance(show, dict):
                        raise FandangoError('Unrecognized Fandango showtime.')
                    ticket_date = show.get('ticketingDate')
                    label = _showtime_label(ticket_date)
                    if _day_from_timestamp(ticket_date) != requested_date:
                        # The theater's selected business day can include an
                        # after-midnight show on the following calendar day.
                        # Observed AAAWX / Sep 26: Sep 27 00:15, displayed 12:15a.
                        # Accept only a scoped successful response, the immediate
                        # next day before 06:00, and a matching display time.
                        next_day = (date.fromisoformat(requested_date) + timedelta(days=1)).isoformat()
                        overnight = (
                            isinstance(response_url, str)
                            and response_matches(response_url, theater_id, requested_date)
                            and ticket_date[:10] == next_day
                            and int(ticket_date[11:13]) < 6
                            and show.get('date') == label.replace('am', 'a').replace('pm', 'p')
                        )
                        if not overnight:
                            raise FandangoError('Fandango response mixes dates between showtimes.')
                        label += ' (+1 day)'
                    formats = _list(show.get('filmFormat'), 'film formats')
                    if any(not isinstance(fmt, dict) or not isinstance(fmt.get('filterName'), str)
                           for fmt in formats):
                        raise FandangoError('Unrecognized Fandango showtime format.')
                    # Availability belongs to the individual session.
                    if (not isinstance(show.get('type'), str)
                            or show['type'] not in KNOWN_AVAILABILITY_TYPES):
                        raise FandangoError(f'Unrecognized Fandango showtime availability: {show.get("type")!r}.')
                    if any(not isinstance(show.get(flag), bool) for flag in ('expired', 'isSoldOut')):
                        raise FandangoError('Unrecognized Fandango showtime availability flags.')
                    if (show.get('expired') or show.get('isSoldOut')
                            or show['type'] != 'available'):
                        continue
                    names = {fmt['filterName'] for fmt in formats}
                    for target, source_name in (('IMAX', 'IMAX'), ('Dolby', 'Dolby Cinema')):
                        if source_name not in names:
                            continue
                        key = (movie_id, is_70mm if target == 'IMAX' else False)
                        item = output[target].setdefault(key, {
                            'title': title.strip(), 'slug': f'fandango-{movie_id}',
                            'showtimes': [], 'is_coming_soon': False,
                            'is_70mm': key[1],
                        })
                        if label not in item['showtimes']:
                            item['showtimes'].append(label)
    return {format_name: list(movies_by_key.values()) for format_name, movies_by_key in output.items()}


class FandangoSource:
    def __init__(self, context, timeout_ms=30000):
        self.context = context
        self.timeout_ms = timeout_ms
        self._cache = {}

    def fetch_day(self, theater_url, date_str):
        theater_id, expected_name = theater_identity(theater_url)
        requested_url = navigation_url(theater_url, date_str)
        key = (theater_id, date_str)
        if key in self._cache:
            # Callers can enrich movies (e.g. ratings) without corrupting the
            # second format's shared theater/day cache.
            return json.loads(json.dumps(self._cache[key]))
        page = None
        try:
            page = self.context.new_page()
            responses = []
            page.on('response', lambda response: responses.append(response)
                    if response_matches(response.url, theater_id, date_str) else None)
            document = page.goto(requested_url, wait_until='domcontentloaded', timeout=self.timeout_ms)
            validate_navigation(page.content(), document.status if document else None,
                                page.url, requested_url, expected_name)
            deadline = time.monotonic() + self.timeout_ms / 1000
            while not responses and time.monotonic() < deadline:
                page.wait_for_timeout(100)
            if not responses:
                raise FandangoError('Fandango page did not return its requested schedule.')
            response = responses[-1]
            if response.status in {401, 403, 429}:
                raise FandangoBlocked(f'Fandango schedule request refused access (HTTP {response.status}).')
            if not 200 <= response.status < 300:
                raise FandangoError(f'Fandango schedule request failed (HTTP {response.status}).')
            payload = response.json()
            result = parse_payload(payload, date_str, theater_id, response_url=response.url)
            # Recheck the document after the asynchronous response completes.
            validate_navigation(page.content(), document.status, page.url, requested_url, expected_name)
            self._cache[key] = result
            return json.loads(json.dumps(result))
        except FandangoError:
            raise
        except Exception as exc:
            raise FandangoError(f'Fandango browser request failed: {type(exc).__name__}.') from exc
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

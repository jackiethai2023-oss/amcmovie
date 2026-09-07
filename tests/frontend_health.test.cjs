const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const movie = { slug: 'test-movie', title: 'Test Movie', showtimes: ['7:00 PM'] };

function schedule(movies = []) {
    return {
        century: {
            name: 'Century City IMAX',
            url: 'https://www.amctheatres.com/showtimes/example/',
            dates: { '2026-09-07': { day: 'Monday', movies } }
        }
    };
}

function app({ data = schedule(), status, statusCode = 200, legacyTimestamp, dataCode = 200, now = '2026-09-07T20:00:00Z' }) {
    const elements = new Map();
    const requests = [];
    const context = vm.createContext({
        Date: class extends Date {
            constructor(...args) { super(...(args.length ? args : [now])); }
            static now() { return Date.parse(now); }
        },
        document: {
            getElementById(id) {
                if (!elements.has(id)) elements.set(id, { style: {}, textContent: '', innerHTML: '' });
                return elements.get(id);
            },
            querySelectorAll() { return []; }
        },
        localStorage: { getItem() { return null; }, setItem() {} },
        window: { addEventListener() {} },
        console: { warn() {}, error() {} },
        fetch: async url => {
            requests.push(url.split('?')[0]);
            let code = 200;
            let value;
            if (url.includes('crawl_status.json')) {
                code = statusCode;
                value = status;
            } else if (url.includes('last_updated.json')) {
                value = { timestamp: legacyTimestamp };
            } else {
                code = dataCode;
                value = data;
            }
            return { ok: code >= 200 && code < 300, status: code, json: async () => value };
        }
    });
    vm.runInContext(script, context);
    return {
        load: () => context.loadData(),
        content: () => elements.get('mainContent').innerHTML,
        sync: () => elements.get('lastSyncTime').textContent,
        filter: () => elements.get('filterList').innerHTML,
        button: () => elements.get('refreshText').textContent,
        requests
    };
}

test('failed retrieval with empty data reports unavailable and never claims a successful sync', async () => {
    const page = app({ status: {
        status: 'error', stale: true,
        attempted_at: '2026-09-07T19:00:00Z', last_successful_at: null,
        message: 'Upstream returned <html> instead of showtimes.'
    } });
    await page.load();
    assert.match(page.content(), /Showtime retrieval failed\. Current showtimes are unavailable/);
    assert.match(page.content(), /Showtimes unavailable/);
    assert.doesNotMatch(page.content(), /Schedule Not Released|TBD/);
    assert.match(page.content(), /Last attempt:/);
    assert.match(page.content(), /&lt;html&gt;/);
    assert.equal(page.sync(), 'Last successful fetch: unknown');
    assert.equal(page.button(), 'RELOAD');
    assert.ok(!page.requests.includes('./data/last_updated.json'));
});

test('failed retrieval retains movies and filters with an explicit stale warning', async () => {
    const page = app({ data: schedule([movie]), status: {
        status: 'error', stale: true,
        attempted_at: '2026-09-07T19:00:00Z', last_successful_at: '2026-09-06T19:00:00Z'
    } });
    await page.load();
    assert.match(page.content(), /previously saved showtimes, which may be out of date/);
    assert.match(page.content(), /Test Movie/);
    assert.match(page.content(), /7:00 PM/);
    assert.match(page.filter(), /Test Movie/);
    assert.match(page.sync(), /9\/6/);
    assert.doesNotMatch(page.sync(), /9\/7/);
});

test('partial retrieval explains incomplete data and does not label empty cells unreleased', async () => {
    const page = app({ data: schedule([movie]), status: { status: 'partial', stale: true } });
    await page.load();
    assert.match(page.content(), /Some showtimes could not be retrieved/);
    assert.match(page.content(), /Showtimes unavailable/);
    assert.match(page.content(), /7:00 PM/);
});

test('successful retrieval shows its success timestamp without an error banner', async () => {
    const page = app({ data: schedule([movie]), status: {
        status: 'ok', stale: false, last_successful_at: '2026-09-07T19:00:00Z'
    } });
    await page.load();
    assert.match(page.sync(), /Last successful fetch: 9\/7/);
    assert.doesNotMatch(page.content(), /class="data-health"/);
    assert.match(page.content(), /No showtimes listed/);
    assert.doesNotMatch(html, /Auto-syncs daily at 9 AM|SCANNING/);
});

test('a missing status file supports legacy data without treating its timestamp as verified success', async () => {
    const page = app({ data: schedule([movie]), statusCode: 404, legacyTimestamp: '2026-09-07T19:00:00Z' });
    await page.load();
    assert.match(page.sync(), /Saved data timestamp: 9\/7/);
    assert.match(page.content(), /Test Movie/);
    assert.doesNotMatch(page.content(), /class="data-health"/);
});

test('unreadable health status warns instead of falsely reporting a healthy fetch', async () => {
    const page = app({ statusCode: 503 });
    await page.load();
    assert.match(page.content(), /freshness of these saved showtimes could not be verified/);
    assert.match(page.content(), /Showtimes unavailable/);
    assert.equal(page.sync(), 'Last successful fetch: unknown');
});

test('a failed saved data download is visible and leaves reload available', async () => {
    const page = app({ dataCode: 503, status: { status: 'ok', stale: false } });
    await page.load();
    assert.match(page.content(), /Saved showtimes could not be loaded/);
    assert.equal(page.sync(), 'Last successful fetch: unknown');
    assert.equal(page.button(), 'RELOAD');
});

test('a successful status becomes uncertain after 48 hours even if the scheduler never runs again', async () => {
    const status = { status: 'ok', stale: false, last_successful_at: '2026-09-07T19:00:00Z' };
    const freshPage = app({ status, now: '2026-09-09T19:00:00Z' });
    await freshPage.load();
    assert.doesNotMatch(freshPage.content(), /class="data-health"/);

    const stalePage = app({ data: schedule([movie]), status, now: '2026-09-09T19:00:01Z' });
    await stalePage.load();
    assert.match(stalePage.content(), /saved showtimes may be out of date/);
    assert.match(stalePage.content(), /Showtimes unavailable/);
    assert.match(stalePage.content(), /Test Movie/);
    assert.match(stalePage.sync(), /9\/7/);
});

test('Fandango source links are deduplicated and AMC movie ticket links are preserved', async () => {
    const data = schedule([movie]);
    data.century.source = 'Fandango';
    data.century.source_url = 'https://www.fandango.com/amc-century-city-15-aaaoz/theater-page';
    data.dolby = { ...data.century, name: 'Century City Dolby Cinema' };
    data.universal = {
        ...data.century,
        name: 'Universal CityWalk IMAX',
        source_url: 'https://www.fandango.com/universal-cinema-an-amc-theatre-aaawx/theater-page'
    };
    const page = app({ data, status: {
        status: 'ok', source: 'Fandango', stale: false, last_successful_at: '2026-09-07T19:00:00Z'
    } });
    await page.load();
    assert.match(page.content(), /Showtime source: Fandango/);
    assert.equal(page.content().split(data.century.source_url).length - 1, 1);
    assert.match(page.content(), /universal-cinema-an-amc-theatre-aaawx\/theater-page/);
    assert.match(page.content(), /href="https:\/\/www\.amctheatres\.com\/showtimes\/example\/2026-09-07"/);
    assert.match(html, /title="Reload saved data; does not run a new fetch"/);
});

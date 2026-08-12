#!/usr/bin/env node
/**
 * Regression test for the Taiga singleton toggle's frontend state machine
 * (docs/design.md's stopped / starting… / running / error states), run
 * against the *real, rendered* <script> extracted verbatim from
 * app.render_page() at test time — not a hand-copied snapshot, so this
 * tracks whatever is actually shipped in app/app.py, not a fork of it.
 *
 * This is the same technique (Node's `vm` module, with document/fetch/
 * setTimeout/setInterval/Date.now stubbed) used ad hoc by the reviewer pass
 * that found docs/test-review.md's "Defect 1" (a race between an explicit
 * toggle-off and a concurrent /status poll landing while the off request —
 * which blocks server-side on `docker compose down`, up to 90s — is still
 * in flight), and by the developer's own earlier ad hoc verification
 * (docs/implementation.md "Verification performed" #12, not committed at
 * the time). Committed here now per the reviewer's suggestion, since it's
 * what caught a real bug.
 *
 * This repo's test suite is otherwise Python-stdlib-unittest-only by
 * explicit prior convention (tests/test_upload.py's own docstring: "no
 * pytest, no third-party test runner") — there is no existing JS test
 * runner anywhere in this repo, so this file is plain Node with no
 * dependencies, run directly:
 *
 *   node tests/test_taiga_frontend.js
 *
 * Exits 0 with "ALL PASS" on success; on any failed assertion, prints the
 * failure and exits 1.
 */
'use strict';

const assert = require('assert');
const vm = require('vm');
const path = require('path');
const { execFileSync } = require('child_process');

const REPO_ROOT = path.resolve(__dirname, '..');

// ─── extract the real, rendered <script> from render_page() ────────────────

const PY_EXTRACTOR = [
  'import sys',
  "sys.path.insert(0, 'app')",
  'import app as appmod',
  'print(appmod.render_page())',
].join('\n');

function extractRenderedScript() {
  const html = execFileSync('python3', ['-c', PY_EXTRACTOR], {
    cwd: REPO_ROOT,
    env: Object.assign({}, process.env, {
      TOTP_SECRET: process.env.TOTP_SECRET || 'JBSWY3DPEHPK3PXP',
      TAIGA_ENABLED: '1',
    }),
    encoding: 'utf8',
  });
  const m = html.match(/<script>([\s\S]*)<\/script>/);
  if (!m) throw new Error('could not find <script>...</script> in render_page() output');
  return m[1];
}

const SCRIPT_SRC = extractRenderedScript();

// ─── minimal DOM/fetch/timer stubs ──────────────────────────────────────────

function makeElementStub(id) {
  const classes = new Set();
  return {
    id,
    classList: {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      contains: (c) => classes.has(c),
    },
    style: {},
    value: '',
    textContent: '',
    innerHTML: '',
    checked: false,
    addEventListener() {},
    focus() {},
  };
}

function makeDocumentStub(elements) {
  return {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, makeElementStub(id));
      return elements.get(id);
    },
    createElement() {
      // Stands in for esc()'s "set textContent, read innerHTML back
      // HTML-escaped" trick — good enough for these tests, which don't
      // assert on escaping.
      const el = { _text: '' };
      Object.defineProperty(el, 'textContent', {
        get() { return el._text; },
        set(v) { el._text = v; },
      });
      Object.defineProperty(el, 'innerHTML', {
        get() {
          return String(el._text)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
        },
      });
      return el;
    },
    addEventListener() {},
  };
}

// One test case = one fresh vm context (avoids any state/timer bleed
// between cases) with its own pending-fetch queue.
function createCase() {
  const elements = new Map();
  const pendingFetches = []; // {url, opts, resolve, reject}

  function fetchStub(url, opts) {
    return new Promise((resolve, reject) => {
      pendingFetches.push({ url, opts, resolve, reject });
    });
  }

  function resolveFetch(matchFn, status, body) {
    const idx = pendingFetches.findIndex(matchFn);
    if (idx === -1) {
      throw new Error(
        'resolveFetch: no matching pending fetch. Pending URLs: [' +
          pendingFetches.map((f) => f.url).join(', ') + ']'
      );
    }
    const [f] = pendingFetches.splice(idx, 1);
    f.resolve({ status, json: async () => body });
  }

  function rejectFetch(matchFn, err) {
    const idx = pendingFetches.findIndex(matchFn);
    if (idx === -1) {
      throw new Error(
        'rejectFetch: no matching pending fetch. Pending URLs: [' +
          pendingFetches.map((f) => f.url).join(', ') + ']'
      );
    }
    const [f] = pendingFetches.splice(idx, 1);
    f.reject(err || new Error('simulated network failure'));
  }

  const sandbox = {
    document: makeDocumentStub(elements),
    fetch: fetchStub,
    // Real timers are never allowed to fire in these tests — every "poll"
    // is an explicit refresh() call the test makes itself, and every
    // setTimeout(refresh, ...) the app code schedules (e.g. after a
    // successful toggle) is just dropped here rather than firing for real,
    // to keep the tests deterministic and instant.
    setTimeout() { return 0; },
    setInterval() { return 0; },
    console,
  };
  vm.createContext(sandbox);
  vm.runInContext(SCRIPT_SRC, sandbox, { filename: 'rendered-taiga-page-script.js' });
  // Give the sandbox's own Date a controllable .now() (used by the 90s
  // starting/error timeout logic) without losing the real Date class.
  vm.runInContext(
    'var __mockNow = Date.now(); ' +
      'Date.now = function() { return __mockNow; }; ' +
      'function __setMockNow(v) { __mockNow = v; }',
    sandbox
  );

  function setMockNow(v) {
    sandbox.__callArgs = [v];
    vm.runInContext('__setMockNow(...__callArgs)', sandbox);
    delete sandbox.__callArgs;
  }
  function advanceMockNow(byMs) {
    setMockNow(vm.runInContext('__mockNow', sandbox) + byMs);
  }

  function call(name, ...args) {
    sandbox.__callArgs = args;
    const result = vm.runInContext(`${name}(...__callArgs)`, sandbox);
    delete sandbox.__callArgs;
    return result;
  }

  function rowsHtml() {
    return elements.has('rows') ? elements.get('rows').innerHTML : '';
  }

  return { sandbox, elements, resolveFetch, rejectFetch, call, advanceMockNow, rowsHtml };
}

async function tick() {
  // Let already-resolved microtask chains (e.g. a fetch we just resolved,
  // whose continuation we don't hold a direct promise handle to — the
  // script's own unawaited bootstrap `refresh()` call at load time) fully
  // settle before we inspect state.
  await new Promise((resolve) => setImmediate(resolve));
}

function taigaStatus({ on, url = null }) {
  return {
    instances: [],
    engines: {},
    host_enabled: false,
    taiga_enabled: true,
    taiga: on,
    taiga_url: on ? (url || 'http://127.0.0.1:9000') : null,
    taiga_label: 'Taiga',
  };
}

async function setupCase() {
  const c = createCase();
  // Drain the script's own unawaited `refresh(); setInterval(...)` bootstrap
  // call at load time with a neutral "off" baseline, so it can't race with
  // (or get mistaken for) the fetches each test issues itself.
  c.resolveFetch((f) => f.url === '/status', 200, taigaStatus({ on: false }));
  await tick();
  await tick();
  return c;
}

// ─── tiny test runner (no framework — matches this repo's stdlib-only
// convention as closely as a JS test can) ───────────────────────────────────

const tests = [];
function test(name, fn) { tests.push({ name, fn }); }

// ─── Test 1: docs/test-review.md Defect 1's exact race ────────────────────
// An explicit toggle-off, with a concurrent /status poll landing while the
// off POST (which blocks server-side on `docker compose down`, up to 90s)
// is still unresolved, must still settle cleanly on "stopped" — never a
// false "starting…"/"error".

test('toggle-off race: concurrent poll mid-flight still settles on stopped', async () => {
  const c = await setupCase();

  // 1. Taiga is running.
  let p = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, taigaStatus({ on: true }));
  await p;
  assert.ok(c.rowsHtml().includes('running'), 'expected "running" after initial poll');

  // 2. User clicks the toggle off — fires POST /taiga/off, left unresolved
  // here to simulate `docker compose down` still running server-side.
  const checkboxEl = { checked: true };
  const togglePromise = c.call('toggle', 'taiga', null, false, checkboxEl);
  await tick();

  // 3. A long time passes with the off request still in flight (well past
  // what would have been the 90s starting/error timeout window, if the
  // pre-fix bug's re-armed taigaWasRunning had been allowed to stand) —
  // and a concurrent /status poll lands mid-flight, correctly reporting
  // taiga: true because the containers genuinely haven't stopped yet.
  c.advanceMockNow(45000);
  let p2 = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, taigaStatus({ on: true }));
  await p2;
  c.advanceMockNow(50000); // total 95s since the click — past the 90s window

  // 4. The off POST finally resolves (`docker compose down` completed
  // server-side).
  c.resolveFetch((f) => f.url === '/taiga/off', 200, {});
  await togglePromise;
  await tick();

  // 5. The next poll correctly reports taiga: false.
  let p3 = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, taigaStatus({ on: false }));
  await p3;

  const html = c.rowsHtml();
  assert.ok(html.includes('>stopped<'), 'expected "stopped" after a successful toggle-off, got: ' + html);
  assert.ok(!html.includes('starting'), 'must not show "starting…" after a successful toggle-off, got: ' + html);
  assert.ok(!html.includes('taiga-err'), 'must not show "error" after a successful toggle-off, got: ' + html);
});

// ─── Test 2: the toggle-off retry path (after a 428 TOTP-code prompt) hits
// the same race window and must be equally immune ───────────────────────────
// submitActionCode() fires the *actual* off request in this path (the first
// attempt in toggle() gets a 428 and never touches the server) — it needs
// the same taigaOffInFlight protection as toggle()'s direct path.

test('toggle-off race via the 428/TOTP-code retry path also settles on stopped', async () => {
  const c = await setupCase();

  let p = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, taigaStatus({ on: true }));
  await p;

  // First attempt: server says TOTP code required (428), nothing touched.
  const checkboxEl = { checked: true };
  const togglePromise = c.call('toggle', 'taiga', null, false, checkboxEl);
  await tick();
  c.resolveFetch((f) => f.url === '/taiga/off', 428, {});
  await togglePromise;
  await tick();

  // While the user is typing the code, a poll runs and (correctly) still
  // sees taiga: true — nothing has actually been requested to stop yet.
  let pMid = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, taigaStatus({ on: true }));
  await pMid;

  // User submits the code — this is the request that actually triggers
  // taiga_run("down") server-side, left unresolved to simulate it still
  // running.
  const submitPromise = c.call('submitActionCode');
  await tick();

  // A concurrent poll lands mid-flight, again correctly reporting
  // taiga: true (containers haven't stopped yet).
  let p2 = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, taigaStatus({ on: true }));
  await p2;

  // The real off POST resolves.
  c.resolveFetch((f) => f.url === '/taiga/off', 200, {});
  await submitPromise;
  await tick();

  let p3 = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, taigaStatus({ on: false }));
  await p3;

  const html = c.rowsHtml();
  assert.ok(html.includes('>stopped<'), 'expected "stopped" after the retried toggle-off, got: ' + html);
  assert.ok(!html.includes('starting'), 'must not show "starting…", got: ' + html);
  assert.ok(!html.includes('taiga-err'), 'must not show "error", got: ' + html);
});

// ─── Test 3: the legitimate case this logic exists for — an unexpected
// stop while running (no toggle) — must still work as designed ────────────
// (docs/design.md: a single transient poll blip re-arms "starting…" rather
// than immediately flashing "error"; a genuine, non-recovering failure
// still eventually surfaces as "error" after the 90s timeout.)

test('unexpected stop while running (no toggle): single blip re-arms starting, then recovers', async () => {
  const c = await setupCase();

  let p = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, taigaStatus({ on: true }));
  await p;
  assert.ok(c.rowsHtml().includes('running'));

  // Nobody touched the toggle — Taiga just stops reporting as on.
  let p2 = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, taigaStatus({ on: false }));
  await p2;
  let html = c.rowsHtml();
  assert.ok(html.includes('starting'), 'expected an unexpected stop to re-arm "starting…", got: ' + html);
  assert.ok(!html.includes('taiga-err'), 'must not flash "error" on a single blip, got: ' + html);

  // It recovers shortly after.
  let p3 = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, taigaStatus({ on: true }));
  await p3;
  html = c.rowsHtml();
  assert.ok(html.includes('running'), 'expected recovery back to "running", got: ' + html);
  assert.ok(!html.includes('taiga-err'), 'must not have shown "error" during a recovered blip, got: ' + html);
});

test('unexpected stop while running that never recovers still surfaces error after 90s', async () => {
  const c = await setupCase();

  let p = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, taigaStatus({ on: true }));
  await p;

  let p2 = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, taigaStatus({ on: false }));
  await p2;
  assert.ok(c.rowsHtml().includes('starting'));

  c.advanceMockNow(91000);
  let p3 = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, taigaStatus({ on: false }));
  await p3;

  const html = c.rowsHtml();
  assert.ok(html.includes('taiga-err'), 'expected "error" after a 90s non-recovering failure, got: ' + html);
});

// ─── Test 5: docs/test-review.md Defect 2 — two overlapping toggle-off
// dispatches must not reopen Defect 1's false starting…/error outcome ──────
// A single shared taigaOffInFlight boolean can't tell "no off requests in
// flight" apart from "one of two just finished, the other is still
// running": the first-to-resolve off request must not be allowed to declare
// the coast clear while a second, independently-dispatched off request is
// still genuinely outstanding.

test('two overlapping toggle-off dispatches: first resolving must not let a mid-flight poll re-arm starting while the second is still outstanding', async () => {
  const c = await setupCase();

  let p = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, taigaStatus({ on: true }));
  await p;
  assert.ok(c.rowsHtml().includes('running'), 'expected "running" after initial poll');

  // Two independent toggle-off dispatches, both left unresolved (simulating
  // an impatient user clicking off again while the first `docker compose
  // down` is still genuinely running and the row still visibly shows "on").
  const cb1 = { checked: true };
  const cb2 = { checked: true };
  const togglePromise1 = c.call('toggle', 'taiga', null, false, cb1);
  await tick();
  const togglePromise2 = c.call('toggle', 'taiga', null, false, cb2);
  await tick();

  // First off request resolves — but the second is still outstanding, so
  // Taiga is genuinely still (accurately) reporting on.
  c.resolveFetch((f) => f.url === '/taiga/off', 200, {});
  await togglePromise1;
  await tick();

  // A poll lands here, correctly reporting taiga: true (the second `down`
  // hasn't finished yet) — this must not be allowed to re-arm
  // taigaWasRunning, since an off request is still genuinely in flight.
  let p2 = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, taigaStatus({ on: true }));
  await p2;

  // The second off request finally resolves (the real completion).
  c.resolveFetch((f) => f.url === '/taiga/off', 200, {});
  await togglePromise2;
  await tick();

  // The next poll correctly reports taiga: false.
  let p3 = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, taigaStatus({ on: false }));
  await p3;

  const html = c.rowsHtml();
  assert.ok(html.includes('>stopped<'), 'expected "stopped" after two overlapping, both-successful toggle-off requests, got: ' + html);
  assert.ok(!html.includes('starting'), 'must not show "starting…" after two overlapping toggle-off requests, got: ' + html);
  assert.ok(!html.includes('taiga-err'), 'must not show "error" after two overlapping toggle-off requests, got: ' + html);
});

test('a network-level failed toggle-off still releases taigaOffPendingCount (does not leak)', async () => {
  const c = await setupCase();

  let p = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, taigaStatus({ on: true }));
  await p;
  assert.ok(c.rowsHtml().includes('running'), 'expected "running" after initial poll');

  // The off request fails at the network level (fetch() itself rejects —
  // not a resolved non-2xx response) — e.g. a dropped connection while
  // `docker compose down` is still running server-side. performAction()
  // has no try/catch of its own (it just returns fetch(...) directly), so
  // this propagates out of toggle()'s await.
  const cb = { checked: true };
  const togglePromise = c.call('toggle', 'taiga', null, false, cb);
  await tick();
  c.rejectFetch((f) => f.url === '/taiga/off', new Error('simulated network failure'));
  await assert.rejects(togglePromise, 'expected toggle() to propagate the network failure');
  await tick();

  // toggle()'s off branch directly sets taigaWasRunning = false regardless
  // of how the request turns out, so that alone can't distinguish "the
  // counter released" from "it leaked". Instead: poll again reporting
  // taiga is (still) genuinely running — this only re-arms taigaWasRunning
  // via the `if (taigaOffPendingCount === 0) taigaWasRunning = true;` guard
  // in refresh(). If the counter leaked (stuck above 0 from the failed
  // request never decrementing), this guard stays permanently suppressed.
  let p2 = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, taigaStatus({ on: true }));
  await p2;
  assert.ok(c.rowsHtml().includes('running'), 'expected "running" — the failed off request did not actually stop it');

  // Now a genuine unexpected stop (nobody toggled anything this time) must
  // trigger the hiccup-detection "starting…" state, per docs/design.md —
  // it can only do that if taigaWasRunning actually got re-armed above,
  // which in turn only happens if the counter reached 0 after the failed
  // request. A leaked counter would keep taigaWasRunning stuck false and
  // this poll would wrongly show "stopped" immediately instead.
  let p3 = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, taigaStatus({ on: false }));
  await p3;
  const html = c.rowsHtml();
  assert.ok(html.includes('starting'), 'expected the unexpected-stop hiccup detection to fire (taigaOffPendingCount must not have leaked), got: ' + html);
  assert.ok(!html.includes('>stopped<'), 'a leaked counter would wrongly suppress hiccup detection and show "stopped" immediately, got: ' + html);
});

// ─── run ────────────────────────────────────────────────────────────────────

(async () => {
  let failed = 0;
  for (const { name, fn } of tests) {
    try {
      await fn();
      console.log('PASS - ' + name);
    } catch (err) {
      failed++;
      console.error('FAIL - ' + name);
      console.error(err && err.stack ? err.stack : err);
    }
  }
  console.log('');
  if (failed > 0) {
    console.error(failed + '/' + tests.length + ' test(s) FAILED');
    process.exit(1);
  } else {
    console.log('ALL PASS (' + tests.length + '/' + tests.length + ')');
    process.exit(0);
  }
})();

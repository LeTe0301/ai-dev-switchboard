#!/usr/bin/env node
/**
 * Regression test for the generalized singleton-toggle frontend state
 * machine (docs/design.md's stopped / starting… / running / error states),
 * run against the *real, rendered* <script> extracted verbatim from
 * app.render_page() at test time — not a hand-copied snapshot, so this
 * tracks whatever is actually shipped in app/app.py, not a fork of it.
 *
 * This supersedes tests/test_taiga_frontend.js (backlog item 1a, commit
 * ed84d73): that file's own six race-condition tests (docs/test-review.md
 * Defects 1 and 2) are reproduced here verbatim in shape, but *parametrized*
 * over `kind` ('taiga' | 'gitea') instead of hardcoded to Taiga alone — per
 * docs/spec.md's backlog item 2a "Risk / rollback notes": generalizing
 * taigaPending/taigaWasRunning/taigaOffPendingCount into the per-kind
 * singletonToggleState map is "the one piece of this spec that touches
 * already-shipped, reviewer-approved code" and "must be verified with the
 * same rigor as any refactor of tested logic — re-run (or adapt) whatever
 * test technique the reviewer used to catch 1a's Defects 1 and 2 against
 * the *generalized* code path for both taiga and gitea kinds, not just
 * spot-check Gitea in isolation". Running the exact same suite twice (once
 * per kind) against the one shared, generalized code path is what actually
 * verifies the refactor itself, not just each kind's own wiring —a bug in
 * the generalization (e.g. state accidentally shared/leaked between kinds)
 * would show up as one kind's test corrupting the other's expected state,
 * which a single-kind-only test run could never catch.
 *
 * Uses the same technique as test_taiga_frontend.js: Node's `vm` module,
 * with document/fetch/setTimeout/setInterval/Date.now stubbed. Plain Node,
 * no dependencies, run directly:
 *
 *   node tests/test_singleton_toggle_frontend.js
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
      // Both kinds enabled at once — same as a real box that ran both
      // --with-taiga and --with-git-hosting (docs/spec.md's own "both flags
      // in one install run" edge case) — so this one extraction covers both
      // rows' rendered JS in a single pass.
      TAIGA_ENABLED: '1',
      GITEA_ENABLED: '1',
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
    // Dedicated team chat page (Taiga #10) added an unconditional top-level
    // `location.pathname.match(...)` router branch at the bottom of the
    // rendered <script> — every file that extracts and runs that script
    // (this one included) needs a location stub or script load itself
    // throws. Shape matches tests/test_team_frontend.js's own stub.
    location: { pathname: '/', href: '' },
    console,
  };
  vm.createContext(sandbox);
  vm.runInContext(SCRIPT_SRC, sandbox, { filename: 'rendered-singleton-toggle-page-script.js' });
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

  // Scopes assertions to exactly one kind's own <div class="row">...</div>
  // slice, keyed off its onchange="toggle('<kind>',null,...)" attribute —
  // needed once both Taiga and Gitea rows render at once (this file's whole
  // point), since e.g. a bare "stopped"/"running" substring check against
  // the *whole* rows container would false-positive/negative on whichever
  // row isn't under test. Anchoring on the toggle(...) call (not the label
  // text) avoids any ambiguity with a project instance that happened to be
  // named "Taiga"/"Gitea".
  function rowHtml(kind) {
    const html = rowsHtml();
    const marker = `toggle('${kind}',null`;
    const markerIdx = html.indexOf(marker);
    if (markerIdx === -1) return '';
    const rowStart = html.lastIndexOf('<div class="row">', markerIdx);
    const rowEnd = html.indexOf('<div class="row">', markerIdx);
    return html.slice(rowStart === -1 ? 0 : rowStart, rowEnd === -1 ? html.length : rowEnd);
  }

  return { sandbox, elements, resolveFetch, rejectFetch, call, advanceMockNow, rowsHtml, rowHtml };
}

async function tick() {
  // Let already-resolved microtask chains (e.g. a fetch we just resolved,
  // whose continuation we don't hold a direct promise handle to — the
  // script's own unawaited bootstrap `refresh()` call at load time) fully
  // settle before we inspect state.
  await new Promise((resolve) => setImmediate(resolve));
}

// Both kinds enabled in every status payload (see extractRenderedScript's
// comment) — statusFor() only varies the ON/URL fields for the kind under
// test in a given case, leaving the *other* kind reporting a stable "off,
// never toggled" baseline throughout. This is deliberate: it means every
// test below is *also*, incidentally, checking that one kind's state
// machine never leaks into or gets disturbed by the other's — exactly the
// generalization-correctness concern docs/spec.md's "Risk / rollback notes"
// flags.
const DEFAULT_URL = { taiga: 'http://127.0.0.1:9000', gitea: 'http://127.0.0.1:3000' };
const OTHER_KIND = { taiga: 'gitea', gitea: 'taiga' };

// The exact per-kind badge text/class app.py's own SINGLETON_TOGGLE_CONFIG
// configures (see docs/test-review.md Defect 2 — this const is duplicated
// deliberately, not imported, so a real regression that accidentally shares
// or copy-pastes the wrong badge config in app.py still shows up here as a
// mismatch rather than trivially passing because both sides read the same
// broken value).
const BADGE_CONFIG = {
  taiga: { text: '⚠ ~3–5 GB RAM when running', class: 'taiga-ram' },
  gitea: { text: 'ℹ ~1 GB RAM when running', class: 'gitea-resources' },
};

// The exact per-kind starting→error timeoutMs app.py's own
// SINGLETON_TOGGLE_CONFIG configures (docs/test-review.md round-5 Finding 1
// — taiga's was raised from 90000 to 180000 to stay >= taiga_run()'s own
// 180s backend "up" timeout; gitea's backend timeout is unchanged, so its
// timeoutMs stays 90000; round 6 raised taiga's again, to 220000, to stay
// >= taiga_run()'s new 220s backend "up" timeout after item 30's
// settle-window recheck was added). Duplicated deliberately, not imported,
// same rationale as BADGE_CONFIG above.
const TIMEOUT_MS_CONFIG = { taiga: 220000, gitea: 90000 };

function statusFor(kind, { on, url = null }) {
  const other = OTHER_KIND[kind];
  const base = {
    instances: [],
    engines: {},
    host_enabled: false,
    taiga_enabled: true,
    gitea_enabled: true,
    taiga: false,
    taiga_url: null,
    taiga_label: 'Taiga',
    gitea: false,
    gitea_url: null,
    gitea_label: 'Gitea',
  };
  base[kind] = on;
  base[`${kind}_url`] = on ? (url || DEFAULT_URL[kind]) : null;
  void other; // the other kind's fields are already the stable "off" baseline above
  return base;
}

async function setupCase() {
  const c = createCase();
  // Drain the script's own unawaited `refresh(); setInterval(...)` bootstrap
  // call at load time with a neutral "off" baseline for both kinds, so it
  // can't race with (or get mistaken for) the fetches each test issues
  // itself.
  c.resolveFetch((f) => f.url === '/status', 200,
    Object.assign(statusFor('taiga', { on: false }), statusFor('gitea', { on: false })));
  await tick();
  await tick();
  return c;
}

// ─── tiny test runner (no framework — matches this repo's stdlib-only
// convention as closely as a JS test can) ───────────────────────────────────

const tests = [];
function test(name, fn) { tests.push({ name, fn }); }

// ─── The six race-condition scenarios, parametrized over `kind` ───────────
// Same test bodies as tests/test_taiga_frontend.js's own six tests, just
// driven off `kind` instead of being hardcoded to 'taiga' — see this file's
// header comment for why running both instantiations matters.

function registerSingletonToggleTests(kind) {
  const errClass = `${kind}-err`;
  const path = `/${kind}/off`;
  const other = OTHER_KIND[kind];
  const badge = BADGE_CONFIG[kind];
  const otherBadge = BADGE_CONFIG[other];

  // ─── docs/test-review.md Defect 2 — the resource badge must show this
  // kind's own text/class, never the other kind's (or a value hardcoded to
  // one kind) — a targeted sabotage of row()'s `SINGLETON_TOGGLE_CONFIG[kind]`
  // lookup previously passed the entire suite undetected; this test exists
  // to close that exact gap ─────────────────────────────────────────────
  test(`[${kind}] resource badge shows this kind's own text/class while running, not another kind's`, async () => {
    const c = await setupCase();

    let p = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: true }));
    await p;

    const html = c.rowHtml(kind);
    assert.ok(html.includes('class="badge ' + badge.class + '"'),
      `[${kind}] expected badge class "${badge.class}", got: ` + html);
    assert.ok(html.includes(badge.text),
      `[${kind}] expected badge text "${badge.text}", got: ` + html);
    assert.ok(!html.includes(otherBadge.class),
      `[${kind}] must not show ${other}'s badge class "${otherBadge.class}", got: ` + html);
    assert.ok(!html.includes(otherBadge.text),
      `[${kind}] must not show ${other}'s badge text "${otherBadge.text}", got: ` + html);
  });

  // ─── docs/test-review.md Defect 1's exact race ───────────────────────
  test(`[${kind}] toggle-off race: concurrent poll mid-flight still settles on stopped`, async () => {
    const c = await setupCase();

    let p = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: true }));
    await p;
    assert.ok(c.rowHtml(kind).includes('running'), 'expected "running" after initial poll');

    const checkboxEl = { checked: true };
    const togglePromise = c.call('toggle', kind, null, false, checkboxEl);
    await tick();

    c.advanceMockNow(45000);
    let p2 = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: true }));
    await p2;
    c.advanceMockNow(50000); // total 95s since the click — past the 90s window

    c.resolveFetch((f) => f.url === path, 200, {});
    await togglePromise;
    await tick();

    let p3 = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: false }));
    await p3;

    const html = c.rowHtml(kind);
    assert.ok(html.includes('>stopped<'), `[${kind}] expected "stopped" after a successful toggle-off, got: ` + html);
    assert.ok(!html.includes('starting'), `[${kind}] must not show "starting…" after a successful toggle-off, got: ` + html);
    assert.ok(!html.includes(errClass), `[${kind}] must not show "error" after a successful toggle-off, got: ` + html);
  });

  // ─── The toggle-off retry path (after a 428 TOTP-code prompt) hits the
  // same race window and must be equally immune ─────────────────────────
  test(`[${kind}] toggle-off race via the 428/TOTP-code retry path also settles on stopped`, async () => {
    const c = await setupCase();

    let p = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: true }));
    await p;

    const checkboxEl = { checked: true };
    const togglePromise = c.call('toggle', kind, null, false, checkboxEl);
    await tick();
    c.resolveFetch((f) => f.url === path, 428, {});
    await togglePromise;
    await tick();

    let pMid = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: true }));
    await pMid;

    const submitPromise = c.call('submitActionCode');
    await tick();

    let p2 = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: true }));
    await p2;

    c.resolveFetch((f) => f.url === path, 200, {});
    await submitPromise;
    await tick();

    let p3 = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: false }));
    await p3;

    const html = c.rowHtml(kind);
    assert.ok(html.includes('>stopped<'), `[${kind}] expected "stopped" after the retried toggle-off, got: ` + html);
    assert.ok(!html.includes('starting'), `[${kind}] must not show "starting…", got: ` + html);
    assert.ok(!html.includes(errClass), `[${kind}] must not show "error", got: ` + html);
  });

  // ─── The legitimate case this logic exists for — an unexpected stop
  // while running (no toggle) — must still work as designed ─────────────
  test(`[${kind}] unexpected stop while running (no toggle): single blip re-arms starting, then recovers`, async () => {
    const c = await setupCase();

    let p = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: true }));
    await p;
    assert.ok(c.rowHtml(kind).includes('running'));

    let p2 = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: false }));
    await p2;
    let html = c.rowHtml(kind);
    assert.ok(html.includes('starting'), `[${kind}] expected an unexpected stop to re-arm "starting…", got: ` + html);
    assert.ok(!html.includes(errClass), `[${kind}] must not flash "error" on a single blip, got: ` + html);

    let p3 = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: true }));
    await p3;
    html = c.rowHtml(kind);
    assert.ok(html.includes('running'), `[${kind}] expected recovery back to "running", got: ` + html);
    assert.ok(!html.includes(errClass), `[${kind}] must not have shown "error" during a recovered blip, got: ` + html);
  });

  test(`[${kind}] unexpected stop while running that never recovers still surfaces error after this kind's own timeoutMs`, async () => {
    const c = await setupCase();
    const timeoutMs = TIMEOUT_MS_CONFIG[kind];

    let p = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: true }));
    await p;

    let p2 = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: false }));
    await p2;
    assert.ok(c.rowHtml(kind).includes('starting'));

    c.advanceMockNow(timeoutMs + 1000);
    let p3 = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: false }));
    await p3;

    const html = c.rowHtml(kind);
    assert.ok(html.includes(errClass), `[${kind}] expected "error" after a ${timeoutMs}ms non-recovering failure, got: ` + html);
  });

  // ─── docs/test-review.md round-5 Finding 1 — the checkbox must be
  // disabled for exactly the "starting…" window (an on-dispatch's own
  // timeoutMs), so an operator can't fire a second, concurrent taiga_run()/
  // gitea_run() "up" against the same Docker Compose stack while the first
  // is still mid-retry — and it must re-enable once that window ends,
  // whether the outcome is a genuine "error" (timeoutMs elapsed) or a
  // recovered "running" ─────────────────────────────────────────────────
  test(`[${kind}] checkbox is disabled while "starting…", re-enabled once it becomes "error" after timeoutMs`, async () => {
    const c = await setupCase();
    const timeoutMs = TIMEOUT_MS_CONFIG[kind];

    const cb = { checked: true };
    const togglePromise = c.call('toggle', kind, null, true, cb);
    await tick();

    let p = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: false }));
    await p;
    let html = c.rowHtml(kind);
    assert.ok(html.includes('starting'), `[${kind}] expected "starting…" right after an on-dispatch, got: ` + html);
    assert.ok(html.includes('<input type="checkbox"  disabled'),
      `[${kind}] expected the checkbox to be disabled while "starting…", got: ` + html);

    c.advanceMockNow(timeoutMs + 1000);
    let p2 = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: false }));
    await p2;
    html = c.rowHtml(kind);
    assert.ok(html.includes(errClass), `[${kind}] expected "error" after timeoutMs elapsed, got: ` + html);
    assert.ok(!html.includes('<input type="checkbox"  disabled') && !html.includes('<input type="checkbox" disabled'),
      `[${kind}] expected the checkbox to be re-enabled once in the (now-terminal) "error" state, got: ` + html);

    c.resolveFetch((f) => f.url === `/${kind}/on`, 200, {});
    await togglePromise;
    await tick();
  });

  test(`[${kind}] checkbox is re-enabled once an on-dispatch actually succeeds ("running")`, async () => {
    const c = await setupCase();

    const cb = { checked: true };
    const togglePromise = c.call('toggle', kind, null, true, cb);
    await tick();

    let p = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: false }));
    await p;
    assert.ok(c.rowHtml(kind).includes('<input type="checkbox"  disabled'),
      `[${kind}] expected the checkbox to be disabled while "starting…"`);

    c.resolveFetch((f) => f.url === `/${kind}/on`, 200, {});
    await togglePromise;
    await tick();

    let p2 = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: true }));
    await p2;
    const html = c.rowHtml(kind);
    assert.ok(html.includes('running'), `[${kind}] expected "running" after a successful on-dispatch, got: ` + html);
    assert.ok(!html.includes('<input type="checkbox"  disabled') && !html.includes('<input type="checkbox" disabled'),
      `[${kind}] expected the checkbox to be re-enabled once running, got: ` + html);
  });

  // ─── docs/test-review.md Defect 2 — two overlapping toggle-off
  // dispatches must not reopen Defect 1's false starting…/error outcome ───
  test(`[${kind}] two overlapping toggle-off dispatches: first resolving must not let a mid-flight poll re-arm starting while the second is still outstanding`, async () => {
    const c = await setupCase();

    let p = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: true }));
    await p;
    assert.ok(c.rowHtml(kind).includes('running'), 'expected "running" after initial poll');

    const cb1 = { checked: true };
    const cb2 = { checked: true };
    const togglePromise1 = c.call('toggle', kind, null, false, cb1);
    await tick();
    const togglePromise2 = c.call('toggle', kind, null, false, cb2);
    await tick();

    c.resolveFetch((f) => f.url === path, 200, {});
    await togglePromise1;
    await tick();

    let p2 = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: true }));
    await p2;

    c.resolveFetch((f) => f.url === path, 200, {});
    await togglePromise2;
    await tick();

    let p3 = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: false }));
    await p3;

    const html = c.rowHtml(kind);
    assert.ok(html.includes('>stopped<'), `[${kind}] expected "stopped" after two overlapping, both-successful toggle-off requests, got: ` + html);
    assert.ok(!html.includes('starting'), `[${kind}] must not show "starting…" after two overlapping toggle-off requests, got: ` + html);
    assert.ok(!html.includes(errClass), `[${kind}] must not show "error" after two overlapping toggle-off requests, got: ` + html);
  });

  test(`[${kind}] a network-level failed toggle-off still releases offPendingCount (does not leak)`, async () => {
    const c = await setupCase();

    let p = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: true }));
    await p;
    assert.ok(c.rowHtml(kind).includes('running'), 'expected "running" after initial poll');

    const cb = { checked: true };
    const togglePromise = c.call('toggle', kind, null, false, cb);
    await tick();
    c.rejectFetch((f) => f.url === path, new Error('simulated network failure'));
    await assert.rejects(togglePromise, `[${kind}] expected toggle() to propagate the network failure`);
    await tick();

    let p2 = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: true }));
    await p2;
    assert.ok(c.rowHtml(kind).includes('running'), `[${kind}] expected "running" — the failed off request did not actually stop it`);

    let p3 = c.call('refresh');
    c.resolveFetch((f) => f.url === '/status', 200, statusFor(kind, { on: false }));
    await p3;
    const html = c.rowHtml(kind);
    assert.ok(html.includes('starting'), `[${kind}] expected the unexpected-stop hiccup detection to fire (offPendingCount must not have leaked), got: ` + html);
    assert.ok(!html.includes('>stopped<'), `[${kind}] a leaked counter would wrongly suppress hiccup detection and show "stopped" immediately, got: ` + html);
  });
}

registerSingletonToggleTests('taiga');
registerSingletonToggleTests('gitea');

// ─── An extra generalization-specific check: toggling one kind off must
// never disturb the other kind's own state (the exact failure mode a buggy
// generalization — e.g. accidentally sharing one flat set of globals instead
// of a real per-kind map — would produce, but that six-test-per-kind loop
// above wouldn't necessarily catch since each case only inspects its own
// kind's row). ─────────────────────────────────────────────────────────────

test('toggling gitea off mid-flight does not disturb taiga\'s already-running state', async () => {
  const c = await setupCase();

  // Both kinds come up running.
  let p = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200,
    Object.assign(statusFor('taiga', { on: true }), statusFor('gitea', { on: true })));
  await p;
  let html = c.rowsHtml();
  assert.ok(html.includes('Taiga'), 'expected a Taiga row');
  assert.ok(html.includes('Gitea'), 'expected a Gitea row');

  // Toggle gitea off, left in flight.
  const cb = { checked: true };
  const togglePromise = c.call('toggle', 'gitea', null, false, cb);
  await tick();

  // A poll lands mid-flight: taiga is still (accurately) running, gitea is
  // still (accurately) reporting on (docker compose down hasn't finished).
  let p2 = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200,
    Object.assign(statusFor('taiga', { on: true }), statusFor('gitea', { on: true })));
  await p2;
  assert.ok(c.rowHtml('taiga').includes('running'), 'expected taiga to still show running while gitea\'s off request is in flight');
  assert.ok(!c.rowHtml('gitea').includes('gitea-err') && !c.rowHtml('taiga').includes('taiga-err'),
    'neither row should show error yet, got: ' + c.rowsHtml());

  // The gitea off request resolves; the next poll reports gitea off, taiga
  // still on.
  c.resolveFetch((f) => f.url === '/gitea/off', 200, {});
  await togglePromise;
  await tick();

  let p3 = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200,
    Object.assign(statusFor('taiga', { on: true }), statusFor('gitea', { on: false })));
  await p3;
  const giteaRow = c.rowHtml('gitea');
  const taigaRow = c.rowHtml('taiga');
  assert.ok(giteaRow.includes('>stopped<'), 'expected gitea to show stopped, got: ' + giteaRow);
  assert.ok(taigaRow.includes('running'), 'expected taiga to still show running (undisturbed by gitea\'s own toggle-off), got: ' + taigaRow);
  assert.ok(!taigaRow.includes('taiga-err'), 'taiga must not have been affected by gitea\'s toggle-off, got: ' + taigaRow);
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

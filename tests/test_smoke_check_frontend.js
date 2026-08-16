#!/usr/bin/env node
/**
 * Frontend tests for the HTTP-level smoke check (backlog item 18,
 * docs/spec.md): the per-project Smoke check button's visibility
 * (smokeCheckRow/row()), its dispatch flow with NO confirm() gate
 * (doSmokeCheck), the optional expect_contains text field surviving a
 * refresh() re-render, and its inline result message
 * (handleActionResult's kind === 'smoke-check' branch) — all run against
 * the *real, rendered* <script> extracted verbatim from app.render_page(),
 * same technique as tests/test_deploy_frontend.js.
 *
 * Plain Node, no dependencies:
 *   node tests/test_smoke_check_frontend.js
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
    }),
    encoding: 'utf8',
  });
  const m = html.match(/<script>([\s\S]*)<\/script>/);
  if (!m) throw new Error('could not find <script>...</script> in render_page() output');
  return m[1];
}

const SCRIPT_SRC = extractRenderedScript();

// ─── minimal DOM/fetch/confirm/timer stubs (same shape as
// tests/test_deploy_frontend.js's own stubs) ────────────────────────────

function makeElementStub(id) {
  return {
    id,
    className: '',
    classList: {
      add() {}, remove() {}, contains() { return false; },
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

function createCase() {
  const elements = new Map();
  const pendingFetches = []; // {url, opts, resolve, reject}
  const confirmCalls = [];

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
    f.resolve({ status, ok: status >= 200 && status < 300, json: async () => body });
  }

  const sandbox = {
    document: makeDocumentStub(elements),
    fetch: fetchStub,
    confirm(msg) { confirmCalls.push(msg); return true; },
    setTimeout() { return 0; },
    setInterval() { return 0; },
    console,
  };
  vm.createContext(sandbox);
  vm.runInContext(SCRIPT_SRC, sandbox, { filename: 'rendered-smoke-check-page-script.js' });

  function call(name, ...args) {
    sandbox.__callArgs = args;
    const result = vm.runInContext(`${name}(...__callArgs)`, sandbox);
    delete sandbox.__callArgs;
    return result;
  }

  function rowsHtml() {
    return elements.has('rows') ? elements.get('rows').innerHTML : '';
  }

  // smokeCheckExpect is a top-level `let` in the rendered script -- vm's
  // own per-script lexical environment means it is NOT reachable as a
  // property of the sandbox object from outside (unlike a `var`/function
  // declaration), only from code actually RUN inside this same context via
  // vm.runInContext -- same technique tests/test_team_frontend.js's own
  // setTeamTaskText() helper already establishes for teamTaskText.
  function setSmokeCheckExpect(name, text) {
    vm.runInContext(
      `smokeCheckExpect[${JSON.stringify(name)}] = ${JSON.stringify(text)};`, sandbox);
  }

  // Same instance-row-slicing technique test_deploy_frontend.js's own
  // instanceRowHtml() uses.
  function instanceRowHtml(name) {
    const html = rowsHtml();
    const marker = '<div class="label">' + name + '</div>';
    const markerIdx = html.indexOf(marker);
    if (markerIdx === -1) return '';
    const rowStart = html.lastIndexOf('<div class="row">', markerIdx);
    const rowEnd = html.indexOf('<div class="row">', markerIdx + marker.length);
    return html.slice(rowStart === -1 ? 0 : rowStart, rowEnd === -1 ? html.length : rowEnd);
  }

  return {
    sandbox, elements, resolveFetch, call, rowsHtml, instanceRowHtml,
    pendingFetches,
    confirmCalls,
    setSmokeCheckExpect,
    msgEl(name) { return elements.get('smoke-check-msg-' + name); },
    inputEl(name) { return elements.get('smoke-expect-' + name); },
  };
}

async function tick() {
  await new Promise((resolve) => setImmediate(resolve));
}

function statusWith(instances) {
  return {
    instances,
    engines: {},
    host_enabled: false, taiga_enabled: false, gitea_enabled: false,
  };
}

async function setupCase(instances) {
  const c = createCase();
  c.resolveFetch((f) => f.url === '/status', 200, statusWith([]));
  await tick();
  await tick();
  const p = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, statusWith(instances));
  await p;
  await tick();
  // renderTeamBranches() (backlog item 13) fires an unrelated, unconditional
  // one-time-per-project /projects/<name>/team/branches fetch as a side
  // effect of rendering ANY kind='inst' row, regardless of on/url/deploy --
  // drained here so it doesn't pollute this file's own pendingFetches
  // assertions below (a pre-existing interaction between item 13 and every
  // kind='inst'-row frontend test file predating this one -- not something
  // this cycle's own code introduces or is in scope to fix elsewhere).
  for (const inst of instances) {
    const url = '/projects/' + inst.name + '/team/branches';
    if (c.pendingFetches.some((f) => f.url === url)) {
      c.resolveFetch((f) => f.url === url, 200, []);
    }
  }
  await tick();
  return c;
}

const tests = [];
function test(name, fn) { tests.push({ name, fn }); }

// ─── visibility ─────────────────────────────────────────────────────────

test('project with a captured url renders a Smoke check button + input + empty message slot', async () => {
  const c = await setupCase([
    { name: 'proj', on: true, url: 'http://127.0.0.1:3000/', engine: 'claude', desc: '',
      code_on: false, code_url: null },
  ]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('class="smoke-btn"'), 'expected a .smoke-btn button, got: ' + html);
  assert.ok(html.includes("doSmokeCheck('proj')"), "expected onclick=\"doSmokeCheck('proj')\", got: " + html);
  assert.ok(html.includes('id="smoke-expect-proj"'), 'expected the expect_contains input, got: ' + html);
  assert.ok(html.includes('id="smoke-check-msg-proj"'), 'expected an empty .smoke-check-msg slot, got: ' + html);
});

// docs/spec.md issue #4: a visible (not hover-only) helper line explaining
// what "Smoke check" does, since the reported context is touch/mobile.
test('the Smoke check row includes a static, always-visible helper line explaining what it does', async () => {
  const c = await setupCase([
    { name: 'proj', on: true, url: 'http://127.0.0.1:3000/', engine: 'claude', desc: '',
      code_on: false, code_url: null },
  ]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('smoke-check-hint'), 'expected a static helper-text element, got: ' + html);
  assert.ok(html.includes('single request') || html.includes('single HTTP request'),
    'expected the helper text to explain the button makes one HTTP request, got: ' + html);
});

test('project without a captured url renders no Smoke check button at all', async () => {
  const c = await setupCase([
    { name: 'plain', on: false, url: null, engine: null, desc: '', code_on: false, code_url: null },
  ]);
  const html = c.instanceRowHtml('plain');
  assert.ok(!html.includes('smoke-btn'), 'must not render a Smoke check button, got: ' + html);
  assert.ok(!html.includes('smoke-check-msg'), 'must not render a smoke-check message slot, got: ' + html);
  assert.ok(!html.includes('smoke-expect'), 'must not render the expect_contains input, got: ' + html);
});

// ─── no confirm() gate ──────────────────────────────────────────────────

test('clicking Smoke check dispatches immediately with no confirm() dialog', async () => {
  const c = await setupCase([
    { name: 'proj', on: true, url: 'http://127.0.0.1:3000/', engine: 'claude', desc: '',
      code_on: false, code_url: null },
  ]);
  const p = c.call('doSmokeCheck', 'proj');
  await tick();
  assert.strictEqual(c.confirmCalls.length, 0, 'a GET-only check must never prompt confirm()');
  assert.strictEqual(c.pendingFetches.length, 1);
  assert.strictEqual(c.pendingFetches[0].url, '/projects/proj/smoke-check');
  assert.strictEqual(c.pendingFetches[0].opts.method, 'POST');
  c.resolveFetch((f) => f.url === '/projects/proj/smoke-check', 200,
    { ok: true, status_code: 200, elapsed_ms: 12, content_ok: null });
  await p;
});

// ─── expect_contains input survives refresh() ──────────────────────────

test('typed expect_contains text survives a refresh() re-render', async () => {
  const c = await setupCase([
    { name: 'proj', on: true, url: 'http://127.0.0.1:3000/', engine: 'claude', desc: '',
      code_on: false, code_url: null },
  ]);
  c.setSmokeCheckExpect('proj', 'Welcome');
  const p = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, statusWith([
    { name: 'proj', on: true, url: 'http://127.0.0.1:3000/', engine: 'claude', desc: '',
      code_on: false, code_url: null },
  ]));
  await p;
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('value="Welcome"'), 'expected the input to re-render with the typed value, got: ' + html);
});

// ─── dispatch + result rendering ────────────────────────────────────────

test('a successful check with no expect_contains shows status + timing, no content verdict', async () => {
  const c = await setupCase([
    { name: 'proj', on: true, url: 'http://127.0.0.1:3000/', engine: 'claude', desc: '',
      code_on: false, code_url: null },
  ]);
  const p = c.call('doSmokeCheck', 'proj');
  await tick();
  c.resolveFetch((f) => f.url === '/projects/proj/smoke-check', 200,
    { ok: true, status_code: 200, elapsed_ms: 84, content_ok: null });
  await p;
  await tick();
  const msg = c.msgEl('proj');
  assert.strictEqual(msg.textContent, '200 · 84ms');
  assert.ok(msg.className.includes('success'), 'expected the success class, got: ' + msg.className);
  assert.ok(!msg.textContent.includes('content:'), 'must not show a content verdict when not checked');
});

test('a successful check where the expected substring IS present shows a positive content match', async () => {
  const c = await setupCase([
    { name: 'proj', on: true, url: 'http://127.0.0.1:3000/', engine: 'claude', desc: '',
      code_on: false, code_url: null },
  ]);
  const p = c.call('doSmokeCheck', 'proj');
  await tick();
  c.resolveFetch((f) => f.url === '/projects/proj/smoke-check', 200,
    { ok: true, status_code: 200, elapsed_ms: 50, content_ok: true });
  await p;
  await tick();
  const msg = c.msgEl('proj');
  assert.strictEqual(msg.textContent, '200 · 50ms · content: found');
  assert.ok(msg.className.includes('success'));
});

test('a successful check where the expected substring is NOT present still shows the real status/timing', async () => {
  const c = await setupCase([
    { name: 'proj', on: true, url: 'http://127.0.0.1:3000/', engine: 'claude', desc: '',
      code_on: false, code_url: null },
  ]);
  const p = c.call('doSmokeCheck', 'proj');
  await tick();
  // A real 200 with a failed content match -- must NOT collapse into a
  // single pass/fail flag (docs/spec.md acceptance criteria).
  c.resolveFetch((f) => f.url === '/projects/proj/smoke-check', 200,
    { ok: true, status_code: 200, elapsed_ms: 50, content_ok: false });
  await p;
  await tick();
  const msg = c.msgEl('proj');
  assert.strictEqual(msg.textContent, '200 · 50ms · content: NOT found');
  assert.ok(msg.className.includes('success'),
    'the HTTP request itself completed fine -- only the content assertion failed');
});

test('an unreachable target (connection refused) shows the error message, never an unhandled failure', async () => {
  const c = await setupCase([
    { name: 'proj', on: true, url: 'http://127.0.0.1:3000/', engine: 'claude', desc: '',
      code_on: false, code_url: null },
  ]);
  const p = c.call('doSmokeCheck', 'proj');
  await tick();
  c.resolveFetch((f) => f.url === '/projects/proj/smoke-check', 200,
    { ok: false, status_code: null, elapsed_ms: 3, error: 'connection refused' });
  await p;
  await tick();
  const msg = c.msgEl('proj');
  assert.strictEqual(msg.textContent, 'connection refused');
  assert.ok(msg.className.includes('error'));
});

test('a second dispatch already in flight (409) shows the in-progress error message', async () => {
  const c = await setupCase([
    { name: 'proj', on: true, url: 'http://127.0.0.1:3000/', engine: 'claude', desc: '',
      code_on: false, code_url: null },
  ]);
  const p = c.call('doSmokeCheck', 'proj');
  await tick();
  c.resolveFetch((f) => f.url === '/projects/proj/smoke-check', 409,
    { ok: false, error: 'a smoke check for this project is already in progress' });
  await p;
  await tick();
  const msg = c.msgEl('proj');
  assert.strictEqual(msg.textContent, 'a smoke check for this project is already in progress');
  assert.ok(msg.className.includes('error'));
});

// ─── TOTP code-overlay retry path ───────────────────────────────────────

test('a 428 mid-dispatch shows the code overlay labeled for this smoke check, and a correct retry succeeds', async () => {
  const c = await setupCase([
    { name: 'proj', on: true, url: 'http://127.0.0.1:3000/', engine: 'claude', desc: '',
      code_on: false, code_url: null },
  ]);
  const p = c.call('doSmokeCheck', 'proj');
  await tick();
  c.resolveFetch((f) => f.url === '/projects/proj/smoke-check', 428, { error: 'totp_required' });
  await p;
  await tick();

  const label = c.elements.get('code-overlay-label');
  assert.strictEqual(label.textContent, 'Smoke checking: proj');

  c.elements.get('action-code').value = '123456';
  const p2 = c.call('submitActionCode');
  await tick();
  assert.strictEqual(c.pendingFetches.length, 1);
  const body = JSON.parse(c.pendingFetches[0].opts.body);
  assert.strictEqual(body.code, '123456');
  c.resolveFetch((f) => f.url === '/projects/proj/smoke-check', 200,
    { ok: true, status_code: 200, elapsed_ms: 10, content_ok: null });
  await p2;
  await tick();

  const msg = c.msgEl('proj');
  assert.strictEqual(msg.textContent, '200 · 10ms');
});

// ─── run ────────────────────────────────────────────────────────────────

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

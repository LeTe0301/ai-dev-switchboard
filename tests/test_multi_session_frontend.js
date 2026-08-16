#!/usr/bin/env node
/**
 * Frontend tests for concurrent sessions per project, part 2 (docs/spec.md,
 * docs/design.md): the "+" start-session control (engineRow()'s always-on
 * picker + the "+ Start session" button, `toggle('session-spawn', ...)`),
 * the per-session list (sessionsRow()) with its independent Stop control
 * per session (`stopSession()` / `toggle('session-stop', ...)`), the
 * `kind === 'inst'` row's checkbox removal, and Host/Taiga/Gitea rows
 * staying pixel-for-pixel/behaviorally unchanged — all run against the
 * *real, rendered* <script> extracted verbatim from app.render_page(),
 * same technique as tests/test_deploy_frontend.js/tests/
 * test_smoke_check_frontend.js. This is the canonical `kind='inst'`-row
 * frontend test file (docs/spec.md "Background" -- none existed before
 * this cycle).
 *
 * Plain Node, no dependencies:
 *   node tests/test_multi_session_frontend.js
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

// ─── minimal DOM/fetch/timer stubs (same shape as
// tests/test_smoke_check_frontend.js's own stubs) ──────────────────────────

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
    confirm() { return true; },
    setTimeout() { return 0; },
    setInterval() { return 0; },
    console,
  };
  vm.createContext(sandbox);
  vm.runInContext(SCRIPT_SRC, sandbox, { filename: 'rendered-multi-session-page-script.js' });

  function call(name, ...args) {
    sandbox.__callArgs = args;
    const result = vm.runInContext(`${name}(...__callArgs)`, sandbox);
    delete sandbox.__callArgs;
    return result;
  }

  function rowsHtml() {
    return elements.has('rows') ? elements.get('rows').innerHTML : '';
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
  };
}

async function tick() {
  await new Promise((resolve) => setImmediate(resolve));
}

const ENGINES = { claude: 'Claude', codex: 'Codex' };

function statusWith(instances, extra) {
  return Object.assign({
    instances,
    engines: ENGINES,
    host_enabled: false, taiga_enabled: false, gitea_enabled: false,
  }, extra);
}

async function setupCase(instances, extra) {
  const c = createCase();
  c.resolveFetch((f) => f.url === '/status', 200, statusWith([]));
  await tick();
  await tick();
  const p = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, statusWith(instances, extra));
  await p;
  await tick();
  // renderTeamBranches() (backlog item 13) fires an unrelated, unconditional
  // one-time-per-project /projects/<name>/team/branches fetch as a side
  // effect of rendering ANY kind='inst' row -- drained here so it doesn't
  // pollute this file's own pendingFetches assertions below, same reasoning
  // tests/test_deploy_frontend.js/tests/test_smoke_check_frontend.js's own
  // setupCase() already documents.
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

// ─── State 1/2: zero sessions ───────────────────────────────────────────

test('0 sessions, >=1 engine configured: renders the engine picker + "+ Start session" control, no checkbox, no session list', async () => {
  const c = await setupCase([
    { name: 'proj', sessions: [], desc: '', code_on: false, code_url: null },
  ]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('class="engine-picker"'), 'expected the engine picker, got: ' + html);
  assert.ok(html.includes('class="session-spawn-btn"'), 'expected the "+ Start session" button, got: ' + html);
  assert.ok(html.includes('+ Start session'), 'expected the button label, got: ' + html);
  assert.ok(!html.includes('type="checkbox"'), 'an inst row must never render a checkbox, got: ' + html);
  assert.ok(!html.includes('sessions-list'), 'zero sessions must render no session list at all, got: ' + html);
  assert.ok(html.includes('>stopped<'), 'expected the "stopped" sub text, got: ' + html);
});

test('0 sessions, 0 engines configured: no picker, no spawn button, no session list', async () => {
  const c = await setupCase([
    { name: 'proj', sessions: [], desc: '', code_on: false, code_url: null },
  ], { engines: {} });
  const html = c.instanceRowHtml('proj');
  assert.ok(!html.includes('engine-picker'), 'expected no engine picker, got: ' + html);
  assert.ok(!html.includes('session-spawn-btn'), 'expected no spawn button, got: ' + html);
  assert.ok(!html.includes('sessions-list'), 'expected no session list, got: ' + html);
  assert.ok(!html.includes('type="checkbox"'), 'an inst row must never render a checkbox, got: ' + html);
});

// ─── State 3: >=1 sessions running ──────────────────────────────────────

test('2 running sessions of different engines are both listed, each with its own engine label and Stop control, no checkbox anywhere in the row', async () => {
  const c = await setupCase([
    { name: 'proj', sessions: [
      { session_id: 'claude-proj-1', engine: 'claude', url: 'http://127.0.0.1:3001/' },
      { session_id: 'codex-proj-2', engine: 'codex', url: null },
    ], desc: '', code_on: false, code_url: null },
  ]);
  const html = c.instanceRowHtml('proj');
  assert.ok(!html.includes('type="checkbox"'), 'a multi-session inst row must never render a checkbox, got: ' + html);
  assert.ok(html.includes('Claude'), 'expected the Claude engine label, got: ' + html);
  assert.ok(html.includes('Codex'), 'expected the Codex engine label, got: ' + html);
  assert.ok(html.includes("stopSession('proj','claude-proj-1')"), 'expected a Stop control for session 1, got: ' + html);
  assert.ok(html.includes("stopSession('proj','codex-proj-2')"), 'expected a Stop control for session 2, got: ' + html);
  const stopCount = (html.match(/session-stop-btn/g) || []).length;
  assert.strictEqual(stopCount, 2, 'expected exactly one Stop button per session, got: ' + html);
});

test('engine picker still renders even with sessions already running (multiplicity -- always "what to spawn next")', async () => {
  const c = await setupCase([
    { name: 'proj', sessions: [
      { session_id: 'claude-proj-1', engine: 'claude', url: 'http://127.0.0.1:3001/' },
    ], desc: '', code_on: false, code_url: null },
  ]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('class="engine-picker"'), 'expected the picker to still render, got: ' + html);
  assert.ok(html.includes('class="session-spawn-btn"'), 'expected the spawn button to still render, got: ' + html);
});

test('a session with no captured URL yet shows a "starting…" placeholder, not a broken/empty link', async () => {
  const c = await setupCase([
    { name: 'proj', sessions: [
      { session_id: 'claude-proj-1', engine: 'claude', url: null },
    ], desc: '', code_on: false, code_url: null },
  ]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('starting…'), 'expected the starting… placeholder, got: ' + html);
  assert.ok(!/<a href="null"/.test(html), 'must never render a broken/empty href, got: ' + html);
});

test('sub text: running with a resolved newest-session URL shows "running — newest: open"', async () => {
  const c = await setupCase([
    { name: 'proj', sessions: [
      { session_id: 'claude-proj-1', engine: 'claude', url: null },
      { session_id: 'codex-proj-2', engine: 'codex', url: 'http://127.0.0.1:3002/' },
    ], desc: '', code_on: false, code_url: null },
  ]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('running — newest:'), 'expected the multi-session running sub text, got: ' + html);
  assert.ok(html.includes('href="http://127.0.0.1:3002/"'), 'expected the newest session\'s own URL, got: ' + html);
});

test('sub text: running with the newest session still starting (no URL) shows plain "running"', async () => {
  const c = await setupCase([
    { name: 'proj', sessions: [
      { session_id: 'claude-proj-1', engine: 'claude', url: 'http://127.0.0.1:3001/' },
      { session_id: 'codex-proj-2', engine: 'codex', url: null },
    ], desc: '', code_on: false, code_url: null },
  ]);
  const html = c.instanceRowHtml('proj');
  const subMatch = html.match(/<div class="sub">([^<]*)<\/div>/);
  assert.ok(subMatch, 'expected a .sub element, got: ' + html);
  assert.strictEqual(subMatch[1], 'running', 'expected plain "running", got: ' + subMatch[1]);
});

// ─── "+ Start session" dispatch ─────────────────────────────────────────

test('clicking "+ Start session" dispatches POST /instance/<name>/spawn with the selected engine', async () => {
  const c = await setupCase([
    { name: 'proj', sessions: [], desc: '', code_on: false, code_url: null },
  ]);
  // pickEngine() itself triggers its own refresh() poll (existing,
  // pre-spec behavior) -- drained here so it doesn't pollute this test's
  // own pendingFetches assertions below.
  c.call('pickEngine', 'proj', 'codex');
  await tick();
  c.resolveFetch((f) => f.url === '/status', 200, statusWith([
    { name: 'proj', sessions: [], desc: '', code_on: false, code_url: null },
  ]));
  await tick();
  const p = c.call('toggle', 'session-spawn', 'proj', true, null);
  await tick();
  assert.strictEqual(c.pendingFetches.length, 1);
  assert.strictEqual(c.pendingFetches[0].url, '/instance/proj/spawn');
  assert.strictEqual(c.pendingFetches[0].opts.method, 'POST');
  const body = JSON.parse(c.pendingFetches[0].opts.body);
  assert.strictEqual(body.engine, 'codex');
  c.resolveFetch((f) => f.url === '/instance/proj/spawn', 200, { ok: true, session_id: 'codex-proj-9' });
  await p;
});

// ─── Stop dispatch + independence between sessions ──────────────────────

test('stopSession dispatches POST /instance/<name>/session/<id>/stop for exactly the clicked session', async () => {
  const c = await setupCase([
    { name: 'proj', sessions: [
      { session_id: 'claude-proj-A', engine: 'claude', url: 'http://127.0.0.1:3001/' },
      { session_id: 'codex-proj-B', engine: 'codex', url: 'http://127.0.0.1:3002/' },
    ], desc: '', code_on: false, code_url: null },
  ]);
  const p = c.call('stopSession', 'proj', 'codex-proj-B');
  await tick();
  assert.strictEqual(c.pendingFetches.length, 1);
  assert.strictEqual(c.pendingFetches[0].url, '/instance/proj/session/codex-proj-B/stop');
  assert.strictEqual(c.pendingFetches[0].opts.method, 'POST');
  c.resolveFetch((f) => f.url === '/instance/proj/session/codex-proj-B/stop', 200, { ok: true });
  await p;
});

test('after Stop resolves and the next refresh() reflects the removal, only the stopped session disappears -- the other session (and its own open-link) is unchanged', async () => {
  const c = await setupCase([
    { name: 'proj', sessions: [
      { session_id: 'claude-proj-A', engine: 'claude', url: 'http://127.0.0.1:3001/' },
      { session_id: 'codex-proj-B', engine: 'codex', url: 'http://127.0.0.1:3002/' },
    ], desc: '', code_on: false, code_url: null },
  ]);
  let html = c.instanceRowHtml('proj');
  assert.ok(html.includes("stopSession('proj','claude-proj-A')"));
  assert.ok(html.includes("stopSession('proj','codex-proj-B')"));

  const p = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, statusWith([
    { name: 'proj', sessions: [
      { session_id: 'claude-proj-A', engine: 'claude', url: 'http://127.0.0.1:3001/' },
    ], desc: '', code_on: false, code_url: null },
  ]));
  await p;
  await tick();

  html = c.instanceRowHtml('proj');
  assert.ok(html.includes("stopSession('proj','claude-proj-A')"), 'session A must remain, got: ' + html);
  assert.ok(html.includes('href="http://127.0.0.1:3001/"'), "session A's own open-link must be unchanged, got: " + html);
  assert.ok(!html.includes("stopSession('proj','codex-proj-B')"), 'session B must be gone, got: ' + html);
});

// ─── TOTP code-overlay retry path ───────────────────────────────────────

test('a 428 mid-spawn shows the code overlay labeled for this project, and a correct retry succeeds', async () => {
  const c = await setupCase([
    { name: 'proj', sessions: [], desc: '', code_on: false, code_url: null },
  ]);
  const p = c.call('toggle', 'session-spawn', 'proj', true, null);
  await tick();
  c.resolveFetch((f) => f.url === '/instance/proj/spawn', 428, { error: 'totp_required' });
  await p;
  await tick();

  const label = c.elements.get('code-overlay-label');
  assert.strictEqual(label.textContent, 'Starting a session: proj');

  c.elements.get('action-code').value = '123456';
  const p2 = c.call('submitActionCode');
  await tick();
  assert.strictEqual(c.pendingFetches.length, 1);
  const body = JSON.parse(c.pendingFetches[0].opts.body);
  assert.strictEqual(body.code, '123456');
  assert.strictEqual(c.pendingFetches[0].url, '/instance/proj/spawn');
  c.resolveFetch((f) => f.url === '/instance/proj/spawn', 200, { ok: true, session_id: 'claude-proj-1' });
  await p2;
});

test('a 428 mid-stop shows the code overlay, and pendingSessionStop survives the retry so the SAME session is targeted', async () => {
  const c = await setupCase([
    { name: 'proj', sessions: [
      { session_id: 'claude-proj-A', engine: 'claude', url: 'http://127.0.0.1:3001/' },
    ], desc: '', code_on: false, code_url: null },
  ]);
  const p = c.call('stopSession', 'proj', 'claude-proj-A');
  await tick();
  c.resolveFetch((f) => f.url === '/instance/proj/session/claude-proj-A/stop', 428, { error: 'totp_required' });
  await p;
  await tick();

  const label = c.elements.get('code-overlay-label');
  assert.strictEqual(label.textContent, 'Stopping session: proj');

  c.elements.get('action-code').value = '654321';
  const p2 = c.call('submitActionCode');
  await tick();
  assert.strictEqual(c.pendingFetches.length, 1);
  // Must retry against the exact same session_id, not a re-read of
  // possibly-already-redrawn state.
  assert.strictEqual(c.pendingFetches[0].url, '/instance/proj/session/claude-proj-A/stop');
  c.resolveFetch((f) => f.url === '/instance/proj/session/claude-proj-A/stop', 200, { ok: true });
  await p2;
});

// ─── Host/Taiga/Gitea rows unaffected ───────────────────────────────────

test('Host/Taiga/Gitea rows keep their checkbox unchanged -- this spec is scoped to kind=inst only', async () => {
  const c = await setupCase([], {
    host_enabled: true, host: true, host_url: 'http://127.0.0.1:9999/', host_label: 'Host',
    taiga_enabled: true, taiga: false, taiga_url: null, taiga_label: 'Taiga',
    gitea_enabled: true, gitea: false, gitea_url: null, gitea_label: 'Gitea',
  });
  const html = c.rowsHtml();
  const checkboxCount = (html.match(/type="checkbox"/g) || []).length;
  assert.strictEqual(checkboxCount, 3, 'expected exactly 3 checkboxes (host/taiga/gitea), got: ' + html);
  assert.ok(html.includes("toggle('host',null"), 'expected the host row\'s own toggle() call, got: ' + html);
  assert.ok(html.includes("toggle('taiga',null"), 'expected the taiga row\'s own toggle() call, got: ' + html);
  assert.ok(html.includes("toggle('gitea',null"), 'expected the gitea row\'s own toggle() call, got: ' + html);
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

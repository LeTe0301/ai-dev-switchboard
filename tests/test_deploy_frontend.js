#!/usr/bin/env node
/**
 * Frontend tests for switchboard-side deploy dispatch (backlog item 2c,
 * part 2b — docs/spec.md, docs/design.md): the per-project Deploy button's
 * visibility (deployRow/row()), the confirm()-gated dispatch flow
 * (doDeploy), and its inline result message (handleActionResult's
 * kind === 'deploy' branch) — all run against the *real, rendered*
 * <script> extracted verbatim from app.render_page(), same technique as
 * tests/test_singleton_toggle_frontend.js (its own header comment explains
 * the "why extraction, not a hand-copied snapshot" reasoning).
 *
 * Plain Node, no dependencies:
 *   node tests/test_deploy_frontend.js
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

// ─── minimal DOM/fetch/confirm/timer stubs ─────────────────────────────────

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
  let confirmReturn = true; // overridden per-test via c.setConfirmReturn(...)

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
    confirm(msg) { confirmCalls.push(msg); return confirmReturn; },
    setTimeout() { return 0; },
    setInterval() { return 0; },
    console,
  };
  vm.createContext(sandbox);
  vm.runInContext(SCRIPT_SRC, sandbox, { filename: 'rendered-deploy-page-script.js' });

  function setMockDate() {} // no time-based logic under test here

  function call(name, ...args) {
    sandbox.__callArgs = args;
    const result = vm.runInContext(`${name}(...__callArgs)`, sandbox);
    delete sandbox.__callArgs;
    return result;
  }

  function rowsHtml() {
    return elements.has('rows') ? elements.get('rows').innerHTML : '';
  }

  // Scopes assertions to exactly one project's own <div class="row">...
  // </div> slice, anchored on its <div class="label">NAME</div> — same
  // slicing technique test_singleton_toggle_frontend.js's own rowHtml()
  // uses, anchored differently since instance rows don't share a single
  // fixed onclick("kind",null,...) shape the way singleton toggles do.
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
    setConfirmReturn(v) { confirmReturn = v; },
    msgEl(name) { return elements.get('deploy-msg-' + name); },
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
  // Drain the script's own unawaited bootstrap refresh() call at load time.
  c.resolveFetch((f) => f.url === '/status', 200, statusWith([]));
  await tick();
  await tick();
  // Now do the real refresh() this test actually wants to assert against.
  const p = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, statusWith(instances));
  await p;
  return c;
}

const tests = [];
function test(name, fn) { tests.push({ name, fn }); }

const DEPLOY = { host: '10.0.0.5', deploy_path: '/opt/myapp', service: 'myapp.service' };

// ─── visibility ─────────────────────────────────────────────────────────

test('project with a deploy-map entry renders a Deploy button + empty message slot', async () => {
  const c = await setupCase([
    { name: 'proj', on: false, url: null, engine: null, desc: '', code_on: false,
      code_url: null, deploy: DEPLOY },
  ]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('class="deploy-btn"'), 'expected a .deploy-btn button, got: ' + html);
  assert.ok(html.includes("doDeploy('proj')"), 'expected onclick="doDeploy(\'proj\')", got: ' + html);
  assert.ok(html.includes('id="deploy-msg-proj"'), 'expected an empty .deploy-msg slot, got: ' + html);
});

test('project without a deploy-map entry renders no Deploy button at all', async () => {
  const c = await setupCase([
    { name: 'plain', on: false, url: null, engine: null, desc: '', code_on: false, code_url: null },
  ]);
  const html = c.instanceRowHtml('plain');
  assert.ok(!html.includes('deploy-btn'), 'must not render a Deploy button, got: ' + html);
  assert.ok(!html.includes('deploy-msg'), 'must not render a deploy message slot, got: ' + html);
});

// ─── confirm() gate ─────────────────────────────────────────────────────

test('clicking Deploy then cancelling the confirm() dialog sends no request', async () => {
  const c = await setupCase([
    { name: 'proj', on: false, url: null, engine: null, desc: '', code_on: false,
      code_url: null, deploy: DEPLOY },
  ]);
  c.setConfirmReturn(false);
  c.call('doDeploy', 'proj');
  await tick();
  assert.strictEqual(c.pendingFetches.length, 0, 'no fetch should have been dispatched');
  assert.strictEqual(c.confirmCalls.length, 1);
  assert.ok(c.confirmCalls[0].includes('10.0.0.5'), 'confirm text should include the target host');
  assert.ok(c.confirmCalls[0].includes('myapp.service'), 'confirm text should include the service name');
});

// Regression guard for the developer's esc()/doDeploy HTML-injection-safety
// deviation (docs/implementation.md / docs/test-review.md Finding #3):
// host/service are operator-hand-edited and unconstrained by any charset
// regex (unlike the already-restricted project `name`), so a stray quote
// character must not break the rendered row or let doDeploy's lookup go
// wrong -- DEPLOY_TARGETS is populated straight from the JSON /status
// response and never interpolated into an HTML attribute at all.
test('a quote-containing host/service value renders safely and still dispatches to the right target', async () => {
  const QUOTED_DEPLOY = { host: `10.0.0.5" onclick="alert(1)`, deploy_path: '/opt/myapp',
    service: `myapp'"; alert(1); //.service` };
  const c = await setupCase([
    { name: 'proj', on: false, url: null, engine: null, desc: '', code_on: false,
      code_url: null, deploy: QUOTED_DEPLOY },
  ]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes("doDeploy('proj')"), 'onclick must stay exactly doDeploy(\'proj\'), got: ' + html);
  assert.ok(!html.includes(QUOTED_DEPLOY.host), 'host must never be embedded into the row HTML at all');
  assert.ok(!html.includes(QUOTED_DEPLOY.service), 'service must never be embedded into the row HTML at all');

  c.setConfirmReturn(true);
  const p = c.call('doDeploy', 'proj');
  await tick();
  assert.strictEqual(c.confirmCalls.length, 1);
  assert.ok(c.confirmCalls[0].includes(QUOTED_DEPLOY.host),
    'confirm() text (a plain JS string, not an HTML sink) should still include the raw host');
  assert.strictEqual(c.pendingFetches.length, 1);
  assert.strictEqual(c.pendingFetches[0].url, '/instance/proj/deploy',
    'must dispatch to the right project despite the quote characters in host/service');
  c.resolveFetch((f) => f.url === '/instance/proj/deploy', 200, { ok: true, message: 'deployed' });
  await p;
});

// ─── dispatch + result rendering ────────────────────────────────────────

test('confirmed deploy that succeeds shows a success message', async () => {
  const c = await setupCase([
    { name: 'proj', on: false, url: null, engine: null, desc: '', code_on: false,
      code_url: null, deploy: DEPLOY },
  ]);
  c.setConfirmReturn(true);
  const p = c.call('doDeploy', 'proj');
  await tick();
  assert.strictEqual(c.pendingFetches.length, 1);
  assert.strictEqual(c.pendingFetches[0].url, '/instance/proj/deploy');
  assert.strictEqual(c.pendingFetches[0].opts.method, 'POST');
  c.resolveFetch((f) => f.url === '/instance/proj/deploy', 200, { ok: true, message: 'deployed' });
  await p;
  await tick();
  const msg = c.msgEl('proj');
  assert.strictEqual(msg.textContent, 'Deployed successfully');
  assert.ok(msg.className.includes('success'), 'expected the success class, got: ' + msg.className);
});

test('confirmed deploy where the push fails shows the push-failed message', async () => {
  const c = await setupCase([
    { name: 'proj', on: false, url: null, engine: null, desc: '', code_on: false,
      code_url: null, deploy: DEPLOY },
  ]);
  const p = c.call('doDeploy', 'proj');
  await tick();
  c.resolveFetch((f) => f.url === '/instance/proj/deploy', 502,
    { ok: false, message: 'push failed: Permission denied' });
  await p;
  await tick();
  const msg = c.msgEl('proj');
  assert.strictEqual(msg.textContent, 'Deploy failed: push failed: Permission denied');
  assert.ok(msg.className.includes('error'), 'expected the error class, got: ' + msg.className);
});

test('confirmed deploy where push succeeds but restart fails shows the distinct message', async () => {
  const c = await setupCase([
    { name: 'proj', on: false, url: null, engine: null, desc: '', code_on: false,
      code_url: null, deploy: DEPLOY },
  ]);
  const p = c.call('doDeploy', 'proj');
  await tick();
  c.resolveFetch((f) => f.url === '/instance/proj/deploy', 502,
    { ok: false, message: 'push succeeded but restart failed: systemctl timeout' });
  await p;
  await tick();
  const msg = c.msgEl('proj');
  assert.strictEqual(msg.textContent, 'Deploy failed: push succeeded but restart failed: systemctl timeout');
  assert.ok(msg.className.includes('error'));
});

test('a second dispatch already in flight (409) shows an "in progress" style failure message', async () => {
  const c = await setupCase([
    { name: 'proj', on: false, url: null, engine: null, desc: '', code_on: false,
      code_url: null, deploy: DEPLOY },
  ]);
  const p = c.call('doDeploy', 'proj');
  await tick();
  c.resolveFetch((f) => f.url === '/instance/proj/deploy', 409,
    { ok: false, message: 'a deploy for this project is already in progress' });
  await p;
  await tick();
  const msg = c.msgEl('proj');
  assert.strictEqual(msg.textContent, 'Deploy failed: a deploy for this project is already in progress');
  assert.ok(msg.className.includes('error'));
});

// ─── TOTP code-overlay retry path ───────────────────────────────────────

test('a 428 mid-dispatch shows the code overlay labeled for this deploy, and a correct retry succeeds', async () => {
  const c = await setupCase([
    { name: 'proj', on: false, url: null, engine: null, desc: '', code_on: false,
      code_url: null, deploy: DEPLOY },
  ]);
  const p = c.call('doDeploy', 'proj');
  await tick();
  c.resolveFetch((f) => f.url === '/instance/proj/deploy', 428, { error: 'totp_required' });
  await p;
  await tick();

  const label = c.elements.get('code-overlay-label');
  assert.strictEqual(label.textContent, 'Deploying: proj');

  c.elements.get('action-code').value = '123456';
  const p2 = c.call('submitActionCode');
  await tick();
  assert.strictEqual(c.pendingFetches.length, 1);
  const body = JSON.parse(c.pendingFetches[0].opts.body);
  assert.strictEqual(body.code, '123456');
  c.resolveFetch((f) => f.url === '/instance/proj/deploy', 200, { ok: true, message: 'deployed' });
  await p2;
  await tick();

  const msg = c.msgEl('proj');
  assert.strictEqual(msg.textContent, 'Deployed successfully');
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

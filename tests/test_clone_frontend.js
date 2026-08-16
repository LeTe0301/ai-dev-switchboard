#!/usr/bin/env node
/**
 * Frontend tests for clone-from-URL (backlog item 16 — docs/spec.md,
 * docs/design.md): the inline form's open/close toggle (openCloneForm/
 * closeCloneForm), the disabled/"Cloning…" loading state (setCloneFormBusy,
 * docs/design.md "Loading State"), the dispatch flow (startClone ->
 * toggle('clone', ...) -> actionBody() reading straight from the URL/name
 * inputs), and its inline result handling (handleActionResult's
 * kind === 'clone' branch, plus the 428 TOTP-retry path and
 * cancelActionCode()'s re-enable-on-cancel behavior) — all run against the
 * *real, rendered* <script> extracted verbatim from app.render_page(),
 * same technique as tests/test_deploy_frontend.js.
 *
 * Plain Node, no dependencies:
 *   node tests/test_clone_frontend.js
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

// ─── minimal DOM/fetch/timer stubs ─────────────────────────────────────────

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
    disabled: false,
    checked: false,
    addEventListener() {},
    focus() {},
  };
}

function makeDocumentStub(elements) {
  // #clone-form button is queried via querySelectorAll (setCloneFormBusy)
  // and querySelector (startClone's textContent flip) -- one synthetic
  // stub element, shared, standing in for the single real <button>Clone
  // </button> inside the rendered #clone-form.
  const cloneButtonKey = '__clone-form-button';
  return {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, makeElementStub(id));
      return elements.get(id);
    },
    querySelectorAll(sel) {
      if (sel === '#clone-form button') {
        if (!elements.has(cloneButtonKey)) elements.set(cloneButtonKey, makeElementStub('clone-form-button'));
        return [elements.get(cloneButtonKey)];
      }
      return [];
    },
    querySelector(sel) {
      const all = this.querySelectorAll(sel);
      return all.length ? all[0] : null;
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
    // Dedicated team chat page (Taiga #10) added an unconditional top-level
    // `location.pathname.match(...)` router branch at the bottom of the
    // rendered <script> -- every file that extracts and runs that script
    // (this one included) needs a location stub or script load itself
    // throws. Shape matches tests/test_team_frontend.js's own stub.
    location: { pathname: '/', href: '' },
    setTimeout(fn) { sandbox.__timeouts = sandbox.__timeouts || []; sandbox.__timeouts.push(fn); return 0; },
    setInterval() { return 0; },
    console,
  };
  vm.createContext(sandbox);
  vm.runInContext(SCRIPT_SRC, sandbox, { filename: 'rendered-clone-page-script.js' });

  function call(name, ...args) {
    sandbox.__callArgs = args;
    const result = vm.runInContext(`${name}(...__callArgs)`, sandbox);
    delete sandbox.__callArgs;
    return result;
  }

  function runPendingTimeouts() {
    const t = sandbox.__timeouts || [];
    sandbox.__timeouts = [];
    t.forEach((fn) => fn());
  }

  return {
    sandbox, elements, resolveFetch, call, pendingFetches, runPendingTimeouts,
    el(id) { return sandbox.document.getElementById(id); },
    cloneButton() { return sandbox.document.querySelector('#clone-form button'); },
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

async function setupCase() {
  const c = createCase();
  // Drain the script's own unawaited bootstrap refresh() call at load time.
  c.resolveFetch((f) => f.url === '/status', 200, statusWith([]));
  await tick();
  await tick();
  return c;
}

const tests = [];
function test(name, fn) { tests.push({ name, fn }); }

// ─── open/close toggle ──────────────────────────────────────────────────

test('openCloneForm shows the form, clears any stale error, and focuses the URL input', async () => {
  const c = await setupCase();
  c.el('clone-form').style.display = 'none';
  c.el('clone-err').textContent = 'stale error';
  c.call('openCloneForm');
  assert.strictEqual(c.el('clone-form').style.display, 'flex');
  assert.strictEqual(c.el('clone-err').textContent, '');
});

test('closeCloneForm hides the form', async () => {
  const c = await setupCase();
  c.el('clone-form').style.display = 'flex';
  c.call('closeCloneForm');
  assert.strictEqual(c.el('clone-form').style.display, 'none');
});

// ─── startClone validation + dispatch ───────────────────────────────────

test('startClone with an empty URL shows an inline error and sends no request', async () => {
  const c = await setupCase();
  c.el('clone-url').value = '   ';
  c.call('startClone');
  await tick();
  assert.strictEqual(c.pendingFetches.length, 0);
  assert.strictEqual(c.el('clone-err').textContent, 'Enter a repository URL.');
});

test('startClone with a URL dispatches POST /projects/clone with url+name, disables the form, and shows a loading message', async () => {
  const c = await setupCase();
  c.el('clone-url').value = 'https://github.com/user/repo.git';
  c.el('clone-name').value = ' custom-name ';
  const p = c.call('startClone');
  await tick();
  assert.strictEqual(c.pendingFetches.length, 1);
  assert.strictEqual(c.pendingFetches[0].url, '/projects/clone');
  const body = JSON.parse(c.pendingFetches[0].opts.body);
  assert.strictEqual(body.url, 'https://github.com/user/repo.git');
  assert.strictEqual(body.name, 'custom-name');

  assert.strictEqual(c.el('clone-url').disabled, true);
  assert.strictEqual(c.el('clone-name').disabled, true);
  assert.strictEqual(c.cloneButton().disabled, true);
  assert.strictEqual(c.cloneButton().textContent, 'Cloning…');
  assert.ok(c.el('clone-err').textContent.includes('can take a while'),
    'expected a "can take a while" loading message, got: ' + c.el('clone-err').textContent);

  c.resolveFetch((f) => f.url === '/projects/clone', 200, { ok: true });
  await p;
  await tick();
});

// ─── result handling ─────────────────────────────────────────────────────

test('a successful clone clears the form, hides it, and re-enables the inputs', async () => {
  const c = await setupCase();
  c.el('clone-url').value = 'https://github.com/user/repo.git';
  c.el('clone-name').value = '';
  const p = c.call('startClone');
  await tick();
  c.resolveFetch((f) => f.url === '/projects/clone', 200, { ok: true });
  await p;
  await tick();

  assert.strictEqual(c.el('clone-url').value, '');
  assert.strictEqual(c.el('clone-name').value, '');
  assert.strictEqual(c.el('clone-form').style.display, 'none');
  assert.strictEqual(c.el('clone-url').disabled, false);
  assert.strictEqual(c.cloneButton().disabled, false);
  assert.strictEqual(c.cloneButton().textContent, 'Clone');
  assert.strictEqual(c.el('clone-err').textContent, '');
});

test('a failed clone (400) shows the server error message and re-enables the form', async () => {
  const c = await setupCase();
  c.el('clone-url').value = 'file:///etc/passwd';
  const p = c.call('startClone');
  await tick();
  c.resolveFetch((f) => f.url === '/projects/clone', 400, { error: 'unsupported URL' });
  await p;
  await tick();

  assert.strictEqual(c.el('clone-err').textContent, 'unsupported URL');
  assert.strictEqual(c.el('clone-url').disabled, false);
  assert.strictEqual(c.cloneButton().disabled, false);
  // Form stays open/editable so the operator can correct the URL (docs/
  // design.md "Error States" -- "Form remains expanded and editable").
  assert.notStrictEqual(c.el('clone-form').style.display, 'none');
});

// ─── TOTP code-overlay retry path ───────────────────────────────────────

test('a 428 mid-dispatch shows the code overlay labeled for the clone, and a correct retry succeeds', async () => {
  const c = await setupCase();
  c.el('clone-url').value = 'https://github.com/user/repo.git';
  const p = c.call('startClone');
  await tick();
  c.resolveFetch((f) => f.url === '/projects/clone', 428, { error: 'totp_required' });
  await p;
  await tick();

  assert.strictEqual(c.el('code-overlay-label').textContent, 'Cloning from URL');

  c.el('action-code').value = '123456';
  const p2 = c.call('submitActionCode');
  await tick();
  assert.strictEqual(c.pendingFetches.length, 1);
  const body = JSON.parse(c.pendingFetches[0].opts.body);
  assert.strictEqual(body.code, '123456');
  assert.strictEqual(body.url, 'https://github.com/user/repo.git');
  c.resolveFetch((f) => f.url === '/projects/clone', 200, { ok: true });
  await p2;
  await tick();

  assert.strictEqual(c.el('clone-form').style.display, 'none');
});

test('cancelling the code overlay mid-clone re-enables the form for a retry', async () => {
  const c = await setupCase();
  c.el('clone-url').value = 'https://github.com/user/repo.git';
  const p = c.call('startClone');
  await tick();
  c.resolveFetch((f) => f.url === '/projects/clone', 428, { error: 'totp_required' });
  await p;
  await tick();

  assert.strictEqual(c.el('clone-url').disabled, true, 'form should be disabled while awaiting the code');
  c.call('cancelActionCode');
  assert.strictEqual(c.el('clone-url').disabled, false);
  assert.strictEqual(c.cloneButton().disabled, false);
  assert.strictEqual(c.el('clone-err').textContent, '');
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

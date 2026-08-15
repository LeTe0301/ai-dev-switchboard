#!/usr/bin/env node
/**
 * Frontend tests for the upload wizard's step 5 ("Review") polish
 * (docs/spec.md / docs/design.md "Upload wizard polish"): the single/split
 * mode choice rendering as pill-styled labels (`renderStep5()`) while
 * keeping real `<input type="radio">` semantics, and the conditional
 * "Back" button (`renderStep5Actions(d)`) only appearing in the ambiguous
 * case — all run against the *real, rendered* <script> extracted verbatim
 * from app.render_page(), same technique as
 * tests/test_deploy_frontend.js / tests/test_singleton_toggle_frontend.js.
 *
 * Plain Node, no dependencies:
 *   node tests/test_upload_frontend.js
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

// ─── minimal DOM/fetch stubs (same shape as test_deploy_frontend.js) ──────

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
      return { addEventListener() {} };
    },
    addEventListener() {},
  };
}

function createCase() {
  const elements = new Map();

  function fetchStub() {
    // Never resolved — the script's own unawaited bootstrap refresh() call
    // just leaves this pending, which is fine: nothing under test here
    // depends on /status resolving.
    return new Promise(() => {});
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
  vm.runInContext(SCRIPT_SRC, sandbox, { filename: 'rendered-upload-page-script.js' });

  function call(name, ...args) {
    sandbox.__callArgs = args;
    const result = vm.runInContext(`${name}(...__callArgs)`, sandbox);
    delete sandbox.__callArgs;
    return result;
  }

  function setState(fields) {
    sandbox.__stateFields = fields;
    vm.runInContext('Object.assign(wizardState, __stateFields)', sandbox);
    delete sandbox.__stateFields;
  }

  return { sandbox, elements, call, setState };
}

// Builds a detectResult payload matching the shape app.py's phase-1
// /projects/upload endpoint returns (see docs/spec.md "Detection").
function makeDetectResult(overrides) {
  return Object.assign({
    token: 'tok123',
    root_name: 'my-project',
    root_has_git: true,
    nested_git_paths: ['vendor/repo-1', 'vendor/repo-2'],
    top_level_subdirs: [],
    loose_top_level_files: 0,
    ambiguous: true,
  }, overrides);
}

function enterStep5(c, detectResult, mode) {
  c.call('resetWizardState');
  c.setState({
    step: 5,
    detectResult,
    mode: mode || 'single',
    splitCandidates: detectResult.root_has_git ? detectResult.nested_git_paths : detectResult.top_level_subdirs,
    splitSelected: (detectResult.root_has_git ? detectResult.nested_git_paths : detectResult.top_level_subdirs)
      .map(() => false),
  });
  c.call('renderWizard');
}

function bodyHtml(c) { return c.elements.get('wizard-body').innerHTML; }
function actionsHtml(c) { return c.elements.get('wizard-actions').innerHTML; }

const tests = [];
function test(name, fn) { tests.push({ name, fn }); }

// ─── pill-styled mode choice (AC3) ─────────────────────────────────────────

test('ambiguous step 5 renders both mode-choice labels with the pill-choice class', () => {
  const c = createCase();
  enterStep5(c, makeDetectResult());
  const html = bodyHtml(c);
  const matches = html.match(/class="wizard-check-row pill-choice"/g) || [];
  assert.strictEqual(matches.length, 2,
    'expected exactly 2 pill-choice labels (single + split), got: ' + html);
});

test('split-candidate checkboxes stay plain wizard-check-row, no pill-choice class', () => {
  const c = createCase();
  enterStep5(c, makeDetectResult(), 'split');
  const html = bodyHtml(c);
  // Two pill-choice mode labels, plus two plain checkbox rows for the two
  // nested_git_paths candidates -- the checkbox rows must NOT get pill-choice.
  const checkboxRows = html.match(/<label class="wizard-check-row"><input type="checkbox"/g) || [];
  assert.strictEqual(checkboxRows.length, 2,
    'expected 2 plain (non-pill) checkbox rows for split candidates, got: ' + html);
});

test('the currently selected mode radio is checked, the other is not', () => {
  const c = createCase();
  enterStep5(c, makeDetectResult(), 'single');
  let html = bodyHtml(c);
  const singleLabel = html.slice(html.indexOf('Single project') - 200, html.indexOf('Single project'));
  const splitLabel = html.slice(html.indexOf('Split out nested repos') - 200, html.indexOf('Split out nested repos'));
  assert.ok(/checked/.test(singleLabel), 'single radio should be checked when mode is single');
  assert.ok(!/checked/.test(splitLabel), 'split radio should not be checked when mode is single');

  c.setState({ mode: 'split' });
  html = bodyHtml(c); // unchanged until re-render
  c.call('renderWizard');
  html = bodyHtml(c);
  const singleLabel2 = html.slice(html.indexOf('Single project') - 200, html.indexOf('Single project'));
  const splitLabel2 = html.slice(html.indexOf('Split out nested repos') - 200, html.indexOf('Split out nested repos'));
  assert.ok(!/checked/.test(singleLabel2), 'single radio should not be checked when mode is split');
  assert.ok(/checked/.test(splitLabel2), 'split radio should be checked when mode is split');
});

test('clicking setWizardMode updates wizardState.mode exactly as before (no behavior regression)', () => {
  const c = createCase();
  enterStep5(c, makeDetectResult(), 'single');
  c.call('setWizardMode', 'split');
  const mode = vm.runInContext('wizardState.mode', c.sandbox);
  assert.strictEqual(mode, 'split');
});

// ─── keyboard semantics preserved (AC4) ────────────────────────────────────

test('mode choice labels still wrap a real, focusable <input type="radio"> (not a bare span)', () => {
  const c = createCase();
  enterStep5(c, makeDetectResult());
  const html = bodyHtml(c);
  const radioCount = (html.match(/<input type="radio" name="wizard-mode"/g) || []).length;
  assert.strictEqual(radioCount, 2, 'expected 2 native radio inputs, got: ' + html);
  assert.ok(!/<span class="pill"/.test(html),
    'must not replace the radios with an unfocusable bare <span class="pill">');
});

// ─── conditional Back button (AC5 / AC6) ───────────────────────────────────

test('unambiguous step 5 renders Confirm only, no Back button', () => {
  const c = createCase();
  enterStep5(c, makeDetectResult({ ambiguous: false, root_has_git: false, top_level_subdirs: [] }));
  const html = actionsHtml(c);
  assert.ok(html.includes('Confirm'), 'expected a Confirm button, got: ' + html);
  assert.ok(!html.includes('Back'), 'must not render a Back button in the unambiguous case, got: ' + html);
  assert.ok(!html.includes('class="secondary"'), 'must not render the secondary (Back) button at all, got: ' + html);
});

test('ambiguous step 5 renders both Back and Confirm, Back resets the wizard as before', () => {
  const c = createCase();
  enterStep5(c, makeDetectResult());
  const html = actionsHtml(c);
  assert.ok(html.includes('Back'), 'expected a Back button in the ambiguous case, got: ' + html);
  assert.ok(html.includes('Confirm'), 'expected a Confirm button, got: ' + html);
  assert.ok(html.includes('resetWizardState(); renderWizard();'),
    'Back must still call resetWizardState(); renderWizard(), got: ' + html);
});

test('renderStep5Actions(d) takes the detectResult explicitly (signature per docs/design.md)', () => {
  const c = createCase();
  const ambiguousHtml = c.call('renderStep5Actions', makeDetectResult({ ambiguous: true }));
  const unambiguousHtml = c.call('renderStep5Actions', makeDetectResult({ ambiguous: false }));
  assert.ok(ambiguousHtml.includes('Back'));
  assert.ok(!unambiguousHtml.includes('Back'));
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

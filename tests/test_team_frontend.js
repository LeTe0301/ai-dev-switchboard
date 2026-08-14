#!/usr/bin/env node
/**
 * Frontend tests for the minimal per-project team control (backlog item 6d,
 * part 2a — docs/spec.md, docs/design.md): teamRow()'s four states (idle/
 * running/blocked/finished/error), the task-text client-side validation +
 * dispatch flow (doTeamStart), the confirm()-gated stop flow (doTeamStop),
 * and both actions' inline result messages (handleActionResult's
 * kind === 'team-start'/'team-stop' branch) — all run against the *real,
 * rendered* <script> extracted verbatim from app.render_page(), same
 * technique as tests/test_deploy_frontend.js (this file's own direct
 * template — deployRow()/doDeploy()'s closest precedent, per docs/spec.md
 * "Proposed approach" §9).
 *
 * Plain Node, no dependencies:
 *   node tests/test_team_frontend.js
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

// ─── minimal DOM/fetch/confirm/timer stubs (identical shape to
// tests/test_deploy_frontend.js — see that file's own header for why
// extraction, not a hand-copied snapshot) ──────────────────────────────────

function makeElementStub(id) {
  return {
    id,
    className: '',
    classList: {
      add() {}, remove() {}, contains() { return false; },
    },
    style: {},
    value: '',
    disabled: false,
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
  vm.runInContext(SCRIPT_SRC, sandbox, { filename: 'rendered-team-page-script.js' });

  function call(name, ...args) {
    sandbox.__callArgs = args;
    const result = vm.runInContext(`${name}(...__callArgs)`, sandbox);
    delete sandbox.__callArgs;
    return result;
  }

  function rowsHtml() {
    return elements.has('rows') ? elements.get('rows').innerHTML : '';
  }

  // teamTaskText is a top-level `let` in the rendered script -- vm's own
  // per-script lexical environment means it is NOT reachable as a property
  // of the sandbox object from outside (unlike a `var`/function
  // declaration), only from code actually RUN inside this same context via
  // vm.runInContext. Mirrors the oninput handler's own real effect
  // (`teamTaskText[name] = this.value`) without needing to parse/execute
  // the rendered onclick/oninput attribute strings themselves.
  function setTeamTaskText(name, text) {
    vm.runInContext(
      `teamTaskText[${JSON.stringify(name)}] = ${JSON.stringify(text)};`, sandbox);
  }

  // Scopes assertions to exactly one project's own <div class="row">...
  // </div> slice, anchored on its <div class="label">NAME</div> — same
  // slicing technique tests/test_deploy_frontend.js's own instanceRowHtml()
  // uses.
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
    sandbox, elements, resolveFetch, call, rowsHtml, instanceRowHtml, setTeamTaskText,
    pendingFetches,
    confirmCalls,
    setConfirmReturn(v) { confirmReturn = v; },
    // Go through the sandbox's own document.getElementById (not a raw Map
    // lookup) -- these per-project IDs are only lazily created the first
    // time something (rendering aside, which never touches the DOM, only
    // an action handler) actually asks for them, exactly like the real
    // browser's own element-doesn't-exist-yet-until-referenced behavior
    // this stub is modeling.
    msgEl(name) { return sandbox.document.getElementById('team-msg-' + name); },
    taskEl(name) { return sandbox.document.getElementById('task-' + name); },
    startBtnEl(name) { return sandbox.document.getElementById('start-btn-' + name); },
  };
}

async function tick() {
  await new Promise((resolve) => setImmediate(resolve));
}

function statusWith(instances, roster) {
  return {
    instances,
    engines: {},
    roster: roster || [],
    host_enabled: false, taiga_enabled: false, gitea_enabled: false,
  };
}

async function setupCase(instances, roster) {
  const c = createCase();
  // Drain the script's own unawaited bootstrap refresh() call at load time.
  c.resolveFetch((f) => f.url === '/status', 200, statusWith([]));
  await tick();
  await tick();
  // Now do the real refresh() this test actually wants to assert against.
  const p = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, statusWith(instances, roster));
  await p;
  return c;
}

// toggleTeamPicker()/fetchTeamGrounding()/refresh() form a fire-and-forget
// async chain (toggleTeamPicker() itself is not async, so there is no
// single promise a caller can await through to the end) -- polls with
// tick() until a matching fetch actually shows up in pendingFetches,
// rather than guessing a fixed number of microtask ticks deep enough to
// reach it.
async function waitForFetch(c, matchFn, maxTicks) {
  const limit = maxTicks || 20;
  for (let i = 0; i < limit; i++) {
    if (c.pendingFetches.some(matchFn)) return;
    await tick();
  }
  throw new Error('waitForFetch: timed out. Pending URLs: [' + c.pendingFetches.map((f) => f.url).join(', ') + ']');
}

// Opens the composition picker for `name` -- drains toggleTeamPicker()'s own
// grounding fetch + refresh() cycle (docs/design.md: "Roster and grounding
// are fetched on picker open"), so callers land with the picker already
// rendered and ready to assert against, exactly like setupCase() itself
// does for the initial /status poll.
async function openPicker(c, name, instances, roster, grounding) {
  c.call('toggleTeamPicker', name);
  await waitForFetch(c, (f) => f.url === '/projects/' + name + '/team/grounding');
  c.resolveFetch((f) => f.url === '/projects/' + name + '/team/grounding', 200,
    grounding || { files: [], skipped: [] });
  await waitForFetch(c, (f) => f.url === '/status');
  c.resolveFetch((f) => f.url === '/status', 200, statusWith(instances, roster));
  await tick();
  await tick();
  await tick();
}

function rosterEntry(overrides) {
  return Object.assign({ name: 'e', kind: 'engine', label: 'E', tier: 2, delegate_capable: true,
                        schema_flag_error: null }, overrides || {});
}

const tests = [];
function test(name, fn) { tests.push({ name, fn }); }

function inst(name, team, overrides) {
  return Object.assign({
    name, on: false, url: null, engine: null, desc: '', code_on: false, code_url: null, team,
  }, overrides || {});
}

// ─── teamRow() states ───────────────────────────────────────────────────

test('idle (team null) renders a task textarea and a disabled Start team button', async () => {
  const c = await setupCase([inst('proj', null)]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('class="team-textarea"'), 'expected a task textarea, got: ' + html);
  assert.ok(html.includes("doTeamStart('proj')"), 'expected onclick="doTeamStart(\'proj\')", got: ' + html);
  assert.ok(html.includes('>Start team<'), 'expected exact "Start team" label, got: ' + html);
  assert.ok(html.includes('id="task-proj"'));
  assert.ok(html.includes('id="team-msg-proj"'));
  assert.ok(html.includes('disabled'), 'Start team button must start disabled with an empty task');
});

test('idle (team.status === "idle") renders the same idle control', async () => {
  const c = await setupCase([inst('proj', { status: 'idle', run_id: null })]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('class="team-textarea"'));
});

test('running renders coarse status label + Stop team button, no textarea', async () => {
  const c = await setupCase([inst('proj', { status: 'running', run_id: 'run-abc123' })]);
  const html = c.instanceRowHtml('proj');
  assert.ok(!html.includes('team-textarea'), 'textarea must be completely hidden once running');
  assert.ok(html.includes('status-running'), 'expected the running status class, got: ' + html);
  assert.ok(html.includes('run-abc123'), 'expected the run id to be shown, got: ' + html);
  assert.ok(html.includes("doTeamStop('proj')"));
  assert.ok(html.includes('>Stop team<'), 'expected exact "Stop team" label, got: ' + html);
  assert.ok(!html.includes('Lead is waiting'), 'the blocked subtitle must not show for running');
});

test('blocked renders the "Lead is waiting" subtitle', async () => {
  const c = await setupCase([inst('proj', { status: 'blocked', run_id: 'run-abc123' })]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('status-blocked'));
  assert.ok(html.includes('Lead is waiting for input'), 'expected the blocked subtitle, got: ' + html);
  assert.ok(html.includes('check tmux attach'));
});

test('finished and error render their own status classes, no subtitle', async () => {
  const cf = await setupCase([inst('proj', { status: 'finished', run_id: 'run-1' })]);
  const hf = cf.instanceRowHtml('proj');
  assert.ok(hf.includes('status-finished'));
  assert.ok(!hf.includes('Lead is waiting'));

  const ce = await setupCase([inst('proj', { status: 'error', run_id: 'run-2' })]);
  const he = ce.instanceRowHtml('proj');
  assert.ok(he.includes('status-error'));
});

// Regression guard mirroring test_deploy_frontend.js's own HTML-injection
// deviation check: a project name is already NAME_RE-restricted, but the
// task textarea's own typed CONTENT is fully operator-controlled and must
// never let a stray quote/HTML break the row.
test('a quote-and-tag-containing in-progress task text round-trips safely via teamTaskText', async () => {
  const c = await setupCase([inst('proj', null)]);
  // Simulate the textarea's own oninput handler having already run once.
  c.setTeamTaskText('proj', `"><script>alert(1)</script> some task`);
  const p = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, statusWith([inst('proj', null)]));
  await p;
  const html = c.instanceRowHtml('proj');
  assert.ok(!html.includes('<script>alert(1)</script>'), 'must never render raw, unescaped task text');
  assert.ok(html.includes('class="team-textarea"'));
});

// ─── doTeamStart(): client-side validation ─────────────────────────────

test('clicking Start team with an empty task sends no request and shows a client-side message', async () => {
  const c = await setupCase([inst('proj', null)]);
  c.taskEl('proj').value = '   ';
  c.call('doTeamStart', 'proj');
  await tick();
  assert.strictEqual(c.pendingFetches.length, 0, 'no fetch should have been dispatched');
  assert.strictEqual(c.msgEl('proj').textContent, 'Enter a task description.');
  assert.ok(c.msgEl('proj').className.includes('error'));
});

test('clicking Start team with a real task dispatches POST /projects/<name>/team/start with {task}', async () => {
  const c = await setupCase([inst('proj', null)]);
  c.taskEl('proj').value = 'Refactor the widget module';
  const p = c.call('doTeamStart', 'proj');
  await tick();
  assert.strictEqual(c.pendingFetches.length, 1);
  const f = c.pendingFetches[0];
  assert.strictEqual(f.url, '/projects/proj/team/start');
  assert.strictEqual(f.opts.method, 'POST');
  const body = JSON.parse(f.opts.body);
  assert.strictEqual(body.task, 'Refactor the widget module');
  c.resolveFetch((f2) => f2.url === '/projects/proj/team/start', 200,
    { ok: true, run_id: 'run-x', session: 'team-proj', lead: {}, members: [] });
  await p;
});

// ─── doTeamStart(): result rendering ────────────────────────────────────

test('a successful start clears any previous error message', async () => {
  const c = await setupCase([inst('proj', null)]);
  c.taskEl('proj').value = 'do it';
  c.msgEl('proj').textContent = 'stale previous error';
  c.msgEl('proj').className = 'team-msg error';
  const p = c.call('doTeamStart', 'proj');
  await tick();
  c.resolveFetch((f) => f.url === '/projects/proj/team/start', 200,
    { ok: true, run_id: 'run-x', session: 'team-proj', lead: {}, members: [] });
  await p;
  await tick();
  assert.strictEqual(c.msgEl('proj').textContent, '');
  assert.strictEqual(c.msgEl('proj').className, 'team-msg');
});

test('a tier-3-only refusal (400) shows the exact server error message inline', async () => {
  const c = await setupCase([inst('proj', null)]);
  c.taskEl('proj').value = 'do it';
  const p = c.call('doTeamStart', 'proj');
  await tick();
  const errMsg = "only a tier-3 (prose-parse, least reliable) lead is available -- configure " +
    "TEAM_LLM_BASE_URL/TEAM_LLM_MODEL, or add a tier-2 (schema-capable) engine to engines.d. " +
    "The CLI's --lead can still select a tier-3 lead explicitly.";
  c.resolveFetch((f) => f.url === '/projects/proj/team/start', 400, { error: errMsg });
  await p;
  await tick();
  const msg = c.msgEl('proj');
  assert.strictEqual(msg.textContent, '✕ Error: ' + errMsg);
  assert.ok(msg.className.includes('error'));
});

test('an empty-task 400 from the server (not just the client check) still shows an error, not the generic new-project field', async () => {
  const c = await setupCase([inst('proj', null)]);
  c.taskEl('proj').value = 'do it';  // passes the CLIENT check
  const p = c.call('doTeamStart', 'proj');
  await tick();
  c.resolveFetch((f) => f.url === '/projects/proj/team/start', 400,
    { error: 'a task description is required' });
  await p;
  await tick();
  assert.strictEqual(c.msgEl('proj').textContent, '✕ Error: a task description is required');
  // Must NOT have been routed to the unrelated new-project error field.
  assert.strictEqual(c.sandbox.document.getElementById('new-project-err').textContent, '');
});

test('after a failed start, the textarea and button remain usable (not disabled)', async () => {
  const c = await setupCase([inst('proj', null)]);
  c.taskEl('proj').value = 'do it';
  const p = c.call('doTeamStart', 'proj');
  await tick();
  c.resolveFetch((f) => f.url === '/projects/proj/team/start', 400, { error: 'nope' });
  await p;
  await tick();
  // handleActionResult never touches the textarea/button elements directly
  // on this path -- confirmed by checking neither was ever disabled.
  assert.strictEqual(c.taskEl('proj').disabled, false);
  assert.strictEqual(c.startBtnEl('proj').disabled, false);
});

// ─── doTeamStop(): confirm() gate + dispatch + result ──────────────────

test('clicking Stop team then cancelling the confirm() dialog sends no request', async () => {
  const c = await setupCase([inst('proj', { status: 'running', run_id: 'run-1' })]);
  c.setConfirmReturn(false);
  c.call('doTeamStop', 'proj');
  await tick();
  assert.strictEqual(c.pendingFetches.length, 0);
  assert.strictEqual(c.confirmCalls.length, 1);
  assert.ok(c.confirmCalls[0].includes('Stop team?'));
  assert.ok(c.confirmCalls[0].includes('Any uncommitted work will be lost.'));
});

test('confirmed stop dispatches POST /projects/<name>/team/stop and shows a success message', async () => {
  const c = await setupCase([inst('proj', { status: 'running', run_id: 'run-1' })]);
  c.setConfirmReturn(true);
  const p = c.call('doTeamStop', 'proj');
  await tick();
  assert.strictEqual(c.pendingFetches.length, 1);
  assert.strictEqual(c.pendingFetches[0].url, '/projects/proj/team/stop');
  assert.strictEqual(c.pendingFetches[0].opts.method, 'POST');
  c.resolveFetch((f) => f.url === '/projects/proj/team/stop', 200,
    { ok: true, session_removed: true, worktrees: {} });
  await p;
  await tick();
  const msg = c.msgEl('proj');
  assert.strictEqual(msg.textContent, '✓ Team stopped successfully');
  assert.ok(msg.className.includes('success'));
});

test('a failed stop shows the server error message inline', async () => {
  const c = await setupCase([inst('proj', { status: 'running', run_id: 'run-1' })]);
  const p = c.call('doTeamStop', 'proj');
  await tick();
  c.resolveFetch((f) => f.url === '/projects/proj/team/stop', 404, { error: 'unknown project' });
  await p;
  await tick();
  const msg = c.msgEl('proj');
  assert.strictEqual(msg.textContent, '✕ Error: unknown project');
  assert.ok(msg.className.includes('error'));
});

// ─── TOTP code-overlay retry path (team-start) ─────────────────────────

test('a 428 mid-start shows the code overlay labeled for this team, and a correct retry succeeds', async () => {
  const c = await setupCase([inst('proj', null)]);
  c.taskEl('proj').value = 'do it';
  const p = c.call('doTeamStart', 'proj');
  await tick();
  c.resolveFetch((f) => f.url === '/projects/proj/team/start', 428, { error: 'totp_required' });
  await p;
  await tick();

  const label = c.elements.get('code-overlay-label');
  assert.strictEqual(label.textContent, 'Starting team: proj');

  c.elements.get('action-code').value = '123456';
  const p2 = c.call('submitActionCode');
  await tick();
  assert.strictEqual(c.pendingFetches.length, 1);
  const body = JSON.parse(c.pendingFetches[0].opts.body);
  assert.strictEqual(body.code, '123456');
  assert.strictEqual(body.task, 'do it', 'the retry must resend the (still-current) task text');
  c.resolveFetch((f) => f.url === '/projects/proj/team/start', 200,
    { ok: true, run_id: 'run-x', session: 'team-proj', lead: {}, members: [] });
  await p2;
  await tick();
  assert.strictEqual(c.msgEl('proj').textContent, '');
});

test('a 428 mid-stop shows the code overlay labeled for this team stop', async () => {
  const c = await setupCase([inst('proj', { status: 'running', run_id: 'run-1' })]);
  const p = c.call('doTeamStop', 'proj');
  await tick();
  c.resolveFetch((f) => f.url === '/projects/proj/team/stop', 428, { error: 'totp_required' });
  await p;
  await tick();
  const label = c.elements.get('code-overlay-label');
  assert.strictEqual(label.textContent, 'Stopping team: proj');
});

// ─── Roster & composition UI (backlog item 6e, docs/design.md) ────────────

test('a composition present renders a "Configure team..." link, no picker panel yet', async () => {
  const roster = [rosterEntry({ name: 'lead2', tier: 2 }), rosterEntry({ name: 'helper', tier: 2 })];
  const comp = { lead: { kind: 'engine', name: 'lead2' }, members: ['helper'] };
  const c = await setupCase([inst('proj', { status: 'idle', run_id: null, composition: comp })], roster);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('Configure team...'), 'expected the configure link, got: ' + html);
  assert.ok(!html.includes('team-lead-proj'), 'the picker itself must not render before it is opened');
});

test('composition === null (no usable roster member) shows the refusal text, omits the configure link, disables Start', async () => {
  const c = await setupCase([inst('proj', { status: 'idle', run_id: null, composition: null })]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('No roster members available'), 'expected the refusal text, got: ' + html);
  assert.ok(!html.includes('Configure team...'), 'no configure link when there is no usable composition at all');
  const startBtnHtml = html.slice(html.indexOf('id="start-btn-proj"'), html.indexOf('</button>'));
  assert.ok(startBtnHtml.includes('disabled'), 'expected the rendered Start button to be disabled, got: ' + startBtnHtml);
});

test('opening the picker fetches grounding and renders every roster member as a lead option', async () => {
  const roster = [rosterEntry({ name: 'lead2', tier: 2 }), rosterEntry({ name: 'helper', tier: 2 })];
  const comp = { lead: { kind: 'engine', name: 'lead2' }, members: ['helper'] };
  const instances = [inst('proj', { status: 'idle', run_id: null, composition: comp })];
  const c = await setupCase(instances, roster);
  await openPicker(c, 'proj', instances, roster);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('id="team-lead-proj"'), 'expected the lead select, got: ' + html);
  assert.ok(html.includes('lead2'), 'expected lead2 listed as a lead option');
  assert.ok(html.includes('helper'), 'expected helper listed as a lead option');
  assert.ok(html.includes('Hide configuration'), 'the configure link toggles to Hide configuration once open');
});

test('the saved composition pre-selects the lead and excludes it from the teammate checkboxes', async () => {
  const roster = [rosterEntry({ name: 'lead2', tier: 2 }), rosterEntry({ name: 'helper', tier: 2 }),
                  rosterEntry({ name: 'other', tier: 2 })];
  const comp = { lead: { kind: 'engine', name: 'lead2' }, members: ['helper'] };
  const instances = [inst('proj', { status: 'idle', run_id: null, composition: comp })];
  const c = await setupCase(instances, roster);
  await openPicker(c, 'proj', instances, roster);
  const html = c.instanceRowHtml('proj');
  const leadSelect = html.slice(html.indexOf('id="team-lead-proj"'));
  assert.ok(/value='[^']*"lead2"[^']*'\s+selected/.test(leadSelect), 'expected lead2 pre-selected, got: ' + leadSelect);
  assert.ok(!html.includes('team-mate-proj-lead2'), 'the current lead must be excluded from the teammate checkboxes');
  assert.ok(html.includes('team-mate-proj-helper'));
  assert.ok(html.includes('team-mate-proj-other'));
});

test('a tier-3 lead shows the plain-language reliability caveat, never blocked', async () => {
  const roster = [rosterEntry({ name: 'prose3', tier: 3 }), rosterEntry({ name: 'helper', tier: 2 })];
  const comp = { lead: { kind: 'engine', name: 'prose3' }, members: ['helper'] };
  const instances = [inst('proj', { status: 'idle', run_id: null, composition: comp })];
  const c = await setupCase(instances, roster);
  await openPicker(c, 'proj', instances, roster);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('team-tier-3-caveat'), 'expected the tier-3 caveat, got: ' + html);
  assert.ok(html.includes('reliability is lower'));
  // Tier 3 is a real option, never blocked -- Start must not be disabled
  // purely because of the lead's tier (empty task is a separate reason).
  c.taskEl('proj').value = 'do it';
  assert.strictEqual(c.call('teamCompositionError', 'proj'), null);
});

// Regression test for the reviewer-found defect (docs/implementation.md's
// "6e fix" cycle): a roster that is real but tier-3-only, with NO saved
// composition yet, is the one case `default_team_composition()` refuses
// for a reason OTHER than "nothing usable at all" -- 6d part 2 settled
// that the automatic default never auto-picks a tier-3 lead. Before the
// fix, GET /status collapsed this into composition=null, indistinguishable
// from a genuinely empty roster, which permanently disabled the Start
// button with no way to ever open the picker. The fix makes /status send
// composition={lead: null, members: []} (nothing pre-selected) whenever
// roster() is non-empty, even when the automatic default declined to pick
// one -- this test drives that exact shape through teamRow()/
// renderTeamPicker(), the same technique the tier-3-caveat test above
// uses, to prove the picker actually opens and tier-3 is selectable, not
// disabled.
test('a tier-3-only roster with no saved composition still shows a Configure link (not the permanent refusal), and the picker opens with tier-3 selectable', async () => {
  const roster = [rosterEntry({ name: 'prose3', tier: 3 })];
  // What GET /status now sends for this exact case (app/app.py's fix):
  // a real, non-null composition object with nothing pre-selected, rather
  // than composition=null.
  const comp = { lead: null, members: [] };
  const instances = [inst('proj', { status: 'idle', run_id: null, composition: comp })];
  const c = await setupCase(instances, roster);
  const closedHtml = c.instanceRowHtml('proj');
  assert.ok(closedHtml.includes('Configure team...'),
    'expected the Configure link to render (a real roster member exists), got: ' + closedHtml);
  assert.ok(!closedHtml.includes('No roster members available'),
    'must not render the permanent-refusal state when a real tier-3 roster member exists, got: ' + closedHtml);

  await openPicker(c, 'proj', instances, roster);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('id="team-lead-proj"'), 'expected the lead select to render, got: ' + html);
  const leadSelect = html.slice(html.indexOf('id="team-lead-proj"'), html.indexOf('</select>'));
  assert.ok(leadSelect.includes('prose3'), 'expected the tier-3 engine listed as a selectable lead option');
  assert.ok(!leadSelect.includes('disabled'), 'the tier-3 lead option must not be disabled -- never blocked');

  // Actually selecting it succeeds -- never blocked.
  c.taskEl('proj').value = 'do it';
  const sel = c.sandbox.document.getElementById('team-lead-proj');
  sel.value = JSON.stringify({ kind: 'engine', name: 'prose3' });
  c.call('onTeamLeadChange', 'proj');
  await tick();
  assert.strictEqual(c.call('teamCompositionError', 'proj'), 'At least one teammate is required',
    'lead selection itself must succeed (only the separate empty-teammates rule blocks Start here)');
});

test('the Ollama roster entry (not delegate_capable) is never offered as a teammate checkbox', async () => {
  const roster = [rosterEntry({ name: 'qwen3:8b', kind: 'ollama', tier: 1, delegate_capable: false,
                                schema_flag_error: undefined }),
                  rosterEntry({ name: 'helper', tier: 2 })];
  const comp = { lead: { kind: 'ollama', name: 'qwen3:8b' }, members: ['helper'] };
  const instances = [inst('proj', { status: 'idle', run_id: null, composition: comp })];
  const c = await setupCase(instances, roster);
  await openPicker(c, 'proj', instances, roster);
  const html = c.instanceRowHtml('proj');
  assert.ok(!html.includes('team-mate-proj-qwen3:8b'), 'Ollama must never appear as a teammate checkbox');
});

test('grounding files render as a fixed four-slot checklist, an absent file marked not found', async () => {
  const roster = [rosterEntry({ name: 'lead2', tier: 2 }), rosterEntry({ name: 'helper', tier: 2 })];
  const comp = { lead: { kind: 'engine', name: 'lead2' }, members: ['helper'] };
  const instances = [inst('proj', { status: 'idle', run_id: null, composition: comp })];
  const c = await setupCase(instances, roster);
  await openPicker(c, 'proj', instances, roster, {
    files: [{ label: 'docs/BACKLOG.md', relpath: 'docs/BACKLOG.md', byte_count: 500 }],
    skipped: [],
  });
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('✓ docs/BACKLOG.md'), 'expected the found file, got: ' + html);
  assert.ok(html.includes('✗ docs/ARCHITECTURE.md') && html.includes('not found'),
    'expected the ABSENT file to be explicitly shown, not silently omitted, got: ' + html);
  assert.ok(html.includes('✗ README.md'));
});

test('deselecting the teammate down to zero shows the client-side validation error and disables Start', async () => {
  const roster = [rosterEntry({ name: 'lead2', tier: 2 }), rosterEntry({ name: 'helper', tier: 2 })];
  const comp = { lead: { kind: 'engine', name: 'lead2' }, members: ['helper'] };
  const instances = [inst('proj', { status: 'idle', run_id: null, composition: comp })];
  const c = await setupCase(instances, roster);
  await openPicker(c, 'proj', instances, roster);
  c.taskEl('proj').value = 'do it';

  c.call('onTeamMateToggle', 'proj', 'helper', false);
  await waitForFetch(c, (f) => f.url === '/status');
  c.resolveFetch((f) => f.url === '/status', 200, statusWith(instances, roster));
  await tick();
  await tick();

  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('At least one teammate is required'), 'expected the validation error, got: ' + html);
  const startBtnHtml = html.slice(html.indexOf('id="start-btn-proj"'), html.indexOf('</button>'));
  assert.ok(startBtnHtml.includes('disabled'), 'expected the rendered Start button to be disabled, got: ' + startBtnHtml);
});

test('clicking Start with a valid open composition dispatches POST with {task, lead, members}', async () => {
  const roster = [rosterEntry({ name: 'lead2', tier: 2 }), rosterEntry({ name: 'helper', tier: 2 })];
  const comp = { lead: { kind: 'engine', name: 'lead2' }, members: ['helper'] };
  const instances = [inst('proj', { status: 'idle', run_id: null, composition: comp })];
  const c = await setupCase(instances, roster);
  await openPicker(c, 'proj', instances, roster);
  c.taskEl('proj').value = 'do it';

  const p = c.call('doTeamStart', 'proj');
  await tick();
  assert.strictEqual(c.pendingFetches.length, 1);
  const body = JSON.parse(c.pendingFetches[0].opts.body);
  assert.strictEqual(body.task, 'do it');
  assert.deepStrictEqual(body.lead, { kind: 'engine', name: 'lead2' });
  assert.deepStrictEqual(body.members, [{ kind: 'engine', name: 'helper' }]);
  c.resolveFetch((f) => f.url === '/projects/proj/team/start', 200,
    { ok: true, run_id: 'run-x', session: 'team-proj', lead: comp.lead, members: comp.members });
  await p;
});

test('clicking Start with the picker closed omits lead/members entirely (unchanged 6d default-composition body)', async () => {
  const roster = [rosterEntry({ name: 'lead2', tier: 2 }), rosterEntry({ name: 'helper', tier: 2 })];
  const comp = { lead: { kind: 'engine', name: 'lead2' }, members: ['helper'] };
  const instances = [inst('proj', { status: 'idle', run_id: null, composition: comp })];
  const c = await setupCase(instances, roster);
  c.taskEl('proj').value = 'do it';

  const p = c.call('doTeamStart', 'proj');
  await tick();
  const body = JSON.parse(c.pendingFetches[0].opts.body);
  assert.strictEqual(body.task, 'do it');
  assert.strictEqual(body.lead, undefined);
  assert.strictEqual(body.members, undefined);
  c.resolveFetch((f) => f.url === '/projects/proj/team/start', 200,
    { ok: true, run_id: 'run-x', session: 'team-proj', lead: comp.lead, members: comp.members });
  await p;
});

test('clicking Start while the open picker composition is invalid sends no request and shows the reason', async () => {
  const roster = [rosterEntry({ name: 'lead2', tier: 2 }), rosterEntry({ name: 'helper', tier: 2 })];
  // No saved composition and no default -- picker opened with nothing
  // pre-selected (an edge case the operator could still reach by clearing
  // the lead select back to "Choose a lead...").
  const instances = [inst('proj', { status: 'idle', run_id: null, composition: null })];
  // composition is null so no Configure link would normally render -- this
  // test instead exercises teamCompositionError()/doTeamStart() directly
  // against a manually-opened, manually-invalidated picker state, the
  // same "call the exported function directly, no renderer needed"
  // technique this file's own header describes.
  const c = await setupCase(instances, roster);
  c.call('toggleTeamPicker', 'proj');
  await tick();
  c.resolveFetch((f) => f.url === '/projects/proj/team/grounding', 200, { files: [], skipped: [] });
  await tick();
  c.resolveFetch((f) => f.url === '/status', 200, statusWith(instances, roster));
  await tick();
  c.taskEl('proj').value = 'do it';

  c.call('doTeamStart', 'proj');
  await tick();
  assert.strictEqual(c.pendingFetches.length, 0, 'no fetch should have been dispatched');
  assert.strictEqual(c.msgEl('proj').textContent, 'Lead is required');
  assert.ok(c.msgEl('proj').className.includes('error'));
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

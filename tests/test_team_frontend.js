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

// Superseded by 6f part 2 (docs/spec.md "Edge cases" -- blocked_ask_user vs
// escalated_max_rounds must render distinctly, not the old shared "Lead is
// waiting for input · check tmux attach" copy). See test_team_frontend.js's
// own "status strip" section below for the two distinct replacements.
test('blocked + waiting_on_you renders "Waiting on you" and the escalation panel, not the old terminal copy', async () => {
  const c = await setupCase([inst('proj', { status: 'blocked', run_id: 'run-abc123', waiting_on_you: true })]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('status-blocked'));
  assert.ok(html.includes('Waiting on you'), 'expected the "Waiting on you" strip copy, got: ' + html);
  assert.ok(!html.includes('Lead is waiting for input'), 'the old shared blocked copy must be gone');
  assert.ok(!html.includes('Max rounds reached'), 'must not show the terminal-escalation copy here');
});

test('blocked without waiting_on_you (escalated_max_rounds) renders terminal copy, no escalation panel', async () => {
  const c = await setupCase([inst('proj', { status: 'blocked', run_id: 'run-abc123', waiting_on_you: false })]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('status-blocked'));
  assert.ok(html.includes('Max rounds reached'), 'expected the terminal-escalation strip copy, got: ' + html);
  assert.ok(!html.includes('Waiting on you'));
  assert.ok(!html.includes('team-escalation-form'), 'no answer form for a terminal escalated_max_rounds run');
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
  // A running team's feed defaults open (docs/design.md 6f part 2), which
  // fires its own /team/events poll from setupCase()'s own bootstrap
  // refresh() -- unrelated to Stop, filtered out here rather than asserting
  // on the raw pending-fetch count.
  assert.ok(!c.pendingFetches.some((f) => f.url.includes('/team/stop')), 'no stop request should have been sent');
  assert.strictEqual(c.confirmCalls.length, 1);
  assert.ok(c.confirmCalls[0].includes('Stop team?'));
  assert.ok(c.confirmCalls[0].includes('Any uncommitted work will be lost.'));
});

test('confirmed stop dispatches POST /projects/<name>/team/stop and shows a success message', async () => {
  const c = await setupCase([inst('proj', { status: 'running', run_id: 'run-1' })]);
  c.setConfirmReturn(true);
  const p = c.call('doTeamStop', 'proj');
  await tick();
  // See the cancel test above for why an unrelated /team/events fetch may
  // also be pending here (the feed's own default-open poll).
  const stopFetch = c.pendingFetches.find((f) => f.url === '/projects/proj/team/stop');
  assert.ok(stopFetch, 'expected a pending POST /projects/proj/team/stop, got: ' +
    c.pendingFetches.map((f) => f.url).join(', '));
  assert.strictEqual(stopFetch.opts.method, 'POST');
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

// ─── Live event feed + escalation inbox (backlog item 6f part 2,
// docs/spec.md / docs/design.md) ──────────────────────────────────────────

// Delivers a /team/events response to whichever project's feed poll is
// currently in flight (a running team's feed defaults open, so setupCase()
// itself already triggered pollTeamFeed()'s own first fetch) and drains the
// microtask queue enough for pollTeamFeed()'s while-loop/finally to settle.
async function openFeedAndDeliverEvents(c, name, events, truncated) {
  await waitForFetch(c, (f) => f.url.indexOf('/projects/' + name + '/team/events') === 0);
  c.resolveFetch((f) => f.url.indexOf('/projects/' + name + '/team/events') === 0, 200,
    { run_id: 'run-events', events, cursors: {}, truncated: truncated || {} });
  await tick();
  await tick();
  await tick();
}

// pollTeamFeed() deliberately does not call refresh() itself once its own
// drain loop completes (see its own doc comment in app/app.py -- avoids a
// self-sustaining fetch loop faster than the intended 4s cadence), so a
// test that wants to see freshly-polled events actually rendered has to
// drive a further refresh() cycle itself, exactly like the real 4s
// setInterval would on the next tick. Any events fetch this new refresh()
// cycle re-triggers is left unresolved/ignored, same convention the
// Stop-team tests above already use for the same reason.
async function rerenderRow(c, instances, roster) {
  const p = c.call('refresh');
  await waitForFetch(c, (f) => f.url === '/status');
  c.resolveFetch((f) => f.url === '/status', 200, statusWith(instances, roster));
  await p;
  await tick();
}
// Several action helpers (setTeamFeedFilter()/onEscalationOptionChange()/
// toggleTeamFeed()) trigger their OWN internal refresh() call as a
// fire-and-forget side effect, exactly like onTeamMateToggle() already does
// for the composition picker. Call this right after one of those -- never
// rerenderRow() -- to drain THAT already-in-flight /status fetch. Calling
// rerenderRow() (which dispatches a SECOND, independent refresh()) instead
// would leave two /status fetches pending at once; resolveFetch()'s own
// find-first-match semantics would resolve the wrong (stale) one and the
// second call's own awaited promise would hang forever.
async function drainTriggeredRefresh(c, instances, roster) {
  await waitForFetch(c, (f) => f.url === '/status');
  c.resolveFetch((f) => f.url === '/status', 200, statusWith(instances, roster));
  await tick();
  await tick();
}

// fetchTeamInbox() (app/app.py) caches the response then calls refresh()
// itself -- exactly like fetchTeamGrounding() already does for the
// composition picker (see openPicker()'s own two-step drain above). That
// follow-up refresh() issues its own /status fetch which must also be
// resolved, or the row's rendered HTML never actually picks up the
// now-cached inbox (the cache would be populated, but the DOM would stay
// on its pre-fetch snapshot).
async function deliverTeamInbox(c, name, runId, instances, payload) {
  await waitForFetch(c, (f) => f.url === '/projects/' + name + '/team/inbox?run_id=' + runId);
  c.resolveFetch((f) => f.url === '/projects/' + name + '/team/inbox?run_id=' + runId, 200, payload);
  await waitForFetch(c, (f) => f.url === '/status');
  c.resolveFetch((f) => f.url === '/status', 200, statusWith(instances));
  await tick();
  await tick();
  await tick();
}

test('running renders the "Working" status strip, not the old "Status: [running]" wording', async () => {
  const c = await setupCase([inst('proj', { status: 'running', run_id: 'run-w' })]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('team-status-strip'));
  assert.ok(html.includes('Working'), 'expected the "Working" copy, got: ' + html);
  assert.ok(!html.includes('Status: [running]'), 'the old static wording must be gone');
});

test('a running team\'s feed panel defaults open ("Hide live feed") and shows "No events yet." with an empty buffer', async () => {
  const c = await setupCase([inst('proj', { status: 'running', run_id: 'run-e' })]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('Hide live feed'), 'expected the feed to default open, got: ' + html);
  assert.ok(html.includes('No events yet.'));
});

// Backlog item 12, part B: docs/design.md "Accessibility & platform notes"
// -- "Event list items should be in an <article> or similar container with
// role="log" and aria-live="polite" to announce new events to screen
// readers." Asserted on the .team-feed-list scrollable container itself
// (the element that actually gains new child rows on each poll), extracted
// from the real rendered markup like every other assertion in this file.
test('the event feed list container carries role="log" and aria-live="polite"', async () => {
  const c = await setupCase([inst('proj', { status: 'running', run_id: 'run-aria-log' })]);
  const html = c.instanceRowHtml('proj');
  assert.ok(/<div class="team-feed-list"[^>]*\brole="log"/.test(html),
    'expected role="log" on .team-feed-list, got: ' + html);
  assert.ok(/<div class="team-feed-list"[^>]*\baria-live="polite"/.test(html),
    'expected aria-live="polite" on .team-feed-list, got: ' + html);
});

// docs/design.md: "Filter pills should be <button> ... with
// aria-pressed="true" ... for selected pill." This codebase's filter pills
// are rendered as <button> elements (not <input type="radio">), so
// aria-pressed -- not aria-checked -- is the attribute that applies here;
// see docs/implementation.md "Deviations from spec" for the full
// aria-checked ambiguity resolution. Asserts the value actually toggles
// between pills, not just that the attribute is present on one of them.
test('per-agent filter pills carry aria-pressed, toggling true/false as the selected pill changes', async () => {
  const instances = [inst('proj', {
    status: 'running', run_id: 'run-aria-pressed', composition: { lead: null, members: ['helper'] },
  })];
  const c = await setupCase(instances);
  const events = [
    { ts: '2026-08-14T12:00:00Z', agent: 'lead', seq: 1, kind: 'message', text: 'lead line', meta: {} },
    { ts: '2026-08-14T12:00:01Z', agent: 'helper', seq: 1, kind: 'message', text: 'helper line', meta: {} },
  ];
  await openFeedAndDeliverEvents(c, 'proj', events);
  await rerenderRow(c, instances);
  let html = c.instanceRowHtml('proj');
  assert.ok(/<button class="team-feed-pill active" aria-pressed="true"[^>]*>All</.test(html),
    'expected the default-selected "All" pill to carry aria-pressed="true", got: ' + html);
  assert.ok(/<button class="team-feed-pill" aria-pressed="false"[^>]*>helper</.test(html),
    'expected the unselected "helper" pill to carry aria-pressed="false", got: ' + html);

  c.call('setTeamFeedFilter', 'proj', 'helper');
  await drainTriggeredRefresh(c, instances);
  html = c.instanceRowHtml('proj');
  assert.ok(/<button class="team-feed-pill" aria-pressed="false"[^>]*>All</.test(html),
    'expected "All" to flip to aria-pressed="false" once deselected, got: ' + html);
  assert.ok(/<button class="team-feed-pill active" aria-pressed="true"[^>]*>helper</.test(html),
    'expected "helper" to flip to aria-pressed="true" once selected, got: ' + html);
});

test('idle renders no status-strip/feed/escalation UI (unchanged from 6d/6e)', async () => {
  const c = await setupCase([inst('proj', null)]);
  const html = c.instanceRowHtml('proj');
  assert.ok(!html.includes('team-status-strip'));
  assert.ok(!html.includes('team-feed'));
  assert.ok(!html.includes('team-escalation'));
});

// ─── Escalation panel ───────────────────────────────────────────────────

test('waiting_on_you fetches the inbox once and renders question/header/options/free-text', async () => {
  const instances = [inst('proj', { status: 'blocked', run_id: 'run-1', waiting_on_you: true })];
  const c = await setupCase(instances);
  await deliverTeamInbox(c, 'proj', 'run-1', instances, {
    pending: true, run_id: 'run-1', question: 'Is this correct?', header: 'from lead',
    options: [{ label: 'Yes', description: 'proceed' }, { label: 'No' }], multi_select: false,
  });
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('team-escalation-form'));
  assert.ok(html.includes('Is this correct?'));
  assert.ok(html.includes('from lead'));
  assert.ok(html.includes('>Yes<'));
  assert.ok(html.includes('>No<'));
  assert.ok(html.includes('escalation-other-proj'), 'the free-text Other input must always be present');
  assert.ok(html.includes('type="radio"'), 'single_select must render radios');
});

// Backlog item 12, part B: docs/design.md "Accessibility & platform notes"
// -- "Escalation form: <fieldset> for radio/checkbox groups with <legend>
// for the question." The legend text is the pending question's own text
// (docs/spec.md part B; see docs/implementation.md for the developer's
// choice of question over header, documented there).
test('the escalation option group is wrapped in <fieldset>/<legend>, legend text is the question', async () => {
  const instances = [inst('proj', { status: 'blocked', run_id: 'run-fs', waiting_on_you: true })];
  const c = await setupCase(instances);
  await deliverTeamInbox(c, 'proj', 'run-fs', instances, {
    pending: true, run_id: 'run-fs', question: 'Is the analysis correct?', header: 'from lead',
    options: [{ label: 'Yes' }, { label: 'No' }], multi_select: false,
  });
  const html = c.instanceRowHtml('proj');
  assert.ok(/<fieldset[^>]*>[\s\S]*<legend[^>]*>Is the analysis correct\?<\/legend>/.test(html),
    'expected a <fieldset> whose <legend> is the question text, got: ' + html);
  // The options themselves must be inside the fieldset, after the legend.
  const fsIdx = html.indexOf('<fieldset');
  const legendCloseIdx = html.indexOf('</legend>', fsIdx);
  const fsCloseIdx = html.indexOf('</fieldset>', legendCloseIdx);
  assert.ok(fsIdx !== -1 && legendCloseIdx !== -1 && fsCloseIdx !== -1);
  const insideFieldset = html.slice(legendCloseIdx, fsCloseIdx);
  assert.ok(insideFieldset.includes('type="radio"'), 'expected the radio options inside the fieldset, got: ' + html);
});

test('multi_select renders checkboxes; zero options still renders the always-present free-text input', async () => {
  const instances = [inst('proj', { status: 'blocked', run_id: 'run-2', waiting_on_you: true })];
  const c = await setupCase(instances);
  await deliverTeamInbox(c, 'proj', 'run-2', instances, {
    pending: true, run_id: 'run-2', question: 'fallback question', header: '', options: [], multi_select: true,
  });
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('escalation-other-proj'));
  // Scoped to "escalation-option-<name>" specifically -- the row's own
  // singleton on/off switch always renders an unrelated
  // `<input type="checkbox">`, which a bare `html.includes('type="checkbox"')`
  // check would false-negative on.
  assert.ok(!html.includes('name="escalation-option-proj"'), 'no options to render yet');
});

test('escalated_max_rounds (waiting_on_you=false) never fetches /team/inbox', async () => {
  const c = await setupCase([inst('proj', { status: 'blocked', run_id: 'run-3', waiting_on_you: false })]);
  await tick();
  await tick();
  assert.ok(!c.pendingFetches.some((f) => f.url.indexOf('/team/inbox') !== -1),
    'must never poll the inbox just to light the "waiting on you" indicator');
});

// Backlog item 12, part A: the "already answered" race (docs/spec.md
// "Background" item A) -- a cached /status snapshot still says
// waiting_on_you: true (a moment-in-time read), but the freshly-fetched
// GET .../team/inbox -- fetched a beat later, e.g. another operator/tab
// just answered it -- already reports {"pending": false} (the real
// backend's own exact shape for a non-blocked_ask_user state, see
// app/app.py's _handle_team_inbox()). renderEscalationPanel()'s
// `!cached.pending` branch must render distinct "already answered" copy,
// not the normal question/options form and not the fetch-failure copy.
test('waiting_on_you true but a fresh /team/inbox already reports pending:false renders "already answered", no form', async () => {
  const instances = [inst('proj', { status: 'blocked', run_id: 'run-late', waiting_on_you: true })];
  const c = await setupCase(instances);
  await deliverTeamInbox(c, 'proj', 'run-late', instances, { pending: false });
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('already answered'), 'expected the distinct "already answered" copy, got: ' + html);
  assert.ok(!html.includes('team-escalation-form'), 'must not render the normal question/options submit form');
  assert.ok(!html.includes('Could not load the pending question'), 'must not be conflated with the fetch-failure copy');
});

test('selecting a single-select option and submitting sends {answer: "<label>"} via team-resolve', async () => {
  const instances = [inst('proj', { status: 'blocked', run_id: 'run-r', waiting_on_you: true })];
  const c = await setupCase(instances);
  await deliverTeamInbox(c, 'proj', 'run-r', instances, {
    pending: true, run_id: 'run-r', question: 'q', header: '', multi_select: false,
    options: [{ label: 'Yes, proceed' }, { label: 'No, revise' }],
  });
  c.call('onEscalationOptionChange', 'proj', 0, false, true);
  await tick();
  c.call('doTeamResolve', 'proj');
  await tick();
  const f = c.pendingFetches.find((x) => x.url === '/projects/proj/team/resolve');
  assert.ok(f, 'expected a pending POST /projects/proj/team/resolve');
  assert.strictEqual(f.opts.method, 'POST');
  const body = JSON.parse(f.opts.body);
  assert.strictEqual(body.answer, 'Yes, proceed');
  c.resolveFetch((x) => x.url === '/projects/proj/team/resolve', 200, { ok: true, run_id: 'run-r' });
  await tick();
  await tick();
  const msg = c.msgEl('proj');
  assert.strictEqual(msg.textContent, '✓ Answer submitted');
  assert.ok(msg.className.includes('success'));
});

test('multi_select joins several chosen labels with ", "', async () => {
  const instances = [inst('proj', { status: 'blocked', run_id: 'run-m', waiting_on_you: true })];
  const c = await setupCase(instances);
  await deliverTeamInbox(c, 'proj', 'run-m', instances, {
    pending: true, run_id: 'run-m', question: 'q', header: '', multi_select: true,
    options: [{ label: 'A' }, { label: 'B' }, { label: 'C' }],
  });
  c.call('onEscalationOptionChange', 'proj', 0, true, true);
  c.call('onEscalationOptionChange', 'proj', 2, true, true);
  await tick();
  assert.strictEqual(c.call('computeTeamResolveAnswer', 'proj'), 'A, C');
});

test('free-text "Other" wins over any selected option when both are present', async () => {
  const instances = [inst('proj', { status: 'blocked', run_id: 'run-o', waiting_on_you: true })];
  const c = await setupCase(instances);
  await deliverTeamInbox(c, 'proj', 'run-o', instances, {
    pending: true, run_id: 'run-o', question: 'q', header: '', multi_select: false,
    options: [{ label: 'Yes' }],
  });
  c.call('onEscalationOptionChange', 'proj', 0, false, true);
  c.sandbox.document.getElementById('escalation-other-proj').value = 'a custom answer instead';
  vm.runInContext(
    "teamEscalationOther['proj'] = document.getElementById('escalation-other-proj').value;", c.sandbox);
  assert.strictEqual(c.call('computeTeamResolveAnswer', 'proj'), 'a custom answer instead');
});

test('an over-2000-char or empty answer is rejected client-side with no request dispatched', async () => {
  const instances = [inst('proj', { status: 'blocked', run_id: 'run-v', waiting_on_you: true })];
  const c = await setupCase(instances);
  await deliverTeamInbox(c, 'proj', 'run-v', instances, {
    pending: true, run_id: 'run-v', question: 'q', header: '', multi_select: false, options: [],
  });
  c.call('doTeamResolve', 'proj');
  await tick();
  assert.ok(!c.pendingFetches.some((f) => f.url === '/projects/proj/team/resolve'),
    'an empty answer must never be dispatched');
  assert.ok(c.msgEl('proj').className.includes('error'));
});

test('a 428 mid-resolve shows the code overlay labeled for this team\'s answer submission', async () => {
  const instances = [inst('proj', { status: 'blocked', run_id: 'run-428', waiting_on_you: true })];
  const c = await setupCase(instances);
  await deliverTeamInbox(c, 'proj', 'run-428', instances, {
    pending: true, run_id: 'run-428', question: 'q', header: '', multi_select: false,
    options: [{ label: 'Yes' }],
  });
  c.call('onEscalationOptionChange', 'proj', 0, false, true);
  await tick();
  c.call('doTeamResolve', 'proj');
  await tick();
  c.resolveFetch((f) => f.url === '/projects/proj/team/resolve', 428, { error: 'totp_required' });
  await tick();
  await tick();
  const label = c.elements.get('code-overlay-label');
  assert.strictEqual(label.textContent, 'Submitting answer: proj');
  c.elements.get('action-code').value = '123456';
  c.call('submitActionCode');
  await tick();
  const retry = c.pendingFetches.find((f) => f.url === '/projects/proj/team/resolve');
  assert.ok(retry);
  assert.strictEqual(JSON.parse(retry.opts.body).code, '123456');
  c.resolveFetch((f) => f.url === '/projects/proj/team/resolve', 200, { ok: true, run_id: 'run-428' });
  await tick();
  await tick();
  assert.strictEqual(c.msgEl('proj').textContent, '✓ Answer submitted');
});

// ─── Merged event feed ──────────────────────────────────────────────────

test('events from the lead and a teammate render merged, in chronological order, colour-coded per agent', async () => {
  // 'coder' (not 'helper') deliberately -- teamAgentColor()'s simple 6-bucket
  // hash happens to collide 'lead'/'helper' into the same palette slot,
  // which is an acceptable general property of a 6-colour hash (more than 6
  // agent names cannot all be unique) but would make THIS specific
  // pairwise-distinctness assertion flaky. 'coder' does not collide with
  // 'lead' under the same hash.
  const instances = [inst('proj', {
    status: 'running', run_id: 'run-feed', composition: { lead: null, members: ['coder'] },
  })];
  const c = await setupCase(instances);
  const events = [
    { ts: '2026-08-14T12:00:00Z', agent: 'lead', seq: 1, kind: 'message', text: 'Starting up', meta: {} },
    { ts: '2026-08-14T12:00:01Z', agent: 'coder', seq: 1, kind: 'message', text: 'Working on it', meta: {} },
  ];
  await openFeedAndDeliverEvents(c, 'proj', events);
  await rerenderRow(c, instances);
  const html = c.instanceRowHtml('proj');
  const leadIdx = html.indexOf('Starting up');
  const coderIdx = html.indexOf('Working on it');
  assert.ok(leadIdx !== -1 && coderIdx !== -1, 'expected both events rendered, got: ' + html);
  assert.ok(leadIdx < coderIdx, 'expected chronological order (lead event first)');
  // Colour-coded per agent -- the two agent name spans must carry different
  // inline colours (a stable hash-based palette, docs/design.md).
  const leadColor = /team-feed-agent" style="color:(#[0-9a-f]+)">lead</.exec(html);
  const coderColor = /team-feed-agent" style="color:(#[0-9a-f]+)">coder</.exec(html);
  assert.ok(leadColor && coderColor, 'expected colour-coded agent spans, got: ' + html);
  assert.notStrictEqual(leadColor[1], coderColor[1]);
});

test('per-agent filter shows only that agent\'s events; "All" restores the merged view', async () => {
  const instances = [inst('proj', {
    status: 'running', run_id: 'run-filter', composition: { lead: null, members: ['helper'] },
  })];
  const c = await setupCase(instances);
  const events = [
    { ts: '2026-08-14T12:00:00Z', agent: 'lead', seq: 1, kind: 'message', text: 'lead line', meta: {} },
    { ts: '2026-08-14T12:00:01Z', agent: 'helper', seq: 1, kind: 'message', text: 'helper line', meta: {} },
  ];
  await openFeedAndDeliverEvents(c, 'proj', events);
  await rerenderRow(c, instances);
  c.call('setTeamFeedFilter', 'proj', 'helper');
  await drainTriggeredRefresh(c, instances);
  let html = c.instanceRowHtml('proj');
  assert.ok(!html.includes('lead line') && html.includes('helper line'), 'expected only helper events, got: ' + html);

  c.call('setTeamFeedFilter', 'proj', 'all');
  await drainTriggeredRefresh(c, instances);
  html = c.instanceRowHtml('proj');
  assert.ok(html.includes('lead line') && html.includes('helper line'), 'expected the merged view restored');
});

test('fact_check found:true renders the claim and each match\'s file_line + passage text, not raw JSON', async () => {
  const instances = [inst('proj', { status: 'running', run_id: 'run-fc' })];
  const c = await setupCase(instances);
  const events = [
    { ts: '2026-08-14T12:00:00Z', agent: 'lead', seq: 1, kind: 'tool_use', text: 'Python is a snake', meta: {} },
    {
      ts: '2026-08-14T12:00:01Z', agent: 'lead', seq: 2, kind: 'tool_result', meta: { found: true },
      text: JSON.stringify({
        claim: 'Python is a snake', found: true,
        matches: [{ label: 'docs', path: 'x', relpath: 'docs/snake.md', line: 42,
                    file_line: 'docs/snake.md:42', text: 'Python is a reptile, not a mammal.', end_line: 42 }],
      }),
    },
  ];
  await openFeedAndDeliverEvents(c, 'proj', events);
  await rerenderRow(c, instances);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('fact_check: Python is a snake'), 'expected the claim rendered, got: ' + html);
  assert.ok(html.includes('docs/snake.md:42'), 'expected the match file_line, got: ' + html);
  assert.ok(html.includes('Python is a reptile'), 'expected the passage text, got: ' + html);
  assert.ok(!html.includes('"matches":'), 'must never render the raw JSON blob');
});

test('fact_check found:false renders an explicit "no supporting passage found"', async () => {
  const instances = [inst('proj', { status: 'running', run_id: 'run-fc2' })];
  const c = await setupCase(instances);
  const events = [
    { ts: '2026-08-14T12:00:00Z', agent: 'lead', seq: 1, kind: 'tool_use', text: 'The sky is green', meta: {} },
    {
      ts: '2026-08-14T12:00:01Z', agent: 'lead', seq: 2, kind: 'tool_result', meta: { found: false },
      text: JSON.stringify({ claim: 'The sky is green', found: false, matches: [] }),
    },
  ];
  await openFeedAndDeliverEvents(c, 'proj', events);
  await rerenderRow(c, instances);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('no supporting passage found'), 'expected the explicit non-match text, got: ' + html);
});

// team.status === 'finished' (a genuinely terminal poll, not "running" --
// see the "poll-boundary" tests below for the running/transient case this
// cycle, backlog item 12 part C, adds) -- there is no ambiguity left to
// resolve: the run is over, so a trailing tool_use with empty meta and no
// following lead event is unambiguously the finish summary.
test('a tool_use with empty meta and no following lead event renders as the finish summary once the run has ended', async () => {
  const instances = [inst('proj', { status: 'finished', run_id: 'run-finish' })];
  const c = await setupCase(instances);
  const events = [
    { ts: '2026-08-14T12:00:00Z', agent: 'lead', seq: 1, kind: 'tool_use', text: 'All done: summary text', meta: {} },
  ];
  await openFeedAndDeliverEvents(c, 'proj', events);
  await rerenderRow(c, instances);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('[Finish summary]'), 'expected the finish-summary rendering, got: ' + html);
  assert.ok(html.includes('All done: summary text'));
  assert.ok(!html.includes('fact_check:'), 'must not be mistaken for a fact_check claim');
});

// ─── Poll-boundary fact_check-vs-finish disambiguation (backlog item 12,
// part C) ────────────────────────────────────────────────────────────────
//
// docs/spec.md "Background" item C / "Proposed approach" §C: a `tool_use`
// event with empty `meta` that is the event buffer's own LAST lead-agent
// event, while `team.status === 'running'` (the paired `tool_result` or a
// terminal status simply hasn't shown up on a poll yet), must render a
// transient "pending classification" state instead of assuming it's the
// finish summary -- this is the exact scenario the now-renamed "...once
// the run has ended" test above used to (mis)cover with `status: 'running'`.

test('a trailing tool_use with empty meta renders a transient pending state while team.status is still "running"', async () => {
  const instances = [inst('proj', { status: 'running', run_id: 'run-pending' })];
  const c = await setupCase(instances);
  const events = [
    { ts: '2026-08-14T12:00:00Z', agent: 'lead', seq: 1, kind: 'tool_use', text: 'All done: summary text', meta: {} },
  ];
  await openFeedAndDeliverEvents(c, 'proj', events);
  await rerenderRow(c, instances);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('kind-pending-classification'), 'expected the transient pending-classification class, got: ' + html);
  assert.ok(!html.includes('[Finish summary]'), 'must not assume finish while still running, got: ' + html);
  assert.ok(!html.includes('fact_check:'), 'must not assume fact_check either, got: ' + html);
});

test('the transient pending state resolves to fact_check once the paired tool_result arrives on a later poll', async () => {
  const instances = [inst('proj', { status: 'running', run_id: 'run-resolve-fc' })];
  const c = await setupCase(instances);
  const firstBatch = [
    { ts: '2026-08-14T12:00:00Z', agent: 'lead', seq: 1, kind: 'tool_use', text: 'Python is a snake', meta: {} },
  ];
  await openFeedAndDeliverEvents(c, 'proj', firstBatch);
  await rerenderRow(c, instances);
  let html = c.instanceRowHtml('proj');
  assert.ok(html.includes('kind-pending-classification'), 'expected the transient state on the first poll, got: ' + html);

  // Second poll: the paired tool_result lands.
  const p = c.call('pollTeamFeed', 'proj');
  await waitForFetch(c, (f) => f.url.indexOf('/projects/proj/team/events') === 0);
  c.resolveFetch((f) => f.url.indexOf('/projects/proj/team/events') === 0, 200, {
    run_id: 'run-resolve-fc',
    events: [{
      ts: '2026-08-14T12:00:01Z', agent: 'lead', seq: 2, kind: 'tool_result', meta: { found: true },
      text: JSON.stringify({ claim: 'Python is a snake', found: true,
        matches: [{ file_line: 'docs/snake.md:42', text: 'Python is a reptile.' }] }),
    }],
    cursors: {}, truncated: {},
  });
  await p;
  await rerenderRow(c, instances);
  html = c.instanceRowHtml('proj');
  assert.ok(!html.includes('kind-pending-classification'), 'the transient state must clear once resolved, got: ' + html);
  assert.ok(html.includes('fact_check: Python is a snake'), 'expected it to resolve to a fact_check claim, got: ' + html);
});

test('the transient pending state resolves to finish once a terminal status arrives on a later poll', async () => {
  const running = [inst('proj', { status: 'running', run_id: 'run-resolve-fin' })];
  const c = await setupCase(running);
  const events = [
    { ts: '2026-08-14T12:00:00Z', agent: 'lead', seq: 1, kind: 'tool_use', text: 'All done: summary text', meta: {} },
  ];
  await openFeedAndDeliverEvents(c, 'proj', events);
  await rerenderRow(c, running);
  let html = c.instanceRowHtml('proj');
  assert.ok(html.includes('kind-pending-classification'), 'expected the transient state while running, got: ' + html);

  const finished = [inst('proj', { status: 'finished', run_id: 'run-resolve-fin' })];
  await rerenderRow(c, finished);
  html = c.instanceRowHtml('proj');
  assert.ok(!html.includes('kind-pending-classification'), 'the transient state must clear once terminal, got: ' + html);
  assert.ok(html.includes('[Finish summary]'), 'expected it to resolve to the finish summary, got: ' + html);
});

test('a handoff event renders "Delegating to <teammate>"', async () => {
  const instances = [inst('proj', { status: 'running', run_id: 'run-handoff' })];
  const c = await setupCase(instances);
  const events = [
    { ts: '2026-08-14T12:00:00Z', agent: 'lead', seq: 1, kind: 'handoff', text: '', meta: { agent: 'helper' } },
  ];
  await openFeedAndDeliverEvents(c, 'proj', events);
  await rerenderRow(c, instances);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('Delegating to helper'), 'got: ' + html);
});

test('a truncated:true response triggers an immediate follow-up /team/events call, not waiting for the next tick', async () => {
  const instances = [inst('proj', { status: 'running', run_id: 'run-trunc' })];
  const c = await setupCase(instances);
  await waitForFetch(c, (f) => f.url.indexOf('/projects/proj/team/events') === 0);
  const first = c.pendingFetches.find((f) => f.url.indexOf('/projects/proj/team/events') === 0);
  assert.ok(first.url.indexOf('cursor=%7B%7D') !== -1, 'first poll must start from cursor={}, got: ' + first.url);
  c.resolveFetch((f) => f.url.indexOf('/projects/proj/team/events') === 0, 200, {
    run_id: 'run-trunc', events: [{ ts: '2026-08-14T12:00:00Z', agent: 'lead', seq: 1, kind: 'message', text: 'a', meta: {} }],
    cursors: { lead: 500 }, truncated: { lead: true },
  });
  // No tick-count guessing needed -- assert the follow-up shows up promptly.
  await waitForFetch(c, (f) => f.url.indexOf('/projects/proj/team/events') === 0 && f.url.indexOf('cursor=%7B%22lead%22%3A500%7D') !== -1);
  c.resolveFetch((f) => f.url.indexOf('cursor=%7B%22lead%22%3A500%7D') !== -1, 200, {
    run_id: 'run-trunc', events: [], cursors: { lead: 900 }, truncated: {},
  });
  await tick();
  await tick();
  // Drained -- no third call outstanding.
  assert.ok(!c.pendingFetches.some((f) => f.url.indexOf('/projects/proj/team/events') === 0));
});

test('reopening the feed after closing it starts fresh from cursor={} (same as a page reload)', async () => {
  const instances = [inst('proj', { status: 'running', run_id: 'run-reopen' })];
  const c = await setupCase(instances);
  await waitForFetch(c, (f) => f.url.indexOf('/projects/proj/team/events') === 0);
  c.resolveFetch((f) => f.url.indexOf('/projects/proj/team/events') === 0, 200,
    { run_id: 'run-reopen', events: [{ ts: '2026-08-14T12:00:00Z', agent: 'lead', seq: 1, kind: 'message', text: 'x', meta: {} }],
      cursors: { lead: 500 }, truncated: {} });
  await tick();
  await tick();
  c.call('toggleTeamFeed', 'proj');  // close -- triggers its own internal refresh()
  await drainTriggeredRefresh(c, instances);
  c.call('toggleTeamFeed', 'proj');  // reopen -- likewise triggers its own refresh()
  // pollTeamFeed() is only invoked from WITHIN refresh()'s own for-loop,
  // after its /status fetch actually resolves -- draining it here is what
  // lets the reopen's own fresh poll actually fire.
  await drainTriggeredRefresh(c, instances);
  const f = c.pendingFetches.find((x) => x.url.indexOf('/projects/proj/team/events') === 0);
  assert.ok(f, 'expected a fresh poll on reopen');
  assert.ok(f.url.indexOf('cursor=%7B%7D') !== -1, 'reopening must start from cursor={}, got: ' + f.url);
});

test('more than 500 events in the buffer are trimmed to the most recent 500, cursor unaffected', async () => {
  const instances = [inst('proj', { status: 'running', run_id: 'run-trim' })];
  const c = await setupCase(instances);
  const events = [];
  for (let i = 0; i < 600; i++) {
    events.push({ ts: '2026-08-14T12:00:' + String(i % 60).padStart(2, '0') + 'Z', agent: 'lead', seq: i,
                  kind: 'message', text: 'evt' + i, meta: {} });
  }
  await openFeedAndDeliverEvents(c, 'proj', events, {});
  await rerenderRow(c, instances);
  const html = c.instanceRowHtml('proj');
  assert.ok(!html.includes('>evt0<'), 'the oldest events must have been trimmed');
  assert.ok(html.includes('evt599'), 'the most recent events must be kept');
});

test('resolving an escalation transitions the strip away from "Waiting on you" within the next poll', async () => {
  const instances = [inst('proj', { status: 'blocked', run_id: 'run-tr', waiting_on_you: true })];
  const c = await setupCase(instances);
  await deliverTeamInbox(c, 'proj', 'run-tr', instances, {
    pending: true, run_id: 'run-tr', question: 'q', header: '', multi_select: false, options: [{ label: 'Yes' }],
  });
  c.call('onEscalationOptionChange', 'proj', 0, false, true);
  // onEscalationOptionChange() triggers its own internal refresh() -- drain
  // it now (see drainTriggeredRefresh()'s own doc comment) so the LATER,
  // real rerenderRow() call below isn't racing a stale already-pending
  // /status fetch.
  await drainTriggeredRefresh(c, instances);
  c.call('doTeamResolve', 'proj');
  await tick();
  c.resolveFetch((f) => f.url === '/projects/proj/team/resolve', 200, { ok: true, run_id: 'run-tr' });
  await tick();
  await tick();
  const nextInstances = [inst('proj', { status: 'running', run_id: 'run-tr', waiting_on_you: false })];
  await rerenderRow(c, nextInstances);
  const html = c.instanceRowHtml('proj');
  assert.ok(!html.includes('Waiting on you'));
  assert.ok(!html.includes('team-escalation-form'));
});

test('a team stopping (going idle) clears its feed/escalation client state', async () => {
  const running = [inst('proj', { status: 'running', run_id: 'run-clear' })];
  const c = await setupCase(running);
  await waitForFetch(c, (f) => f.url.indexOf('/projects/proj/team/events') === 0);
  c.resolveFetch((f) => f.url.indexOf('/projects/proj/team/events') === 0, 200,
    { run_id: 'run-clear', events: [{ ts: '2026-08-14T12:00:00Z', agent: 'lead', seq: 1, kind: 'message', text: 'x', meta: {} }],
      cursors: { lead: 10 }, truncated: {} });
  await tick();
  await tick();
  const idle = [inst('proj', null)];
  await rerenderRow(c, idle);
  const html = c.instanceRowHtml('proj');
  assert.ok(!html.includes('team-feed'));
  assert.ok(!html.includes('team-status-strip'));
  // Re-running the SAME project again must default the feed back open, not
  // stay wherever it was left before going idle.
  const c2 = await setupCase([inst('proj', { status: 'running', run_id: 'run-clear-2' })]);
  assert.ok(c2.instanceRowHtml('proj').includes('Hide live feed'));
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

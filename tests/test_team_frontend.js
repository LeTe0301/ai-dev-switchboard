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
  // classList (Taiga #10) -- a real, stateful Set-backed stub, not the
  // no-op every sibling test file's own makeElementStub() still uses (this
  // file is the first to actually assert .contains() -- #team-page/#rows'
  // own show/hide toggling via .active/.hidden-for-team-page). Scoped to
  // this file only, per docs/spec.md's own minimal-diff instinct -- the
  // other four test files' identical no-op stub is untouched.
  const classes = new Set();
  return {
    id,
    className: '',
    classList: {
      add(...cls) { cls.forEach((c) => classes.add(c)); },
      remove(...cls) { cls.forEach((c) => classes.delete(c)); },
      contains(c) { return classes.has(c); },
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

// locationPathname (Taiga #10, docs/spec.md "Proposed approach" §2) --
// defaults to '/' so every pre-existing caller (which never passes this)
// keeps triggering the script's own unawaited bootstrap refresh() call at
// load time, unchanged. Passing e.g. '/team/proj' instead makes the script's
// own top-level router branch fire renderTeamPage('proj') at load time
// instead -- used by the router-dispatch tests further down.
function createCase(locationPathname) {
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
    // Dedicated team chat page (Taiga #10) -- a plain mutable stub, not a
    // real browser Location: goToDashboard()'s onclick sets .href directly,
    // assertable the same way c.sandbox.location.href is read below.
    location: { pathname: locationPathname || '/', href: '' },
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

  // Same vm-lexical-scope reasoning as setTeamTaskText() above, for the
  // chat-UI compose surface's own draft-text mirror (backlog item 19 part
  // 2, docs/spec.md "Proposed approach" §1).
  function setTeamInterjectText(name, text) {
    vm.runInContext(
      `teamInterjectText[${JSON.stringify(name)}] = ${JSON.stringify(text)};`, sandbox);
  }

  // Scopes assertions to exactly one project's own <div class="row">...
  // </div> slice, anchored on its <div class="label">NAME</div> — same
  // slicing technique tests/test_deploy_frontend.js's own instanceRowHtml()
  // uses. Post-Taiga #10, this is the *dashboard's* own compact team
  // summary (status badge + "Open team chat" link) -- used by the new
  // dashboard-summary tests further down, NOT by the pre-existing full-body
  // team tests (see instanceRowHtml() below).
  function dashboardRowHtml(name) {
    const html = rowsHtml();
    const marker = '<div class="label">' + name + '</div>';
    const markerIdx = html.indexOf(marker);
    if (markerIdx === -1) return '';
    const rowStart = html.lastIndexOf('<div class="row">', markerIdx);
    const rowEnd = html.indexOf('<div class="row">', markerIdx + marker.length);
    return html.slice(rowStart === -1 ? 0 : rowStart, rowEnd === -1 ? html.length : rowEnd);
  }

  // Taiga #10 (docs/spec.md "Proposed approach" §3): the full team surface
  // this file's own pre-existing tests exercise no longer lives in the
  // dashboard's #rows at all -- it moved to renderTeamPageBody(), mounted
  // from the dedicated /team/<project> page. Rather than touch every one of
  // those pre-existing test bodies, this helper is retargeted to call
  // renderTeamPageBody() directly (the same function renderTeamPage() itself
  // calls, so this is still exercising the real, shared implementation, not
  // a parallel one) -- every existing `c.instanceRowHtml('proj')` call site
  // keeps working, and keeps proving the same thing it always did, just
  // against the sub-renderers' new home. TEAM_BY_NAME is populated by every
  // refresh()/toggleTeamPicker() cycle these tests already drive, so it's
  // always up to date here (same vm-lexical-scope reasoning setTeamTaskText
  // above already documents).
  function instanceRowHtml(name) {
    return vm.runInContext(
      `renderTeamPageBody(${JSON.stringify(name)}, TEAM_BY_NAME[${JSON.stringify(name)}])`, sandbox);
  }
  // Old teamRow(), called from refresh()'s per-project loop, rendered EVERY
  // project's full body on every poll -- which is what fired renderTeamBranches()'s
  // own one-time fetchTeamBranches() side effect (idle or not) and, for a
  // non-idle project, seeded teamFeedOpen[name] and let refresh() immediately
  // fire pollTeamFeed() for it on the same tick. Both of those now live in
  // renderTeamPage() instead (refresh() itself no longer renders any of this
  // -- the dashboard doesn't show it anymore, Taiga #10). simulateTeamPageRender()
  // replicates that same adjacency in the test harness -- called once per
  // simulated "poll tick" (i.e. once per c.call('refresh') this file's own
  // helpers already perform) -- so every pre-existing feed/escalation/
  // branches test keeps observing the same behavior it always did, without
  // needing its own body touched.
  function simulateTeamPageRender(instances) {
    for (const inst of (instances || [])) {
      vm.runInContext(
        `renderTeamPageBody(${JSON.stringify(inst.name)}, TEAM_BY_NAME[${JSON.stringify(inst.name)}])`, sandbox);
      const team = inst.team;
      if (team && team.status !== 'idle' &&
          vm.runInContext(`!!teamFeedOpen[${JSON.stringify(inst.name)}]`, sandbox)) {
        vm.runInContext(`pollTeamFeed(${JSON.stringify(inst.name)})`, sandbox);
      }
    }
  }

  return {
    sandbox, elements, resolveFetch, call, rowsHtml, instanceRowHtml, dashboardRowHtml, simulateTeamPageRender,
    setTeamTaskText, setTeamInterjectText,
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
    interjectEl(name) { return sandbox.document.getElementById('interject-' + name); },
    interjectSendBtnEl(name) { return sandbox.document.getElementById('interject-send-' + name); },
    interjectCounterEl(name) { return sandbox.document.getElementById('interject-counter-' + name); },
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

// Bootstraps a case through the same two-step bootstrap-refresh()-then-
// real-refresh() dance setupCase() below performs, but stops there --
// leaves any per-project /team/branches fetch (backlog item 13) that
// refresh() just triggered UNresolved and pending. Used directly by the
// "Past team branches panel" tests further down, which need to control
// that fetch's own payload/timing themselves; setupCase() itself instead
// auto-drains it with a default empty-array response (see its own doc
// comment) so the rest of this file's tests -- which don't care about this
// panel -- see a clean pendingFetches list.
async function bootstrapCase(instances, roster) {
  const c = createCase();
  // Drain the script's own unawaited bootstrap refresh() call at load time.
  c.resolveFetch((f) => f.url === '/status', 200, statusWith([]));
  await tick();
  await tick();
  // Now do the real refresh() this test actually wants to assert against.
  const p = c.call('refresh');
  c.resolveFetch((f) => f.url === '/status', 200, statusWith(instances, roster));
  await p;
  // Taiga #10: refresh() itself no longer renders any per-project team
  // detail (see simulateTeamPageRender()'s own doc comment above) -- fire
  // its replacement here so bootstrapCase()'s own callers (including the
  // "Past team branches panel" tests that call this directly, expecting a
  // pending /team/branches fetch afterward) see the same side effects a
  // real dashboard-then-team-page load would have produced.
  c.simulateTeamPageRender(instances);
  return c;
}

async function setupCase(instances, roster) {
  const c = await bootstrapCase(instances, roster);
  // Past team branches panel (backlog item 13, docs/spec.md) -- every
  // project row fires its own one-time /team/branches fetch as a side
  // effect of rendering (see fetchTeamBranches()'s own doc comment in
  // app/app.py, and renderTeamBranches() which triggers it). Drained here
  // with an empty-array default -- fetchTeamBranches() deliberately does
  // NOT re-render itself once resolved, so this drain has no cascading
  // effect on pendingFetches beyond removing this one entry.
  for (const inst of (instances || [])) {
    if (c.pendingFetches.some((f) => f.url === '/projects/' + inst.name + '/team/branches')) {
      c.resolveFetch((f) => f.url === '/projects/' + inst.name + '/team/branches', 200, []);
    }
  }
  await tick();
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

// Detects a real `checked` boolean attribute in a sliced checkbox tag
// string, without false-positiving on the unrelated substring "this.checked"
// that every teammate checkbox's own onchange="...this.checked)" attribute
// already contains verbatim.
function hasCheckedAttr(tagStr) {
  return /\schecked(\s|>)/.test(tagStr);
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

// docs/spec.md issue #1: the idle/launcher state has no indication that
// anything appears here once a team starts -- a short static hint makes
// the in-page live feed + interject compose box (backlog item 19)
// discoverable before Start is ever clicked.
test('idle state includes a static hint that live team activity appears here once started', async () => {
  const roster = [rosterEntry({ name: 'lead2', tier: 2 }), rosterEntry({ name: 'helper', tier: 2 })];
  const comp = { lead: { kind: 'engine', name: 'lead2' }, members: ['helper'] };
  const c = await setupCase([inst('proj', { status: 'idle', run_id: null, composition: comp })], roster);
  const html = c.instanceRowHtml('proj');
  assert.ok(/once started/i.test(html), 'expected a discoverability hint mentioning "once started", got: ' + html);
  assert.ok(!/\bchat\b/i.test(html), 'must not overstate this as a dedicated "chat" UI beyond what item 19 built');
});

test('the idle-state hint disappears once the team is running (superseded by the real feed)', async () => {
  const c = await setupCase([inst('proj', { status: 'running', run_id: 'run-1' })]);
  const html = c.instanceRowHtml('proj');
  assert.ok(!/once started/i.test(html), 'the idle-only hint must not linger once the team is actually running');
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

// backlog item 7 part 2, docs/spec.md §1 / docs/design.md "Status Strip:
// Board Write Pending Approval" -- escalation_kind distinguishes the strip
// copy from the ask_user case above without opening the panel.
test('blocked + waiting_on_you + escalation_kind board_write renders distinct strip copy, not "Waiting on you"', async () => {
  const c = await setupCase([inst('proj', {
    status: 'blocked', run_id: 'run-bw1', waiting_on_you: true, escalation_kind: 'board_write',
  })]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('status-blocked'));
  assert.ok(html.includes('Board write pending approval'), 'expected the board_write strip copy, got: ' + html);
  assert.ok(!html.includes('Waiting on you'), 'must not render the ask_user copy for a board_write escalation');
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

// Backlog item 45, docs/spec.md "Proposed approach" (Frontend) / "Acceptance
// criteria" -- the finishedSummary sibling block under the status strip,
// reusing the escalatedNote/.team-sub pattern verbatim.
test('finished + non-empty summary shows "Finished" and a .team-sub summary line', async () => {
  const c = await setupCase([inst('proj', {
    status: 'finished', run_id: 'run-1', summary: 'Could not complete: build tool unavailable.',
  })]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('status-finished'));
  assert.ok(html.includes('>Finished'), 'expected the unchanged "Finished" strip label, got: ' + html);
  assert.ok(/<div class="team-sub">Could not complete: build tool unavailable\.<\/div>/.test(html),
    'expected a .team-sub summary line, got: ' + html);
});

test('finished + empty-string summary renders no summary line (unchanged "Finished" only)', async () => {
  const c = await setupCase([inst('proj', { status: 'finished', run_id: 'run-1', summary: '' })]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('status-finished'));
  const subCount = (html.match(/class="team-sub"/g) || []).length;
  assert.strictEqual(subCount, 0, 'expected no .team-sub block for an empty summary, got: ' + html);
});

test('finished + a summary containing HTML is escaped, not injected raw', async () => {
  const c = await setupCase([inst('proj', {
    status: 'finished', run_id: 'run-1', summary: '<script>alert(1)</script>',
  })]);
  const html = c.instanceRowHtml('proj');
  assert.ok(!html.includes('<script>alert(1)</script>'), 'raw HTML must not be injected, got: ' + html);
  assert.ok(html.includes('&lt;script&gt;'), 'expected the summary to be HTML-escaped, got: ' + html);
});

test('a non-finished status never renders a summary line even if team.summary happens to be set', async () => {
  const c = await setupCase([inst('proj', {
    status: 'running', run_id: 'run-1', summary: 'should never show',
  })]);
  const html = c.instanceRowHtml('proj');
  assert.ok(!html.includes('should never show'), 'summary must only render for status === "finished", got: ' + html);
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

test('the saved composition pre-selects the lead, shows it disabled+unchecked in the teammate checkboxes (never hidden)', async () => {
  const roster = [rosterEntry({ name: 'lead2', tier: 2 }), rosterEntry({ name: 'helper', tier: 2 }),
                  rosterEntry({ name: 'other', tier: 2 })];
  const comp = { lead: { kind: 'engine', name: 'lead2' }, members: ['helper'] };
  const instances = [inst('proj', { status: 'idle', run_id: null, composition: comp })];
  const c = await setupCase(instances, roster);
  await openPicker(c, 'proj', instances, roster);
  const html = c.instanceRowHtml('proj');
  const leadSelect = html.slice(html.indexOf('id="team-lead-proj"'));
  assert.ok(/value='[^']*"lead2"[^']*'\s+selected/.test(leadSelect), 'expected lead2 pre-selected, got: ' + leadSelect);
  // docs/spec.md issue #3: the lead's own engine is rendered DISABLED, not
  // omitted -- present in the markup, unchecked, with the disabled attribute.
  assert.ok(html.includes('team-mate-proj-lead2'), 'expected the lead\'s own checkbox present (disabled), got: ' + html);
  const lead2Checkbox = html.slice(html.indexOf('id="team-mate-proj-lead2"'), html.indexOf('>', html.indexOf('id="team-mate-proj-lead2"')));
  assert.ok(lead2Checkbox.includes('disabled'), 'expected the lead checkbox to carry the disabled attribute, got: ' + lead2Checkbox);
  assert.ok(!hasCheckedAttr(lead2Checkbox), 'the lead checkbox must never render checked, got: ' + lead2Checkbox);
  assert.ok(html.includes('team-mate-proj-helper'));
  assert.ok(html.includes('team-mate-proj-other'));
  // The saved composition can never itself contain lead-in-members
  // (server-side save_composition() only persists post-validation
  // compositions) -- regression check that the pre-populated picker never
  // shows the lead's own engine checked.
  assert.strictEqual(c.call('teamCompositionError', 'proj'), null);
});

test('changing Lead away from an engine previously checked as a teammate clears the stale membership (docs/spec.md issue #3)', async () => {
  const roster = [rosterEntry({ name: 'lead2', tier: 2 }), rosterEntry({ name: 'helper', tier: 2 })];
  const comp = { lead: { kind: 'engine', name: 'lead2' }, members: ['helper'] };
  const instances = [inst('proj', { status: 'idle', run_id: null, composition: comp })];
  const c = await setupCase(instances, roster);
  await openPicker(c, 'proj', instances, roster);

  // Check 'helper' as a teammate first, then change Lead to 'helper' --
  // reproduces the exact stale-Set scenario docs/spec.md traces.
  c.call('onTeamMateToggle', 'proj', 'helper', true);
  await tick();
  const sel = c.sandbox.document.getElementById('team-lead-proj');
  sel.value = JSON.stringify({ kind: 'engine', name: 'helper' });
  c.call('onTeamLeadChange', 'proj');
  await tick();

  // The bug (pre-fix): 'helper' would still be in teamPickerMembers even
  // though its checkbox is no longer rendered checked -- teamCompositionError
  // would incorrectly report "Lead cannot also be a teammate" for a state
  // invisible in the UI. The fix: the Set is actively cleared on lead change.
  assert.strictEqual(c.call('teamCompositionError', 'proj'), 'At least one teammate is required',
    'expected the stale membership to be cleared (only the separate empty-teammates rule should block here)');
});

test('rapid Lead switching clears only the newly-selected lead\'s own stale membership, never an unrelated engine\'s legitimate one', async () => {
  const roster = [rosterEntry({ name: 'e1', tier: 2 }), rosterEntry({ name: 'e2', tier: 2 }),
                  rosterEntry({ name: 'helper', tier: 2 })];
  const comp = { lead: null, members: [] };
  const instances = [inst('proj', { status: 'idle', run_id: null, composition: comp })];
  const c = await setupCase(instances, roster);
  await openPicker(c, 'proj', instances, roster);

  // 'helper' is checked once and never itself selected as Lead -- it is
  // the control that must stay untouched across every switch below.
  c.call('onTeamMateToggle', 'proj', 'helper', true);
  await tick();

  const sel = c.sandbox.document.getElementById('team-lead-proj');
  async function setLead(engineName) {
    sel.value = JSON.stringify({ kind: 'engine', name: engineName });
    c.call('onTeamLeadChange', 'proj');
    await waitForFetch(c, (f) => f.url === '/status');
    c.resolveFetch((f) => f.url === '/status', 200, statusWith(instances, roster));
    await tick();
    await tick();
  }
  // e1 (never checked) -> e2 (never checked) -> e1 again: rapid back-and-
  // forth between two engines, neither of which was ever a teammate here,
  // so each switch's Set-clearing step is a no-op -- proves it never
  // touches 'helper's own legitimate membership.
  await setLead('e1');
  await setLead('e2');
  await setLead('e1');

  assert.strictEqual(c.call('teamCompositionError', 'proj'), null,
    'a valid composition (lead=e1, helper checked) must not be blocked by the switching');
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('team-mate-proj-helper'));
  const helperCheckbox = html.slice(html.indexOf('id="team-mate-proj-helper"'), html.indexOf('>', html.indexOf('id="team-mate-proj-helper"')));
  assert.ok(hasCheckedAttr(helperCheckbox), 'helper\'s own legitimate membership must survive the lead switching, got: ' + helperCheckbox);
  assert.ok(!helperCheckbox.includes('disabled'), 'helper was never Lead -- must not render disabled, got: ' + helperCheckbox);
});

test('Lead cleared back to "Choose a lead..." makes every teammate checkbox selectable again (none disabled)', async () => {
  const roster = [rosterEntry({ name: 'lead2', tier: 2 }), rosterEntry({ name: 'helper', tier: 2 })];
  const comp = { lead: { kind: 'engine', name: 'lead2' }, members: ['helper'] };
  const instances = [inst('proj', { status: 'idle', run_id: null, composition: comp })];
  const c = await setupCase(instances, roster);
  await openPicker(c, 'proj', instances, roster);

  const sel = c.sandbox.document.getElementById('team-lead-proj');
  sel.value = '';
  c.call('onTeamLeadChange', 'proj');
  await waitForFetch(c, (f) => f.url === '/status');
  c.resolveFetch((f) => f.url === '/status', 200, statusWith(instances, roster));
  await tick();
  await tick();

  const html = c.instanceRowHtml('proj');
  const lead2Checkbox = html.slice(html.indexOf('id="team-mate-proj-lead2"'), html.indexOf('>', html.indexOf('id="team-mate-proj-lead2"')));
  assert.ok(!lead2Checkbox.includes('disabled'), 'no engine should render disabled once Lead is cleared, got: ' + lead2Checkbox);
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
  c.simulateTeamPageRender(instances);  // see its own doc comment -- Taiga #10
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
  c.simulateTeamPageRender(instances);  // see its own doc comment -- Taiga #10
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

// Past team branches panel (backlog item 13, docs/spec.md) -- resolves the
// project's own pending /team/branches fetch with `payload`, then drives an
// explicit further refresh() (rerenderRow()) to observe it rendered --
// unlike deliverTeamInbox() above, fetchTeamBranches() itself does NOT
// trigger its own refresh() (see that function's own doc comment in
// app/app.py for why), so there is no automatic cascade to wait on here.
async function deliverTeamBranches(c, name, instances, payload) {
  await waitForFetch(c, (f) => f.url === '/projects/' + name + '/team/branches');
  c.resolveFetch((f) => f.url === '/projects/' + name + '/team/branches', 200, payload);
  await tick();
  await rerenderRow(c, instances);
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
    // members (not composition.members) is the pill list's own source
    // (backlog item 21 part 2, docs/spec.md "Proposed approach" §4 --
    // renderTeamFeed() now reads the live team.members, not the stale
    // team.composition.members).
    status: 'running', run_id: 'run-aria-pressed', members: ['helper'],
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

// ─── Board-write escalation panel (backlog item 7 part 2, docs/spec.md §5 /
// docs/design.md) ──────────────────────────────────────────────────────

test('board_write set_status renders the from/to summary, Approve/Reject, no free-text field', async () => {
  const instances = [inst('proj', {
    status: 'blocked', run_id: 'run-sv', waiting_on_you: true, escalation_kind: 'board_write',
  })];
  const c = await setupCase(instances);
  await deliverTeamInbox(c, 'proj', 'run-sv', instances, {
    pending: true, run_id: 'run-sv', kind: 'board_write', verb: 'set_status', ref: 42,
    value: 'In progress', note: null, current_value: { status_id: 1, status_name: 'New' },
    proposed_at: '2026-08-14T12:00:00Z', subject: 'Implement auth system',
  });
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('Implement auth system'), 'expected the enriched subject, got: ' + html);
  assert.ok(html.includes('New'), 'expected the current status name, got: ' + html);
  assert.ok(html.includes('In progress'), 'expected the proposed status name, got: ' + html);
  assert.ok(html.includes("doTeamBoardResolve('proj', 'approve')"));
  assert.ok(html.includes("doTeamBoardResolve('proj', 'reject')"));
  assert.ok(!html.includes('escalation-other-proj'), 'board_write must never render the ask_user free-text field');
  assert.ok(!html.includes('team-escalation-form'), 'board_write must not render the ask_user options form');
});

test('board_write falls back to "#ref" when subject enrichment failed', async () => {
  const instances = [inst('proj', {
    status: 'blocked', run_id: 'run-noref', waiting_on_you: true, escalation_kind: 'board_write',
  })];
  const c = await setupCase(instances);
  await deliverTeamInbox(c, 'proj', 'run-noref', instances, {
    pending: true, run_id: 'run-noref', kind: 'board_write', verb: 'set_status', ref: 99,
    value: 'Done', note: null, current_value: { status_id: 2, status_name: 'In progress' },
    proposed_at: '2026-08-14T12:00:00Z',
    // no "subject" key -- Taiga was unreachable (docs/spec.md "Edge cases")
  });
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('#99'), 'expected the #ref fallback, got: ' + html);
});

test('board_write amend_description renders Current/Proposed comparison blocks', async () => {
  const instances = [inst('proj', {
    status: 'blocked', run_id: 'run-desc', waiting_on_you: true, escalation_kind: 'board_write',
  })];
  const c = await setupCase(instances);
  await deliverTeamInbox(c, 'proj', 'run-desc', instances, {
    pending: true, run_id: 'run-desc', kind: 'board_write', verb: 'amend_description', ref: 35,
    value: 'New description text', note: 'Updated per delegate feedback',
    current_value: { description: 'Old description text' },
    proposed_at: '2026-08-14T12:00:00Z', subject: 'Fix login redirect',
  });
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('Current:'));
  assert.ok(html.includes('Old description text'));
  assert.ok(html.includes('Proposed:'));
  assert.ok(html.includes('New description text'));
  assert.ok(html.includes('Lead') && html.includes('note') && html.includes('Updated per delegate feedback'),
    'expected the lead\'s note rendered, got: ' + html);
});

test('board_write append_comment renders only the proposed comment text, no current-value comparison', async () => {
  const instances = [inst('proj', {
    status: 'blocked', run_id: 'run-cmt', waiting_on_you: true, escalation_kind: 'board_write',
  })];
  const c = await setupCase(instances);
  await deliverTeamInbox(c, 'proj', 'run-cmt', instances, {
    pending: true, run_id: 'run-cmt', kind: 'board_write', verb: 'append_comment', ref: 67,
    value: 'Verified in staging, ready to deploy.', note: null, current_value: {},
    proposed_at: '2026-08-14T12:00:00Z', subject: 'Fix password reset',
  });
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('Verified in staging, ready to deploy.'));
  assert.ok(!html.includes('Current:'), 'append_comment must never render a current-value comparison block');
});

test('board_write waiting_on_you true but a fresh /team/inbox reports pending:false shows the distinct board_write "already resolved" copy', async () => {
  const instances = [inst('proj', {
    status: 'blocked', run_id: 'run-late-bw', waiting_on_you: true, escalation_kind: 'board_write',
  })];
  const c = await setupCase(instances);
  await deliverTeamInbox(c, 'proj', 'run-late-bw', instances, { pending: false });
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('already approved or rejected'), 'expected the board_write race copy, got: ' + html);
  assert.ok(!html.includes('already answered'), 'must not reuse the ask_user race copy');
});

test('doTeamBoardResolve("proj", "approve") dispatches POST /team/board-resolve with {action: "approve"}', async () => {
  const instances = [inst('proj', {
    status: 'blocked', run_id: 'run-appr', waiting_on_you: true, escalation_kind: 'board_write',
  })];
  const c = await setupCase(instances);
  await deliverTeamInbox(c, 'proj', 'run-appr', instances, {
    pending: true, run_id: 'run-appr', kind: 'board_write', verb: 'set_status', ref: 1,
    value: 'Done', note: null, current_value: { status_id: 1, status_name: 'New' },
    proposed_at: '2026-08-14T12:00:00Z',
  });
  c.call('doTeamBoardResolve', 'proj', 'approve');
  await tick();
  const f = c.pendingFetches.find((x) => x.url === '/projects/proj/team/board-resolve');
  assert.ok(f, 'expected a pending POST /projects/proj/team/board-resolve');
  assert.strictEqual(f.opts.method, 'POST');
  assert.strictEqual(JSON.parse(f.opts.body).action, 'approve');
  c.resolveFetch((x) => x.url === '/projects/proj/team/board-resolve', 200, { ok: true, run_id: 'run-appr' });
  await tick();
  await tick();
  const msg = c.msgEl('proj');
  assert.strictEqual(msg.textContent, '✓ Board write resolved');
  assert.ok(msg.className.includes('success'));
});

test('doTeamBoardResolve("proj", "reject") dispatches {action: "reject"}', async () => {
  const instances = [inst('proj', {
    status: 'blocked', run_id: 'run-rej', waiting_on_you: true, escalation_kind: 'board_write',
  })];
  const c = await setupCase(instances);
  await deliverTeamInbox(c, 'proj', 'run-rej', instances, {
    pending: true, run_id: 'run-rej', kind: 'board_write', verb: 'append_comment', ref: 1,
    value: 'a comment', note: null, current_value: {}, proposed_at: '2026-08-14T12:00:00Z',
  });
  c.call('doTeamBoardResolve', 'proj', 'reject');
  await tick();
  const f = c.pendingFetches.find((x) => x.url === '/projects/proj/team/board-resolve');
  assert.ok(f);
  assert.strictEqual(JSON.parse(f.opts.body).action, 'reject');
});

test('a 400 from board-resolve shows the server error inline, not the generic new-project field', async () => {
  const instances = [inst('proj', {
    status: 'blocked', run_id: 'run-400', waiting_on_you: true, escalation_kind: 'board_write',
  })];
  const c = await setupCase(instances);
  await deliverTeamInbox(c, 'proj', 'run-400', instances, {
    pending: true, run_id: 'run-400', kind: 'board_write', verb: 'set_status', ref: 1,
    value: 'Done', note: null, current_value: {}, proposed_at: '2026-08-14T12:00:00Z',
  });
  c.call('doTeamBoardResolve', 'proj', 'approve');
  await tick();
  c.resolveFetch((x) => x.url === '/projects/proj/team/board-resolve', 400,
    { error: 'no pending board write for this project' });
  await tick();
  await tick();
  const msg = c.msgEl('proj');
  assert.ok(msg.textContent.includes('no pending board write for this project'), 'got: ' + msg.textContent);
  assert.ok(msg.className.includes('error'));
});

test('a 428 mid-board-resolve shows the code overlay labeled for this team, and a correct retry resends the same action', async () => {
  const instances = [inst('proj', {
    status: 'blocked', run_id: 'run-bw428', waiting_on_you: true, escalation_kind: 'board_write',
  })];
  const c = await setupCase(instances);
  await deliverTeamInbox(c, 'proj', 'run-bw428', instances, {
    pending: true, run_id: 'run-bw428', kind: 'board_write', verb: 'set_status', ref: 1,
    value: 'Done', note: null, current_value: {}, proposed_at: '2026-08-14T12:00:00Z',
  });
  c.call('doTeamBoardResolve', 'proj', 'approve');
  await tick();
  c.resolveFetch((f) => f.url === '/projects/proj/team/board-resolve', 428, { error: 'totp_required' });
  await tick();
  await tick();
  const label = c.elements.get('code-overlay-label');
  assert.strictEqual(label.textContent, 'Resolving board write: proj');
  c.elements.get('action-code').value = '123456';
  c.call('submitActionCode');
  await tick();
  const retry = c.pendingFetches.find((f) => f.url === '/projects/proj/team/board-resolve');
  assert.ok(retry);
  const body = JSON.parse(retry.opts.body);
  assert.strictEqual(body.code, '123456');
  assert.strictEqual(body.action, 'approve', 'the retry must resend the SAME action the operator originally clicked');
  c.resolveFetch((f) => f.url === '/projects/proj/team/board-resolve', 200, { ok: true, run_id: 'run-bw428' });
  await tick();
  await tick();
  assert.strictEqual(c.msgEl('proj').textContent, '✓ Board write resolved');
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

// ─── Widened poll-boundary gate (backlog item 12 piece C, docs/spec.md) ───
//
// The reviewer confirmed (adversarially) a structurally identical gap while
// status === 'blocked': a trailing empty-meta lead tool_use with no next
// lead event, while a DIFFERENT in-flight round's ask_user escalation has
// already flipped status to 'blocked', used to fall through to 'finish' --
// the exact bug the 'running'-only gate above already existed to prevent,
// just for a status that narrower check didn't cover. Widened to the full
// non-terminal status set ('idle'/'running'/'blocked'); 'finished'/'error'
// remain genuinely terminal and still classify as 'finish'.

test('teamFeedEventKindClass classifies a trailing empty-meta lead tool_use as pending-classification for every non-terminal status', async () => {
  const instances = [inst('proj', { status: 'running', run_id: 'run-gate' })];
  const c = await setupCase(instances);
  const event = { ts: '2026-08-14T12:00:00Z', agent: 'lead', seq: 1, kind: 'tool_use', text: 'All done: summary text', meta: {} };
  for (const status of ['idle', 'running', 'blocked']) {
    assert.strictEqual(c.call('teamFeedEventKindClass', event, [], status), 'pending-classification',
      `expected pending-classification for status=${status}, the adversarial case being status=blocked (a DIFFERENT in-flight round's ask_user escalation)`);
  }
});

test('teamFeedEventKindClass still classifies a trailing empty-meta lead tool_use as finish for genuinely terminal statuses', async () => {
  const instances = [inst('proj', { status: 'running', run_id: 'run-gate-terminal' })];
  const c = await setupCase(instances);
  const event = { ts: '2026-08-14T12:00:00Z', agent: 'lead', seq: 1, kind: 'tool_use', text: 'All done: summary text', meta: {} };
  for (const status of ['finished', 'error']) {
    assert.strictEqual(c.call('teamFeedEventKindClass', event, [], status), 'finish',
      `expected finish for terminal status=${status} -- not a poll-boundary artifact here`);
  }
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

// ─── Board-write event feed classification (backlog item 7 part 2,
// docs/spec.md §6) -- both checked BEFORE the existing generic
// 'tool_result'+meta.resolved -> 'resolved' branch, since a
// board_write_resolved transcript entry's own meta ALSO sets
// meta.resolved: true on both approve and reject (part 1).

test('a board_write proposal (tool_use, meta.verb) renders the board-write-proposal class, not generic tool_use', async () => {
  const instances = [inst('proj', { status: 'running', run_id: 'run-bwp' })];
  const c = await setupCase(instances);
  const events = [
    { ts: '2026-08-14T12:00:00Z', agent: 'lead', seq: 1, kind: 'tool_use',
      text: 'board_write(set_status, ref=42)', meta: { verb: 'set_status', ref: 42 } },
  ];
  await openFeedAndDeliverEvents(c, 'proj', events);
  await rerenderRow(c, instances);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('kind-board-write-proposal'), 'expected the board-write-proposal class, got: ' + html);
  assert.ok(html.includes('board_write (set_status): ref #42'), 'got: ' + html);
});

test('a board_write approved-and-applied resolution renders the board-write-resolved class, not generic "resolved"/"Answer:"', async () => {
  const instances = [inst('proj', { status: 'running', run_id: 'run-bwr1' })];
  const c = await setupCase(instances);
  const events = [
    { ts: '2026-08-14T12:00:01Z', agent: 'lead', seq: 1, kind: 'tool_result',
      text: 'approved and applied', meta: { resolved: true, approved: true } },
  ];
  await openFeedAndDeliverEvents(c, 'proj', events);
  await rerenderRow(c, instances);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('kind-board-write-resolved'), 'expected the board-write-resolved class, got: ' + html);
  assert.ok(!html.includes('kind-resolved"'), 'must not ALSO/instead match the generic resolved class, got: ' + html);
  assert.ok(html.includes('Change approved and applied'), 'got: ' + html);
  assert.ok(!html.includes('Answer: approved and applied'), 'must not reuse the generic ask_user copy, got: ' + html);
});

test('a board_write approved-but-Taiga-rejected resolution renders the Taiga error detail', async () => {
  const instances = [inst('proj', { status: 'running', run_id: 'run-bwr2' })];
  const c = await setupCase(instances);
  const events = [
    { ts: '2026-08-14T12:00:01Z', agent: 'lead', seq: 1, kind: 'tool_result',
      text: 'approved but Taiga rejected the write: version mismatch',
      meta: { resolved: true, approved: true } },
  ];
  await openFeedAndDeliverEvents(c, 'proj', events);
  await rerenderRow(c, instances);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('kind-board-write-resolved'));
  assert.ok(html.includes('approved but Taiga rejected the write: version mismatch'), 'got: ' + html);
});

test('a board_write rejected-by-human resolution renders "rejected by human", using meta.approved === false', async () => {
  const instances = [inst('proj', { status: 'running', run_id: 'run-bwr3' })];
  const c = await setupCase(instances);
  const events = [
    { ts: '2026-08-14T12:00:01Z', agent: 'lead', seq: 1, kind: 'tool_result',
      text: 'rejected by human', meta: { resolved: true, approved: false } },
  ];
  await openFeedAndDeliverEvents(c, 'proj', events);
  await rerenderRow(c, instances);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('kind-board-write-resolved'));
  assert.ok(html.includes('Change rejected by human'), 'got: ' + html);
});

// Regression guard (docs/spec.md §6): an ask_user-shaped tool_result -- only
// meta.resolved, no meta.approved at all -- must still classify/render
// exactly as before, never mistaken for a board_write resolution.
test('an ask_user resolution (tool_result, only meta.resolved) still renders the generic "resolved"/"Answer:" class, unaffected by the new board_write checks', async () => {
  const instances = [inst('proj', { status: 'running', run_id: 'run-au-regress' })];
  const c = await setupCase(instances);
  const events = [
    { ts: '2026-08-14T12:00:01Z', agent: 'lead', seq: 1, kind: 'tool_result',
      text: 'Yes, proceed', meta: { resolved: true } },
  ];
  await openFeedAndDeliverEvents(c, 'proj', events);
  await rerenderRow(c, instances);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('kind-resolved'), 'expected the unchanged generic resolved class, got: ' + html);
  assert.ok(!html.includes('kind-board-write-resolved'), 'must not match the new, narrower board_write class');
  assert.ok(html.includes('Answer: Yes, proceed'), 'got: ' + html);
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

// ─── Chat-UI compose surface (backlog item 19 part 2, docs/spec.md /
// docs/design.md) ─────────────────────────────────────────────────────────

test('teamAcceptsInterject() matches exactly the statuses teams.interject() accepts server-side', async () => {
  const c = await setupCase([inst('proj', null)]);
  assert.strictEqual(c.call('teamAcceptsInterject', { status: 'running' }), true);
  assert.strictEqual(c.call('teamAcceptsInterject', { status: 'blocked', waiting_on_you: true }), true);
  assert.strictEqual(c.call('teamAcceptsInterject', { status: 'blocked', waiting_on_you: false }), false);
  assert.strictEqual(c.call('teamAcceptsInterject', { status: 'idle' }), false);
  assert.strictEqual(c.call('teamAcceptsInterject', { status: 'finished' }), false);
  assert.strictEqual(c.call('teamAcceptsInterject', { status: 'error' }), false);
  assert.strictEqual(c.call('teamAcceptsInterject', null), false);
});

test('running renders the compose box (textarea + Send button)', async () => {
  const c = await setupCase([inst('proj', { status: 'running', run_id: 'run-ij1' })]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('id="interject-proj"'), 'expected the compose textarea, got: ' + html);
  assert.ok(html.includes('id="interject-send-proj"'), 'expected the Send button, got: ' + html);
  assert.ok(html.includes('Send a message to the team…'), 'expected the neutral placeholder, got: ' + html);
});

test('blocked + waiting_on_you renders BOTH the escalation panel and the compose box, with the context-aware placeholder', async () => {
  const c = await setupCase([inst('proj', { status: 'blocked', run_id: 'run-ij2', waiting_on_you: true })]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('class="team-escalation"'), 'expected the escalation panel to still render, got: ' + html);
  assert.ok(html.includes('id="interject-proj"'), 'expected the compose box to render alongside it, got: ' + html);
  assert.ok(html.includes('this will not answer the pending question above'),
    'expected the context-aware placeholder, got: ' + html);
});

test('idle, finished, error, and blocked-without-waiting_on_you each omit the compose box', async () => {
  const cases = [
    inst('proj', null),
    inst('proj', { status: 'finished', run_id: 'run-1' }),
    inst('proj', { status: 'error', run_id: 'run-2' }),
    inst('proj', { status: 'blocked', run_id: 'run-3', waiting_on_you: false }),
  ];
  for (const one of cases) {
    const c = await setupCase([one]);
    const html = c.instanceRowHtml('proj');
    assert.ok(!html.includes('id="interject-proj"'),
      'expected no compose box for status=' + (one.team && one.team.status) + ', got: ' + html);
  }
});

test('an empty or whitespace-only draft keeps Send disabled; an over-2000-char draft disables Send and marks the counter over-limit', async () => {
  const c = await setupCase([inst('proj', { status: 'running', run_id: 'run-ij3' })]);
  const html1 = c.instanceRowHtml('proj');
  const sendHtml1 = html1.slice(html1.indexOf('id="interject-send-proj"'), html1.indexOf('</button>', html1.indexOf('id="interject-send-proj"')));
  assert.ok(sendHtml1.includes('disabled'), 'expected Send disabled with an empty draft, got: ' + sendHtml1);

  c.setTeamInterjectText('proj', 'x'.repeat(2001));
  c.call('updateTeamInterjectControls', 'proj');
  assert.strictEqual(c.interjectSendBtnEl('proj').disabled, true, 'expected Send disabled over the 2000-char limit');
  assert.ok(c.interjectCounterEl('proj').className.includes('over-limit'),
    'expected the counter to carry the over-limit class');

  c.setTeamInterjectText('proj', '   ');
  c.call('updateTeamInterjectControls', 'proj');
  assert.strictEqual(c.interjectSendBtnEl('proj').disabled, true, 'expected Send disabled for a whitespace-only draft');

  c.setTeamInterjectText('proj', 'a real message');
  c.call('updateTeamInterjectControls', 'proj');
  assert.strictEqual(c.interjectSendBtnEl('proj').disabled, false, 'expected Send enabled for a real, in-limit draft');
});

test('clicking Send dispatches POST /projects/<name>/team/interject with {text} and the 428 code-overlay label reads "Sending message: <name>"', async () => {
  const c = await setupCase([inst('proj', { status: 'running', run_id: 'run-ij4' })]);
  c.setTeamInterjectText('proj', '  please check the logs  ');
  c.interjectEl('proj').value = '  please check the logs  ';
  const p = c.call('doTeamInterject', 'proj');
  await tick();
  const f = c.pendingFetches.find((x) => x.url === '/projects/proj/team/interject');
  assert.ok(f, 'expected a pending POST /projects/proj/team/interject, got: ' +
    c.pendingFetches.map((x) => x.url).join(', '));
  assert.strictEqual(f.opts.method, 'POST');
  assert.strictEqual(JSON.parse(f.opts.body).text, 'please check the logs');
  c.resolveFetch((x) => x.url === '/projects/proj/team/interject', 428, { error: 'totp_required' });
  await p;
  await tick();
  const label = c.elements.get('code-overlay-label');
  assert.strictEqual(label.textContent, 'Sending message: proj');
  c.elements.get('action-code').value = '123456';
  const p2 = c.call('submitActionCode');
  await tick();
  const retry = c.pendingFetches.find((x) => x.url === '/projects/proj/team/interject');
  assert.ok(retry);
  const body = JSON.parse(retry.opts.body);
  assert.strictEqual(body.code, '123456');
  assert.strictEqual(body.text, 'please check the logs', 'the retry must resend the (still-current) trimmed draft');
  c.resolveFetch((x) => x.url === '/projects/proj/team/interject', 200, { ok: true, run_id: 'run-ij4' });
  await p2;
  await tick();
});

test('an empty/whitespace draft sends no request and shows a client-side error, mirroring doTeamResolve()', async () => {
  const c = await setupCase([inst('proj', { status: 'running', run_id: 'run-ij5' })]);
  c.setTeamInterjectText('proj', '   ');
  c.call('doTeamInterject', 'proj');
  await tick();
  assert.ok(!c.pendingFetches.some((f) => f.url === '/projects/proj/team/interject'),
    'no fetch should have been dispatched for an empty draft');
  assert.ok(c.msgEl('proj').textContent.includes('Message must be non-empty'), 'got: ' + c.msgEl('proj').textContent);
  assert.ok(c.msgEl('proj').className.includes('error'));
});

test('a successful send shows "Message sent", clears the textarea and the draft mirror, and re-disables Send', async () => {
  const c = await setupCase([inst('proj', { status: 'running', run_id: 'run-ij6' })]);
  c.setTeamInterjectText('proj', 'hello team');
  c.interjectEl('proj').value = 'hello team';
  c.call('updateTeamInterjectControls', 'proj');
  const p = c.call('doTeamInterject', 'proj');
  await tick();
  c.resolveFetch((f) => f.url === '/projects/proj/team/interject', 200, { ok: true, run_id: 'run-ij6' });
  await p;
  await tick();
  const msg = c.msgEl('proj');
  assert.strictEqual(msg.textContent, '✓ Message sent');
  assert.ok(msg.className.includes('success'));
  assert.strictEqual(c.interjectEl('proj').value, '', 'expected the textarea to be cleared');
  assert.strictEqual(c.interjectSendBtnEl('proj').disabled, true, 'expected Send to be disabled again after clearing');
});

test('a failed send preserves the draft text (textarea not cleared) and shows the server error', async () => {
  const c = await setupCase([inst('proj', { status: 'running', run_id: 'run-ij7' })]);
  c.setTeamInterjectText('proj', 'a message that will fail');
  c.interjectEl('proj').value = 'a message that will fail';
  const p = c.call('doTeamInterject', 'proj');
  await tick();
  c.resolveFetch((f) => f.url === '/projects/proj/team/interject', 400,
    { error: 'run run-ij7 is not accepting messages (status=finished)' });
  await p;
  await tick();
  const msg = c.msgEl('proj');
  assert.strictEqual(msg.textContent, '✕ Error: run run-ij7 is not accepting messages (status=finished)');
  assert.ok(msg.className.includes('error'));
  assert.strictEqual(c.interjectEl('proj').value, 'a message that will fail',
    'draft must be preserved on failure so the operator can retry without retyping');
});

test('a human-authored feed event classifies as human-message and renders with the kind-human-message row class', async () => {
  const instances = [inst('proj', { status: 'running', run_id: 'run-human1' })];
  const c = await setupCase(instances);
  const events = [
    { ts: '2026-08-14T12:00:00Z', agent: 'human', seq: 1, kind: 'message', text: 'please verify the schema', meta: {} },
  ];
  assert.strictEqual(c.call('teamFeedEventKindClass', events[0], [], 'running'), 'human-message');
  await openFeedAndDeliverEvents(c, 'proj', events);
  await rerenderRow(c, instances);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('kind-human-message'), 'expected the kind-human-message row class, got: ' + html);
  assert.ok(html.includes('please verify the schema'), 'expected the message text rendered, got: ' + html);
});

test('renderTeamFeed() lists filter pills in order All, lead, human, <member1>, ..., and clicking human filters via the existing generic filter', async () => {
  const instances = [inst('proj', {
    // members (not composition.members) is the pill list's own source --
    // see the aria-pressed test above for the same note.
    status: 'running', run_id: 'run-human2', members: ['helper'],
  })];
  const c = await setupCase(instances);
  const events = [
    { ts: '2026-08-14T12:00:00Z', agent: 'lead', seq: 1, kind: 'message', text: 'lead line', meta: {} },
    { ts: '2026-08-14T12:00:01Z', agent: 'human', seq: 1, kind: 'message', text: 'human line', meta: {} },
    { ts: '2026-08-14T12:00:02Z', agent: 'helper', seq: 1, kind: 'message', text: 'helper line', meta: {} },
  ];
  await openFeedAndDeliverEvents(c, 'proj', events);
  await rerenderRow(c, instances);
  let html = c.instanceRowHtml('proj');
  const allIdx = html.indexOf('>All<');
  const leadIdx = html.indexOf('>lead<');
  const humanIdx = html.indexOf('>human<');
  const helperIdx = html.indexOf('>helper<');
  assert.ok(allIdx !== -1 && leadIdx !== -1 && humanIdx !== -1 && helperIdx !== -1,
    'expected all four pills present, got: ' + html);
  assert.ok(allIdx < leadIdx && leadIdx < humanIdx && humanIdx < helperIdx,
    'expected pill order All, lead, human, helper, got: ' + html);

  c.call('setTeamFeedFilter', 'proj', 'human');
  await drainTriggeredRefresh(c, instances);
  html = c.instanceRowHtml('proj');
  assert.ok(html.includes('human line') && !html.includes('lead line') && !html.includes('helper line'),
    'expected only the human event via the existing generic agent filter, got: ' + html);
});

test('the human filter pill renders even before any interjection has been sent for a run (empty-state parity with other pills)', async () => {
  const c = await setupCase([inst('proj', { status: 'running', run_id: 'run-human3' })]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('>human<'), 'expected the human pill unconditionally present, got: ' + html);
});

test('an unsent draft is discarded once the status transitions to a compose-ineligible one on the next poll', async () => {
  const running = [inst('proj', { status: 'running', run_id: 'run-ij8' })];
  const c = await setupCase(running);
  c.setTeamInterjectText('proj', 'an unsent draft');
  const finished = [inst('proj', { status: 'finished', run_id: 'run-ij8' })];
  await rerenderRow(c, finished);
  const html = c.instanceRowHtml('proj');
  assert.ok(!html.includes('id="interject-proj"'), 'expected the compose box gone, got: ' + html);

  // A brand-new run later starting for the same project must not resurrect
  // the stale draft (docs/spec.md "Edge cases").
  const c2 = await setupCase([inst('proj', { status: 'running', run_id: 'run-ij8-new' })]);
  const html2 = c2.instanceRowHtml('proj');
  assert.ok(html2.includes('id="interject-proj"'));
  assert.ok(html2.includes('placeholder="Send a message to the team…"'),
    'expected a fresh, empty compose box with no stale draft content, got: ' + html2);
  assert.ok(!html2.includes('an unsent draft'), 'the stale draft must not reappear in the freshly rendered textarea');
});

test('a team stopping (going idle) clears the compose-box draft state', async () => {
  const running = [inst('proj', { status: 'running', run_id: 'run-ij9' })];
  const c = await setupCase(running);
  c.setTeamInterjectText('proj', 'draft before stop');
  const idle = [inst('proj', null)];
  await rerenderRow(c, idle);
  const html = c.instanceRowHtml('proj');
  assert.ok(!html.includes('id="interject-proj"'));
  // Re-running the same project must not resurrect the stale draft.
  const c2 = await setupCase([inst('proj', { status: 'running', run_id: 'run-ij9-new' })]);
  const html2 = c2.instanceRowHtml('proj');
  assert.ok(html2.includes('placeholder="Send a message to the team…"'),
    'expected a fresh, empty compose box, got: ' + html2);
});

// ─── "+" add-teammate control (backlog item 21 part 2, docs/spec.md /
// docs/design.md) ───────────────────────────────────────────────────────

test('a running team with members [codex] and roster [codex, aider, claude] (lead is a separate Ollama entry) shows a select with exactly aider and claude', async () => {
  const roster = [rosterEntry({ name: 'codex', tier: 2 }), rosterEntry({ name: 'aider', tier: 1 }),
                  rosterEntry({ name: 'claude', tier: 2 })];
  const instances = [inst('proj', {
    status: 'running', run_id: 'run-add1', members: ['codex'],
    lead: { kind: 'ollama', name: 'qwen3:8b' },
  })];
  const c = await setupCase(instances, roster);
  const html = c.instanceRowHtml('proj');
  const selectHtml = html.slice(html.indexOf('id="team-add-member-select-proj"'),
    html.indexOf('</select>', html.indexOf('id="team-add-member-select-proj"')));
  assert.ok(selectHtml.includes('value="aider"'), 'expected aider offered, got: ' + selectHtml);
  assert.ok(selectHtml.includes('value="claude"'), 'expected claude offered, got: ' + selectHtml);
  assert.ok(!selectHtml.includes('value="codex"'), 'codex is already a member, must not be offered');
});

test('excludes the current engine lead from the eligible options', async () => {
  const roster = [rosterEntry({ name: 'codex', tier: 2 }), rosterEntry({ name: 'aider', tier: 1 })];
  const instances = [inst('proj', {
    status: 'running', run_id: 'run-add2', members: [],
    lead: { kind: 'engine', name: 'codex' },
  })];
  const c = await setupCase(instances, roster);
  const html = c.instanceRowHtml('proj');
  const selectHtml = html.slice(html.indexOf('id="team-add-member-select-proj"'),
    html.indexOf('</select>', html.indexOf('id="team-add-member-select-proj"')));
  assert.ok(!selectHtml.includes('value="codex"'), 'the engine lead must never be offered as a teammate');
  assert.ok(selectHtml.includes('value="aider"'));
});

test('clicking + Add dispatches POST /projects/<name>/team/add-member with {agent}, and a 428 mid-flow resends the same agent', async () => {
  const roster = [rosterEntry({ name: 'aider', tier: 1 })];
  const instances = [inst('proj', { status: 'running', run_id: 'run-add3', members: [], lead: null })];
  const c = await setupCase(instances, roster);
  const sel = c.sandbox.document.getElementById('team-add-member-select-proj');
  sel.value = 'aider';
  const p = c.call('doTeamAddMember', 'proj');
  await tick();
  const f = c.pendingFetches.find((x) => x.url === '/projects/proj/team/add-member');
  assert.ok(f, 'expected a pending POST /projects/proj/team/add-member, got: ' +
    c.pendingFetches.map((x) => x.url).join(', '));
  assert.strictEqual(f.opts.method, 'POST');
  assert.strictEqual(JSON.parse(f.opts.body).agent, 'aider');
  c.resolveFetch((x) => x.url === '/projects/proj/team/add-member', 428, { error: 'totp_required' });
  await p;
  await tick();
  const label = c.elements.get('code-overlay-label');
  assert.strictEqual(label.textContent, 'Adding teammate: proj');
  c.elements.get('action-code').value = '123456';
  const p2 = c.call('submitActionCode');
  await tick();
  const retry = c.pendingFetches.find((x) => x.url === '/projects/proj/team/add-member');
  assert.ok(retry);
  const body = JSON.parse(retry.opts.body);
  assert.strictEqual(body.code, '123456');
  assert.strictEqual(body.agent, 'aider', 'the retry must resend the SAME agent originally selected');
  c.resolveFetch((x) => x.url === '/projects/proj/team/add-member', 200, { ok: true, agent: 'aider' });
  await p2;
  await tick();
});

test('a successful add shows the exact "will join at its next round" message, never "has joined"', async () => {
  const roster = [rosterEntry({ name: 'aider', tier: 1 })];
  const instances = [inst('proj', { status: 'running', run_id: 'run-add4', members: [], lead: null })];
  const c = await setupCase(instances, roster);
  c.sandbox.document.getElementById('team-add-member-select-proj').value = 'aider';
  const p = c.call('doTeamAddMember', 'proj');
  await tick();
  c.resolveFetch((f) => f.url === '/projects/proj/team/add-member', 200, { ok: true, agent: 'aider' });
  await p;
  await tick();
  const msg = c.msgEl('proj');
  assert.strictEqual(msg.textContent, "✓ 'aider' will join the team at its next round");
  assert.ok(msg.className.includes('success'));
  assert.ok(!msg.textContent.includes('has joined'));
});

test('a server-side 400 rejection shows the exact error message, select/button remain usable for a retry', async () => {
  const roster = [rosterEntry({ name: 'aider', tier: 1 })];
  const instances = [inst('proj', { status: 'running', run_id: 'run-add5', members: [], lead: null })];
  const c = await setupCase(instances, roster);
  c.sandbox.document.getElementById('team-add-member-select-proj').value = 'aider';
  const p = c.call('doTeamAddMember', 'proj');
  await tick();
  c.resolveFetch((f) => f.url === '/projects/proj/team/add-member', 400,
    { error: 'team already has the maximum of 6 teammates' });
  await p;
  await tick();
  const msg = c.msgEl('proj');
  assert.strictEqual(msg.textContent, '✕ Error: team already has the maximum of 6 teammates');
  assert.ok(msg.className.includes('error'));
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('id="team-add-member-select-proj"'), 'expected the select still rendered for a retry');
});

test('at TEAM_MAX_MEMBERS the control is disabled with the exact "at the maximum" reason, no select/button', async () => {
  const roster = Array.from({ length: 8 }, (_, i) => rosterEntry({ name: 'e' + i, tier: 1 }));
  const members = Array.from({ length: 6 }, (_, i) => 'e' + i); // TEAM_MAX_MEMBERS_CLIENT default is 6
  const instances = [inst('proj', { status: 'running', run_id: 'run-add6', members, lead: null })];
  const c = await setupCase(instances, roster);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('Team is at the maximum of 6 teammates.'), 'got: ' + html);
  assert.ok(!html.includes('id="team-add-member-select-proj"'), 'no select once at cap');
  assert.ok(!/<button[^>]*>\+ Add<\/button>/.test(html), 'no + Add button once at cap');
});

test('under the cap but every roster engine is already a member shows the distinct "no more roster engines" reason', async () => {
  const roster = [rosterEntry({ name: 'aider', tier: 1 }), rosterEntry({ name: 'claude', tier: 2 })];
  const instances = [inst('proj', {
    status: 'running', run_id: 'run-add7', members: ['aider', 'claude'], lead: null,
  })];
  const c = await setupCase(instances, roster);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('No more roster engines available to add.'), 'got: ' + html);
  assert.ok(!html.includes('Team is at the maximum of'),
    'must be the distinct no-eligible-engines reason, not the at-cap reason');
  assert.ok(!html.includes('id="team-add-member-select-proj"'));
});

test('blocked_ask_user and blocked_board_write show the control; escalated_max_rounds/finished/error/idle omit it', async () => {
  const roster = [rosterEntry({ name: 'aider', tier: 1 })];
  const shown = [
    inst('proj', { status: 'running', run_id: 'run-1', members: [], lead: null }),
    inst('proj', { status: 'blocked', run_id: 'run-2', waiting_on_you: true, members: [], lead: null }),
    inst('proj', {
      status: 'blocked', run_id: 'run-3', waiting_on_you: true, escalation_kind: 'board_write',
      members: [], lead: null,
    }),
  ];
  for (const one of shown) {
    const c = await setupCase([one], roster);
    const html = c.instanceRowHtml('proj');
    assert.ok(html.includes('id="team-add-member-select-proj"'),
      'expected the control for status=' + one.team.status + ', got: ' + html);
  }
  const hidden = [
    inst('proj', null),
    inst('proj', { status: 'finished', run_id: 'run-4' }),
    inst('proj', { status: 'error', run_id: 'run-5' }),
    inst('proj', { status: 'blocked', run_id: 'run-6', waiting_on_you: false }),
  ];
  for (const one of hidden) {
    const c = await setupCase([one], roster);
    const html = c.instanceRowHtml('proj');
    assert.ok(!html.includes('class="team-add-member"'),
      'expected no add-member control for status=' + (one.team && one.team.status) + ', got: ' + html);
  }
});

test('a member_joined feed event classifies as member-joined and renders "→ joined the team" with the kind-member-joined row class', async () => {
  const instances = [inst('proj', { status: 'running', run_id: 'run-add8', members: ['aider'], lead: null })];
  const c = await setupCase(instances, [rosterEntry({ name: 'aider', tier: 1 })]);
  const event = { ts: '2026-08-14T12:00:00Z', agent: 'aider', seq: 1, kind: 'member_joined', worktree: '/tmp/wt', meta: {} };
  assert.strictEqual(c.call('teamFeedEventKindClass', event, [], 'running'), 'member-joined');
  assert.strictEqual(c.call('teamFeedEventBody', event, [], 'running'), '→ joined the team');
  await openFeedAndDeliverEvents(c, 'proj', [event]);
  await rerenderRow(c, instances, [rosterEntry({ name: 'aider', tier: 1 })]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('kind-member-joined'), 'expected the kind-member-joined row class, got: ' + html);
  assert.ok(html.includes('→ joined the team'), 'expected the join text, got: ' + html);
});

test('a member_joined feed event\'s outer row carries an inline border-left-color matching the joined agent\'s own established color (post-review fix, test-review.md finding 1)', async () => {
  const instances = [inst('proj', { status: 'running', run_id: 'run-add9', members: ['aider', 'codex'], lead: null })];
  const c = await setupCase(instances, [rosterEntry({ name: 'aider', tier: 1 }), rosterEntry({ name: 'codex', tier: 1 })]);
  for (const agent of ['aider', 'codex']) {
    const color = c.call('teamAgentColor', agent);
    const event = { ts: '2026-08-14T12:00:00Z', agent, seq: 1, kind: 'member_joined', worktree: '/tmp/wt', meta: {} };
    const html = c.call('renderTeamFeedEvent', event, [], 'running');
    // The outer <div class="team-feed-event kind-member-joined"> itself must
    // carry the inline border-left-color -- CSS currentColor resolves
    // against the element it's declared on, not a descendant's inline
    // style, so setting color only on the nested .team-feed-agent span (as
    // before this fix) leaves the CSS rule's currentColor pointed at the
    // outer div's own (unset -> inherited/neutral) color instead.
    const outerDivMatch = html.match(/^<div class="team-feed-event kind-member-joined"([^>]*)>/);
    assert.ok(outerDivMatch, 'expected an outer kind-member-joined div, got: ' + html);
    assert.ok(outerDivMatch[1].includes('style="border-left-color:' + color + '"'),
      'expected the outer div for agent ' + agent + ' to carry border-left-color:' + color +
      ', got attrs: ' + outerDivMatch[1]);
    // The agent-name span's own color must still match too, so both the
    // text and the border accent agree on the same color for the same event.
    assert.ok(html.includes('<span class="team-feed-agent" style="color:' + color + '">'),
      'expected the agent-name span to still carry color:' + color + ', got: ' + html);
  }
});

// ─── Past team branches panel (backlog item 13) ────────────────────────

test('idle row fetches team branches once and shows a loading placeholder first', async () => {
  const c = await bootstrapCase([inst('proj', null)]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('Loading past team branches'), 'expected the loading placeholder, got: ' + html);
  assert.ok(c.pendingFetches.some((f) => f.url === '/projects/proj/team/branches'),
    'expected a /team/branches fetch to have been dispatched');
});

test('empty branch list renders "No past team branches"', async () => {
  const instances = [inst('proj', null)];
  const c = await bootstrapCase(instances);
  await deliverTeamBranches(c, 'proj', instances, []);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('No past team branches'), 'got: ' + html);
});

test('branch entries render name/short commit/subject/date, no action buttons', async () => {
  const instances = [inst('proj', null)];
  const c = await bootstrapCase(instances);
  const branches = [{
    branch: 'team-1700000000-abc123def456-claude', run_id: '1700000000-abc123def456',
    agent: 'claude', commit: 'deadbeefcafefeed1234567890abcdef12345678',
    subject: 'teammate commit', committer_date: '2026-08-01T10:00:00+00:00',
  }];
  await deliverTeamBranches(c, 'proj', instances, branches);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('team-1700000000-abc123def456-claude'), 'expected branch name, got: ' + html);
  assert.ok(html.includes('deadbee'), 'expected short (7-char) commit, got: ' + html);
  assert.ok(!html.includes('deadbeefcafefeed'), 'commit must be shortened, not the full hash, got: ' + html);
  assert.ok(html.includes('teammate commit'), 'expected commit subject, got: ' + html);
  assert.ok(html.includes('2026-08-01'), 'expected date, got: ' + html);
  assert.ok(!/<button[^>]*>/.test(html.slice(html.indexOf('team-branches'))),
    'no action buttons for the branches panel (list-only, per scope), got: ' + html);
});

test('a fetch failure (non-ok status) renders "Past team branches unavailable"', async () => {
  const instances = [inst('proj', null)];
  const c = await bootstrapCase(instances);
  await waitForFetch(c, (f) => f.url === '/projects/proj/team/branches');
  c.resolveFetch((f) => f.url === '/projects/proj/team/branches', 500, {});
  await tick();
  await rerenderRow(c, instances);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('Past team branches unavailable'), 'got: ' + html);
});

test('team branches are fetched only once per project, cached across later poll cycles', async () => {
  const instances = [inst('proj', null)];
  const c = await bootstrapCase(instances);
  await deliverTeamBranches(c, 'proj', instances, []);
  await rerenderRow(c, instances);
  assert.ok(!c.pendingFetches.some((f) => f.url === '/projects/proj/team/branches'),
    'must not re-fetch team branches on a later 4s poll cycle -- this data does not join that cycle');
});

test('a running team row also renders the past branches panel', async () => {
  const c = await setupCase([inst('proj', { status: 'running', run_id: 'run-b' })]);
  const html = c.instanceRowHtml('proj');
  assert.ok(html.includes('class="team-branches"'),
    'expected the branches panel to render for a running team too, got: ' + html);
});

// ─── Dedicated team chat page (Taiga #10, docs/spec.md / docs/design.md) ───
// Dashboard's compact teamRow() summary -- status badge + "Open team chat"
// link, no task textarea/picker/feed/escalation/interject inline, for every
// team status.

test('dashboard row: idle (no team yet) shows an Idle badge and an "Open team chat" link, no launcher', async () => {
  const c = await setupCase([inst('proj', null)]);
  const html = c.dashboardRowHtml('proj');
  assert.ok(html.includes('class="team-status status-idle"'), 'expected an idle status badge, got: ' + html);
  assert.ok(/>Idle</.test(html));
  assert.ok(html.includes('href="/team/proj"'), 'expected a link to the dedicated page, got: ' + html);
  assert.ok(/Open team chat/.test(html));
  assert.ok(!html.includes('team-textarea'), 'no inline task textarea on the dashboard anymore');
  assert.ok(!html.includes('doTeamStart'), 'no inline Start button on the dashboard anymore');
});

test('dashboard row: idle (team.status === "idle") renders the same compact badge, not the old launcher', async () => {
  const c = await setupCase([inst('proj', { status: 'idle', run_id: null })]);
  const html = c.dashboardRowHtml('proj');
  assert.ok(html.includes('class="team-status status-idle"'));
  assert.ok(!html.includes('team-textarea'));
  assert.ok(!html.includes('team-configure-row'), 'no inline composition picker toggle on the dashboard anymore');
});

test('dashboard row: running shows a Running badge and the link, no status strip/feed/interject inline', async () => {
  const c = await setupCase([inst('proj', { status: 'running', run_id: 'run-1' })]);
  const html = c.dashboardRowHtml('proj');
  assert.ok(html.includes('class="team-status status-running"'));
  assert.ok(/>Running</.test(html));
  assert.ok(html.includes('href="/team/proj"'));
  assert.ok(!html.includes('team-status-strip'), 'the full status strip must not render on the dashboard anymore');
  assert.ok(!html.includes('team-feed'), 'no inline event feed on the dashboard anymore');
  assert.ok(!html.includes('team-interject'), 'no inline interject compose box on the dashboard anymore');
  assert.ok(!html.includes('doTeamStop'), 'no inline Stop button on the dashboard anymore');
});

test('dashboard row: blocked (waiting_on_you) shows a Blocked badge, no escalation panel inline', async () => {
  const c = await setupCase([inst('proj', { status: 'blocked', run_id: 'run-1', waiting_on_you: true })]);
  const html = c.dashboardRowHtml('proj');
  assert.ok(html.includes('class="team-status status-blocked"'));
  assert.ok(/>Blocked</.test(html));
  assert.ok(!html.includes('team-escalation'), 'no inline escalation panel on the dashboard anymore -- must click through');
});

test('dashboard row: finished shows a Finished badge, no summary inline', async () => {
  const c = await setupCase([inst('proj', { status: 'finished', run_id: 'run-1', summary: 'All done.' })]);
  const html = c.dashboardRowHtml('proj');
  assert.ok(html.includes('class="team-status status-finished"'));
  assert.ok(/>Finished</.test(html));
  assert.ok(!html.includes('All done.'), 'the finished-run summary text must not render on the dashboard anymore');
});

test('dashboard row: error shows an Error badge', async () => {
  const c = await setupCase([inst('proj', { status: 'error', run_id: 'run-1' })]);
  const html = c.dashboardRowHtml('proj');
  assert.ok(html.includes('class="team-status status-error"'));
  assert.ok(/>Error</.test(html));
});

test('dashboard row: the "Open team chat" link URL-encodes the project name', async () => {
  const c = await setupCase([inst('a project/weird name', { status: 'running', run_id: 'run-1' })]);
  const html = c.dashboardRowHtml('a project/weird name');
  assert.ok(html.includes('href="/team/' + encodeURIComponent('a project/weird name') + '"'),
    'expected the href to reuse encodeURIComponent, got: ' + html);
});

// ─── renderTeamPage() -- the dedicated page's own entry point ─────────────

// renderTeamPage() always issues its OWN fresh fetch('/status') (it never
// reuses whatever setupCase()'s own bootstrap already resolved) -- every
// test below must resolve that second, independent fetch itself, exactly
// like doTeamStart()/doTeamAddMember()/etc.'s own single-POST tests already
// resolve their own dispatched fetch.
async function callRenderTeamPage(c, projectName, instances, roster) {
  const p = c.call('renderTeamPage', projectName);
  c.resolveFetch((f) => f.url === '/status', 200, statusWith(instances, roster));
  await p;
}

test('renderTeamPage(): a found project renders the full surface via the SAME shared sub-renderer functions ' +
  'the dashboard used to call directly (no forked duplicate)', async () => {
  const instances = [inst('proj', { status: 'running', run_id: 'run-1' })];
  const c = await setupCase(instances);
  // Spy on renderTeamStatusStrip by replacing it inside the sandbox's own
  // lexical scope (same vm.runInContext technique setTeamTaskText() already
  // establishes above) -- if renderTeamPage() still produces the strip's
  // real output, it can only be because it called through this same
  // (now-wrapped) function, not a parallel hand-copied implementation.
  vm.runInContext(
    'var __origStrip = renderTeamStatusStrip; var __stripCalls = 0; ' +
    'renderTeamStatusStrip = function(t) { __stripCalls++; return __origStrip(t); };',
    c.sandbox);
  await callRenderTeamPage(c, 'proj', instances);
  const calls = vm.runInContext('__stripCalls', c.sandbox);
  assert.strictEqual(calls, 1, 'expected renderTeamPage() to call the real, shared renderTeamStatusStrip()');
  const pageHtml = c.sandbox.document.getElementById('team-page').innerHTML;
  assert.ok(pageHtml.includes('team-status-strip'), 'expected the status strip to actually be mounted, got: ' + pageHtml);
  assert.ok(pageHtml.includes('status-running'));
});

test('renderTeamPage(): mounts the header, hides dashboard chrome, and shows #team-page', async () => {
  const instances = [inst('proj', { status: 'running', run_id: 'run-1' })];
  const c = await setupCase(instances);
  await callRenderTeamPage(c, 'proj', instances);
  const pageHtml = c.sandbox.document.getElementById('team-page').innerHTML;
  assert.ok(pageHtml.includes('team-page-header'), 'expected the header, got: ' + pageHtml);
  assert.ok(/ai-dev-switchboard/.test(pageHtml) && /proj/.test(pageHtml));
  assert.ok(c.sandbox.document.getElementById('rows').classList.contains('hidden-for-team-page'));
  assert.ok(c.sandbox.document.getElementById('team-page').classList.contains('active'));
  assert.strictEqual(c.sandbox.document.getElementById('page-title').style.display, 'none');
  assert.strictEqual(c.sandbox.document.getElementById('new-project-row').style.display, 'none');
});

test('renderTeamPage(): idle project renders the full idle launcher (textarea + Start), not blank/read-only', async () => {
  const instances = [inst('proj', null)];
  const c = await setupCase(instances);
  await callRenderTeamPage(c, 'proj', instances);
  const pageHtml = c.sandbox.document.getElementById('team-page').innerHTML;
  assert.ok(pageHtml.includes('class="team-textarea"'), 'expected the task textarea, got: ' + pageHtml);
  assert.ok(pageHtml.includes("doTeamStart('proj')"));
  assert.ok(pageHtml.includes('>Start team<'));
});

test('renderTeamPage(): blocked + waiting_on_you renders the escalation panel', async () => {
  const instances = [inst('proj', { status: 'blocked', run_id: 'run-1', waiting_on_you: true })];
  const c = await setupCase(instances);
  await callRenderTeamPage(c, 'proj', instances);
  const pageHtml = c.sandbox.document.getElementById('team-page').innerHTML;
  assert.ok(pageHtml.includes('team-escalation'), 'expected the escalation panel to render, got: ' + pageHtml);
});

test('renderTeamPage(): unknown project renders a clear "Unknown project" message with a link back, no exception', async () => {
  const instances = [inst('other-proj', { status: 'running', run_id: 'run-1' })];
  const c = await setupCase(instances);
  await callRenderTeamPage(c, 'nonexistent-proj', instances);
  const pageHtml = c.sandbox.document.getElementById('team-page').innerHTML;
  assert.ok(pageHtml.includes('team-page-not-found'), 'expected the not-found panel, got: ' + pageHtml);
  assert.ok(/Unknown project/.test(pageHtml) && /nonexistent-proj/.test(pageHtml));
  assert.ok(pageHtml.includes('goToDashboard()'), 'expected a way back to the dashboard, got: ' + pageHtml);
  assert.ok(c.sandbox.document.getElementById('rows').classList.contains('hidden-for-team-page'));
});

test('renderTeamPage(): a 401 from /status shows the login overlay, same as refresh()', async () => {
  const c = createCase();
  // Drain the auto-bootstrap refresh() call this default ('/') location
  // triggers at load, same as bootstrapCase() does for every other test.
  c.resolveFetch((f) => f.url === '/status', 200, statusWith([]));
  await tick();
  await tick();
  const p = c.call('renderTeamPage', 'proj');
  c.resolveFetch((f) => f.url === '/status', 401, { error: 'not authenticated' });
  await p;
  assert.ok(c.sandbox.document.getElementById('overlay').classList.contains('show'),
    'expected the login overlay to be shown on a 401, same as unauthenticated access to "/"');
});

test('the back-link navigates to the dashboard via a real navigation, not an in-page switch', async () => {
  const c = await setupCase([inst('proj', { status: 'running', run_id: 'run-1' })]);
  c.call('goToDashboard');
  assert.strictEqual(c.sandbox.location.href, '/');
});

// ─── Client-side router: dispatches /team/<project> to renderTeamPage() ───

test('router: location.pathname matching /team/<name> calls renderTeamPage(), not refresh(), at load', async () => {
  const c = createCase('/team/proj');
  // Whichever function the router picked, it hits the same /status endpoint
  // either way -- draining it and inspecting which container got populated
  // is what actually distinguishes renderTeamPage() from refresh() here.
  c.resolveFetch((f) => f.url === '/status', 200, statusWith([inst('proj', { status: 'running', run_id: 'run-1' })]));
  await tick();
  await tick();
  const pageHtml = c.sandbox.document.getElementById('team-page').innerHTML;
  assert.ok(pageHtml.includes('team-status-strip'), 'expected renderTeamPage() to have populated #team-page, got: ' + pageHtml);
  assert.strictEqual(c.sandbox.document.getElementById('rows').innerHTML, '',
    'refresh() must not also have run and populated #rows');
});

test('router: a URL-encoded project name in the path is decoded before matching /status instances', async () => {
  const c = createCase('/team/' + encodeURIComponent('a project/weird name'));
  c.resolveFetch((f) => f.url === '/status', 200,
    statusWith([inst('a project/weird name', { status: 'running', run_id: 'run-1' })]));
  await tick();
  await tick();
  const pageHtml = c.sandbox.document.getElementById('team-page').innerHTML;
  assert.ok(!pageHtml.includes('team-page-not-found'), 'expected the decoded name to match the found instance, got: ' + pageHtml);
  assert.ok(pageHtml.includes('team-status-strip'));
});

test('router: location.pathname not matching /team/... falls back to the normal dashboard refresh() poll', async () => {
  const c = createCase('/');
  c.resolveFetch((f) => f.url === '/status', 200, statusWith([inst('proj', { status: 'running', run_id: 'run-1' })]));
  await tick();
  await tick();
  assert.ok(c.sandbox.document.getElementById('rows').innerHTML.length > 0,
    'expected refresh() to have populated #rows');
  assert.strictEqual(c.sandbox.document.getElementById('team-page').innerHTML, '',
    'renderTeamPage() must not also have run and populated #team-page');
});

// refreshCurrentView() (Taiga #10) -- action handlers that used to just call
// refresh() to reflect their own state change immediately (composition
// picker, escalation option, feed toggle/filter, ...) must re-render
// whichever view is actually on screen. On the team page, refresh() alone
// would leave #team-page stale until the next 4s tick (it only ever
// touches the hidden #rows) -- these two regression tests lock in the fix.
test('refreshCurrentView(): toggling the composition picker on the team page re-renders #team-page, not #rows', async () => {
  const roster = [rosterEntry({ name: 'lead2', tier: 2 }), rosterEntry({ name: 'helper', tier: 2 })];
  const comp = { lead: { kind: 'engine', name: 'lead2' }, members: ['helper'] };
  const instances = [inst('proj', { status: 'idle', run_id: null, composition: comp })];
  const c = createCase('/team/proj');
  c.resolveFetch((f) => f.url === '/status', 200, statusWith(instances, roster));
  await tick();
  await tick();
  assert.ok(!c.sandbox.document.getElementById('team-page').innerHTML.includes('id="team-lead-proj"'),
    'picker should not be open yet');

  c.call('toggleTeamPicker', 'proj');
  await waitForFetch(c, (f) => f.url === '/projects/proj/team/grounding');
  c.resolveFetch((f) => f.url === '/projects/proj/team/grounding', 200, { files: [], skipped: [] });
  // refreshCurrentView() -> renderTeamPage() issues its OWN fresh /status
  // fetch (same as every other renderTeamPage() test in this file).
  await waitForFetch(c, (f) => f.url === '/status');
  c.resolveFetch((f) => f.url === '/status', 200, statusWith(instances, roster));
  await tick();
  await tick();
  await tick();

  const pageHtml = c.sandbox.document.getElementById('team-page').innerHTML;
  assert.ok(pageHtml.includes('id="team-lead-proj"'),
    'expected the picker to now be rendered into #team-page, got: ' + pageHtml);
  assert.strictEqual(c.sandbox.document.getElementById('rows').innerHTML, '',
    'refresh() must never have run -- #rows should stay untouched on the team page');
});

test('refreshCurrentView(): logging in from the team page\'s shared overlay lands on the team page, not the dashboard', async () => {
  const c = createCase('/team/proj');
  // Drain the router's own initial (unauthenticated) renderTeamPage() call.
  c.resolveFetch((f) => f.url === '/status', 401, { error: 'not authenticated' });
  await tick();
  await tick();
  assert.ok(c.sandbox.document.getElementById('overlay').classList.contains('show'));

  c.sandbox.document.getElementById('login-user').value = 'testuser';
  c.sandbox.document.getElementById('login-pass').value = 'testpass';
  const p = c.call('login');
  await waitForFetch(c, (f) => f.url === '/login');
  c.resolveFetch((f) => f.url === '/login', 200, {});
  await p;
  await waitForFetch(c, (f) => f.url === '/status');
  c.resolveFetch((f) => f.url === '/status', 200,
    statusWith([inst('proj', { status: 'running', run_id: 'run-1' })]));
  await tick();
  await tick();

  assert.ok(c.sandbox.document.getElementById('team-page').innerHTML.includes('team-status-strip'),
    'expected login() to land back on the team page via refreshCurrentView(), not the dashboard');
  assert.strictEqual(c.sandbox.document.getElementById('rows').innerHTML, '');
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

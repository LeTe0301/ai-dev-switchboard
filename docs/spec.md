# Spec: follow-up fixes — items 8, 12 (piece C), 20 (broader audit)

## Summary
Three independent, non-blocking follow-ups recorded in `docs/BACKLOG.md`,
all found by the reviewer during earlier cycles and deliberately deferred
rather than fixed in-cycle. All three are fully diagnosed already — this
spec is mechanical, not exploratory (except item 20's audit step, which is
inherently investigative). Bundled into one cycle since all three are
small, same-file (`app/app.py`), non-overlapping edits — mirrors item 12's
own PR #4 precedent of bundling A/B/C sub-fixes together.

## Orchestrator note
No product-manager/ux-designer dispatch — each fix's shape was already
fully diagnosed by a reviewer in a prior cycle (items 8/12) or is a
mechanical CSS-value swap following an already-shipped pattern (item 20,
PR #12). Matches this project's own "skip full triage for a
fully-diagnosed follow-up" precedent.

---

## Piece 1 — Item 8: AI reviewer lock keyed on `(pr_key, episode)`

### Problem (from `docs/BACKLOG.md` item 8)
`_ai_reviewer_pr_lock_for()` keys the in-flight lock on `pr_key` alone. If
a human removes and re-adds the "ready for review" label while the
*previous* episode's review thread is still running, `_ai_reviewer_poll_repo()`
correctly detects the label-absent→present edge as a fresh trigger and
writes fresh state — but `_ai_reviewer_review_bg()`'s lock acquisition then
fails (the stale thread still holds the `pr_key`-only lock), so the new
review is silently dropped, no error surfaced. When the stale thread later
finishes, its completion write (success or failure) overwrites the state
the new episode's trigger edge just wrote, making the new episode look
already-handled when it never actually ran.

### Root cause
No episode identity exists anywhere — state is keyed purely by `pr_key`,
so there's no way to tell "this completion belongs to episode N" from "a
newer episode M > N is now current."

### Fix
Add a persisted `episode: int` field to the per-PR state entry (absent/old
entries default to `0` via `.get("episode", 0)`, no migration needed).
Increment it exactly at the trigger edge (the one place a *new* review
cycle begins). Thread `episode` through the whole review-run call chain so
a completion write only applies if it's still for the current episode, and
key the in-flight lock on `(pr_key, episode)` instead of `pr_key` alone so
a new episode is never silently dropped just because a stale one is still
running.

**`_save_ai_reviewer_state_entry`** (`app/app.py:1369`): add an `episode`
keyword param, store it in the entry:
```python
def _save_ai_reviewer_state_entry(pr_key: str, *, label_present: bool, attempts: int,
                                  reviewed_at, last_error, episode: int) -> None:
    with _ai_reviewer_state_lock:
        s = _load_ai_reviewer_state()
        s[pr_key] = {"label_present": label_present, "attempts": attempts,
                    "reviewed_at": reviewed_at, "last_error": last_error,
                    "episode": episode}
        ...  # unchanged below
```

**`_ai_reviewer_record_failure`** (`app/app.py:1382`): add `episode: int`
param; only write if the state's current episode still matches (a stale
thread whose episode has since been superseded is a silent no-op, not an
error — the newer episode's own state is authoritative):
```python
def _ai_reviewer_record_failure(pr_key: str, message: str, episode: int) -> None:
    prev = _load_ai_reviewer_state().get(pr_key, {})
    if prev.get("episode", 0) != episode:
        return  # superseded by a newer episode; this completion is stale
    _save_ai_reviewer_state_entry(
        pr_key, label_present=True, attempts=prev.get("attempts", 0) + 1,
        reviewed_at=prev.get("reviewed_at"), last_error=message, episode=episode)
```

**`_ai_reviewer_review_run`** (`app/app.py:1439`): add an `episode: int`
param; pass it to every `_ai_reviewer_record_failure(...)` call site inside
it (9 call sites — mechanical, add `, episode` to each); guard the final
success write the same way:
```python
def _ai_reviewer_review_run(host: str, owner_repo: str, entry: dict, pr: dict, episode: int) -> None:
    ...
    # every _ai_reviewer_record_failure(pr_key, "...") call becomes
    # _ai_reviewer_record_failure(pr_key, "...", episode)
    ...
    # final success save, guarded the same way as record_failure:
    prev = _load_ai_reviewer_state().get(pr_key, {})
    if prev.get("episode", 0) == episode:
        _save_ai_reviewer_state_entry(pr_key, label_present=True, attempts=0,
                                      reviewed_at=teams._now_iso(), last_error=None,
                                      episode=episode)
    except Exception as e:
        _ai_reviewer_record_failure(pr_key, f"{type(e).__name__}: {e}", episode)
```

**`_ai_reviewer_review_bg`** (`app/app.py:1534`): add `episode: int` param;
key the lock on `(pr_key, episode)`; clean up the lock entry once the
thread finishes (bounded growth — otherwise `_ai_reviewer_pr_locks` grows
one entry per label-toggle cycle forever, not just once per PR):
```python
def _ai_reviewer_review_bg(host: str, owner_repo: str, entry: dict, pr: dict, episode: int) -> None:
    pr_key = _ai_reviewer_pr_key(host, owner_repo, pr.get("number"))
    lock_key = (pr_key, episode)
    lock = _ai_reviewer_pr_lock_for(lock_key)
    if not lock.acquire(blocking=False):
        return

    def _run():
        try:
            _ai_reviewer_review_run(host, owner_repo, entry, pr, episode)
        finally:
            lock.release()
            with _ai_reviewer_pr_locks_guard:
                _ai_reviewer_pr_locks.pop(lock_key, None)

    threading.Thread(target=_run, daemon=True).start()
```
`_ai_reviewer_pr_lock_for()` itself (`app/app.py:1403`) needs no change —
it's already generic over its key type (`dict.get`/`dict[key] = ...` work
identically for a `str` or `tuple` key); only its type hint
(`pr_key: str` → `key`) needs updating for accuracy.

**`_ai_reviewer_poll_repo`** (`app/app.py:1555`): the trigger edge
(currently line ~1610-1618) increments episode and passes it down; the
retry branch (currently line ~1620-1622) passes the *current* (unchanged)
episode; the label-absent branch (currently line ~1601-1608) carries the
episode forward unchanged (it's not starting a new one, just arming the
next add):
```python
        if not label_present:
            if was_present or pr_key not in state:
                _save_ai_reviewer_state_entry(
                    pr_key, label_present=False, attempts=0,
                    reviewed_at=prev.get("reviewed_at"), last_error=None,
                    episode=prev.get("episode", 0))
            continue

        if not was_present:
            episode = prev.get("episode", 0) + 1
            _save_ai_reviewer_state_entry(
                pr_key, label_present=True, attempts=prev.get("attempts", 0),
                reviewed_at=prev.get("reviewed_at"), last_error=None, episode=episode)
            _ai_reviewer_review_bg(host, owner_repo, entry, pr, episode)
            continue

        attempts = prev.get("attempts", 0)
        if prev.get("last_error") is not None and attempts < AI_REVIEWER_MAX_ATTEMPTS:
            _ai_reviewer_review_bg(host, owner_repo, entry, pr, prev.get("episode", 0))
```

### Acceptance criteria
- [ ] A label removed-and-re-added while the previous episode's review
      thread is still in-flight results in a NEW thread actually being
      dispatched (not silently dropped) — verifiable with a harness that
      starts a slow fake "in-flight" thread holding the old episode's lock,
      then calls `_ai_reviewer_poll_repo` again with a fresh label-absent
      then label-present pair, and asserts a second thread was started.
- [ ] When the stale (old-episode) thread eventually completes (success or
      failure), its state write is a no-op — the state file's `episode`
      field still reflects whichever episode is actually current, and
      `reviewed_at`/`attempts`/`last_error` reflect the NEW episode's own
      outcome, not the stale one's.
- [ ] A normal, non-racing single-episode review (today's existing
      behavior) is unaffected: trigger → review → success/failure write,
      exactly as before, just with `episode` now present in the persisted
      entry.
- [ ] `_ai_reviewer_pr_locks` does not grow by one entry per label-toggle
      cycle forever — a finished thread's `(pr_key, episode)` lock entry is
      removed.
- [ ] Pre-existing state-file entries with no `episode` key (from before
      this fix) are read correctly (`.get("episode", 0)`), no crash, no
      forced re-review.

---

## Piece 2 — Item 12 piece C: widen the transient poll-boundary gate

### Problem (from `docs/BACKLOG.md` item 12)
`teamFeedEventKindClass()` (`app/app.py:3789`)'s transient-classification
branch, line ~3833, only guards against the poll-boundary
misclassification-as-'finish' bug while `status === 'running'`. The
reviewer confirmed (adversarially, not just by reading) a structurally
identical gap while `status === 'blocked'`: a trailing empty-meta
`tool_use` from the lead with no paired `tool_result` yet, while a
*different* in-flight round's `ask_user` escalation has already flipped
status to `blocked`, still falls through to `'finish'` — the exact bug
this branch exists to prevent, just for a status this narrow `'running'`
check didn't cover.

### Fix
Backend-confirmed status vocabulary the frontend actually receives
(`app/app.py:5731-5735`'s `/status` mapping): `"idle"`, `"running"`,
`"blocked"`, `"finished"`, `"error"` — exactly five values, no others ever
reach this JS. `"finished"`/`"error"` are the only genuinely terminal ones
(no further events will ever arrive); `"idle"`/`"running"`/`"blocked"` are
all non-terminal. Widen the gate to the non-terminal set, per the
backlog's own suggested shape:

```javascript
    if (!next && e.agent === 'lead' && status !== 'finished' && status !== 'error') return 'pending-classification';
```
(single-line change at `app/app.py:3833`; update the explanatory comment
immediately above it, currently written only in terms of `'running'`, to
describe the widened non-terminal set instead of just one status value).

### Acceptance criteria
- [ ] Given `status === 'blocked'`, a trailing empty-meta lead `tool_use`
      event with no next lead event classifies as `'pending-classification'`,
      not `'finish'` — the exact case the reviewer's adversarial test in
      part 2's review already reproduced.
- [ ] `status === 'running'` behavior is unchanged (already covered before
      this fix, must remain covered after).
- [ ] `status === 'idle'` also now gets the transient-gate treatment
      (previously fell through to `'finish'` same as `'blocked'` did) —
      no acceptance criterion in the original bug report calls this out
      specifically, but it's the same class of gap and the widened
      condition covers it for free; note this in `docs/implementation.md`
      as a bonus, not a scope expansion requiring its own justification.
- [ ] `status === 'finished'` / `status === 'error'` still classify a
      trailing empty-meta `tool_use` as `'finish'`, unchanged (these are
      the genuinely terminal cases where "assume finish" is correct, not
      a poll-boundary artifact).
- [ ] `tests/test_team_frontend.js` (or wherever this function's existing
      coverage lives) passes; add a case for the newly-covered `'blocked'`
      scenario if no existing test already covers it.

---

## Piece 3 — Item 20: broader button/control contrast audit

### Problem (from `docs/BACKLOG.md` item 20, "Open for the future session")
PR #12 fixed `.team-btn`/`.deploy-btn`'s specific white-on-`#34c759` AA
failure but did not audit whether other button/control color pairings in
`app/app.py`'s CSS have the same undetected drift. This piece is that
audit.

### Approach (inherently investigative — developer executes, not
pre-computed here)
1. Extract every CSS rule in `app/app.py`'s `<style>` block that sets both
   a `color` and a `background`/`background-color` on the same selector
   (or a selector whose ancestor sets the background — check computed
   pairs, not just same-rule pairs) for interactive controls (buttons,
   pills, badges, status strips) specifically — not body text/links,
   which is a much larger surface and not what item 20 was scoped to
   originally (`.team-btn` was a *button*).
2. For each pair, compute the real WCAG contrast ratio from the actual hex
   values (same method PR #12 used to independently recompute rather than
   trust `docs/design.md`'s claimed numbers, which were wrong twice
   already for `.team-btn` — do not trust any existing in-repo comment
   claiming a ratio without recomputing it).
3. Flag any pairing under 4.5:1 (normal text) / 3:1 (large-or-bold text,
   ~18.66px+ or ~14.66px+bold) as a failure.
4. Fix each failure found using PR #12's own established pattern: match
   text color to an already-passing pairing used elsewhere for the same
   background color in this file (e.g. dark `#111` text on light/saturated
   backgrounds, matching `.pill.active`/wizard buttons/the already-fixed
   `.team-btn`) rather than inventing a new color. If a background has no
   existing passing-text precedent in the file, darken/lighten per PR #12's
   own reasoning (recompute, don't guess) and note the new pairing's ratio
   in a comment the same way `.team-btn`'s fix did.
5. If zero additional failures are found, that is a valid, complete
   outcome for this piece — say so plainly in `docs/implementation.md`
   with the full list of pairs checked and their ratios, not just "looked
   fine."

### Non-goals
- Body text, links, and non-interactive text colors — out of scope, this
  audit is about controls (the class of thing `.team-btn` was), matching
  item 20's own backlog framing ("other button/control color pairings").
- Any pairing already covered by PR #12 (`.team-btn`/`.deploy-btn`) —
  already fixed, not re-touched here.

### Acceptance criteria
- [ ] `docs/implementation.md` lists every interactive-control color pair
      checked and its computed ratio (pass or fail), not just the ones
      that failed.
- [ ] Every pairing under the applicable WCAG AA threshold is fixed using
      an existing in-file passing precedent (or, failing that, a freshly
      computed and stated ratio), and no fix introduces a new failure
      elsewhere (e.g. a hover/disabled/focus variant using a different
      background with the same text color).
- [ ] No change to `.team-btn`/`.deploy-btn` itself (already correct,
      already shipped).

---

## Affected areas
`app/app.py` only, three non-overlapping regions: the AI-reviewer poll/lock
block (~1345-1626), `teamFeedEventKindClass()` (~3789-3838), and whatever
CSS rules item 20's audit finds (scoped to interactive-control selectors
in the `<style>` block).

## Risk / rollback notes
Piece 1 is the highest-risk of the three (concurrency-sensitive, threading
changes) — mitigated by the acceptance criteria's explicit race-simulation
test requirement. Pieces 2 and 3 are low-risk, single-value/single-line
changes. All three are independently revertable (no shared code path
between them).

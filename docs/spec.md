# Spec: BACKLOG item 20 — `.team-btn`/`.deploy-btn` WCAG AA contrast fix

Orchestrator-authored (not a full product-manager dispatch) per the
Entwicklung workflow's right-sizing rule 1: this is a mechanical CSS
value fix, already fully diagnosed by two independent reviewer
confirmations (items 16 and 19 part 2's reviews) and root-caused by the
orchestrator before writing this spec. No architecture or design
judgment call remains.

## Problem

`app/app.py`'s shared CSS rule `.deploy-btn, .team-btn { background:
#34c759; color: #fff; ... }` pairs white text on this green background,
which computes to **≈2.2:1** contrast — well under WCAG AA's 4.5:1
minimum for normal text. `docs/design.md` has repeatedly (at least twice,
across separate ux-designer dispatches) claimed this pairing passes AA at
figures like 5.05:1/9.15:1, both wrong when recomputed from the actual
hex values.

## Root cause and fix

This exact green (`#34c759`) is used elsewhere in the same file
(`.pill.active`, `.wizard-check-row.pill-choice:has(input:checked)`, the
upload-wizard's `.wizard-actions .primary`, the deploy-toggle overlay
buttons) paired with **dark** text (`color: #111`), which computes to
**8.51:1** — comfortably passing AA. `.deploy-btn`/`.team-btn` are the
only two selectors in this file pairing this green with white text
instead. The fix is to change `.deploy-btn, .team-btn`'s `color: #fff` to
`color: #111`, matching the codebase's own already-passing pattern for
this exact background color — no new color value needed, single shared
rule, fixes every call site at once (team start/stop/resolve/board-
resolve/interject buttons, deploy button).

## Changes

- `app/app.py`: `.deploy-btn, .team-btn` rule — `color: #fff` → `color:
  #111`.
- `docs/design.md`: correct the two (at least) known-wrong contrast
  claims for this pairing to note the fix and the real numbers, so a
  future design pass doesn't inherit a third wrong claim.
- No JS, route, or test-*logic* changes — this is a visual-only fix.
  Existing frontend tests that assert on button text/behavior should be
  unaffected; if any test asserts a literal `color:#fff` string for these
  selectors, update it to match.

## Acceptance criteria

1. `.deploy-btn, .team-btn`'s text/background pairing computes to at
   least 4.5:1 contrast (verify with a real contrast calculation, not a
   visual guess).
2. No existing test breaks; if a test hardcodes the old white-text
   assertion for these buttons, it's updated to match the new color.
3. `docs/design.md`'s known-wrong contrast claims for this pairing are
   corrected.

## Open questions

None — this is a confirmed, root-caused, single-line CSS fix.

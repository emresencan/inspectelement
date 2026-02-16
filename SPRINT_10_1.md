# Sprint 10.1 — Inspect Stability + Locator Quality Recovery

## What Changed
- Added click target refinement (`elementsFromPoint`) to avoid generic wrapper/container selections:
  - Scores stack nodes and prefers actionable/link/button targets.
  - Refines wrapper hits to nearest meaningful clickable child.
  - Logs raw vs refined target details and reason to `~/.inspectelement/ui.log`.
- Fixed inspect repeatability with a robust capture guard in managed browser flow:
  - Added `_capture_busy` lock in `BrowserManager`.
  - Added start/end/skip-busy logs and exception stack logging to `~/.inspectelement/ui.log`.
  - Guaranteed busy reset via `finally`.
- Improved coordinate capture accuracy:
  - Embedded payload now includes `devicePixelRatio`.
  - Managed capture applies viewport mapping + DPR compensation + URL/scroll sync.
- Restored and upgraded locator candidate heuristics:
  - Added richer summary extraction (`value`, `href`, `title`, `type`, outerHTML snippet, sibling label text).
  - Added stronger candidates:
    - `By.ID(...)`, `By.NAME(...)`
    - test attributes (`data-testid`, `data-test`, `data-qa`, `data-cy`) in multiple forms
    - XPath exact text + normalize-space + clickable generic text
    - Ant modal-safe XPath patterns
    - following-sibling value patterns
    - ancestor-context XPath fallback
    - explicit risky ancestry-based XPath fallback (`xpath_fallback`) last resort
  - Increased candidate generation limit to 12.
- Reduced aggressive table highlighting:
  - Removed focus-mode hard table highlight styling.
  - Kept only selected-row style and subtle recommended indicator text.
  - Enabled vertical scrolling for larger candidate lists.
- Embedded inspect visuals improved:
  - Live hover highlight while Inspect is ON.
  - Click marker shown at exact click point.

## How To Run
```bash
cd inspectelement
.venv/bin/python -m inspectelement
```

## Manual Verification Checklist
1. Launch app, select project/module/page, open URL.
2. Turn `Inspect Mode` ON.
3. Click multiple different elements sequentially:
   - Capture should work on every click.
   - Left table should update on each click.
4. Check `~/.inspectelement/ui.log`:
   - `Embedded inspect click received`
   - `Capture started`
   - `Capture ended`
5. Validate richer locator output:
   - See ID/name/test-attr candidates when available.
   - See text-based XPath variants.
   - In modal context, see ant-modal-safe XPath variants.
6. Confirm table behavior:
   - No aggressive hover/row flashing.
   - Recommended shown via label in guidance column.
7. Verify Java safe flow unchanged:
   - `Add -> Preview` then `Apply` and backup `.bak` creation.

## Rollback
```bash
cd inspectelement
git checkout -- src/inspectelement/browser_manager.py \
  src/inspectelement/main_window.py \
  src/inspectelement/dom_extractor.py \
  src/inspectelement/locator_generator.py \
  src/inspectelement/locator_recommendation.py \
  src/inspectelement/scoring.py \
  src/inspectelement/models.py \
  tests/test_hybrid_capture.py \
  tests/test_locator_generation_quality.py \
  SPRINT_10_1.md
```

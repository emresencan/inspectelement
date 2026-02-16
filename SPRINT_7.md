# Sprint 7+: Table + ComboBox Actions + Table Root Detection + Parameter UI

## Scope Delivered
- Added `Table` and `ComboBox` action families to Action Picker.
- Kept safe write flow unchanged:
  - preview only on Add
  - explicit Apply/Cancel
  - `.bak` backup
  - atomic replace
- Added table root auto-detection from captured DOM ancestry.
- Added manual override flow with `Pick Table Root`.
- Added compact Action Parameters panel that shows required inputs for selected actions.
- Added method catalog extraction and markdown docs:
  - `TABLE_ACTIONS_CATALOG.md`
  - `SELECT_ACTIONS_CATALOG.md`

## New Required Actions

### Table (MVP)
- `tableAssertRowExists`
- `tableHasAnyRow`
- `tableAssertHasAnyRow`
- `tableFilter`
- `tableAssertRowMatches`
- `tableAssertRowAllEquals`

### Table (Advanced)
- `tableClickInColumn`
- `tableClickInRow`
- `tableClickButtonInRow`
- `tableSetInputInColumn`
- `tableAssertColumnTextEquals`
- `tableGetColumnText`
- `tableClickInFirstRow`
- `tableClickRadioInRow`
- `tableClickLink`

### ComboBox (MVP)
- `selectBySelectIdAuto`
- `selectByLabel`

## Generated Import Handling
When needed, writer injects missing imports safely:
- `java.time.Duration`
- `com.turkcell.common.components.table.HtmlTableVerifier`
- `com.turkcell.common.components.selectHelper.UniversalSelectHelper`

## Manual Verification
1. Run app:
   - macOS/Linux: `.venv/bin/python -m inspectelement`
   - Windows: `.venv\Scripts\python -m inspectelement`
2. Select project/module/page.
3. Inspect an element inside table area.
4. Pick Table actions (`Table` category).
5. Confirm `Table Root` section auto-fills locator.
6. If not detected, click `Pick Table Root` and select table container in browser.
7. Select action parameters in panel (`timeoutSec`, `columnHeader`, etc.).
8. Confirm sticky preview updates signatures and badges live.
9. Click Add and verify diff modal:
   - method signatures listed
   - table methods use table locator constant
10. Apply and verify:
   - target Java updated
   - `.bak` created
11. Select ComboBox actions and verify:
   - `selectBySelectIdAuto` requires/uses `selectId`
   - generated wrapper uses `UniversalSelectHelper`

## Rollback
- Restore from backup:
  - macOS/Linux: `cp <Page.java>.bak <Page.java>`
  - Windows: `copy <Page.java>.bak <Page.java>`

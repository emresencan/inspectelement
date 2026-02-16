# Sprint 5: Action Picker (BaseLibrary-backed) + Modern UX

## What Was Added
- Replaced old `click/sendKeys` checkbox area with a grouped Action Picker UI.
- Added 10 BaseLibrary-backed action options:
  - Click: `clickElement`, `javaScriptClicker`
  - Read: `getText`, `getAttribute`, `javaScriptGetInnerText`, `javaScriptGetValue`
  - State: `isElementDisplayed`, `isElementEnabled`
  - Scroll: `scrollToElement`
  - JS Input: `javaScriptClearAndSetValue`
- Added per-selected-action method signature preview in the Inspect panel.
- Extended Java generator templates for all 10 actions with correct return types and BaseLibrary calls.
- Diff preview now includes generated method signatures.

## Generation Rules
- Methods are generated only inside `// region AUTO_ACTIONS`.
- Locator insertion remains inside `// region AUTO_LOCATORS`.
- Duplicate selector behavior remains reuse-first.
- Name collisions still suffix constants and methods (`_2`, `_3`, ...).
- Safe flow remains unchanged: preview -> explicit Apply -> `.bak` backup -> atomic replace.

## Manual Verification
1. Launch app:
   - macOS/Linux: `.venv/bin/python -m inspectelement`
   - Windows: `.venv\Scripts\python -m inspectelement`
2. Select project/module/page in wizard.
3. Inspect an element and confirm recommended locator is selected.
4. In Action Picker, select mixed actions (example: `javaScriptClicker`, `getText`, `isElementDisplayed`).
5. Confirm method preview lines appear for selected actions.
6. Click Add:
   - Verify modal shows target file, final locator name, method names, and method signatures.
   - Verify unified diff shows insertion under `AUTO_ACTIONS`.
7. Click Apply:
   - Verify Java file updated.
   - Verify `.bak` created beside target Java file.
8. Repeat Add for same selector:
   - Verify status indicates existing selector reuse.
   - Verify no duplicate locator line is added.

## Rollback
- Restore previous Java file with backup:
  - macOS/Linux: `cp <Page.java>.bak <Page.java>`
  - Windows: `copy <Page.java>.bak <Page.java>`

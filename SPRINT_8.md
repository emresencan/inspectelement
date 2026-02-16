# Sprint 8: Production Hardening + Page Auto-Creation

## Scope Delivered
- Added page auto-creation flow from Page dropdown via `+ Create New Page...`.
- Added creation preview/apply flow using existing diff modal pattern.
- Added atomic file creation for new page files (no `.bak` for first create).
- Added hardened Java import injection through dedicated `import_parser` module.
- Added strict validation before preview/apply and a `Validate Only` button.
- Hardened table root detection with:
  - candidate ranking
  - no index-based xpath fallback
  - unstable warning text
  - multi-candidate selection dialog
- Added tests for page creation, import parser, validation, and table root hardening.

## Page Auto-Creation Template
Generated page file includes:
- package declaration (detected or derived)
- imports:
  - `org.openqa.selenium.By`
  - `org.openqa.selenium.WebDriver`
  - detected/fallback `BaseLibrary` import
- class extends `BaseLibrary`
- constructor `(WebDriver driver)`
- default locator `BTN_EDIT_MODE`
- marker regions:
  - `AUTO_LOCATORS`
  - `AUTO_ACTIONS`

## Manual Verification
1. Run app:
   - macOS/Linux: `.venv/bin/python -m inspectelement`
   - Windows: `.venv\\Scripts\\python -m inspectelement`
2. Select context (project/module).
3. Open Page dropdown and choose `+ Create New Page...`.
4. Enter valid page name (PascalCase, alphanumeric).
5. Confirm preview modal shows target new file and diff.
6. Click `Apply`.
7. Confirm:
   - new page file is created in `<module>-pages/src/main/java/<package-path>/`
   - dropdown refreshes
   - newly created page is auto-selected.
8. Select element + action(s), then click `Validate Only`.
9. Confirm validation status message appears and no file is written.
10. Click `Add`, preview changes, then `Apply`.
11. Confirm `.bak` is created for modified page and file update is atomic.
12. For table actions:
   - inspect inside table/grid
   - verify table root auto-filled
   - if multiple roots detected, select from dialog
   - if unstable root selected, warning text is shown.

## Rollback
- For modified existing pages:
  - macOS/Linux: `cp <Page.java>.bak <Page.java>`
  - Windows: `copy <Page.java>.bak <Page.java>`
- For newly created pages:
  - delete the new `.java` file manually if you want to revert creation.

# Sprint 9 - Workspace UI (Single Window)

## Scope
- Refactor UI to a single-window workspace flow.
- Keep existing safe generation semantics unchanged:
  - preview before write
  - explicit apply/cancel
  - `.bak` for file modifications
  - atomic replace
  - strict validation gate
- Add persistent workspace context (`config.json`).
- Keep page creation inline in the workspace (no modal in main flow).

## Implemented

### 1) One-window information architecture
- Added top context bar:
  - Project path + browse
  - Module dropdown
  - Page dropdown + `+ New Page`
  - URL + Launch
  - Inspect toggle
  - Validate Only
  - Add -> Preview
  - Apply
  - Cancel Preview
  - status pill (`OK`, `Warning`, `Error`)
- Added split workspace:
  - Left panel (controls, candidates, action picker, parameters, table root, generated signatures, diff dock)
  - Right panel (embedded browser container + fallback)
- Added bottom status bar:
  - last action / warning / write result

### 2) Inline flows (no disruptive modals in main generation path)
- Diff preview is docked in the left panel (embedded).
- Page creation is inline drawer:
  - page name input
  - package + target path preview
  - generated file content preview
  - create/cancel inline
- Table-root multiple candidates handled inline via dropdown.

### 3) Workspace state persistence
- Added `ui_state.py`:
  - load/save `~/.inspectelement/config.json`
  - persist project/module/page/url/inspect preference
  - helper for button enable-state rules
- App auto-loads last context on startup when config exists.

### 4) Launch/inspect UX hardening
- Inspect toggle starts disabled and is enabled after launch/page availability.
- Launch normalizes URL for workspace view and managed browser launch.
- Browser page updates current URL in top bar.

### 5) Component structure improvements
- Introduced reusable UI component classes:
  - `WorkspaceWindow`
  - `TopBar`
  - `LeftPanel`
  - `BrowserPanel`
  - `BottomStatusBar`
- Backward compatibility kept:
  - `MainWindow = WorkspaceWindow`

## Screenshot Placeholders
- `docs/screenshots/sprint9-topbar.png`
- `docs/screenshots/sprint9-split-workspace.png`
- `docs/screenshots/sprint9-inline-preview-dock.png`
- `docs/screenshots/sprint9-inline-new-page-drawer.png`

## Run

### macOS / Linux
```bash
cd inspectelement
.venv/bin/python -m inspectelement
```

### Windows (PowerShell)
```powershell
cd inspectelement
.\.venv\Scripts\python.exe -m inspectelement
```

## Manual Verification Checklist
1. Open app and verify single workspace window appears.
2. Select Project -> Module -> Page in top bar.
3. Enter URL and press `Launch`.
4. Confirm right browser panel loads and Inspect toggle becomes enabled.
5. Enable Inspect and click an element in browser.
6. Confirm locator candidates render; recommended candidate auto-selected.
7. Select actions/params; confirm generated signatures update live.
8. Click `Add -> Preview`; confirm diff appears in embedded preview dock.
9. Click `Cancel Preview`; confirm preview clears and no write happens.
10. Click `Add -> Preview` again, then `Apply`; confirm file updates and `.bak` is created.
11. Click `+ New Page`; fill page name and verify inline preview + apply.
12. Confirm new page auto-selects and generation flow continues.
13. Change action chips/params and verify browser panel does not reload.

## Notes
- Existing safe-write internals (preview/apply/backup/atomic/validation/import hardening) are preserved.
- Existing action generation semantics are unchanged.

# Sprint 9: Workspace UI (Single Window)

## Scope Delivered
- Refactored to a single-window workspace flow with always-visible context controls.
- Preserved safe generation semantics:
  - preview before write
  - explicit apply/cancel
  - `.bak` for file modifications
  - atomic replace
  - strict validation gate before preview/apply
- Added persistent workspace context in `~/.inspectelement/config.json`.
- Replaced disruptive modals in main flow with inline UI:
  - docked diff preview
  - inline new-page drawer
  - inline table-root multi-candidate selection

## Information Architecture

### Top Context Bar
- Project path + `Browse...`
- Module dropdown
- Page dropdown + `+ New Page`
- URL + `Launch`
- `Inspect` toggle
- `Validate Only`
- `Add -> Preview`
- `Apply`
- `Cancel Preview`
- status pill (`OK` / `Warning` / `Error`)

### Split Workspace
- Left panel:
  - element snapshot
  - locator candidates
  - action picker / params
  - table-root controls
  - generated method signatures
  - inline new-page drawer
  - docked diff preview
- Right panel:
  - embedded browser container (Qt WebEngine when available)
  - fallback info when embedded web engine is unavailable

### Bottom Status Bar
- last action message
- warning channel
- write result channel

## UX Flow Changes
- Startup restores saved project/module/page/url/inspect preference from `config.json` when available.
- Inspect toggle starts disabled and is enabled after page launch callback.
- Launch normalizes URL and keeps URL in sync with browser callback updates.
- Browser panel is not reloaded when changing actions/params.
- `Add -> Preview` now generates and docks preview without writing.
- `Apply` writes only when pending preview exists.
- `Cancel Preview` clears staged preview with no write.

## Inline New Page Flow
- `+ New Page` opens inline drawer in left panel.
- Inputs/preview shown inline:
  - page name (PascalCase)
  - target package/path
  - generated file content
  - diff
- `Create Page` applies creation atomically.
- Page dropdown refreshes and auto-selects newly created class.

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
2. Select Project -> Module -> Page from top bar.
3. Enter URL and click `Launch`.
4. Confirm right browser panel updates and Inspect toggle becomes enabled.
5. Enable Inspect and click an element in browser.
6. Confirm locator candidates render and recommended entry is selected.
7. Select actions/params; confirm generated signatures update.
8. Click `Add -> Preview`; confirm docked diff appears in left panel.
9. Click `Cancel Preview`; confirm preview clears and no write happens.
10. Click `Add -> Preview`, then `Apply`; confirm target file updates and `.bak` exists.
11. Click `+ New Page`; fill name and preview inline.
12. Click `Create Page`; confirm page is created and auto-selected.
13. Change actions/params and confirm browser panel does not reload.

## Rollback
- Modified file rollback:
  - macOS/Linux: `cp <Page.java>.bak <Page.java>`
  - Windows: `copy <Page.java>.bak <Page.java>`
- New page rollback:
  - delete created `.java` file manually.

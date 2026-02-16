# Sprint 9.1 - Workspace Regression Fixes

## Fix Scope
- Enforce embedded browser as the active Launch + Inspect surface.
- Remove external-browser launch path from workspace launch flow.
- Add embedded JS inspector with click interception and Python bridge.
- Fix `+ New Page` usability in workspace.

## What Changed

### Single embedded browser flow
- `Launch` now loads URL only in embedded `QWebEngineView`.
- Workspace no longer calls external Playwright launch in Launch flow.
- Inspect toggle now drives embedded inspector injection/removal.

### Embedded inspect implementation
- Added JS injector for inspect mode:
  - hover highlight overlay
  - click interception (`preventDefault`, `stopPropagation`, `stopImmediatePropagation`)
  - payload capture (element summary, attrs, ancestry, outerHTML snippet)
  - candidate extraction (CSS/XPath/Selenium forms + uniqueness counts)
- Added Qt bridge (`QWebChannel`) for JS -> Python payload/status.
- Python pipeline reuses:
  - `ElementSummary` mapping
  - table-root detection from ancestry
  - existing scoring + recommendation/render path

### New Page action stability
- `+ New Page` remains connected and now logs invocation.
- New Page button enable rule tied to project+module availability.
- Inline drawer is moved higher in left panel so opening is visible immediately.

### Diagnostics
- Added UI logger lines for:
  - inspect toggle changes
  - inspector injected status
  - click payload received
  - new page handler invocation

## Manual Smoke Checklist
1. Run app and select project/module/page.
2. Enter URL and click `Launch`.
3. Verify only embedded browser panel is used.
4. Toggle `Inspect ON`.
5. Click an element in embedded page:
   - no navigation/click action should fire
   - locator candidates should populate.
6. Toggle `Inspect OFF`:
   - page behaves normally again.
7. Click `+ New Page`:
   - inline drawer opens immediately
   - enter valid name -> preview appears
   - apply creates file and refreshes page dropdown.

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

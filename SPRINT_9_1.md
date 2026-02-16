# Sprint 9.1: Embedded Browser Backend + Inspect + New Page Fix

## Scope Delivered
- Removed dual-browser behavior in workspace mode.
- Embedded browser is now the only launch/inspect backend.
- Added embedded JS inspector with click interception, overlay highlight, and payload bridge to Python.
- Reused existing candidate recommendation + table-root detection flow using embedded payload.
- Fixed `+ New Page` inline drawer action and enablement rules.
- Added debug log lines for key diagnostics.

## Key Fixes

### 1) Single Browser Backend (Embedded)
- `Launch` now loads URL only in embedded WebEngine panel.
- External Playwright browser launch is no longer used by workspace UI.

### 2) Embedded Inspect
- Inspect ON injects JS inspector in embedded page.
- JS inspector behavior:
  - hover highlight overlay
  - click interception with `preventDefault` + propagation stop
  - payload capture: summary + attributes + ancestry + candidate selectors
- Python bridge receives payload via Qt WebChannel and updates:
  - locator candidates
  - recommendation labels
  - table-root auto detection/candidates
  - existing left-panel generation flow
- Inspect OFF detaches listeners and disables overlay.

### 3) New Page Button Regression
- `+ New Page` handler is connected and logs invocation.
- Enablement requires project+module+pages source root (page selection not required).
- Clicking opens inline drawer, supports preview/apply, then refreshes and auto-selects created page.

### 4) Safety Guarantees Preserved
- Preview before write
- explicit Apply/Cancel Preview
- `.bak` backup for file modifications
- atomic writes
- strict validation gate before write

## Diagnostics Logs Added
- Inspect toggle changes
- JS inspector injection success
- embedded click payload received
- New Page handler invoked

## Manual Smoke Checklist
1. Launch app, choose project/module/page.
2. Enter URL and click `Launch`.
3. Confirm URL loads in embedded panel only (no external browser opens).
4. Toggle `Inspect: ON`.
5. Click element in page and verify:
   - page action/navigation is blocked
   - locator candidates update in left panel.
6. Toggle `Inspect: OFF` and verify page behaves normally.
7. Click `+ New Page` and verify inline drawer opens.
8. Enter valid page name, click preview, then create.
9. Confirm new page is created and auto-selected.

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

# Sprint 10 - Hybrid Coordinate Capture Architecture

## Why Hybrid
- Production sites (Trendyol, BMW, enterprise portals) often run strict CSP.
- CSP can block injected inspector scripts and qwebchannel bootstrap scripts.
- To make inspect stable, capture is now split:
  - Embedded Qt browser: visual UI surface only
  - Managed Playwright Chromium: authoritative DOM extraction/locator engine

## New Architecture

### 1) Embedded Browser (Qt WebEngine)
- Loads target URL for user interaction.
- Inspect mode captures click coordinates via native Qt mouse events.
- No JS overlay injection and no qwebchannel dependency.

### 2) Managed Chromium (Playwright)
- Launched once per session and kept persistent.
- Synchronized with embedded URL/viewport on launch and navigation.
- On inspect click:
  - receives `(x, y, viewport_w, viewport_h)`
  - maps coordinates to managed viewport
  - uses `document.elementFromPoint(x, y)` to locate element
  - runs existing summary + locator generation + scoring pipeline

### 3) Fallback
- If point extraction fails or cross-origin iframe is detected:
  - UI status shows warning
  - `Open Managed Inspector` button opens a visible managed Chromium session for manual fallback.

## CSP Avoidance
- Removed runtime dependency on:
  - `qrc:///qtwebchannel/qwebchannel.js`
  - embedded inspector overlay JS injection
- Inspect capture path now works even when page scripts are restricted by CSP.

## Production Readiness Notes
- No Playwright page reload on each click.
- No duplicate managed browser launches in normal flow.
- Safe Java write flow is unchanged:
  - Add -> Preview
  - explicit Apply/Cancel
  - `.bak` backup
  - atomic replace
  - strict validation

## Debug Guide

### Runtime logs
- UI logs: `~/.inspectelement/ui.log`
- Look for:
  - `Inspect toggle changed`
  - `Embedded inspect click captured`
  - `Add->Preview requested`
  - `Apply result`

### Expected inspect flow
1. Launch URL.
2. Enable Inspect mode.
3. Click element in embedded browser.
4. Locator candidates appear.
5. Add -> Preview -> Apply.

### Common fallback situations
- `Cross-origin iframe detected...`
  - click `Open Managed Inspector` and inspect from managed browser session.
- `No element found at clicked point...`
  - retry click after page stabilizes or open managed inspector.

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

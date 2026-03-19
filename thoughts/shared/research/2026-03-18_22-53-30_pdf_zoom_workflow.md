---
date: 2026-03-18 22:53:30 ACDT
researcher: nguyendangquang
git_commit: 0b08e93474f98ea23c6c3ede024a74973ae858a2
branch: master
repository: reader3-custom
topic: "PDF zoom in/out workflow per page"
tags: [research, codebase, pdf, zoom, scale, pdf.js]
status: complete
last_updated: 2026-03-18
last_updated_by: nguyendangquang
---

# Research: PDF Zoom In/Out Workflow

**Date**: 2026-03-18 22:53:30 ACDT
**Git Commit**: 0b08e93474f98ea23c6c3ede024a74973ae858a2
**Branch**: master
**Repository**: reader3-custom

## Research Question
How does zooming in and out into each page work for reading PDF?

## Summary

Zoom is implemented entirely in `templates/reader.html`. It uses a discrete set of zoom steps (13 levels, 50%–300%), stored as an index. When zoom changes, the system performs a two-phase update: (1) immediately resize the content container and schedule a debounced canvas re-render, (2) after 400ms, synchronously resize all canvases then asynchronously repaint pixels. Scroll position is preserved via a custom scroll-anchoring function.

## Detailed Findings

### 1. Zoom State (lines 1975–1989)

```javascript
const ZOOM_STEPS = [0.5, 0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0];
const DEFAULT_ZOOM_IDX = ZOOM_STEPS.indexOf(1.0);  // → 5
let currentZoomIdx = DEFAULT_ZOOM_IDX;
let baseMaxWidth = 860; // fallback; overwritten from first PDF page dimensions
```

- 13 discrete levels from 50% to 300%
- Default is 100% (index 5)
- `baseMaxWidth` is computed from the first page's unscaled width + 32px padding

### 2. Entry Points (user interactions)

| Trigger | Location |
|---|---|
| Zoom In button click | line 2133 |
| Zoom Out button click | line 2139 |
| Click zoom label (reset to 100%) | line 2145 |
| Cmd/Ctrl + `+` or `=` | line 2150 |
| Cmd/Ctrl + `-` | line 2155 |
| Cmd/Ctrl + `0` (reset) | line 2160 |
| Trackpad pinch (wheel + ctrlKey) | line 2167 |

All paths increment/decrement `currentZoomIdx` then call `applyZoom()`.

### 3. applyZoom() — lines 2031–2044

```javascript
function applyZoom() {
    const zoom = getZoomLevel();                        // ZOOM_STEPS[currentZoomIdx]
    const newMax = Math.round(baseMaxWidth * zoom);

    withScrollAnchor(() => {
        contentContainer.style.maxWidth = newMax + 'px'; // immediate layout change
    });
    updateZoomLabel();                                  // updates "100%" label text

    clearTimeout(reRenderTimer);
    reRenderTimer = setTimeout(() => {
        withScrollAnchor(() => { reRenderAllPages(); }); // deferred canvas repaint
    }, 400);
}
```

Phase 1 (immediate): Adjust `contentContainer.style.maxWidth`. Because canvases use `width: 100%` in CSS, they stretch/shrink to the new container width visually without a repaint.

Phase 2 (after 400ms): `reRenderAllPages()` — repaints canvas pixels at the new resolution for sharpness.

### 4. Scroll Anchoring — lines 1993–2028 (`withScrollAnchor`)

Preserves reading position across zoom changes:
1. Before layout change: find the `.pdf-page-wrapper` or `.pdf-page-placeholder` whose Y range contains the viewport center; record the ratio within that element.
2. Execute the layout change callback.
3. After layout: recompute the anchor element's new position and restore `scrollTop`.

### 5. Canvas Re-render on Zoom — lines 2089–2130 (`reRenderAllPages`)

Two phases to avoid cascading reflows:

**Phase 1 (synchronous)**: For every rendered page, compute new `renderScale = cssScale * dpr` and resize `canvas.width` / `canvas.height`. This settles the layout in one reflow.

**Phase 2 (async)**: Call `page.render({ viewport })` for pixel painting, then clear and re-render the text layer with the updated `--scale-factor` CSS variable.

Scale math:
```javascript
const unscaled = page.getViewport({ scale: 1 });
const cssScale = containerWidth / unscaled.width;   // fit-to-width
const renderScale = cssScale * dpr;                 // HiDPI sharpness
```

### 6. Initial Page Render — lines 2047–2087 (`renderPDFPage`)

Same scale math applies on first render. Each page:
- Creates a `<canvas>` sized to `renderScale` viewport (pixel-perfect)
- Creates a `.textLayer` `<div>` with `--scale-factor: cssScale` for text selection
- Replaces placeholder `<div>` with the finished wrapper
- Stores `{ wrapper, page }` in `renderedPages` Map for later re-render access

### 7. HTML / CSS

Zoom controls UI: `templates/reader.html:984–992`
- Fixed-position panel, bottom-right
- Moves left when chat is open: `body.chat-open .pdf-zoom-controls { right: calc(var(--chat-width) + 24px); }`

Page wrapper CSS: `templates/reader.html:221–229`
- `canvas { width: 100%; height: auto }` — responsive to container width

Text layer scale compensation: `templates/reader.html:231–247`
```css
.textLayer > div {
    transform: scaleX(calc(1 / var(--scale-factor))) scaleY(calc(1 / var(--scale-factor)));
}
```

## Code References

| File:Line | Description |
|---|---|
| `templates/reader.html:1975–1989` | Zoom state constants and variables |
| `templates/reader.html:1993–2028` | `withScrollAnchor()` scroll position preserver |
| `templates/reader.html:2031–2044` | `applyZoom()` — main zoom entry point |
| `templates/reader.html:2047–2087` | `renderPDFPage()` — initial page render with scale |
| `templates/reader.html:2089–2130` | `reRenderAllPages()` — re-render on zoom |
| `templates/reader.html:2133–2186` | All zoom event listeners (buttons, keyboard, trackpad) |
| `templates/reader.html:221–247` | CSS for page wrapper, canvas, text layer |
| `templates/reader.html:250–288` | CSS for zoom controls panel |
| `templates/reader.html:984–992` | HTML zoom controls markup |

## Architecture Insights

- **No native browser zoom**: `e.preventDefault()` on Cmd/Ctrl+scroll blocks browser zoom; all zoom is custom.
- **Fit-to-width scaling**: scale is always derived from container width ÷ page natural width, not stored as an absolute value. Zoom changes the container width, which drives scale.
- **HiDPI awareness**: canvas pixel size = CSS size × `window.devicePixelRatio` for retina sharpness.
- **Two-phase re-render**: batching canvas size changes before async painting avoids cascading reflows.
- **Text layer**: uses `--scale-factor` CSS variable so PDF.js text positioning remains accurate after zoom.

## Open Questions

- Zoom state is not persisted (resets to 100% on page reload). Could persist to `localStorage` per book.
- Trackpad pinch only increments by one zoom step per gesture; fast pinches might feel sluggish.

---
date: 2026-03-18 22:58:45 ACDT
researcher: quangnguyentechno@gmail.com
git_commit: 0b08e93474f98ea23c6c3ede024a74973ae858a2
branch: master
repository: reader3-custom
task: "Fix PDF zoom scroll-jump bug"
tags: [implementation-plan, pdf, zoom, scroll-anchor, pdf.js]
status: completed
last_updated: 2026-03-18
last_updated_by: quangnguyentechno@gmail.com
---

# Fix PDF Zoom Scroll-Jump Bug — Implementation Plan

## Overview

When the user zooms in/out in the PDF reader, the viewport should stay centered on the same page, but currently jumps or drifts. Based on the current code path, the highest-confidence fix is to stop animating `contentContainer.style.maxWidth` during PDF zoom. The existing scroll-anchor correction in `withScrollAnchor()` runs synchronously, but the CSS transition keeps changing page positions for 200ms afterward, which can undo the correction.

## Current State Analysis

Zoom is implemented entirely in `templates/reader.html`. The relevant path is:

1. `applyZoom()` changes `contentContainer.style.maxWidth`.
2. That width change is wrapped in `withScrollAnchor()`, which measures the page at the viewport center, applies the layout change, and immediately restores `scrollTop`.
3. In PDF mode, `.content-container` has `transition: max-width 0.2s ease`, so page positions continue moving after `withScrollAnchor()` has already finished.
4. After 400ms, `reRenderAllPages()` runs inside a second `withScrollAnchor()` call to repaint canvases at the new resolution.

**Most likely root cause**

The anchor logic assumes the layout change is complete when it recomputes `scrollTop`. That assumption is false while `max-width` is animating. The scroll correction is applied against an intermediate layout, then the container keeps resizing, which causes the viewport to drift or jump away from the intended page.

**Why this is a stronger fix target than the current plan**

- The width animation is definitely active in the current code path.
- It directly conflicts with the "measure now, restore now" design of `withScrollAnchor()`.
- Replacing `offsetTop` math with `getBoundingClientRect()` may still be a useful hardening step, but by itself it does not solve the fact that layout continues changing after the correction.

## Desired End State

After the fix:
- Zooming in or out keeps the page that was in the center of the viewport still visible and centered after zoom.
- No jump to unrelated pages.
- The layout change used for zoom is fully settled before `withScrollAnchor()` finishes.

### Verification:
Open a multi-page PDF (10+ pages), scroll to the middle, zoom in → page stays centered. Zoom out → page stays centered. Reset zoom (click "100%") → page stays centered.

## What We're NOT Doing

- Not changing the two-phase canvas re-render logic unless Phase 1 fails.
- Not changing zoom steps, zoom UI, or keyboard shortcuts.
- Not persisting zoom level to `localStorage`.
- Not changing text layer or HiDPI logic.

## Root Cause (Summary)

The current scroll-anchor logic is synchronous, but PDF zoom is animated via `transition: max-width 0.2s ease` on `.content-container`. That means the anchor is restored before the zoomed layout has finished moving, so the viewport no longer stays locked to the same page.

## Implementation Approach

Implement the fix in two phases:

- **Phase 1 (primary fix):** disable animated `max-width` changes for PDF zoom so `withScrollAnchor()` operates on a settled layout.
- **Phase 2 (optional hardening):** if any residual jump remains after Phase 1, replace the `offsetTop` / `offsetParent` traversal in `withScrollAnchor()` with `getBoundingClientRect()` arithmetic.

---

## Phase 1: Remove PDF Zoom Width Animation

### Phase 1: Overview

Stop animating `max-width` in PDF mode so zoom width changes happen immediately. This lets the existing `withScrollAnchor()` measurement and correction run against the final layout instead of an in-between animated state.

### Phase 1: Changes Required

#### 1. `templates/reader.html` — PDF mode CSS for `.content-container`

**Current code:**

```css
body.pdf-mode .content-container {
    max-width: none;
    padding: 32px 16px 40px;
    transition: max-width 0.2s ease;
}
```

**Fixed code:**

```css
body.pdf-mode .content-container {
    max-width: none;
    padding: 32px 16px 40px;
}
```

**Key changes:**
- The zoomed width now changes immediately instead of animating over 200ms.
- `withScrollAnchor()` can keep its current "measure, change layout, restore scroll" model.
- The re-render debounce remains unchanged.

### Phase 1: Success Criteria

#### Phase 1: Automated Verification
- [ ] No JavaScript errors in the browser console when zooming.
- [ ] No repeated scroll corrections or oscillation visible during a single zoom action.

#### Phase 1: Manual Verification
- [ ] Open a PDF with 10+ pages in the reader.
- [ ] Scroll to the middle (e.g., page 5 of 10).
- [ ] Click Zoom In — verify the same page remains visible and centered.
- [ ] Click Zoom Out — verify the same page remains visible and centered.
- [ ] Click the zoom label to reset to 100% — verify position is preserved.
- [ ] Test with Cmd/Ctrl+`+`, Cmd/Ctrl+`-`, Cmd/Ctrl+`0`.
- [ ] Test with trackpad pinch zoom.
- [ ] Verify behavior at the very top and bottom of the document (edge cases).

### Phase 1: Discoveries and Notable Information

*(To be filled by the implementing agent during execution.)*

---

## Phase 2: Optional Hardening of `withScrollAnchor`

### Phase 2: When To Do This

Only do this if Phase 1 removes most of the problem but some position error still remains.

### Phase 2: Overview

Replace the `offsetTop` / `offsetParent` traversal in `withScrollAnchor()` with `getBoundingClientRect()` arithmetic in both the pre-layout and post-layout measurements.

### Phase 2: Rationale

- This makes the anchor measurement independent of `offsetParent` behavior.
- It is a robustness improvement even if it is not the primary cause of the current jump.
- It should be evaluated after the transition fix, not before, so the effect of each change is clear.

### Phase 2: Proposed Code Shape

```javascript
const mainRect = mainEl.getBoundingClientRect();
const elRect = el.getBoundingClientRect();
const top = mainEl.scrollTop + elRect.top - mainRect.top;
```

Apply the same formula when recomputing `newTop` after the layout change.

### Phase 2: Success Criteria

- [ ] Zoom remains stable after Phase 1.
- [ ] Switching to `getBoundingClientRect()` does not regress top-of-document or bottom-of-document behavior.
- [ ] No issues appear when the anchor element is a rendered wrapper or an aspect-ratio placeholder.

---

## Testing Strategy

### Manual Testing Steps
1. Open any multi-page PDF (ideally 20+ pages).
2. Scroll to approximately page 10.
3. Zoom in twice with the + button — confirm page 10 remains visible.
4. Zoom out three times — confirm page 10 remains visible.
5. Click the zoom percentage label (reset) — confirm position preserved.
6. Test keyboard shortcuts: Cmd+`+`, Cmd+`-`, Cmd+`0`.
7. If available, test trackpad pinch gesture.
8. Repeat the above starting from the first and last pages.

### Edge Cases
- **At top of document** (`scrollTop = 0`): scroll should stay at or near 0.
- **At bottom of document**: last page should remain visible without jumping upward.
- **Unrendered placeholders**: `anchorEl` may be a `.pdf-page-placeholder`; because placeholders use `aspect-ratio`, their height still changes with width, so they must be included in manual verification.
- **Page gaps / section spacing**: viewport center may land in spacing between pages, so anchor selection should still feel stable and not jump to a distant page.

## References

- Research: `thoughts/shared/research/2026-03-18_22-53-30_pdf_zoom_workflow.md`
- Primary fix location: PDF mode `.content-container` CSS in `templates/reader.html`
- Related logic: `templates/reader.html:1993–2029` (`withScrollAnchor`)
- `applyZoom()` caller: `templates/reader.html:2031–2044`
- MDN `getBoundingClientRect`: https://developer.mozilla.org/en-US/docs/Web/API/Element/getBoundingClientRect
- MDN `offsetParent`: https://developer.mozilla.org/en-US/docs/Web/API/HTMLElement/offsetParent

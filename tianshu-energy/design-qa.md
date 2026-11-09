# Device card alignment design QA

- Source visual truth:
  - `device-card-alignment.png` (1717 × 517)
  - `device-card-message-layout.png` (1315 × 574)
- Intended implementation: `http://127.0.0.1:8794/#/devices/facilities`
- Implementation screenshot: unavailable
- Intended viewport: desktop, approximately 1440–1920 CSS px wide, device-card grid state with one over-limit card and recent command results
- Density normalization: not performed because an implementation capture could not be obtained

## Full-view comparison evidence

The source screenshots were available and show two P1 layout problems: an over-limit alert increases one card's height, and the optional execution-result tag pushes the action row to different vertical positions. The implementation was changed to reserve fixed rows for metadata, identity, statistic, alert, details, execution result, and actions. Browser Use refused the local loopback page under its URL policy, so the revised rendered view could not be captured for comparison.

## Focused region comparison evidence

The focused source regions are the alert slot and bottom action row. The implementation now uses `.v1-device-alert`, `.v1-device-command`, and `.v1-device-actions` slots with fixed grid tracks. Post-fix pixel evidence is unavailable for the same browser-policy reason.

## Required fidelity surfaces

- Fonts and typography: existing Art Design Pro / Element Plus typography retained; rendered verification blocked.
- Spacing and layout rhythm: fixed card rows and a shared bottom action baseline implemented; rendered verification blocked.
- Colors and visual tokens: existing Element Plus semantic colors retained; no new custom color system introduced.
- Image quality and assets: no raster or illustrative assets are part of this card change; existing icon library retained.
- Copy and content: existing labels retained; plan eligibility now explains disabled states through an Element Plus tooltip.

## Comparison history

1. Source finding: optional over-limit and execution-result messages changed individual card height and button position (P1).
2. Fix: introduced stable content slots and a non-wrapping desktop action row; mobile keeps controlled wrapping.
3. Source finding: HTTP gateway devices showed a permanently disabled Plan button despite having switch capability (P1 functional issue).
4. Fix: allowed non-protected, switch-capable HTTP gateway devices to create schedules; execution still requires fresh online telemetry and a confirmed gateway response.
5. Post-fix evidence: production build and 29 backend regressions passed, but these are not substitutes for visual comparison. Local browser capture was blocked by Browser Use URL policy.

## Findings

- [P1] Rendered alignment cannot be visually confirmed.
  - Location: facility device cards at the desktop grid viewport.
  - Evidence: local server started, but Browser Use refused the loopback implementation and produced no implementation screenshot.
  - Impact: exact pixel alignment, clipping, and responsive wrapping remain unverified.
  - Fix: reopen the packaged application or local page in a browser surface that permits loopback capture and compare the same over-limit/command-result state.

## Implementation checklist

- [x] Reserve alert and command-result height.
- [x] Pin all action rows to the same card baseline.
- [x] Keep desktop actions on one row and retain mobile wrapping.
- [x] Enable plans for eligible HTTP gateway devices.
- [x] Pass production build and backend regressions.
- [ ] Capture revised cards and compare against both source screenshots.

final result: blocked

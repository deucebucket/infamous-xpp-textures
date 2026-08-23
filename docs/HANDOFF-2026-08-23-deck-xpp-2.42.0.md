# Handoff — Deck XPP tool 2.42.0 wrap (2026-08-23 04:38 PM CDT)

Local Steam Deck Codex session. Do not pull remotes over this tree.

## What was true when wrap started

- Repo: `/home/deck/Projects/infamous-xpp-textures`
- Branch: `feat/material-component-assembly-106`
- HEAD: `8a49e7d` `8a49e7d Correct final verification counts` (already on origin)
- Working tree was clean. Remaining work was **install/repro of the 2.42.0 wheel**, not uncommitted source.

## Proven

- Fresh venv installed `infamous_xpp_textures-2.42.0-py3-none-any.whl` and reported version **2.42.0**.
- Wheel path: `/home/deck/infamous-hd/model-lab/xpp-tool-2.42.0-release-final-20260823/wheel-a/`
- Strict export wrote:
  - `.../installed/strict/zeke-page2-five-component.glb`
  - 5 components / 1434 vertices / 2026 triangles
- Preview export wrote matching glb+json under `.../installed/preview/`
- Scope: title_id `infamous-1`, build_id `bcus98119-v0100`

## In flight, not finished

- Byte-for-byte compare of installed-wheel strict vs clean-hair preview (started, not recorded as pass/fail here).

## Next

1. Finish the installed-wheel byte-for-byte strict vs preview check on the Deck.
2. Keep model-lab artifacts under `/home/deck/infamous-hd/model-lab/` — they are outside this git tree.
3. Do not launch Second Son; that install is not a working retail hookup.

No gameplay claim.

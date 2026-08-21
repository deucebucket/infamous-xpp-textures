# Changelog

## 1.4.0 - 2026-08-21

- Added strict descriptor, mip-count, heap-bound, overlap, and complete-layout validation.
- Added retail-to-candidate XPP reports with exact promoted records and texture-chain growth.
- Added `profile-validate` for full replacement-set preflight without building PSARCs.
- Integrated strict retail comparison into `profile-build` before either output is built.
- Added optional startup-path pass/fail bounds, JSON reports, and fail-on-budget behavior.
- Kept runtime evidence path-specific: every promoted texture still requires scene coverage.

## 1.3.0 - 2026-08-21

- Added the neutral `xpp-tool` command while preserving `if1-tex` as a compatible alias.
- Added `profile-extract` for atomic extraction of a complete install1/install2 PSARC pair with `workspace.json` hashes and entry metadata.
- Added `profile-build` for strict replacement ownership, XPP validation, two-archive staging, complete payload auditing, and `profile.json` output.
- Added visible phase progress for long Steam Deck extraction, compression, hashing, and audit runs.
- Added six synthetic full-pipeline tests; the complete suite now contains 18 tests.
- Rebuilt the 203-package `a21one2x` profile from the protected retail pair and audited all 2,298 PSARC entries.
- Verified the new outputs are byte-identical to the known-good profile:
  - `infamous1.psarc_s`: `10d0b1f492ff64b5dbfa2c15e3a1d8a43bc88004cc6aa75a9e8881996aee551d`
  - `infamous2.psarc_s`: `0f1e193a425e56d5b2448b03c49399d44b726b5b2667e6705511b2ec372cb8d3`

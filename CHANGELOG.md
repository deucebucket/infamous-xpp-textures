# Changelog

## 2.8.0 - 2026-08-22

- Added a deterministic runtime-topology diagnostic GLB exporter for complete, caller-owned topology census bundles.
- Validated the completion marker, every binding row, descriptor reconstruction, exact payload set and identity, u16 triangle bounds, and one explicit finite `float32x3` position hypothesis before export.
- Kept runtime draw ownership, XPP correlation, position meaning, UVs, materials, rigging, and injection false so rapid visual triage cannot become a false character claim.

## 2.7.0 - 2026-08-22

- Added a deterministic diagnostic GLB export for one exact character topology and one caller-selected, hash-bound `float32x3` position hypothesis.
- Required a complete RSX draw binding, exact XPP index identity, bounded payload size and SHA-256, finite coordinates, and at least one nondegenerate triangle before writing output.
- Embedded explicit diagnostic metadata and kept position meaning, UVs, materials, skin weights, joint palettes, skeletons, inverse binds, rigged export, and game injection unproved.

## 2.6.0 - 2026-08-22

- Added fail-closed cross-build texture rebasing that keeps the target retail XPP as the structural base.
- Bound source edits to target records by exact retail chain bytes plus format, face count, dimensions, and mip topology instead of descriptor ordinal.
- Detected resized and same-size texture edits, preserved every unselected target texture, and rejected missing, duplicate, ambiguous, cubemap, and unchanged selections before publication.
- Added an explicit byte-identical zero-change control and aggregate target validation report; runtime proof remains required.

## 2.5.0 - 2026-08-22

- Added explicit `install1/` and `install2/` replacement ownership for packed profiles with cross-archive duplicate basenames.
- Preserved legacy flat replacement routing when one package owner is globally unique and rejected every ambiguous, wrong-slot, or duplicate-target input before output staging.
- Preserved exact manifest-relative paths for within-slot duplicate basenames through validation, PSARC rebuild, and full payload audit.
- Allowed slot-separated profile extraction when the same basename occurs in both install archives.

## 2.4.0 - 2026-08-22

- Added a payload-free `profile-oracle` comparison for two caller-supplied packed install pairs.
- Counted archive contracts, full-name and basename overlap, ambiguous routing, added packages, and exact shared-package byte identity without persisting names, paths, payloads, or payload hashes.
- Added catalog-only mode for fast structural comparison and kept direct replacement transfer fail-closed even when retail names, sizes, or bytes match.
- Documented NPUA80480 as a separate packed target profile while retaining BCUS98119 as the primary established render/decomp authority.

## 2.3.0 - 2026-08-22

- Added exact big-endian RSX `cmp32` decoding for signed X11G11Z10 components and derived W=1.
- Mirrored the maintained RPCS3 sign-extension and normalization contract and required exact word reconstruction.
- Closed numeric coverage for all five bound visible-draw arrays while keeping their meanings unproved.

## 2.2.0 - 2026-08-22

- Added a fail-closed search for interleaved numeric RSX arrays that uniquely rebuild XPP stream zero.
- Required exact bytes, hashes, vertex records, layout coverage, geometry-heap bounds, and non-constant evidence.
- Kept attribute meanings, the three compressed streams, model export, and model injection unproved.

## 2.1.0 - 2026-08-22

- Added aggregate numeric decoding for bound RSX `float32`, `float16`, and `unorm8` character arrays.
- Required exact decode/re-encode byte and SHA-256 round trips while keeping captured payload bytes out of reports.
- Kept `cmp32` and every model semantic fail-closed; export and injection remain unauthorized.

## 2.0.0 - 2026-08-21

- Replayed captured RSX draw state and bound exact character topology hits to active vertex descriptors and memory extents.
- Kept raw attribute numbers semantic-free until numeric and packed-XPP correlation can prove their meaning.

## 1.7.0 - 2026-08-21

- Added deterministic skinned-XPP and owned Fallout 4/76 NIF compatibility reports.
- Added payload-free RPCS3 capture matching and live draw-memory binding for exact character index streams.
- Kept model conversion and injection blocked behind target stream, palette, weight, hierarchy, material, and runtime gates.

## 1.6.0 - 2026-08-21

- Added atomic, explicit `runtime-bundle` output for hash-bound host GPU replacement canaries.

## 1.5.0 - 2026-08-21

- Added `runtime-index` to hash strictly validated descriptor, face-chain, mip, and mip-prefix payloads.
- Covered the RSX BCn boundary that omits sub-4x4 tail mips during upload.
- Added deterministic JSON indexes and plain SHA-256 allowlists for opt-in emulator tracing.
- Kept runtime conclusions honest: an exact miss means unused or transformed-before-upload, not automatically unused.

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

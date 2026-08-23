# xpp-tool durable tool inventory

This inventory registers bounded commands that were added to unblock a parent
modding or reverse-engineering goal. A command enters this file only after it
has a callable contract, rejection tests, no-overwrite behavior, deterministic
output where applicable, an operator card, and a maintained source location.

## `xpp-tool.character-material-gap-locator.v1`

- Status: maintained; introduced and callable in xpp-tool 2.38.0.
- Parent goal: finish every character/item once as a correctly textured,
  editable Blender asset for near-term RPCS3 mods and later native-decomp
  import, while choosing runtime captures from evidence rather than guesses.
- Binary question: are the faces left unresolved by one strict material union a
  detached mesh chunk, or bounded patches embedded in the already recovered
  component, and where do they sit in diagnostic mesh and proved UV space?
- First answer: Zeke hair's six unresolved faces are two edge-disconnected
  three-triangle patches. They use 10 unique vertices, all 10 are also used by
  observed hair, every gap face shares an edge with observed hair, and the gap
  occupies 1.2627171% of diagnostic surface area and 0.9167829% of UV triangle
  area. This rejects a missing detachable hair chunk.
- Entry point: `xpp-tool character-material-gap-locator` (also exposed by the
  compatible `if1-tex` alias).
- Implementation:
  `src/infamous_xpp_textures/material_gap_locator.py`, SHA-256
  `781443afdb83438970ff0e7a54dd44733b56841e6f4b3c5dd268a994f75c4249`;
  CLI wiring `src/infamous_xpp_textures/cli.py`, SHA-256
  `3354f83acd2a629c386edf6072aa64dd63fa17017f9c035b00a4828d0e74837a`.
- Tests: `tests/test_material_gap_locator.py`, SHA-256
  `3678e4f4e8bcb569eddd574fe6a12e8873405284eb199ddb685eee4937ab67a1`.
  Seven focused tests cover deterministic aggregates, input-hash drift, report
  count drift, unknown primitive roles, symlink input, atomic no-overwrite CLI,
  and full-coverage rejection. The complete suite passes **273 tests**.
- Operator card: [README — Version 2.38](README.md#version-238--strict-material-gap-spatial-and-uv-locator).
- Inputs: one canonical strict observed-union GLB plus its exact export report;
  both require explicit SHA-256 pins and regular non-symlink files.
- Output: deterministic payload-free JSON at a new path with connectivity,
  adjacency, aggregate diagnostic position/UV bounds and normalized centroids,
  area fractions, and dominant diagnostic orientation counts.
- Bounds: 64 MiB GLB; 256 KiB input report/output; 1,048,576 vertices and
  triangles; one process; no runtime, network, input mutation, symlink output,
  or overwrite.
- First evidence: source A/B and isolated installed-wheel output are
  byte-identical at **5,020 bytes**, SHA-256
  `1af28fdb445b9c6f8b75f801f92f9f7023ecadfb787b8120bb9cf0e337d0acab`.
- Build proof: two pinned-epoch **252,287-byte** wheels are byte-identical,
  SHA-256 `870c6fe97bb9251906a2a50cf3cf8b1fd6c056d9d4410c4f82e1dbad856b504c`;
  a fresh isolated environment installed 2.38.0, exposed the command, and
  reproduced the exact owned report.
- Limitations: diagnostic positions are not proved object/world space;
  dominant directions are not camera directions; UV location does not assign
  a material. The tool does not close the six faces, recover retail normals/
  tangents or rigging, create 4x/PBR textures, repack an RPCS3 mod, or import
  into the native decomp.
- Return status: `returned-with-capability-and-evidence`. Use exact runtime pass
  history to seek a different material pass, state, or LOD. A camera rotation
  alone does not prove that the game will issue a different index/material draw.

## `xpp-tool.runtime-xpp-source-census.v1`

- Status: maintained since 2.19.0; extended with callable retail index-coverage
  receipts in xpp-tool 2.35.0.
- Parent goal: complete every character/item once as an editable Blender asset
  for near-term RPCS3 mods and later native-decomp import without discarding a
  useful partial-range draw or assigning material to unproved faces.
- Binary question: after one runtime draw is uniquely bound to an exact XPP
  stream slice, is its triangle multiset a retail subset, do all indices stay
  inside the mapped vertex range, and what does the safe per-record union leave
  unobserved?
- First answer: all 45 uniquely bound events across three Zeke pages validate.
  Hair page one contributes a third index payload with 276 retail triangles and
  a maximum index of 182 inside the exact 183-vertex slice. Page two adds 11
  unique occurrences and page three adds one, proving a 288/294 topology union
  with six unobserved.
- Entry point: `xpp-tool runtime-xpp-source-census` (also exposed by compatible
  `if1-tex`).
- Implementation: `src/infamous_xpp_textures/source_correlation.py`, SHA-256
  `b212b2f7acdc88025ea38f11e2692a716a697808dcd172e60a3e818f741f67ef`;
  CLI wiring remains in `src/infamous_xpp_textures/cli.py`, SHA-256
  `687289ccf0a5fa9cd7fabd3144981fb547bd503d8a51a9070bbcc40348137e83`.
- Tests: `tests/test_source_correlation.py`, including deterministic union,
  partial-range containment, out-of-retail rejection, mapped-range escape,
  payload privacy, bounds, and no-overwrite behavior; SHA-256
  `37aab68ab01fbfa4c3ed5971a58af86d22942f75c63836c8e0e98e915e12d621`.
  The complete repository suite passes **247 tests**.
- Operator card: [README — Version 2.35](README.md#version-235--safe-retail-coverage-from-partial-range-runtime-draws).
- Inputs: one regular non-symlink XPP capped at 64 MiB; one exact allowlist;
  one through 17 immutable v3/v4 page bundles and exact cumulative exclusions.
- Output: deterministic payload-free JSON at a new path outside every input,
  with per-event admission and per-record retail-ordered union receipts.
- Bounds: inherited 16 events per page, 64-byte source stride, 256 KiB report,
  one process, no runtime, network, input mutation, symlink output, or overwrite.
- Proven capability: exact source/bundle/exclusion/index identities; retail
  triangle-subset and mapped-range containment; deterministic distinct-payload
  union; fail-closed malformed, out-of-retail, out-of-range, bound, and occupied
  output behavior.
- First evidence: two byte-identical 103,798-byte reports, SHA-256
  `990a50de1cee155e5efc887e43c291c00047dedf74109bb8d658b5b4d474f865`;
  occupied-output retry exited 1 and preserved the same hash.
- Build proof: two builds with pinned `SOURCE_DATE_EPOCH=1787491200`
  produced the identical 232,337-byte 2.35.0 wheel, SHA-256
  `8e264dd9ec4e672d6c0996e9371b5549ef8ef2f6390c918056f72ef726d46ee3`;
  a fresh isolated environment installed it and exposed the extended command.
- Limitations: 288/294 is topology evidence, not yet the canonical material
  assignment. Page-one UV/shader/named-texture admission, six remaining faces,
  full character assembly, rig/skin, 4×, authored PBR, RPCS3 round trip, and
  native import remain separate gates. No render was created or withheld.
- Return status: `returned-with-capability-and-evidence`. Wire the admitted
  page-one receipt into the strict material union without changing the locked
  matte hair baseline, then capture again only for the final six topology faces.

## `xpp-tool.character-material-pass-census.v1`

- Status: maintained; introduced and callable in xpp-tool 2.33.0.
- Parent goal: preserve every real texture/material possibility for each
  character component while completing one editable asset for RPCS3 mods and
  later native-decomp import, without mistaking a layered pass for a missing
  mesh piece.
- Binary question: across different exact shader/texture passes for one retail
  character record, which triangle multisets are identical, subset, superset,
  partially overlapping, or disjoint, and what does their any-pass union leave
  unobserved?
- First answer: Zeke hair has three exact pass signatures but only two distinct
  runtime index payloads. Page-three `A/C/N/S` and `C/N` are coextensive over
  275 triangles; page two versus page three overlaps on 268 and contributes
  seven unique triangles per side. The union remains 282/294 with 12 unobserved.
- Entry point: `xpp-tool character-material-pass-census` (also exposed by the
  compatible `if1-tex` alias).
- Implementation: `src/infamous_xpp_textures/material_pass_census.py`, SHA-256
  `f96f5d15a8222087b5bda42b22339d6850763043d69df17b975a7e9729516ff4`;
  CLI wiring `src/infamous_xpp_textures/cli.py`, SHA-256
  `7dff6593dbe3fa184c28d0f4bde4c5a7db195bfb002f41b89bd2de1be2d60ac8`.
- Tests: `tests/test_material_pass_census.py`, SHA-256
  `a3bff3adc3326ea0a6f67b1053b55efbae98c17b256bd4d2bf1c157314778832`.
  The complete repository suite passes **244 tests**.
- Operator card: [README — Version 2.33](README.md#version-233--exact-cross-material-pass-census).
- Inputs: exact retail XPP and SHA-256; exact texture allowlist; one record
  offset; two through 32 checksum-pinned strict observed-only material reports,
  their immutable v3/v4 bundles, and required v4 capture-key exclusions.
- Output: deterministic, input-order-independent, payload-free JSON at a new
  path. It preserves exact pass signatures, normalized authorities, pairwise
  multiset relations, and the retail-ordered any-pass covered/unobserved hashes.
- Bounds: 64 MiB XPP; 1 MiB per report; 32 observations; inherited bounded
  bundles; 512 KiB output; one process; no runtime, network, symlink output, or
  overwrite.
- Proven capability: exact source/topology/bundle/page/index/shader/UV/texture
  reconciliation; cross-signature coextensive-pass detection; deterministic
  pairwise multiset classification and any-pass union; fail-closed duplicate,
  drift, out-of-retail, bound, and occupied-output behavior.
- First evidence: A/B 13,180-byte report, SHA-256
  `6b363998f495ea9e8bd318cff8da62a3e06c920088f8098f9b2c34cb8c280048`;
  occupied-output retry exited 1 and preserved the same hash.
- Build proof: two builds with pinned `SOURCE_DATE_EPOCH=1787486400`
  produced the identical 225,509-byte 2.33.0 wheel, SHA-256
  `0cfec335a3376de6d761a0a315ddc062eb9448087b03f0c1cc5d4301e3943633`;
  a fresh isolated environment installed it and exposed the command.
- Limitations: this does not infer authored PBR roles or compositing order,
  assign the twelve unobserved faces, find missing body pieces, recover original
  normals/tangents or rigging, upscale textures, repack an RPCS3 mod, or import
  into the native decomp. It creates no render; no image is withheld.
- Return status: `returned-with-capability-and-evidence`. Preserve all three
  pass signatures, keep the approved matte hair render as the visual baseline,
  and capture again only for a genuinely different pose/state/draw.

## `xpp-tool.character-material-coverage-export.v1`

- Status: maintained; introduced in 2.32.0 and extended with strict partial-
  range material observations in xpp-tool 2.36.0.
- Parent goal: finish every character/item once as a complete editable asset,
  first for validated RPCS3 package mods and later for native-decomp import.
- Binary question: can one exact repeated-draw material union become a
  deterministic strict Blender GLB without serializing game payloads in its
  receipt or assigning the material to unproved faces?
- First answer: two full-range Zeke hair observations plus one safely bounded
  partial-range observation export record 533752 as one 184-vertex /
  294-triangle GLB with 288 exact retail `Zeke_Hair_C/N` triangle assignments
  and six separate orange diagnostic triangles.
- Entry point: `xpp-tool character-material-coverage-export` (also exposed by
  the compatible `if1-tex` alias).
- Implementation: `src/infamous_xpp_textures/material_coverage_export.py`,
  SHA-256 `79d387decf7ce8c404575525865b4985195f241b7297eb6eae3f71d055d7c54f`;
  union owner `src/infamous_xpp_textures/material_coverage.py`, SHA-256
  `3c3298c28703257d656a8f7db7c33953867185b93643277e3f1855ae99dd80c8`;
  strict GLB owner `src/infamous_xpp_textures/character_material_export.py`,
  SHA-256 `df5d9fa41663fb6596f8ec04d822db2f33030eb4c7f02f172393a06b7dc7b8b9`;
  CLI wiring `src/infamous_xpp_textures/cli.py`, SHA-256
  `342820d98f72e844697a8fb3f7c33bcaa7cc427805ca6c21c3760c65a2f8b003`.
- Tests: `tests/test_material_coverage_export.py`, SHA-256
  `8b2f54831b9641cab534cfe2b895e7e1fa781ebe3f0a3690c1f784cdeb4c6628`,
  plus union, strict exporter, and component-ledger suites. The complete
  repository suite passes **252 tests**.
- Operator card: [README — Version 2.36](README.md#version-236--strict-partial-range-material-observations).
- Inputs: one exact retail XPP/SHA-256; allowlist; record offset; one through 16
  checksum-pinned strict material observations with immutable v3/v4 bundles and
  required exclusions; zero through 15 eight-value partial observations with
  pinned lineage/source-census/character-census authorities; one pinned anchor
  lineage that must identify exactly one accepted full-range observation.
- Outputs: deterministic private/operator GLB plus deterministic payload-free
  JSON receipt, atomically published to two different new paths outside every
  immutable input and bundle.
- Bounds: 64 MiB XPP/GLB; 1 MiB per material report; 16 observations; inherited
  bundle limits; 256 KiB union/export receipts; one process; no runtime, network,
  symlink input/output, or overwrite.
- Proven capability: full revalidation and in-memory exact retail-ordered union;
  exact anchor selection; union/topology/UV/family/texture/count/hash
  reconciliation; strict proved/unresolved primitive partition; deterministic
  A/B GLB/report; component-ledger admission; fail-closed ambiguity, drift,
  malformed evidence, and occupied-output behavior.
- First evidence: GLB A/B 179,204 bytes / SHA-256
  `f11dc2be73ccba0aaad2576b76ae8e904c7302e8e44f3a6aa18c1cbc81705e3b`;
  report A/B 5,430 bytes / SHA-256
  `87a41528677928bb555217b6c21916dfef65ea85f81eabbbae91433a357034eb`;
  approved immediate unlit audit 1,187,390 bytes / SHA-256
  `6b80f816abb734ad3e2dde45225252dd28521c34b099c0142e4a024d42095cc1`.
- Build proof: two builds with pinned `SOURCE_DATE_EPOCH=1787494800`
  produced the identical 238,586-byte 2.36.0 wheel, SHA-256
  `10096f105406b83260591b12d1b60ecda63ce1196f7f06d78671c92a761463d7`.
- Limitations: this does not close the six remaining hair assignments,
  identify every hair/head/body piece, recover retail normals/tangents or
  rig/skin, create 4×/authored-PBR material, reverse-pack an RPCS3 mod, or import
  into the native decomp. The approved clean matte render remains the separate
  appearance baseline.
- Return status: `returned-with-capability-and-evidence`. Keep this 288/294
  strict component and the approved matte/even-brown look; capture a genuinely
  different compatible hair pass for the remaining six faces.

## `xpp-tool.character-material-coverage-union.v1`

- Status: maintained; introduced in 2.31.0 and extended with strict partial-
  range observations in xpp-tool 2.36.0.
- Parent goal: finish every character/item once as a complete editable asset,
  first for validated RPCS3 package mods and later for native-decomp import.
- Binary question: do repeated exact runtime draws of one retail record expose
  new material-bound triangle occurrences, and does their union cover the full
  retail triangle multiset?
- First answer: page one plus page two proves jacket 493/1,002, head 185/404,
  packs 170/302, and jacket detail 24/24. Page three then advances compatible
  full-range hair coverage from 275 to 282/294. Revalidating the old page-one
  183-of-184 vertex tray as a safe partial observation advances hair to 288/294.
- Entry point: `xpp-tool character-material-coverage-union` (also exposed by
  the compatible `if1-tex` alias).
- Implementation: `src/infamous_xpp_textures/material_coverage.py`, SHA-256
  `3c3298c28703257d656a8f7db7c33953867185b93643277e3f1855ae99dd80c8`;
  CLI wiring `src/infamous_xpp_textures/cli.py`, SHA-256
  `342820d98f72e844697a8fb3f7c33bcaa7cc427805ca6c21c3760c65a2f8b003`.
- Tests: `tests/test_material_coverage.py`, SHA-256
  `ba31735594589fd66f00446ddb9b51671ec3928aaa40765c61921573f661addb`.
  The complete repository suite passes **252 tests**.
- Operator card: [README — Version 2.36](README.md#version-236--strict-partial-range-material-observations).
- Inputs: exact retail XPP and SHA-256; exact texture allowlist; one source
  record offset; one through 16 pinned strict material reports with immutable
  v3/v4 bundles and required v4 capture-key exclusions; optional eight-value
  partial observations add pinned lineage, source census, and character census
  authorities while one full-range report remains mandatory.
- Output: deterministic, input-order-independent, payload-free JSON at a new
  path, preserving per-observation new/overlap/cumulative counts and exact
  covered/unobserved multiset hashes.
- Bounds: 64 MiB XPP; 1 MiB per report; 16 observations; inherited bounded
  bundles; 64 KiB completion marker; 256 KiB output; one process; no runtime,
  network, symlink output, or overwrite.
- Proven capability: exact source/topology/UV/family/texture/bundle/page/index
  reconciliation; multiset union without duplicate inflation; deterministic
  A/B results; bounded partial-range index and census revalidation; fail-closed
  duplicate, conflict, dishonest multiset, out-of-range, out-of-retail, and
  overwrite behavior. A/B union reports are 5,283 bytes / SHA-256
  `3f90252ab92980500ceea34dca3f5a9a486adb874109079aea233509a5dae825`.
- Build proof: two builds with pinned `SOURCE_DATE_EPOCH=1787494800`
  produced the identical 238,586-byte 2.36.0 wheel, SHA-256
  `10096f105406b83260591b12d1b60ecda63ce1196f7f06d78671c92a761463d7`.
- Limitations: an exact triangle union does not identify assembly placement,
  recover original normals/tangents, create bones/weights, upscale textures,
  author PBR, repack an RPCS3 mod, or import into the native decomp.
- Return status: `returned-with-capability-and-evidence`. Preserve the accepted
  matte Zeke render; capture a different compatible hair draw for the six open
  faces and deliberately different pose/state/occlusion draws for jacket,
  head, and packs.

## `xpp-tool.character-component-ledger.v1`

- Status: maintained; introduced in 2.30.0 and extended with exact material-pass
  receipts in 2.34.0; the 2.36 canonical run admits strict partial-derived
  material exports without changing this ledger's schema.
- Parent goal: recover every character/item component once, retain every useful
  render immediately, and assemble one canonical editable asset for near-term
  RPCS3 mods and later native-decomp import without confusing partial proof for
  a finished character.
- Binary question: which checksum-pinned material exports are distinct source
  components, which are repeat runtime observations of one component, what
  exact gates has each closed, and what work remains without repetition?
- First answer: five exact BCUS Zeke records are reconciled: hair 533752,
  jacket 534628, packs 535048, head 536280, and jacket detail 536488. Jacket
  detail alone has complete material coverage; the other four retain explicit
  unresolved face counts. The current ledger promotes hair to 288/6, receipts
  six images, and preserves both approved matte/unlit hair views. The immutable
  2.34 ledger remains the authority for the older three-signature 282/12 pass
  census; it is not falsely attached to the expanded 288-face union.
- Entry point: `xpp-tool character-component-ledger` (also exposed by the
  compatible `if1-tex` alias).
- Implementation: `src/infamous_xpp_textures/component_ledger.py`, SHA-256
  `bbec0935e13eb3e13dccff670522740176628c0249f6a04e98fe4c1f60fe0a76`;
  CLI wiring `src/infamous_xpp_textures/cli.py`, SHA-256
  `342820d98f72e844697a8fb3f7c33bcaa7cc427805ca6c21c3760c65a2f8b003`.
- Tests: `tests/test_component_ledger.py`, SHA-256
  `9e90757be9d31463cc528eb5b40d81b2308ff2d0d71512e662512de407bccd57`.
  The complete repository suite passes **252 tests**.
- Operator card: [README — Version 2.34](README.md#version-234--preserve-material-passes-in-the-canonical-character-ledger).
- Inputs: one through 256 exact material-export JSON files, each with a matching
  SHA-256 pin; canonical title/build/candidate tokens; optional exact visual-
  baseline receipt manifest and pin; zero through 128 exact material-pass-census
  JSON files, each with its matching SHA-256 pin.
- Output: deterministic payload-free JSON at a new path. It preserves component,
  source, event, lineage, topology, coverage, GLB, retail texture, published
  image, and open-gate identities without serializing private paths or payloads.
- Bounds: 1 MiB per material report; 512 KiB per pass census; 256 KiB visual
  manifest; 256 material observations; 128 pass censuses/components; 32 pass
  observations per census; 256 render receipts; 1 MiB output; one process; no
  runtime, network, symlink input/output, or overwrite.
- Proven capability: exact hash validation; source-record component identity;
  conservative alias merge; strict cross-pass receipt normalization and union-
  export reconciliation; immutable-geometry conflict, duplicate, schema,
  texture-family, triangle-coverage, relationship, group, and unknown-evidence
  rejection; deterministic 44,794-byte A/B output SHA-256
  `d64a613f6be9a165537493c98f730d77945108a7969524207dcc7fd748e7f44e`.
- Current promotion proof: two 30,923-byte ledgers are byte-identical at
  SHA-256 `e61a78fbdc80cecaa97984cbc0cca3bd9a53df075134b0afbd7b5bba79a9553c`;
  five components, five material observations, six render receipts, one
  accepted-baseline component, hair 288/6, and no overwritten historical
  pass-census claim.
- Build proof: two builds with pinned `SOURCE_DATE_EPOCH=1787488800`
  produced the identical 230,065-byte 2.34.0 wheel, SHA-256
  `81f68fcc6ccd4005c03a6302c8d2e184c5e867367eaa34d9f926473db05e303c`;
  a fresh isolated environment installed it and exposed both pass-census inputs.
- Current distribution proof: two 2.36.0 builds with pinned
  `SOURCE_DATE_EPOCH=1787494800` produced the identical 238,586-byte wheel,
  SHA-256
  `10096f105406b83260591b12d1b60ecda63ce1196f7f06d78671c92a761463d7`.
- Limitations: a visual baseline protects appearance only. It cannot prove
  unresolved material faces, assembly placement, missing body pieces, original
  normals/tangents, bones/weights, 4×, authored PBR, RPCS3 repack/gameplay, or
  native import. A pass receipt preserves possibilities but does not infer PBR
  roles or compositing order. Every delivery gate remains independently false.
- Return status: `returned-with-capability-and-evidence`. Continue with the four
  exact incomplete material-coverage component IDs before widening to the next
  page; never redo the completed jacket-detail component.

## `xpp-tool.character-material-candidate-census.v1`

- Status: maintained; introduced and callable in xpp-tool 2.29.0.
- Parent goal: recover every unfinished character/item component once, render it
  immediately, and preserve one canonical asset record for both RPCS3 package
  mods and the later native-decomp importer.
- Binary question: after exact completed draws are excluded, which full-source-
  range candidates on one runtime page pass the complete geometry → packed UV →
  shader → named retail texture lineage, and why do the others fail?
- First answer: the page-two source census contains six eligible candidates.
  Excluding completed event 5 / record 536488 and hair events 15–16 / record
  533752 leaves three; all three pass as exact `Zeke_Jacket`, `Zeke_Head`, and
  `Zeke_Packs` lineages.
- Entry point: `xpp-tool character-material-candidate-census` (also exposed by
  the compatible `if1-tex` alias).
- Implementation: `src/infamous_xpp_textures/character_material_census.py`,
  SHA-256 `6170a8370550a4f7af5ed64f63d4f2606682d448167e13d9fa99574b209f01c4`;
  CLI wiring `src/infamous_xpp_textures/cli.py`, SHA-256
  `c70667545fbdb142a0ec43a2d4ac734909cab927c395c8f4d4f2b5c7287de7c6`.
- Tests: `tests/test_character_material_census.py`, SHA-256
  `e00f04ef4e5aea3c133bc087b8660b8d2c28d2afbb35f0d4cdd907f5459a3b42`.
  The complete repository suite passes **226 tests**.
- Operator card: [README — Version 2.29](README.md#version-229--full-range-character-material-candidate-census).
- Inputs: complete v3/v4 bundle; exact allowlist and required page exclusion;
  checksum-pinned source and character censuses; page/side; repeatable exact
  completed `EVENT:RECORD_OFFSET` exclusions.
- Output: deterministic payload-free JSON at a new path. It records every
  accepted lineage identity and every rejected candidate/reason without raw
  model, shader, index, vertex, or texture bytes.
- Bounds: 272 source event rows; 16 eligible candidates/exclusions; 2 MiB per
  JSON authority; inherited bounded bundle payloads; 512 KiB output; one
  process; no runtime, network, symlink output, or overwrite.
- Proven capability: complete full-range selection from the source authority;
  exact completed-candidate subtraction; per-candidate reuse of the maintained
  lineage proof; deterministic accepted/rejected reconciliation; final A/B
  5,901-byte report SHA-256 `6a5b5c88…87692b`.
- Limitations: the census does not write the full per-candidate lineage or GLB,
  assign unobserved faces, identify assembly placement, prove rigging/PBR/4×,
  repack RPCS3 content, or import into the native decomp.
- Return status: `returned-with-capability`. Use the three accepted identities
  to retain their already published renders and reconcile them into the
  canonical completion inventory; continue with the next page without repeating
  completed work.

## `xpp-tool.character-material-export.v1`

- Status: maintained; introduced in 2.26.0 and callable in xpp-tool 2.29.0.
- Parent goal: complete editable character/item assets once, deliver them first
  through validated retail RPCS3 packages, then reuse the same canonical
  records through a native-decomp importer.
- Binary question: can one exact shader-lineage record become a deterministic
  GLB with its full topology, proved UV layer, and exact runtime-matched retail
  shader-selected images without promoting unresolved semantics?
- First answer: Zeke hair record 533752 exports as 184 vertices, 294 triangles,
  one `TEXCOORD_0` layer, generated inspection normals, and embedded
  `Zeke_Hair_C.psd` / `Zeke_Hair_N.psd` mip-zero PNGs. The exact runtime draw
  proves that material for 275 triangles and leaves 19 explicitly unobserved.
- Second answer: Zeke record 536488 exports as 26 vertices / 24 triangles with
  full exact runtime material coverage and four embedded `Zeke_Jacket` images.
  `C` and `N` receive display wiring; `A` and `S` remain embedded with explicit
  unassigned roles instead of being promoted into a guessed PBR contract.
- Entry point: `xpp-tool character-material-export` (also exposed by the
  compatible `if1-tex` alias).
- Implementation: `src/infamous_xpp_textures/character_material_export.py`;
  deterministic PNG bytes in `src/infamous_xpp_textures/pngio.py`; CLI wiring
  in `src/infamous_xpp_textures/cli.py`.
- Tests: `tests/test_character_material_export.py` plus the existing material,
  PNG, shader-lineage, fragment-sampler, topology, and XPP suites.
- Maintained source pins at 2.32.0: exporter
  `df5d9fa41663fb6596f8ec04d822db2f33030eb4c7f02f172393a06b7dc7b8b9`;
  PNG encoder
  `2ce34b184d48822ae578eadae96c1717c88ee2a8eef001fcf40ac7563c54c6b2`;
  CLI `1a3c5c13c703e08fa361fd3f1232bd11f901773aed0bc8c678b8cc83ab8592e9`;
  focused tests
  `de153beab18fb8c52a02a7159bd7de3f59001219d3a6bdb96a6d3f57b89f3363`.
- Operator card: [README — Version 2.28](README.md#version-228--complete-shader-bound-texture-family-glb-export).
- Inputs: exact retail XPP; complete v3/v4 bundle; allowlist and required paging
  exclusion; checksum-pinned 2.25-or-later compatible lineage report.
- Outputs: deterministic payload-bearing GLB for private/operator asset work and
  a separate deterministic payload-free JSON receipt. Both destinations are
  new-only and published as one fail-closed pair.
- Bounds: 64 MiB XPP, inherited 64 MiB bundle payload, 64 MiB GLB, 256 KiB
  report, regular immutable authorities, one process, no network, no overwrite.
- Proven capability: full retail index topology, exact full vertex range,
  shader-proved half2/half3 `TEXCOORD_0`, encoded runtime-prefix/retail-descriptor
  identity before decode, all shader-bound retail images embedded in sampler
  order, required color/normal display wiring with extra roles left unassigned,
  exact runtime material subsets, deterministic strict/preview GLBs, and Blender
  import/render through the maintained decomp review tool. Hair strict is
  178,024 bytes / `1a2a3eaa...018f0b68`; its clean preview is 177,084 bytes /
  `39d8773b...0e71f6b`. Jacket detail is 513,796 bytes /
  `bd364627...e3c47` with full 24-triangle coverage.
- Limitations: position attribute 0 remains diagnostic; inspection normals are
  generated; `C`/`N` roles are retail-name-derived; the hair record's 19
  unobserved triangles remain unresolved while the jacket-detail record is fully
  covered. The old metallic review override is rejected. Full character, all
  materials, authored 4x/PBR, rigging, RPCS3 round trip, and native import remain
  false.
- Return status: `returned-with-evidence`. Preserve the accepted hair and jacket
  detail components, then repeat the chain across the remaining Zeke pieces and
  join them only through the canonical assembly/completeness inventory.

## `xpp-tool.character-uv-texture-binding.v1`

- Status: maintained; introduced in 2.25.0 and extended with fail-closed safe
  partial-range lineage in xpp-tool 2.36.0.
- Parent goal: turn every character/item into a complete, correctly textured,
  editable Blender asset that can first round-trip through retail RPCS3 and
  later import unchanged through the native decomp's asset API.
- Binary question: for one exact source-bound draw, which packed input
  components feed the fragment texture coordinates, and which unique named XPP
  texture descriptors are sampled through that path?
- First answer: Zeke record 533752 attribute 9 XY feeds vertex output 7 / TEX0;
  samplers 0 and 2 select `Zeke_Hair_N.psd` and `Zeke_Hair_C.psd`. The 8-byte
  stream has one valid complete layout: attribute 3 at byte 0, half2 attribute
  9 at byte 4.
- Second answer: Zeke record 536488 has one valid complete 10-byte packed-source
  layout: four-byte attribute 3 at byte 0 and six-byte half3 attribute 9 at
  byte 4. Attribute 9 XY feeds TEX0 and samplers 0–3 select the exact
  `Zeke_Jacket` N/A/S/C family for all 26 vertices / 24 retail triangles.
- Third answer: the older page-one Zeke hair tray is a safe partial lineage,
  not a full source range. Its 183 rows cover source vertices 0-182; all 276
  runtime triangle occurrences stay inside that range and exist in the exact
  294-triangle retail multiset. Its A/C/N/S descriptors and mip prefixes
  independently match the pinned character census.
- Entry point: `xpp-tool character-uv-texture-binding` (also exposed by the
  compatible `if1-tex` alias).
- Implementation: `src/infamous_xpp_textures/shader_lineage.py`; fragment
  source decode in `src/infamous_xpp_textures/fragment_sampler.py`; CLI wiring
  in `src/infamous_xpp_textures/cli.py`.
- Tests: `tests/test_shader_lineage.py` and `tests/test_fragment_sampler.py`.
- Maintained source pins at 2.36.0: implementation
  `20d05ea1b56b3e90c7231fbfee34a6fbe12cf4e0df5206edf3ec2254298e0ebd`;
  fragment decoder
  `6db0ea37623a262299b50e1c071a6aa4846711b404d6bdce3c50a899b11443e3`;
  CLI `342820d98f72e844697a8fb3f7c33bcaa7cc427805ca6c21c3760c65a2f8b003`;
  focused lineage tests
  `6c696da717ad0e12726ffd337404a03d818cc68a84b99d1304c8da2d031e61fc`
  and
  `afd10a8f77fed71a6895eb5ad1a1e592c9bd9be499437588347c524d927bde09`.
- Operator card: [README — Version 2.36](README.md#version-236--strict-partial-range-material-observations).
- Inputs: complete v3/v4 shader bundle; exact texture allowlist and prior-page
  exclusion where required; source census and pin; character census and pin;
  bounded page/event/source-record/character-side selection. Partial lineage
  additionally requires the source census to prove every runtime index stays
  inside the exact captured vertex range and retail triangle multiset.
- Output: deterministic payload-free JSON at a new path. The report records
  only identities, counts, bounded float minima/maxima, descriptor names, and
  lineage tokens.
- Bounds: 16 draw events; 64 MiB inherited bundle payload; six attributes in
  the selected layout permutation; 2 MiB per JSON authority; 256 KiB output;
  regular immutable inputs; no overwrite, runtime, network, or concurrency.
- Proven capability: exact source record/block bytes; target sampler coordinate
  input; component-level branch-free vertex lineage; unique complete packed
  layout using guest source-storage widths rather than host-upload padding;
  unique named texture-prefix identities; one geometry-to-UV-to-texture binding.
- Proven partial capability: the report keeps `full_source_vertex_range=false`,
  records exact range bounds and multiset hashes, and becomes eligible for a
  material union only when every source/bundle/shader/texture/index authority
  independently reconciles.
- Limitations: the old capture did not directly serialize attribute byte
  offsets, so byte 4 is a unique finite complete-tiling reconstruction. Packed
  guest widths are not host-upload widths. Name suffixes do not prove native
  PBR channel semantics. Full character, every material, 4x/PBR, RPCS3 round
  trip, and native import remain false.
- Return status: `returned-with-evidence`. Preserve the safe page-one hair
  lineage and continue the remaining six faces or the next source-bound record
  without treating a partial tray as complete.

## `xpp-tool.asset-completion-inventory.v1`

- Status: maintained; source-defined and callable as xpp-tool 2.24.0.
- Parent goal: avoid duplicate extraction/render work while producing one
  canonical character/item asset record for a near-term RPCS3 retail mod and a
  later native-decomp import.
- Binary question: which exact work classes are already proved for each known
  asset, which evidence remains unmatched, and what is the first unfinished
  batch whose source identity is backed by a checksum-pinned census?
- Answer for the first inventory: 57 retail static GLB exports and 19 unique 8K
  asset renders are retained; one gameplay screenshot is separated from those
  renders, one duplicate Drive entry is recorded, and character renders remain
  zero. The 68 canonical/evidence records are 0 complete, 58 partial, and 10
  unknown; Zeke is the first evidence-selected unfinished batch.
- Entry point: `xpp-tool asset-completion-inventory` (also available through the
  compatible `if1-tex` alias).
- Implementation: `src/infamous_xpp_textures/asset_inventory.py`, SHA-256
  `16122c956130147ee18eba8d27d6312c4122e5594395b6038db0f5d195a37e83`;
  CLI wiring `src/infamous_xpp_textures/cli.py`, SHA-256
  `e9b46f80b21a3f6269c89bc19be13ef133949711ced6d775a5488617b0c02213`.
- Tests: `tests/test_asset_inventory.py`, SHA-256
  `1c6b1cbae88e8e805e7b0e8c31ca01d46257ff79d9a7f55027bcfc8dbbd547d3`.
- Operator card: [README — Version 2.24](README.md#version-224--canonical-completion-inventory-and-dual-output-manifest).
- Inputs: exact decomp tally and pin; retail GLB manifest and pin; metadata-only
  gallery snapshot and pin; character/item census and pin; candidate token that
  must occur in both census targets.
- Output: deterministic payload-free JSON to a new path. Private contact paths,
  game payload bytes, Drive IDs, and raw texture/model data are not serialized.
- Bounds: 5,000 static rows; 5,000 gallery rows; 4 MiB tally/static inputs;
  512 KiB gallery; 2 MiB census; 4 MiB output; regular non-symlink inputs;
  no-overwrite same-directory atomic publication; no runtime or network.
- Proven capability: exact source reconciliation, conservative render/model
  joins, duplicate separation, per-work-class skip decisions, partial/unknown
  preservation, first-batch evidence, and independent RPCS3/native delivery
  gates on one canonical record.
- Limitations: a retail GLB proves only that export, and an 8K PNG proves only
  that image. Orientation, alignment, complete piece inventory, material/UV
  correctness, rigging, 4x/PBR, retail round trip, and native import remain
  false until separately evidenced.
- Return status: `returned-with-evidence`. Resume the parent goal at the Zeke
  geometry-owner/material/UV chain, then prove a complete Blender asset and
  harmless retail round trip without waiting for native decomp readiness.

## `xpp-tool.character-asset-census.v1`

- Status: maintained; source-defined and callable as xpp-tool 2.23.0.
- Parent goal: produce complete, correctly oriented/aligned/textured Blender
  assets for every character and item, then round-trip edits into RPCS3 while
  preserving one canonical manifest for the native decomps.
- Binary question: across two complete checksum-pinned profiles, which names,
  texture descriptors, exact cross-package payloads, and packed geometry counts
  belong to one target—and which proposed piece/material/state relations remain
  unproved?
- Answer for the first Zeke audit: both builds have 31 named textures and 16
  packed geometry contracts. The 31 identities match uniquely but are reordered
  31/31. No substantial exact or partial target texture is supplied by another
  package; only one 8-byte 1×1 utility texture is shared outside the target.
- Entry point: `xpp-tool character-asset-census` (also available through the
  compatible `if1-tex` alias).
- Implementation:
  `src/infamous_xpp_textures/character_asset_census.py`, SHA-256
  `152d51eaa5763ba207f35dd5341caf7b9a52a2a659f99784ccd8407b71f18c85`;
  CLI wiring `src/infamous_xpp_textures/cli.py`, SHA-256
  `d154cb71127ce3afacef92da88d86fdd97b6035feae8567ec000bf0ad9428fbb`.
- Tests: `tests/test_character_asset_census.py`, SHA-256
  `72836c608183330a217d5f878555438e322d6f5514a9b06af9ceb095cd193d27`.
- Operator card: [README — Version 2.23](README.md#version-223--multipart-character-and-item-asset-census).
- Inputs: two extracted profile roots; exact workspace-manifest SHA-256 pins;
  two ordinal OID manifests and pins; workspace-relative target package per
  build; one unique manifest anchor; one name token; bounded anchor windows.
- Output: deterministic payload-free JSON to a new path. The command refuses an
  existing/symlink output and uses same-directory no-overwrite publication.
- Bounds: 4,096 packages / 8 GiB per profile; 256 MiB per package; 16 MiB per
  workspace/OID manifest; 512-row anchor windows; 20,000 detailed matches;
  2 MiB report; no network or runtime dependency.
- Proven capability: complete profile integrity scan; descriptor OID names;
  aligned package/chunk name references; topology-contract counts; exact
  descriptor and significant mip/prefix sharing; cross-build reorder-safe
  texture mapping; explicit completion and delivery gates.
- Limitations: an aligned name reference is not a per-mesh owner. Geometry/name,
  geometry/material/texture, UV, orientation, alignment, skeleton, LOD/state,
  completeness, Blender, RPCS3 round-trip, and native-decomp gates remain false.
- Return status: `returned-with-evidence`. Resume by proving the exact
  geometry-owner/name/material chain, then consume a verified completion
  inventory before corpus-wide batching so completed assets are not duplicated.

## `xpp-tool.character-source-runtime-correlate.v1`

- Status: maintained; source-defined and callable as xpp-tool 2.22.0.
- Parent goal: recover Zeke's complete, textured, editable character model and
  a verified reverse-import path from the packed retail source rather than a
  single camera-projected GPU fragment.
- Binary question: does one descriptor-backed packed XPP stream have a stable,
  full-three-axis relationship with an exact topology-paired runtime
  `float32x3` array, and does a narrower proper rotation/translation/uniform-
  scale constraint distinguish the affine-equivalent numeric families?
- Answer: yes for the two independent owned stream-1 records.
  `scale-offset-unsigned` ranks first under proper similarity for the
  184-vertex hair record (`R² 0.9999999971`) and the 26-vertex visible fragment
  (`R² 0.9912978824`).
- Entry point: `xpp-tool character-source-runtime-correlate` (the compatible
  `if1-tex` alias exposes the same command).
- Implementation:
  `src/infamous_xpp_textures/character_source_correlation.py`, SHA-256
  `ca9dba6cdbc5cb86c6a22914f892b3acb3026c257fa699c8b1f7dd63e9e60a9d`;
  CLI wiring `src/infamous_xpp_textures/cli.py`, SHA-256
  `9489fe6f53ee728778a07d21dae34e293446c6e616051d2f713dd0696b607429`.
- Tests: `tests/test_character_export.py`, SHA-256
  `6c6b7eb52815c07d3eada4b9b778ec45cb10f2b7c5d0e655ff1d08476e464180`.
- Operator card: [README — Version 2.22](README.md#version-222--proper-similarity-decoder-discriminator).
- Call shape:

  ```console
  timeout 60s xpp-tool character-source-runtime-correlate \
    --xpp INPUT.xpp --record-offset OFFSET \
    --runtime-index INDEX.bin --runtime-index-sha256 INDEX_SHA256 \
    --runtime-positions POSITIONS.bin \
    --runtime-positions-sha256 POSITIONS_SHA256 \
    --runtime-byte-order big --runtime-first-row 0 \
    --output NEW.json
  ```

- Bounds: XPP payload 64 MiB; runtime index and position array 16 MiB each;
  report 256 KiB; concurrency one; network none; operator wall clock 60
  seconds; immutable regular non-symlink inputs; new output only.
- Proven capability: exact topology identity, whole-buffer runtime identity,
  explicit row-window identity, packed stream identity, source three-axis rank,
  deterministic unrestricted-affine metrics, proper and mirrored similarity
  metrics, family margins, and cross-record stream/formula ranking.
- Limitations: proper-similarity ranking supplies a strong candidate but does
  not execute or prove the retail decoder, so the numeric family remains
  deliberately unselected. Position meaning, object/world space, component
  name, UVs, textures, materials, bones, skinning, complete-character
  assembly, PBR, and injection remain unproved.
- Return status: `returned-with-evidence`; the parent resumes by proving the
  candidate against the executed decode arithmetic and coordinate space for
  stream 1, then decoding UV and rig streams without relying on one
  flat-looking runtime view.

## `xpp-tool.character-source-diagnostic-export.v1`

- Status: maintained; source-defined and callable as xpp-tool 2.20.0.
- Parent goal: recover Zeke's complete, textured, editable character model and
  a verified reverse-import path without confusing diagnostic evidence for a
  finished model.
- Binary question: can one exact packed XPP character stream and its retail
  triangle topology be exported reproducibly for visual inspection under one
  explicit numeric hypothesis?
- Answer: yes; this returns a diagnostic GLB, not a semantic or rigged model.
- Entry point: `xpp-tool character-source-diagnostic-export` (the compatible
  `if1-tex` alias exposes the same command).
- Implementation:
  `src/infamous_xpp_textures/character_source_export.py`, SHA-256
  `90378a871f18387c50aefa4ac484424334c91025e5411750404550cce39de7cc`;
  CLI wiring `src/infamous_xpp_textures/cli.py`, SHA-256
  `dbeebb1b7c777083d77e57bb4336294f682f640b89b198395f7de0a6ce190e9b`.
- Tests: `tests/test_character_export.py`, SHA-256
  `1141938731997e1497c414812df0c201628e178e2bfa87962ac0c333d032369b`.
- Operator card: [README — Version 2.20](README.md#version-220--permanent-packed-source-diagnostic-export).
- Call shape:

  ```console
  timeout 60s xpp-tool character-source-diagnostic-export \
    --xpp INPUT.xpp --record-offset OFFSET --stream-index 1 \
    --numeric-family endpoint-unsigned \
    --output NEW.glb --json-out NEW.json
  ```

- Bounds: XPP payload 64 MiB; GLB 64 MiB; concurrency one; network none;
  operator wall clock 60 seconds; immutable input; new outputs only.
- Proven capability: exact record/stream selection, packed-byte and index-byte
  identity, MSB integer unpack, explicit numeric hypothesis, finite
  nondegenerate GLB, deterministic repeat output.
- Limitations: the numeric family and position meaning are not proved. UVs,
  textures, materials, skin weights, joints, skeleton, inverse binds, complete
  character assembly, PBR, and injection are not produced or authorized.
- Return status: `returned-with-capability`; the parent resumes by comparing
  the diagnostic source shape with the exact runtime-decoded record and then
  proving or rejecting the position formula.

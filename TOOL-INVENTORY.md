# xpp-tool durable tool inventory

This inventory registers bounded commands that were added to unblock a parent
modding or reverse-engineering goal. A command enters this file only after it
has a callable contract, rejection tests, no-overwrite behavior, deterministic
output where applicable, an operator card, and a maintained source location.

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

- Status: maintained; introduced and callable in xpp-tool 2.32.0.
- Parent goal: finish every character/item once as a complete editable asset,
  first for validated RPCS3 package mods and later for native-decomp import.
- Binary question: can one exact repeated-draw material union become a
  deterministic strict Blender GLB without serializing game payloads in its
  receipt or assigning the material to unproved faces?
- First answer: two compatible Zeke hair observations export record 533752 as
  one 184-vertex / 294-triangle GLB with 282 exact retail `Zeke_Hair_C/N`
  triangle assignments and 12 separate orange diagnostic triangles.
- Entry point: `xpp-tool character-material-coverage-export` (also exposed by
  the compatible `if1-tex` alias).
- Implementation: `src/infamous_xpp_textures/material_coverage_export.py`,
  SHA-256 `6c1abb242ce48f5e5882935d671e8b4afd9a601802a3b3e98c29f25c61f5cc2f`;
  union owner `src/infamous_xpp_textures/material_coverage.py`, SHA-256
  `fa233ba9cd8e64d02421bf25715beeb7af781cb87aa0659eecddf7696c7dd8e6`;
  strict GLB owner `src/infamous_xpp_textures/character_material_export.py`,
  SHA-256 `df5d9fa41663fb6596f8ec04d822db2f33030eb4c7f02f172393a06b7dc7b8b9`;
  CLI wiring `src/infamous_xpp_textures/cli.py`, SHA-256
  `1a3c5c13c703e08fa361fd3f1232bd11f901773aed0bc8c678b8cc83ab8592e9`.
- Tests: `tests/test_material_coverage_export.py`, SHA-256
  `b9fd2efad69c10fbc70d01115b2ce4f36207880ee32ced7b60dcfdd2a36003a3`,
  plus union, strict exporter, and component-ledger suites. The complete
  repository suite passes **240 tests**.
- Operator card: [README — Version 2.32](README.md#version-232--exact-repeated-draw-material-union-to-blender-glb).
- Inputs: one exact retail XPP/SHA-256; allowlist; record offset; one through 16
  checksum-pinned strict material observations with immutable v3/v4 bundles and
  required exclusions; one pinned anchor lineage that must identify exactly one
  accepted observation.
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
  `e4199e6e8e31635bbe7624164bed04b665224771e3a73851599d68b1fc534879`;
  report A/B 5,423 bytes / SHA-256
  `45bcea0b007be9b164b03168c7582806310ccf4195b58d3bfba892ece3f4566e`;
  immediate unlit audit 1,206,359 bytes / SHA-256
  `fb297acd2c725e57d8e2e0c59c99985d024a25daf59296e10d22392c20401fe4`.
- Build proof: two builds with pinned `SOURCE_DATE_EPOCH=1787484000`
  produced the identical 219,696-byte 2.32.0 wheel, SHA-256
  `f1bc08008d652fe73cbb4e86ab70b20658ff692947bea103139a786b1ae4fa1c`.
- Limitations: this does not close the twelve remaining hair assignments,
  identify every hair/head/body piece, recover retail normals/tangents or
  rig/skin, create 4×/authored-PBR material, reverse-pack an RPCS3 mod, or import
  into the native decomp. The approved clean matte render remains the separate
  appearance baseline.
- Return status: `returned-with-capability-and-evidence`. Keep this 282/294
  strict component; capture a genuinely different compatible hair pass for the
  remaining twelve faces instead of repeating export work.

## `xpp-tool.character-material-coverage-union.v1`

- Status: maintained; introduced and callable in xpp-tool 2.31.0.
- Parent goal: finish every character/item once as a complete editable asset,
  first for validated RPCS3 package mods and later for native-decomp import.
- Binary question: do repeated exact runtime draws of one retail record expose
  new material-bound triangle occurrences, and does their union cover the full
  retail triangle multiset?
- First answer: page one plus page two proves jacket 493/1,002, head 185/404,
  packs 170/302, and jacket detail 24/24. Page three then advances compatible
  hair coverage from 275 to 282/294 and head coverage from 185 to 200/404.
- Entry point: `xpp-tool character-material-coverage-union` (also exposed by
  the compatible `if1-tex` alias).
- Implementation: `src/infamous_xpp_textures/material_coverage.py`, SHA-256
  `fa233ba9cd8e64d02421bf25715beeb7af781cb87aa0659eecddf7696c7dd8e6`;
  CLI wiring `src/infamous_xpp_textures/cli.py`, SHA-256
  `1a3c5c13c703e08fa361fd3f1232bd11f901773aed0bc8c678b8cc83ab8592e9`.
- Tests: `tests/test_material_coverage.py`, SHA-256
  `dea39682ed337d3cb2841770b5b382b24b7ad5439caadbdb7de69d05269accd0`.
  The complete repository suite passes **240 tests**.
- Operator card: [README — Version 2.31](README.md#version-231--exact-material-coverage-across-repeated-draws).
- Inputs: exact retail XPP and SHA-256; exact texture allowlist; one source
  record offset; one through 16 pinned strict material reports with immutable
  v3/v4 bundles and required v4 capture-key exclusions.
- Output: deterministic, input-order-independent, payload-free JSON at a new
  path, preserving per-observation new/overlap/cumulative counts and exact
  covered/unobserved multiset hashes.
- Bounds: 64 MiB XPP; 1 MiB per report; 16 observations; inherited bounded
  bundles; 64 KiB completion marker; 256 KiB output; one process; no runtime,
  network, symlink output, or overwrite.
- Proven capability: exact source/topology/UV/family/texture/bundle/page/index
  reconciliation; multiset union without duplicate inflation; deterministic
  A/B results; fail-closed duplicate, conflict, out-of-retail, and overwrite
  behavior.
- Build proof: two builds with pinned `SOURCE_DATE_EPOCH=1787482208`
  produced the identical 214,881-byte 2.31.0 wheel, SHA-256
  `4ce849b9f0528ba7404d056de93a20ee11f24fa23d5f77f87e5ddf28e09d94a0`.
- Limitations: an exact triangle union does not identify assembly placement,
  recover original normals/tangents, create bones/weights, upscale textures,
  author PBR, repack an RPCS3 mod, or import into the native decomp.
- Return status: `returned-with-capability-and-evidence`. Preserve the accepted
  matte Zeke render; capture deliberately different pose/state/occlusion draws
  for jacket, head, and packs instead of repeating the same ordinary view.

## `xpp-tool.character-component-ledger.v1`

- Status: maintained; introduced and callable in xpp-tool 2.30.0.
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
  unresolved face counts. Five images are receipted, with only the approved
  matte/unlit hair look marked as an accepted visual baseline.
- Entry point: `xpp-tool character-component-ledger` (also exposed by the
  compatible `if1-tex` alias).
- Implementation: `src/infamous_xpp_textures/component_ledger.py`, SHA-256
  `a0a040ca4dd862659b822d65934f20436242906eceb6f1518b964216560ca63e`;
  CLI wiring `src/infamous_xpp_textures/cli.py`, SHA-256
  `1a3c5c13c703e08fa361fd3f1232bd11f901773aed0bc8c678b8cc83ab8592e9`.
- Tests: `tests/test_component_ledger.py`, SHA-256
  `f329881cf80502199c270a10d126d94b48ec83f4801bf005464343087e054a43`.
  The complete repository suite passes **240 tests**.
- Operator card: [README — Version 2.30](README.md#version-230--canonical-multipart-character-component-ledger).
- Inputs: one through 256 exact material-export JSON files, each with a matching
  SHA-256 pin; canonical title/build/candidate tokens; optional exact visual-
  baseline receipt manifest and pin.
- Output: deterministic payload-free JSON at a new path. It preserves component,
  source, event, lineage, topology, coverage, GLB, retail texture, published
  image, and open-gate identities without serializing private paths or payloads.
- Bounds: 1 MiB per material report; 256 KiB visual manifest; 256 observations;
  128 components; 256 render receipts; 1 MiB output; one process; no runtime,
  network, symlink input/output, or overwrite.
- Proven capability: exact hash validation; source-record component identity;
  conservative alias merge; immutable-geometry conflict, duplicate, schema,
  texture-family, triangle-coverage, and unknown-render rejection; deterministic
  28,843-byte A/B/C output SHA-256 `755fb441…e6046`.
- Build proof: the 2.30.0 wheel is 209,114 bytes, SHA-256
  `685e5f2fe633f0c53419727e8b980d0d154b692b93a03cd72fde34c15b17b215`.
- Limitations: a visual baseline protects appearance only. It cannot prove
  unresolved material faces, assembly placement, missing body pieces, original
  normals/tangents, bones/weights, 4×, authored PBR, RPCS3 repack/gameplay, or
  native import. Every one of those gates remains independently false.
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

- Status: maintained; introduced in 2.25.0 and callable in xpp-tool 2.29.0.
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
- Entry point: `xpp-tool character-uv-texture-binding` (also exposed by the
  compatible `if1-tex` alias).
- Implementation: `src/infamous_xpp_textures/shader_lineage.py`; fragment
  source decode in `src/infamous_xpp_textures/fragment_sampler.py`; CLI wiring
  in `src/infamous_xpp_textures/cli.py`.
- Tests: `tests/test_shader_lineage.py` and `tests/test_fragment_sampler.py`.
- Maintained source pins at 2.27.0: implementation
  `16477c804d59b9b2bdb72e2c3bcdec3e29c1bd3078a664e322f032620bd2c81c`;
  fragment decoder
  `6db0ea37623a262299b50e1c071a6aa4846711b404d6bdce3c50a899b11443e3`;
  CLI `fbe2156fd8662879c6001b1b1d85222c9076cbb2de0d436237fc06439de1d653`;
  focused tests
  `e4b1eeb2bac1675bcf128a09773672fbe754f868f834ebb60909d14f45ac0133`
  and
  `afd10a8f77fed71a6895eb5ad1a1e592c9bd9be499437588347c524d927bde09`.
- Operator card: [README — Version 2.27](README.md#version-227--packed-three-component-character-streams).
- Inputs: complete v3/v4 shader bundle; exact texture allowlist and prior-page
  exclusion where required; source census and pin; character census and pin;
  bounded page/event/source-record/character-side selection.
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
- Limitations: the old capture did not directly serialize attribute byte
  offsets, so byte 4 is a unique finite complete-tiling reconstruction. Packed
  guest widths are not host-upload widths. Name suffixes do not prove native
  PBR channel semantics. Full character, every material, 4x/PBR, RPCS3 round
  trip, and native import remain false.
- Return status: `returned-with-evidence`. The four-map exporter is now complete;
  resume by proving the next source-bound record's UV/material family and joining
  components only after exact placement/identity evidence.

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

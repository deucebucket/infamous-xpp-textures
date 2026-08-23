# Changelog

## 2.42.0 - 2026-08-23

- Added permanent `character-material-assembly-export` with stable inventory ID
  `xpp-tool.character-material-assembly-export.v1`. It accepts two through 32
  checksum-pinned strict material report/GLB pairs and combines only unique
  source records from one requested runtime page.
- Every input report, GLB, embedded buffer, scene, node, transform, accessor,
  primitive, material, texture, image, topology count, and source-position
  frame is revalidated. The output translation for each component is derived
  only by reversing its recorded per-component recenter; hand alignment and
  semantic coordinate guesses are not admitted.
- The output keeps the source meshes, proved/unresolved primitive split,
  materials, embedded images, and UVs in one deterministic editable GLB.
  Normal and reversed real inputs are byte-identical at **2,081,256 bytes**,
  SHA-256
  `afb6eb3221367bc95275a812d6fa52cd6741f3efb78462a1a808c6151f248c1a`;
  the payload-free receipt is SHA-256
  `d143c366c62e97e06d3e3a45291c4a1bf1ecc33acfc12d36da3da9ba3d79a2ee`.
- The first owned page-two result joins five current Zeke records into one
  relative assembly: **1,434 vertices / 2,026 retail triangle occurrences**,
  split into **1,147 material-proved / 879 unresolved** occurrences. This is a
  recognizable partial pose, not complete Zeke or original object-space proof.
- The first real run failed closed on the current exporter-owned
  `runtime-observed exact triangle subset` primitive role. The assembler and
  existing gap locator now admit that exact maintained role as well as the
  historical/union roles; pre-extrapolated component inputs remain rejected.
- Added opt-in, record-selective clean presentation through repeated
  `--preview-record`. Only a requested component's unresolved primitive is
  redirected to its exactly one observed material. Strict observed/unresolved
  counts remain unchanged in the receipt, the selected records and triangle
  counts are explicit, and runtime proof remains false. This removes the two
  peach Zeke-hair spots (four triangles) without repainting unresolved head or
  clothing faces. Normal/reversed inputs reproduce a **2,081,628-byte** GLB,
  SHA-256
  `abac165ee1d4f8e356964e91d22935cad67fe03b91f0aed3ec5c942dbea700d6`;
  receipt SHA-256
  `fd1232aa811aee2d9f744320e0d81f65cb9e35c0464fc66d0f59833f4c4425d5`.
- Blender 5.2.0 and the maintained decomp review renderer produced and
  immediately published strict and hair-clean unlit -58°, 35°, and 145° views.
  The strict images expose all unresolved coverage; the clean images fill only
  the two tiny hair regions and leave every other gap visible.
- Added deterministic assembly, input-order, path/page/record/hash/frame,
  transform, primitive-role, selective-preview, occupied-output, and CLI
  coverage. All **293
  tests** pass; changed-file Ruff format/check, Python compilation, and diff
  validation pass.
- Full character/component completeness, original coordinate semantics,
  retail normals/tangents, rigging, 4× maps, authored PBR, RPCS3 mod round
  trip, and native-decomp import remain false.

## 2.41.0 - 2026-08-23

- Added explicit permanent `character-component-ledger`
  `--group-cross-page-source-records` mode with stable inventory ID
  `xpp-tool.character-component-ledger.v2`. The default v1 path and schema are
  unchanged.
- V2 groups only exact source-record matches across runtime pages: XPP hash and
  size, record offset, vertex count, retail triangle/index identity, and UV
  offset/payload must all agree. Page/event/draw identities and every distinct
  position-payload hash remain in the individual observations.
- Visual receipts must name a page+record admitted by material evidence.
  Conflicting cross-page geometry/topology/UV, unknown render pages, malformed
  flags, duplicate inputs, and occupied output still fail closed. One complete
  observation is sufficient to prove source-component material completion;
  older retained partial observations do not erase that proof.
- The canonical Zeke v2 ledger remains **five source components** while storing
  **six material observations**, including separate page-one and page-two head
  poses. Hair is **290 / 294**; head retains both **174 / 404** and the current
  **212 / 404** receipt; jacket is 492 / 1,002; packs are 167 / 302; jacket
  detail remains complete at 24 / 24. Eight published renders are retained.
- Normal and reversed real inputs produce the identical 38,655-byte ledger,
  SHA-256
  `2f74d3e4db3d51256053c39626ceea1a1fb1c5f9375b7bbd63e6b17552cd1c40`.
  The extended visual receipt manifest is 5,004 bytes, SHA-256
  `bd3440b805679e32b0637a438e366b1cca8bccd3c59001946e1a08282eba2957`.
- The v1 compatibility run is byte-identical to the released 2.36 ledger:
  30,923 bytes, SHA-256
  `e61a78fbdc80cecaa97984cbc0cca3bd9a53df075134b0afbd7b5bba79a9553c`.
- Added cross-page grouping, pose preservation, source/index/UV drift,
  unknown-render-page, malformed-mode, pass-census linkage, compatibility, and
  input-order coverage. All **286 tests** pass; changed-file Ruff format/check,
  Python compilation, and diff validation pass.
- Two builds with pinned `SOURCE_DATE_EPOCH=1787506200` produced the identical
  257,033-byte 2.41.0 wheel, SHA-256
  `880a589aa9b67b165e3b16eccb8a671763fd89febb4023460e7e869c65cbb82d`.
  A fresh isolated environment installed that wheel and reproduced both the
  exact v2 grouped ledger and the byte-compatible v1 ledger.

## 2.40.0 - 2026-08-23

- Repaired permanent `character-material-coverage-export` validation for a
  four-map anchor whose component summary deliberately lists only its `C/N`
  display textures while `compatible_full_range_texture_names` preserves the
  complete sorted `A/C/N/S` shader-bound set.
- The exporter still fails closed when the compatible list is missing,
  incomplete, duplicated, unsorted, unbounded, or does not include every
  display and anchor texture. `A/S` remain embedded evidence and explicitly
  unassigned; the tool does not guess PBR roles or change the approved matte
  display material.
- The first repaired real export combines five exact observations of Zeke head
  record 536280. It assigns **212 / 404** retail triangle occurrences and keeps
  **192** in the orange unresolved primitive. Two runs are byte-identical: GLB
  493,956 bytes / SHA-256
  `59d7e3b477bc5f2c28fc935874d52d39b33e2d182cd1af90544aaa73bb05131f`;
  report 6,574 bytes / SHA-256
  `2bf265a8e9684c39a09afc035725029aa33cb50465ff62c92c76e6bf4f080978`.
- Blender 5.2.0 produced and immediately published the unlit -58-degree
  component audit: 1,671,003 bytes / SHA-256
  `66c2092628b912972adbfda2cae6271871a138e230988f920b0f9daf6028d68f`.
  It is a three-dimensional head/neck component, not complete Zeke: hair,
  eyes, teeth, glasses, body, rigging, and the unresolved assignments remain
  separate gates.
- Added positive and fail-closed tests for the display/full-range identity
  split. All **284 tests** pass; changed-file Ruff format/check, Python
  compilation, and diff validation pass.
- Two builds with pinned `SOURCE_DATE_EPOCH=1787504400` produced the identical
  255,656-byte 2.40.0 wheel, SHA-256
  `98713fd06510f095321be72ae6acc100cc565a1e9fa605ce254e02afec1d5306`.
  A fresh isolated environment installed that wheel and reproduced the exact
  five-observation GLB and report byte-for-byte.

## 2.39.0 - 2026-08-23

- Extended permanent `character-material-coverage-union` support so a strict
  full-range pass may carry additional shader-bound textures without being
  rejected when its exact display-assigned textures still match the canonical
  anchor. Assignment lists must form one bounded, unique, disjoint partition;
  the declared shader-bound count and every repeated texture identity must
  reconcile or the union fails closed.
- Display compatibility deliberately pins descriptor, name, suffix,
  dimensions, decoded RGBA, and runtime-prefix identity while ignoring only
  the generated embedded-PNG container hash. The anchor still owns the final
  display PNG. Extra `A`/`S` maps remain named compatible evidence and are not
  invented as PBR roles.
- The first real full-range compatible pass adds exactly two Zeke hair triangle
  occurrences. Strict editable coverage advances from **288 / 294** to
  **290 / 294**, leaving four orange audit faces. Two exports are byte-identical:
  GLB 179,204 bytes / SHA-256
  `a18546b7dcf6db48e54affa0acdb5c045f074fa369c1e3df8386a321b2e78745`;
  report 5,430 bytes / SHA-256
  `3cbdff5d85ab857c881e3b9d2bf73fc6ba7fc0addf481ae219be3344c5cd731e`.
- Blender 5.2.0 produced the immediate approved matte/unlit -58-degree audit:
  1,753,289 bytes / SHA-256
  `fd4dc943c38f1dd697eddf787c9f8756fe0af8dc48656adecb35cdc930f0f56e`.
  The new proof changes face coverage, not the locked hair appearance.
- Added deterministic, input-order, container-hash compatibility, malformed
  assignment, count-drift, reassignment, missing-anchor, and conflicting-extra
  rejection coverage. All **279 tests** pass.
- Two pinned-epoch builds produced the identical 254,618-byte 2.39.0 wheel,
  SHA-256 `c503da8d5e12741b271aa90f2ca286f501de022f18a95c22c8e6f17214a0c0b5`;
  a fresh isolated environment installed it and
  reproduced the full-range compatibility behavior.

## 2.38.0 - 2026-08-23

- Added permanent `character-material-gap-locator` with stable inventory ID
  `xpp-tool.character-material-gap-locator.v1`.
- Revalidates a checksum-pinned strict material-union GLB and its exact export
  report, identifies primitive roles from the fail-closed evidence contract,
  and checks exact accessor component/shape, buffer, count, vertex, and triangle
  bounds. Eight focused rejection/behavior tests and all 274 repository tests
  pass.
- Emits deterministic payload-free spatial/UV aggregates: connected component
  sizes, observed-boundary adjacency, diagnostic bounds and normalized
  centroids, area fractions, and dominant diagnostic face orientations.
- The first owned Zeke hair result locates six unresolved faces as two
  edge-disconnected three-triangle patches using 10 vertices. All 10 vertices
  already belong to observed hair, all six faces share an edge with observed
  hair, and the gaps occupy 1.2627171% of diagnostic surface area and
  0.9167829% of UV triangle area.
- The result rejects the missing-detachable-chunk hypothesis. The next capture
  must target a different material pass, state, or LOD; a camera angle alone
  does not prove a different runtime draw.
- Raw positions, UV rows, triangle indices, textures, paths, and payload lists
  remain outside the report. Position meaning, camera direction, the six
  material assignments, retail normals/tangents, rigging, 4x/PBR, RPCS3 round
  trip, and native import remain open.

## 2.36.0 - 2026-08-23

- Extended permanent `character-uv-texture-binding`,
  `character-material-coverage-union`, and
  `character-material-coverage-export` support with checksum-pinned, safe
  partial-range material observations.
- A partial observation is admitted only after the lineage, source census,
  character census, immutable runtime bundle, source-record mapping, packed UV
  stream, shaders, named texture descriptors, mip prefixes, runtime indices,
  mapped vertex range, and retail triangle multiset all independently
  revalidate.
- At least one normal full-range material report remains mandatory as the GLB
  topology/UV/display-material anchor. Partial evidence can add only the exact
  retail triangles it actually calls; it cannot promote a partial vertex tray
  into a full source range or invent `A`/`S` PBR roles.
- The owned page-one Zeke hair draw safely uses vertices 0-182 from its
  183-of-184 range. Combined with page two and page three, strict material
  coverage advances from 282/294 to **288/294**, leaving six orange audit
  triangles.
- Two installed-wheel runs are byte-identical: union report 5,283 bytes /
  `3f90252ab92980500ceea34dca3f5a9a486adb874109079aea233509a5dae825`;
  GLB 179,204 bytes /
  `f11dc2be73ccba0aaad2576b76ae8e904c7302e8e44f3a6aa18c1cbc81705e3b`;
  export report 5,430 bytes /
  `87a41528677928bb555217b6c21916dfef65ea85f81eabbbae91433a357034eb`.
- The canonical five-component Zeke ledger now records the 288/6 hair receipt
  and six published images, including the approved strict matte/even-brown
  baseline. Its prior 282/12 three-pass census remains preserved in the 2.34
  historical ledger; it is not falsely relinked to the larger union.
- An occupied export retry exited 1 and preserved both output hashes. All
  **252 tests** pass; changed-file Ruff format/check, Python compilation, and
  diff validation pass.
- Two builds with pinned `SOURCE_DATE_EPOCH=1787494800` produced the identical
  238,586-byte 2.36.0 wheel, SHA-256
  `10096f105406b83260591b12d1b60ecda63ce1196f7f06d78671c92a761463d7`;
  a fresh isolated environment installed it and exposed the eight-value
  partial-observation contract.

## 2.35.0 - 2026-08-23

- Extended permanent `runtime-xpp-source-census` evidence with fail-closed
  retail triangle-multiset validation for every uniquely source-bound draw,
  including partial-range vertex slices.
- Requires each admitted runtime index to be a bounded triangle list, a subset
  of the exact retail record multiset, and entirely inside its exact mapped
  vertex range; rejected events remain explicit and never enter the union.
- Added deterministic per-event coverage receipts plus a retail-ordered
  per-record union with distinct payload counts, incremental overlap/new counts,
  and covered/unobserved hashes without serializing source or runtime payloads.
- The first owned three-page run validates 45/45 source-bound events. Zeke hair
  page one safely references vertices 0-182 inside its 183-of-184 slice and
  advances topology evidence from the strict material union's 282/294 to
  **288/294**, leaving six topology occurrences unobserved.
- This does not yet promote the canonical textured GLB: page-one UV/shader/
  texture lineage still needs a strict material-union bridge, so the accepted
  matte visual baseline and current 282/294 material receipt remain unchanged.
- Added partial-range acceptance, out-of-retail orientation rejection,
  mapped-range escape rejection, deterministic union, payload privacy, bound,
  and no-overwrite coverage. All **247 tests** pass; changed-file Ruff
  format/check and Python compilation pass.
- Two final owned runs are byte-identical at 103,798 bytes, SHA-256
  `990a50de1cee155e5efc887e43c291c00047dedf74109bb8d658b5b4d474f865`.
  An occupied-output retry exited 1 and preserved that hash.
- Two builds with pinned `SOURCE_DATE_EPOCH=1787491200` produced the identical
  232,337-byte 2.35.0 wheel, SHA-256
  `8e264dd9ec4e672d6c0996e9371b5549ef8ef2f6390c918056f72ef726d46ee3`;
  a fresh isolated environment installed it and exposed the extended command.

## 2.34.0 - 2026-08-23

- Extended the permanent `character-component-ledger` with optional repeatable,
  checksum-pinned `character-material-pass-census` receipts so every proved
  shader/texture pass survives in the canonical character record.
- Added strict internal schema, authority, canonical ID, texture, pass-group,
  pairwise relation, count, hash, bound, limitation, and any-pass-union
  validation before a census can enter the ledger.
- Reconciles each census against an already admitted union material export by
  exact XPP bytes/hash, source record, vertex/triangle topology, retail index,
  texture family, covered/unobserved counts, and both union multiset hashes.
- Keeps runtime pages and their position identities separate while linking the
  cross-page census to every exact matching component ID; it does not turn a
  layered pass into duplicate geometry or silently merge animated poses.
- The first Zeke ledger remains five components, five material observations,
  five published images, and one accepted matte baseline. It now also preserves
  one hair census with three pass signatures, two runtime index payloads, one
  coextensive layered pair, 282 covered triangles, and 12 unobserved.
- Two reversed-input real builds are byte-identical: 44,794 bytes, SHA-256
  `d64a613f6be9a165537493c98f730d77945108a7969524207dcc7fd748e7f44e`.
  An occupied-output retry exited 1 and preserved that hash.
- Built the 230,065-byte 2.34.0 wheel twice with pinned
  `SOURCE_DATE_EPOCH=1787488800`; both have SHA-256
  `81f68fcc6ccd4005c03a6302c8d2e184c5e867367eaa34d9f926473db05e303c`.
  A fresh isolated environment installed it and exposed both pass-census inputs.
- Added permanent CLI inputs, deterministic normalization, malformed/drifted/
  duplicate/unknown-evidence rejection, synthetic reconciliation tests, and RR
  operator documentation. All **245 tests** pass; changed-file Ruff
  format/check and Python compilation pass.

## 2.33.0 - 2026-08-23

- Added permanent `character-material-pass-census` support for comparing two
  through 32 checksum-pinned, strict one-draw material observations from one
  exact retail character record across different shader and texture passes.
- Revalidates the retail XPP, record topology, texture allowlist, immutable
  runtime bundles, page exclusions, shader programs, UV identity, named texture
  families, and exact index payloads before classifying every pair as identical,
  subset, superset, partial overlap, or disjoint.
- The first Zeke hair result proves that the page-three four-map `A/C/N/S` pass
  and page-three `C/N` pass are coextensive over the same 275 triangle
  occurrences. They are distinct shader/material passes, not extra geometry.
- Page two versus page three contributes seven unique triangles in each
  direction; the exact any-pass union remains **282 / 294**, leaving the same
  **12** unobserved triangles. A new capture is useful only if it exposes a
  genuinely different draw rather than another layered pass over those 275.
- Two final runs with reversed observation order are byte-identical: 13,180
  bytes, SHA-256
  `6b363998f495ea9e8bd318cff8da62a3e06c920088f8098f9b2c34cb8c280048`.
  An occupied-output retry exited 1 and preserved that hash.
- Built the 225,509-byte 2.33.0 wheel twice with pinned
  `SOURCE_DATE_EPOCH=1787486400`; both have SHA-256
  `0cfec335a3376de6d761a0a315ddc062eb9448087b03f0c1cc5d4301e3943633`.
  A fresh isolated environment installed it and exposed the new command.
- Added deterministic pass signatures, exact multiset relations and union,
  strict authority and payload bounds, duplicate/conflict/out-of-retail
  rejection, atomic no-overwrite publication, CLI coverage, and permanent
  operator documentation. All **244 tests** pass; changed-file Ruff
  format/check and Python compilation pass.

## 2.32.0 - 2026-08-23

- Added permanent `character-material-coverage-export` support that revalidates one through 16 strict repeated-draw observations and carries their exact retail-ordered triangle union into a deterministic Blender GLB.
- Requires one checksum-pinned anchor lineage belonging to exactly one accepted observation; reuses only that observation's proved position, UV, shader, and retail texture authorities while replacing its one-draw indices with the independently recomputed union.
- Extended the strict material exporter and canonical component ledger with explicit `observed-union` receipts, union hashes/counts, exact anchor identity, and fail-closed reconciliation without allowing union outputs to become new union observations.
- The first real Zeke hair export advances the editable strict GLB from 275 to **282 / 294** proved material triangles and leaves exactly **12** orange/unproved faces. Two runs are byte-identical: GLB 179,204 bytes / `e4199e6e...34879`; report 5,423 bytes / `45bcea0b...4566e`.
- Blender 5.2.0 produced an immediate 1600×1200 unlit -58° audit, 1,206,359 bytes / `fb297acd...01fe4`; the approved clean matte baseline remains unchanged.
- Built the 219,696-byte 2.32.0 wheel twice with pinned `SOURCE_DATE_EPOCH=1787484000`; both have SHA-256 `f1bc08008d652fe73cbb4e86ab70b20658ff692947bea103139a786b1ae4fa1c`.
- Added deterministic union-index recovery, strict exporter, wrapper/anchor, CLI, component-ledger, declared-suffix identity, rejection, bounds, and atomic no-overwrite tests. All **240 tests** pass; changed-file Ruff format/check and Python compilation pass.

## 2.31.0 - 2026-08-23

- Added permanent `character-material-coverage-union` analysis for exact triangle-multiset coverage across repeated runtime draws of one retail character record and texture family.
- Revalidates checksum-pinned observed-only material reports against the retail XPP, runtime bundle, paging exclusion, texture allowlist, UV identity, named texture family, and exact runtime index payload before unioning evidence.
- Added deterministic input-order independence, duplicate/conflict/out-of-retail rejection, bounded payload-free reporting, atomic no-overwrite publication, a repeatable CLI contract, focused tests, and a durable operator card/tool-inventory entry.
- The first Zeke page-one/page-two run proves that ordinary repeated views mostly redraw the same faces: jacket 493/1,002 (+1), head 185/404 (+31 over page one, +11 over page two), packs 170/302 (+0 over page one), and jacket detail 24/24 (duplicate full-coverage control).
- Two final runs per component are byte-identical. Exact report hashes are `be2bd569...dde4` (jacket), `ae86a31d...1d0` (head), `e5f48730...ccf6` (packs), and `f7d0d39a...bd64a` (detail).
- The result keeps full character, rig/skin, 4×, authored PBR, RPCS3 round trip, and native import false. It redirects capture work toward genuinely different pose/state/camera draws instead of redundant ordinary views.
- Built the 214,881-byte 2.31.0 wheel twice with pinned `SOURCE_DATE_EPOCH=1787482208`; both have SHA-256 `4ce849b9f0528ba7404d056de93a20ee11f24fa23d5f77f87e5ddf28e09d94a0`.
- All **234 tests** pass; changed-file Ruff and format checks plus Python compilation pass.

## 2.30.0 - 2026-08-23

- Added permanent `character-component-ledger` reconciliation for repeatable checksum-pinned material-export reports and an optional payload-free visual-baseline receipt manifest.
- Canonicalized each multipart character component by title, build, candidate, runtime page, and source record offset while preserving event/lineage observations, exact topology and material-coverage counts, GLB and retail-texture identities, and explicit open delivery gates.
- Added duplicate-path/content/observation rejection, immutable-geometry conflict detection, texture-family/name reconciliation, triangle-coverage validation, bounded deterministic output, and atomic no-overwrite publication.
- The first real Zeke run records five distinct proved components—hair, jacket detail, jacket, head, and packs—plus five published render receipts. Only the user-approved matte/unlit hair view is an accepted visual baseline.
- The ledger truthfully retains one fully material-covered component and four components with unresolved material faces; full character, rig/skin, 4×, authored PBR, RPCS3 round trip, and native import all remain false.
- Three final-code runs produced byte-identical 28,843-byte ledgers with SHA-256 `755fb441c735671697953141074e92bce357049addb1bb831388cfccb76e6046`; an occupied-output retry exited 1 without changing the original hash.
- Built the 209,114-byte 2.30.0 wheel with SHA-256 `685e5f2fe633f0c53419727e8b980d0d154b692b93a03cd72fde34c15b17b215`.
- All **230 tests** pass; changed-file Ruff and format checks plus Python compilation pass.

## 2.29.0 - 2026-08-23

- Added permanent `character-material-candidate-census` selection and classification for every unexcluded full-source-range candidate on one exact runtime page.
- Reused the exact checksum-pinned shader-lineage validator per candidate, preserving accepted and rejected outcomes instead of relying on a manually maintained page/event/record list.
- Added exact `EVENT:RECORD_OFFSET` completion exclusions with duplicate, unknown, invalid, and empty-selection rejection so completed components are not silently redone.
- The first final-code census found six eligible page-two candidates, excluded three completed hair/jacket-detail draws, and accepted all three remaining jacket/head/packs candidates with exact lineage-report identities.
- Added bounded deterministic payload-free reporting, atomic no-overwrite publication, focused rejection/CLI tests, an operator card, and exact A/B output proof.
- All **226 tests** pass; changed-file Ruff and format checks plus Python compilation pass.
- Published clean multi-angle and strict observed/unresolved material renders for all three accepted components as soon as each image was created. These remain component evidence, not full Zeke, authored PBR/4×, rigging, RPCS3 round trip, or native import.

## 2.28.0 - 2026-08-23

- Extended permanent `character-material-export` inputs from exactly two shader-bound textures to one bounded family of two through eight unique sampler/suffix bindings containing required `C` and `N` descriptors.
- Embedded every runtime-matched retail image in deterministic shader-sampler order while assigning only the existing name-derived `C` base-color and `N` normal display roles. Additional `A` and `S` images remain explicitly unassigned; no alpha/specular/roughness/metallic or native-PBR role is invented.
- Generalized GLB family, node, mesh, material, texture, and receipt labels instead of hardcoding Zeke hair, and accepted shader-proved half3 rows while exporting only their sampled X/Y pair as `TEXCOORD_0`.
- The first real four-map export preserves record 536488 as 26 vertices / 24 triangles with exact full runtime material coverage, four embedded `Zeke_Jacket` images, two display-assigned suffixes, and two unassigned suffixes.
- Added deterministic four-map GLB tests, retained the two-map hair and strict/preview tests, and made the CLI report the actual embedded image count.
- Published three immediate 1600×1200 unlit component views. They show two separated textured detail islands and are not promoted to a full jacket, full Zeke, PBR/4×, rig, RPCS3 round trip, or native import.

## 2.27.0 - 2026-08-23

- Corrected `character-uv-texture-binding` to distinguish packed guest source-storage widths from padded renderer host-upload widths for source-proved three-component unorm8 and half-float vertex arrays.
- Kept the complete-tiling proof fail-closed: packed descriptors must still cover the captured stride exactly, without invented padding or overlap, and every decoded float component must be finite.
- Proved a second Zeke component end to end. Record 536488 is 26 vertices / 24 triangles with one exact 10-byte layout: four-byte attribute 3 followed by six-byte half3 attribute 9 at byte 4. Shader lineage uses attribute 9 XY as TEX0.
- Bound all four observed samplers uniquely to the `Zeke_Jacket` family: `N`, `A`, `S`, and `C`. Unlike the hair draw, this component has full runtime-to-retail index identity and no material-unobserved triangles.
- Preserved component identity, position meaning, native material-channel semantics, full jacket/full character, authored PBR/4x, Blender render, RPCS3 round trip, and native import as explicit open gates.
- Added packed half3 and unorm3 regression coverage while retaining all existing hair-lineage tests.

## 2.26.0 - 2026-08-23

- Added permanent `character-material-export` conversion from one checksum-pinned 2.25 shader lineage, immutable runtime bundle, and exact retail XPP into a deterministic GLB plus payload-free receipt.
- The first Zeke hair result preserves the full 184-vertex / 294-triangle retail topology, writes the shader-proved half-float rows as `TEXCOORD_0`, and embeds the exact runtime-matched `Zeke_Hair_C.psd` and `Zeke_Hair_N.psd` mip-zero PNGs. Exact runtime indices prove that material for 275 triangles; the remaining 19 are kept separate instead of silently inheriting it.
- Added explicit `observed-only` and `preview-full-record` presentation modes. The strict default renders the 19 unproved faces as diagnostic clay; the preview may extrapolate the observed material across the retail record while keeping full material coverage false in the GLB and receipt.
- Added exact encoded-prefix validation before decode, deterministic in-memory PNG encoding, generated inspection normals, bounded atomic two-file publication, no-overwrite behavior, authority reconciliation, and synthetic rejection tests.
- Preserved the position-attribute hypothesis, generated-normal status, retail-name-derived material roles, full-character, 4x/PBR, RPCS3 round-trip, and native-import gates as explicit limitations.
- Imported the GLBs in Blender 5.2.0 with the maintained decomp review renderer. Historical shiny views were rejected after localizing a renderer-side metallic override; the clean matte preview is presentation-only, while the orange strict audit view and exact 275/19 split preserve the unresolved material boundary.

## 2.25.0 - 2026-08-23

- Added permanent `character-uv-texture-binding` analysis for one checksum-pinned XPP source census, character census, and complete RSX v3/v4 shader bundle.
- Extended the fragment sampler decoder with exact direct-input register, TEX coordinate name, and component-swizzle evidence while adding branch-free component-level vertex-input lineage.
- Proved BCUS Zeke hair record 533752 uses vertex attribute 9 half-float XY through vertex output 7 / fragment TEX0 for the exact `Zeke_Hair_N.psd` and `Zeke_Hair_C.psd` runtime prefixes.
- Reconstructed one valid complete packed layout from the exact 8-byte source-bound stream: attribute 3 at byte 0 and the two-component attribute 9 at byte 4, with explicit disclosure that the old capture did not directly serialize attribute byte offsets.
- Preserved full-character, all-material, Blender-render, 4x/PBR, RPCS3 round-trip, and native-decomp import gates as false; this release creates no render and does not call one hair piece full Zeke.
- Added strict authority pins, bundle/paging/payload reconciliation, unique descriptor matching, bounded deterministic payload-free output, no-overwrite publication, rejection tests, operator documentation, and durable tool registration.

## 2.24.0 - 2026-08-22

- Added permanent `asset-completion-inventory` reconciliation for the checksum-pinned decomp tally, retail GLB manifest, gallery metadata, and first unfinished character/item census.
- Corrected the declared 20-file gallery total into 19 unique asset renders plus one gameplay screenshot, retained one duplicate Drive entry, and kept character renders at zero.
- Preserved 57 completed retail static exports and 19 existing renders as narrowly scoped skip decisions while refusing to promote any of them to complete, RPCS3-ready, or native-ready assets.
- Emitted 68 records with exact 0 complete / 58 partial / 10 unknown status, nine conservative render-to-GLB joins, and ten unresolved render subjects instead of guessing identities.
- Selected Zeke from cross-build census evidence as the first unfinished batch and defined independent near-term retail RPCS3 and long-term native-decomp exits on one canonical record.
- Added strict input pins, schema/count/path/duplicate/candidate validation, private-path stripping, deterministic bounded no-overwrite output, tests, operator documentation, and permanent tool registration.

## 2.23.0 - 2026-08-22

- Added permanent `character-asset-census` analysis for one character or item across two complete, checksum-pinned extracted profiles and two ordinal OID manifests.
- Bound all 31 Zeke descriptors to their recovered names, proved multipart package references, retained the 16/16 packed geometry-contract count, and kept name-to-geometry plus geometry-to-material binding gates false.
- Verified all 2,296 BCUS and 2,742 NPUA packages: every substantial Zeke texture is self-contained in the named target package; only one 8-byte 1×1 utility texture is shared outside it, with no substantive partial mip/prefix sharing.
- Confirmed 31/31 location-independent texture identities across builds despite 31/31 descriptor reorderings, so descriptor indices remain build-local.
- Added explicit all-asset completeness, emulator-mod round-trip, native-decomp import, and completion-inventory gates so a partial render or name hit cannot masquerade as a finished reusable asset.
- Added strict workspace/package/manifest hashes, profile and report bounds, deterministic output, no-overwrite publication, synthetic failures, and permanent inventory documentation.

## 2.22.0 - 2026-08-22

- Extended permanent `character-source-runtime-correlate` reports with deterministic best-fit proper and mirrored rotation/translation/one-uniform-scale metrics for every numeric family while preserving the 2.21 unrestricted affine fields.
- Added a standard-library symmetric-eigen/quaternion implementation with no NumPy, Blender, network, or runtime dependency and no serialized transform coefficients.
- Broke the affine-family tie under the narrower proper-similarity constraint: `scale-offset-unsigned` ranks first for both owned stream-1 pairs, including near-exact proved hair (`R² 0.9999999971`) and the visible fragment (`R² 0.9912978824`).
- Kept numeric-family selection and every semantic/model/injection gate false because proper-similarity ranking is strong candidate evidence rather than executed-decoder proof.
- Added exact proper-versus-mirrored synthetic tests and reproduced both owned reports byte-identically within the existing immutable, hash-pinned, bounded, new-only contract.

## 2.21.0 - 2026-08-22

- Added permanent `character-source-runtime-correlate` analysis for one exact proved retail character record, hash-pinned runtime topology, and hash-pinned contiguous runtime `float32x3` array.
- Compared every eligible packed three-component stream with the direct-order runtime rows using a full three-axis affine fit, source rank, R-squared, RMSE, normalized RMSE, and maximum point residual without serializing payload values.
- Added an explicit recorded runtime-row window so a larger capture buffer is never silently trimmed to the topology vertex count.
- Independently validated stream 1 as the top correlation for the 184-vertex hair record (`R² 0.9999999972`) and the 26-vertex visible fragment (`R² 0.9943533660`) while leaving numeric family, position meaning, ownership, UVs, rigging, materials, completeness, and injection false.
- Enforced immutable bounded inputs, exact SHA-256 and topology identity, deterministic reports, fail-closed no-overwrite publication including race preservation, and positive/rejection/window/CLI tests.

## 2.20.0 - 2026-08-22

- Added the permanent `character-source-diagnostic-export` command for one exact proved XPP character record, packed stream, and caller-selected numeric hypothesis.
- Preserved exact retail topology, stream identity, parameter identity, and deterministic GLB output while keeping position meaning, UVs, rigging, materials, completeness, and injection explicitly unproved.
- Enforced 64 MiB input/output bounds, immutable inputs, fail-closed no-overwrite publication, finite float32 coordinates, and positive, rejection, bound, CLI, and deterministic-repeat tests.
- Registered the command in the repository tool inventory with its operator card and return-to-goal contract.

## 2.19.0 - 2026-08-22

- Added `runtime-xpp-source-census` to bind paged RSX draw blocks to one unique exact byte slice of a retail character XPP stream-zero record while preserving observed per-record strides.
- Revalidated the complete character-contract set, geometry-heap bounds, exact v3/v4 page chain, cumulative capture-key exclusion, texture allowlist, payload sizes, and SHA-256 identities before comparing any bytes.
- Kept full XPP index identity separate from partial vertex-window identity, reported ambiguous and unmatched draws without promotion, and emitted only a bounded payload-free report.
- Preserved human component names, position/UV/material semantics, skin weights, skeleton, full-character completeness, PBR, and mod readiness as explicit open gates.

## 2.18.0 - 2026-08-22

- Added `runtime-page-family-census` to distinguish exact geometry reuse, exact ordered vertex-stream reuse, stable-layout partial-stream candidates, weak target-texture/vertex-program compatibility, and novel observed signatures across a strict v3/v4 page chain.
- Kept family evidence conservative and payload-free: ambiguous many-to-one groups remain explicit, weak target-texture/vertex-program matches never become component identities, and same-source ownership/new-geometry claims remain false.
- Reused the exact cumulative exclusion, allowlist, completion, capture-key nonoverlap, immutable-input, no-overwrite, page/event/pair, and 256 KiB report bounds.
- Added deterministic real-chain proof so a changed capture key is no longer mistaken for a new character part when only animation, index selection, or render state changed.

## 2.17.0 - 2026-08-22

- Added strict `if1-texture-bound-topology-v4` validation for later capture pages bound to an exact external capture-key exclusion manifest, including manifest identity/count, observed-exclusion bounds, nonoverlapping captured keys, and the complete payload/file contract.
- Added `runtime-capture-key-exclusion` to deterministically write the cumulative prior-page key set from a complete v3/v4 bundle, with a 256-key/16,705-byte bound, immutable inputs, and no overwrite.
- Added `runtime-screen-position-page-merge` to validate one base v3 page plus an exact chain of v4 pages and place selected draws in one unchanged screenshot-space frame with deterministic globally unique diagnostic materials and per-page provenance.
- Preserved v1/v2/v3 behavior and kept component labels, world space, a full character, UV/material meaning, skinning, skeleton, PBR, and mod readiness explicitly unproved.

## 2.16.0 - 2026-08-22

- Added bounded screenshot-aligned replay of exact captured RSX output-zero clip coordinates with a checked homogeneous divide and deterministic per-event GLB export.
- Preserved each selected draw's NDC bounds and relative on-screen position so exact foreground evidence can distinguish prop fragments from plausible character fragments without inventing world-space transforms.
- Kept attribute meaning, runtime branch execution, component ownership, world space, a complete character, skinning, skeleton, retail materials, and mod readiness explicitly unproved.

## 2.15.0 - 2026-08-22

- Added strict `if1-texture-bound-topology-v3` validation for exact bounded RSX fragment-program payloads, independently decoded sampler references, captured-mask reconciliation, and target-slot proof.
- Added `runtime-fragment-sampler-census` for deterministic payload-free filtering before character-part assembly.
- Kept v1/v2 compatibility and taught the transform census and affine position replay to consume v3 without turning static shader reference into a runtime-path, material-semantic, or ownership claim.

## 2.14.0 - 2026-08-22

- Added a bounded `runtime-position-replay-export` that symbolically replays straight-line fixed-constant RSX vertex arithmetic and accepts output zero only when it is a finite affine function of attribute zero alone.
- Validated a caller-selected projection candidate against multiple exact program decompositions, bounded its inverse residual, and exported selected events in one shared pre-projection frame.
- Preserved topology, relative draw placement, deterministic neutral per-event materials, and exact payload identities while keeping attribute meaning, shader texture sampling, component ownership, full-character assembly, skinning, rigging, and retail materials false.

## 2.13.0 - 2026-08-22

- Added a strict, offline `runtime-vertex-transform-census` for complete `if1-texture-bound-topology-v2` bundles.
- Reproduced RPCS3's bounded reachable-instruction walk and RSX source-field decoding to report exact input attributes, fixed constant IDs, indexed-constant use, opcodes, output registers, and payload-free constant identities.
- Grouped identical vertex programs across draws and separated stable from varying referenced constants without persisting game payloads or raw values.
- Kept position, matrix, bone-palette, skinning, ownership, assembled-character, rigging, and render gates explicitly false.

## 2.12.0 - 2026-08-22

- Added strict validation for `if1-texture-bound-topology-v2` bundles carrying the exact 8,708-byte RSX vertex-program image/entry point and 8,192-byte transform-constant bank used by each bounded draw.
- Required event/SHA-derived filenames, fixed extents, exact SHA-256 identity, complete-file reconciliation, and the existing 16-draw/64 MiB ceiling while preserving v1 bundle output byte-for-byte.
- Exposed only a vertex-transform-payload identity gate; bone palettes, shader semantics, assembled character ownership, UVs, materials, rigging, and injection remain separate proof gates.

## 2.11.0 - 2026-08-22

- Raised the texture-bound runtime allowlist to the smallest bounds that contain the cross-build-stable surface identity set: 512 unique hashes and 40 KiB.
- Kept strict ASCII, lowercase SHA-256, duplicate, symlink, size, and count rejection while adding exact-bound positive and over-bound negative tests.

## 2.10.0 - 2026-08-22

- Added explicit validation/export support for `if1-texture-bound-topology-v1` bundles without weakening the 2.8.0 census format.
- Required the exact external target-texture allowlist, slot/hash reconciliation, capture-key reconstruction, enabled-address binding scope, and an honest `shader_reference_proven=0` claim.
- Enforced 64-hash, 256-address, 16-draw, per-payload, and 64 MiB bounds; rejected mixed schemas, counter drift, unallowed hashes, duplicate keys, malformed files, and overwrite attempts.
- Kept texture identity correlation separate from unique component ownership, geometry-to-XPP identity, UV, materials, rigging, injection, and full-character claims.
- Preserved byte-compatible 2.8.0 GLB/report output for the original `if1-topology-census-v1` bundles.

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

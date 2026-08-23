# xpp-tool durable tool inventory

This inventory registers bounded commands that were added to unblock a parent
modding or reverse-engineering goal. A command enters this file only after it
has a callable contract, rejection tests, no-overwrite behavior, deterministic
output where applicable, an operator card, and a maintained source location.

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

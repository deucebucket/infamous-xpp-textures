# xpp-tool durable tool inventory

This inventory registers bounded commands that were added to unblock a parent
modding or reverse-engineering goal. A command enters this file only after it
has a callable contract, rejection tests, no-overwrite behavior, deterministic
output where applicable, an operator card, and a maintained source location.

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

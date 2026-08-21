# xpp-tool

End-to-end XPP/PSARC tools for inFAMOUS 1 (PS3, BCUS-98119):

- extract textures to PNG
- encode PNGs back into XPP (same format the game already reads)
- derive lower-memory 1x/2x XPPs losslessly from existing 2x/4x packs
- strictly validate every descriptor and compare candidate texture residency with retail
- rebuild install PSARCs with selected XPP replacements
- extract and rebuild a complete install1/install2 profile with a byte audit
- generate exact texture hash indexes for scene-coverage tracing
- export **static** mesh sections to GLB

Python 3.10+, standard library only. This repo does not include game files.

The game keeps reading XPP. PNG is the edit format. This tool does not make the executable load PNG.

`xpp-tool` is the neutral command name. The original `if1-tex` entry point is
kept as a fully compatible alias, including all existing commands and options.

## How packages are laid out

Textures use 0x70-byte descriptors (chunk `0x03100000`) plus a texel heap
(chunk `0x0D800000`). Descriptor `+0x40` is a payload-relative mip-chain
address. Header `+0x28` is the payload's file offset.

```
file_offset = header[+0x28] + desc[+0x40]
heap_offset = desc[+0x40] − texel_chunk.offset
next_addr − this_addr  ==  align128(chain_bytes) × faces
```

The explicit mip count at descriptor `+0x2c` and the embedded mip byte at
`+0x45` must agree. The writer keeps them synchronized, preserves retail
texture order and opaque heap gaps, updates the owning segment and chunk spans,
and moves the untouched final link segment without rewriting it.

Formats: DXT1 `0x86`, DXT3 `0x87`, DXT5 `0x88`, X8R8G8B8 `0x85`,
R5G6B5 `0x84`, R6G5B5 `0x8F`, HILO8 `0x95`.

Static meshes: rigid sections with positions/UVs. Joint-local pieces (helicopter
rotors) are placed at rest pose. Skinned/character packages have **no** static
sections and are refused.

## Install

```bash
git clone https://github.com/deucebucket/infamous-xpp-textures.git
cd infamous-xpp-textures
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
xpp-tool --help
if1-tex --help  # compatibility alias
```

```bash
pip install -e ".[dev]"
python3 -m pytest -q
```

## Textures

```bash
if1-tex list --xpp /path/to/package.xpp
if1-tex extract --xpp /path/to/package.xpp --outdir ./out
if1-tex extract --psarc /path/to/USRDIR/data.psarc --entry /some.xpp --outdir ./out
if1-tex extract-all --xpp-dir /path/to/xpp-folder --outdir ./out
if1-tex verify --xpp /path/to/package.xpp
```

`verify` now fails on every malformed or unresolved descriptor, explicit versus
embedded mip-count disagreement, incomplete 128-byte heap layout, overlap, or
out-of-bounds texel chain. It never silently drops a bad descriptor from the
reported total.

`--level`, `--index`, `--max`, `--outdir` work on `extract` as before.

## Pack (PNG → XPP)

Replace one or more 2D textures, then rewrite the heap and descriptors:

```bash
if1-tex pack --xpp /path/to/package.xpp --out ./edited.xpp --replace 0=./out/package.0.mip0.png
```

All PNGs produced by `extract` (`STEM.N.mip0.png`):

```bash
if1-tex pack --xpp /path/to/package.xpp --out ./edited.xpp --from-dir ./out
```

Nearest-neighbor upscale every 2D texture, rebuild mips, rewrite addresses:

```bash
if1-tex pack --xpp /path/to/package.xpp --out ./edited.xpp --scale 4
```

`--scale` implies a size change. Cubemaps are left alone (the packer only
replaces 2D textures). The package must have **one** texel-heap chunk.

Round-trip check:

```bash
if1-tex extract --xpp ./edited.xpp --outdir ./check
if1-tex verify --xpp ./edited.xpp
```

## Derive a gameplay-sized pack

If a 4x XPP already contains complete mip chains, derive a true 2x package by
dropping only its largest mip. This copies the remaining BCn bytes exactly and
does not recompress them:

```bash
if1-tex derive \
  --retail ./retail/male_base_Zeke.xpp \
  --source ./generated-4x/male_base_Zeke.xpp \
  --target-scale 2 \
  --out ./mod-xpp/male_base_Zeke.xpp
```

Use `--target-scale 1` for enhanced texels at retail dimensions. Mixed 2x/4x
sources are supported. `--include-index`, `--exclude-index`, and
`--max-upscaled` build selective profiles for the game's fixed PS3-era texture
budget.

A global true-2x corpus can be structurally correct and still exceed the
retail game's resident-memory budget. Prefer a hybrid: enhanced 1x globally,
then promote selected visible packages to true 2x after gameplay testing.

## Build the install archive

inFAMOUS does not override its install data with loose XPPs. Rebuild the
owner's retail install PSARC with the selected packages:

```bash
if1-tex psarc-pack \
  --psarc ./retail/infamous1.psarc_s \
  --xpp-dir ./mod-xpp \
  --out ./mod/infamous1.psarc_s
```

The archive writer retains entry order, manifest bytes, uppercase-path MD5s,
and every entry not selected for replacement. The same mod directory can be
used for both install archives; packages belonging to the other archive are
reported as ignored. An explicit `--include` list is strict and fails if an
included name is absent. Keep protected retail archives outside the game
directory, never swap a live archive while RPCS3 is running, and use separate
retail and HD launch profiles.

## Full chain: retail PSARCs → editable XPPs → verified profile

### 1. Extract the complete retail pair

```bash
xpp-tool profile-extract \
  --install1 ./retail/install1/infamous1.psarc_s \
  --install2 ./retail/install2/infamous2.psarc_s \
  --outdir ./workspace
```

This writes every XPP/XPPS under `workspace/xpp/install1` or
`workspace/xpp/install2`, preserving its manifest path. `workspace.json`
records both source hashes plus the name, size, and hash of every PSARC entry.
The output directory must not already exist; an interrupted extraction never
publishes a partial workspace.

### 2. Extract, edit, and repack selected XPPs

Use the existing `extract`, `pack`, `derive`, and `verify` commands. Put only
the finished replacement `.xpp`/`.xpps` files in one replacement directory.
Nested folders are allowed; basenames must be unique.

```bash
xpp-tool extract --xpp ./workspace/xpp/install1/textures/A21.xpp --outdir ./png
xpp-tool pack \
  --xpp ./workspace/xpp/install1/textures/A21.xpp \
  --from-dir ./png \
  --out ./replacements/A21.xpp
xpp-tool verify --xpp ./replacements/A21.xpp
```

### 3. Strictly preflight the replacements

Compare one candidate with the exact retail XPP it replaces:

```bash
xpp-tool validate \
  --retail ./workspace/xpp/install1/textures/A21.xpp \
  --candidate ./replacements/A21.xpp \
  --known-startup-pass-extra 608288 \
  --known-startup-fail-extra 2705440
```

Or route and validate the whole replacement set without spending time building
either PSARC:

```bash
xpp-tool profile-validate \
  --install1 ./retail/install1/infamous1.psarc_s \
  --install2 ./retail/install2/infamous2.psarc_s \
  --xpp-dir ./replacements \
  --json
```

The report includes every descriptor, promoted-record count, exact raw and
128-byte-padded texture-chain growth, package growth, and per-record changes.
Optional observed pass/fail bounds are explicitly labeled **startup-path only**.
A pack can pass the opening simply because its promoted textures were not used;
scene coverage is always required before calling a texture gameplay-safe.
`--fail-on-budget` exits 2 (or refuses `profile-build`) at or above the supplied
observed startup-fail bound.

### Prove whether a scene reaches exact pack bytes

Generate a full JSON identity map and a compact hash allowlist from any strictly
valid XPP:

```bash
xpp-tool runtime-index \
  --xpp ./replacements/A21.xpp \
  --label A21-candidate \
  --json-out ./trace/A21.json \
  --allowlist-out ./trace/A21.sha256
```

The index covers each complete descriptor upload, face chain, individual mip,
and leading mip prefix. Prefixes matter because the RSX path can omit BCn tail
mips smaller than 4x4 while retaining the encoded bytes that precede them. The
allowlist is consumed by the opt-in texture observer in the private
RPCS3 research fork, so unrelated render targets and animated buffers never
fill the log.

RR — Really Readable rundown: the tool makes fingerprints of the encoded
texture bytes. A matching runtime fingerprint proves those exact bytes reached
the observed GPU-upload boundary. No match means either the scene never asked
for them **or** the game rearranged/copied the bytes first; it does not prove
which one by itself. Pair the hash result with upload dimensions and a scene
where the texture is visibly present.

### 4. Build and audit the installable pair

```bash
xpp-tool profile-build \
  --install1 ./retail/install1/infamous1.psarc_s \
  --install2 ./retail/install2/infamous2.psarc_s \
  --xpp-dir ./replacements \
  --outdir ./profile
```

`profile-build` finds which source archive owns each replacement, rejects
unknown or duplicate basenames, performs the same strict retail comparison on
every XPP, and only then builds both outputs in a hidden staging directory. It
then reopens both archives and checks:

- PSARC version, compression, block size, flags, entry count, name digests, and exact manifest bytes/order;
- every replacement payload against the supplied XPP bytes;
- every unchanged payload against the retail source bytes;
- source and output sizes and SHA-256 hashes.

Only a completely verified pair is renamed to `profile/`. `profile.json`
contains the audit counts and hashes. The resulting flat
`infamous1.psarc_s`/`infamous2.psarc_s` directory can be selected directly in
the universal inFAMOUS Mod Manager's `BCUS98119` packed-profile controls.

## Static meshes

```bash
if1-tex mesh-list --xpp /path/to/package.xpp
```

One section:

```bash
if1-tex mesh-export --xpp /path/to/package.xpp --output ./prop.glb
```

Several pieces (rotors, chassis, …):

```bash
if1-tex mesh-export --xpp /path/to/package.xpp --output ./heli.glb \
  --record-offset 0x1450 --record-offset 0x14b0 --record-offset 0x1510
```

`--texture some.png` embeds that PNG. If omitted, the tool decodes a 2D texture
from the same package.

Character packages print that there are no static sections and exit non-zero.

## Typical HD texture pass

1. `profile-extract` the protected retail install pair.
2. `extract` the package.
3. Edit or upscale the PNGs (or use `--scale`).
4. `pack` to a new `.xpp`.
5. `verify` and `extract` the new file.
6. Build and byte-audit the complete install pair with `profile-build`.
7. Run `profile-validate` before the expensive PSARC build.
8. Test startup and a scene that actually uses every promoted texture before
   increasing 2x residency.

Do not commit or distribute game files or transformed textures. The tool and
presets can be distributed; each owner builds the mod from their own dump.

## License

[CC0 1.0](LICENSE). The code is public domain. The game is not.

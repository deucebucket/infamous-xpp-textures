# if1-tex

Work with inFAMOUS 1 (PS3, BCUS-98119) `.xpp` packages:

- extract textures to PNG
- encode PNGs back into XPP (same format the game already reads)
- derive lower-memory 1x/2x XPPs losslessly from existing 2x/4x packs
- rebuild install PSARCs with selected XPP replacements
- export **static** mesh sections to GLB

Python 3.10+, standard library only. This repo does not include game files.

The game keeps reading XPP. PNG is the edit format. This tool does not make the executable load PNG.

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
if1-tex --help
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

Use `--target-scale 1` for AI-enhanced texels at retail dimensions. Mixed 2x/4x
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

1. `extract` the package.
2. Edit or upscale the PNGs (or use `--scale`).
3. `pack` to a new `.xpp`.
4. `verify` and `extract` the new file.
5. Build replacement install PSARCs with `psarc-pack`.
6. Test a selective profile in gameplay before increasing its 2x residency.

Do not commit or distribute game files or transformed textures. The tool and
presets can be distributed; each owner builds the mod from their own dump.

## License

[CC0 1.0](LICENSE). The code is public domain. The game is not.

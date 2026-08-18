# if1-tex

Work with inFAMOUS 1 (PS3, BCUS-98119) `.xpp` packages:

- extract textures to PNG
- encode PNGs back into XPP (same format the game already reads)
- export **static** mesh sections to GLB

Python 3.10+, standard library only. This repo does not include game files.

The game keeps reading XPP. PNG is the edit format. This tool does not make the executable load PNG.

## How packages are laid out

Textures: 0x70-byte descriptors (chunk `0x03100000`) plus a texel heap (chunk `0x0D800000`). Descriptor `+0x40` is the mip-chain address.

```
heap_offset = desc[+0x40] − min(desc[+0x40] in this package)
next_addr − this_addr  ==  align128(chain_bytes) × faces
```

Formats: DXT1 `0x86`, DXT3 `0x87`, DXT5 `0x88`, X8R8G8B8 `0x85`, R5G6B5 `0x84`, R6G5B5 `0x8F`, HILO8 `0x95`.

Static meshes: rigid sections with positions/UVs. Joint-local pieces (helicopter rotors) are placed at rest pose. Skinned/character packages have **no** static sections and are refused.

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

`--scale` implies a size change. Cubemaps are left alone (the packer only replaces 2D textures). The package must have **one** texel-heap chunk.

Round-trip check:

```bash
if1-tex extract --xpp ./edited.xpp --outdir ./check
if1-tex verify --xpp ./edited.xpp
```

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

`--texture some.png` embeds that PNG. If omitted, the tool decodes a 2D texture from the same package.

Character packages print that there are no static sections and exit non-zero.

## Typical HD texture pass

1. `extract` the package.
2. Edit or upscale the PNGs (or use `--scale`).
3. `pack` to a new `.xpp`.
4. `verify` and `extract` the new file.
5. Point the decomp at the new package. Do not commit game files.

## License

[CC0 1.0](LICENSE). The code is public domain. The game is not.

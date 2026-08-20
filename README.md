# if1-tex

Work with inFAMOUS 1 (PS3, BCUS-98119) `.xpp` packages:

- extract textures to PNG
- encode PNGs back into XPP (same format the game already reads)
- export **static** mesh sections to GLB

Python 3.10+, standard library only. This repo does not include game files.

The game keeps reading XPP. PNG is the edit format. This tool does not make the executable load PNG.

## Window (no typing)

```bash
if1-tex
```

or `if1-tex ui`, or `if1-tex-ui`. Drop an `.xpp` on the window, or click the big box and pick one.

Three buttons:

1. Get the pictures out (PNG)
2. Make them HD and pack a new `.xpp` the game can still read
3. Save the 3D model (GLB)

Same jobs as the commands below. The window is the easy front. The CLI is for scripts.

If the desktop toolkit is missing, `if1-tex ui --web` opens a local page instead.

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
if1-tex          # window
if1-tex --help   # commands
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

`--scale` implies a size change. Cubemaps are left alone (the packer only replaces 2D textures). The package may have extra unused texel-heap chunks (those are kept). If textures actually live in more than one heap, pack refuses instead of dropping texels. The last mip chain may omit its 128-byte pad.

Round-trip check:

```bash
if1-tex extract --xpp ./edited.xpp --outdir ./check
if1-tex verify --xpp ./edited.xpp
```

## Static meshes

```bash
if1-tex mesh-list --xpp /path/to/package.xpp
if1-tex mesh-list --xpp /path/to/package.xpp --oids oid-names.csv --contact-out ./heli.contact.json
if1-tex mesh-export --xpp /path/to/package.xpp --output ./heli.glb --contact ./heli.contact.json --pbr
```

Every static section (rotors, chassis, …) in one GLB:

```bash
if1-tex mesh-export --xpp /path/to/package.xpp --output ./prop.glb
```

One piece at a time (for painting a single mesh):

```bash
if1-tex mesh-export --xpp /path/to/package.xpp --output ./pieces --each
```

Assemble like the helicopter (one intact object, joints placed, Blender-ready GLB):

```bash
if1-tex mesh-export --xpp ./wf_helicopter_transport.xpp \
  --output ./heli.glb --assemble recipe
```

`unique-largest` keeps the biggest piece per oid (usually the intact hull, not the wreck). `recipe` uses the measured intact transport heli. `--record-offset` is still the explicit list.

Open the GLB in Blender. That is the viewer. There is no separate XPP viewport.

Compile an edited GLB back into an XPP the game can read (same vertex/triangle counts):

```bash
if1-tex mesh-compile --xpp ./original.xpp --glb ./edited.glb --out ./compiled.xpp
```

That also writes `compiled.glb` so you can check the compile in Blender.

Dump the whole package:

```bash
if1-tex inspect --xpp /path/to/package.xpp
```

Remaster look (derived PBR — not in the game file). Pass the 4× albedo folder if you have one:

```bash
if1-tex mesh-export --xpp /path/to/package.xpp --output ./prop.glb \
  --pbr --hd-dir ./textures_4x --maps-dir ./pbr
```

Only some pieces:

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

## What it needs (public tool)

No GPU. No Blender. No CUDA. Python 3.10+ and the standard library.

| Job | How | Hardware |
|---|---|---|
| Extract PNG | decode DXT on CPU | any |
| HD pack (`--scale 2/4`) | nearest-neighbor resize on CPU, rebuild mips, rewrite the XPP | any (a 2048² pack is a few seconds) |
| Derived PBR | Sobel normals + roughness/metal from the albedo, on CPU | any |
| Static mesh → GLB | read float3 + UVs | any |
| Window | GTK 3 if present, otherwise `if1-tex ui --web` | any |

HD is **not** AI upscale and **not** GPU. It is integer nearest-neighbor so the game still sees DXT the same way. If someone later wants a nicer upscaler they edit the PNGs in whatever they have and `--from-dir` pack; the tool does not require that.

PBR maps are **invented from the color texture**. Infamous 1 does not store metalness/roughness. The GLB remaster is for looking at the mesh. The game still reads DXT in the XPP.

Settings in the window: HD scale (auto / 2× / 4×), assemble mode (unique-largest / first / all / heli recipe), remaster PBR on/off.

## License

[CC0 1.0](LICENSE). The code is public domain. The game is not.

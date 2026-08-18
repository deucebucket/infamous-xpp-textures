# if1-tex

Extract textures from inFAMOUS 1 (PS3, BCUS-98119) `.xpp` packages to PNG.

Python 3.10+, standard library only. This repo does not include game files. You supply your own `.xpp` or `.psarc`.

## How it reads a package

Textures live in PACK version 8 `.xpp` files (often inside a `.psarc`).

Each texture is a **0x70-byte descriptor** (chunk `0x03100000`) plus texel bytes in the heap (chunk `0x0D800000`).

Descriptor **`+0x40`** is the absolute address of that texture’s mip chain:

```
heap_offset = desc[+0x40] − min(desc[+0x40] in this package)
```

Mip chains are stored largest first, each padded to 128 bytes. A cubemap is six of those padded chains.

```
next_addr − this_addr  ==  align128(chain_bytes) × faces
```

Formats:

| Byte | Name |
| ---: | --- |
| `0x86` | DXT1 |
| `0x87` | DXT3 |
| `0x88` | DXT5 |
| `0x85` | X8R8G8B8 / A8R8G8B8 |
| `0x84` | R5G6B5 |
| `0x8F` | R6G5B5 |
| `0x95` | HILO8 |

The tool is read-only. It does not rebuild packages.

## Install

```bash
git clone https://github.com/deucebucket/infamous-xpp-textures.git
cd infamous-xpp-textures

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e .
```

```bash
if1-tex --help
```

Optional tests (synthetic fixture, no game files):

```bash
pip install -e ".[dev]"
python3 -m pytest -q
```

## Run

### List textures (writes nothing)

```bash
if1-tex list --xpp /path/to/package.xpp
```

From a PSARC:

```bash
if1-tex list --psarc /path/to/USRDIR/data.psarc --entry /some_package.xpp
```

Example:

```
[0] 256x256 mips=9 format=0x88 (DXT5)  heap+0
[17] 32x32 mips=6 format=0x88 (DXT5)  heap+2,401,024  CUBEMAP(6 faces)
```

### Extract one package

```bash
if1-tex extract --xpp /path/to/package.xpp --outdir ./out
```

```bash
if1-tex extract \
  --psarc /path/to/USRDIR/data.psarc \
  --entry /some_package.xpp \
  --outdir ./out
```

Writes:

```
out/some_package.0.mip0.png
out/some_package.1.mip0.png
```

| Flag | Meaning |
| --- | --- |
| `--level N` | Mip level. `0` is the largest (default). |
| `--index N` | Only descriptor `N`. |
| `--max N` | Stop after `N` textures. |
| `--outdir DIR` | Output folder (created if needed). |

### Extract a folder of `.xpp`

```bash
if1-tex extract-all --xpp-dir /path/to/xpp-folder --outdir ./out
```

Walks `**/*.xpp`.

### Check heap layout on one package

```bash
if1-tex verify --xpp /path/to/package.xpp
```

Reports how many adjacent address deltas match `align128(chain) × faces`.

## Workflow

1. Use your own disc / RPCS3 `USRDIR`. Do not commit game files.
2. Point `--psarc --entry` at a package, or extract the PSARC and use `--xpp` / `--xpp-dir`.
3. `list` one package, then `extract` or `extract-all`.
4. Cubemaps: `--level 0` writes the first face (mip 0) as a 2D PNG. All six faces stay in the heap.

## License

[CC0 1.0](LICENSE). The code is public domain. The game is not. You need a copy you have the right to use.

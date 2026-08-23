# xpp-tool

End-to-end XPP/PSARC tools for packed inFAMOUS 1 PS3 profiles, with
BCUS98119 as the primary established render/decomp authority and NPUA80480 as
a separately validated deployment target:

- extract textures to PNG
- encode PNGs back into XPP (same format the game already reads)
- derive lower-memory 1x/2x XPPs losslessly from existing 2x/4x packs
- strictly validate every descriptor and compare candidate texture residency with retail
- rebuild install PSARCs with selected XPP replacements
- extract and rebuild a complete install1/install2 profile with a byte audit
- generate exact texture hash indexes for scene-coverage tracing
- export **static** mesh sections to GLB
- export one exact packed character record as a bounded diagnostic GLB
- correlate exact packed character streams with a topology-paired runtime draw

Python 3.10+, standard library only. This repo does not include game files.

The game keeps reading XPP. PNG is the edit format. This tool does not make the executable load PNG.

`xpp-tool` is the neutral command name. The original `if1-tex` entry point is
kept as a fully compatible alias, including all existing commands and options.
Durable diagnostic commands are registered in [TOOL-INVENTORY.md](TOOL-INVENTORY.md).

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

### Compare two builds before transferring anything

```bash
xpp-tool profile-oracle \
  --left-install1 ./bcus/install1/infamous1.psarc_s \
  --left-install2 ./bcus/install2/infamous2.psarc_s \
  --right-install1 ./npua/install1/infamous1.psarc_s \
  --right-install2 ./npua/install2/infamous2.psarc_s \
  --left-label BCUS98119 \
  --right-label NPUA80480 \
  --json-out ./oracle.json
```

The oracle emits aggregate archive contracts, package counts, case-insensitive
full-name and basename overlap, duplicate-routing counts, and exact shared
package byte identity. It never emits input paths, package names, payloads, or
payload hashes. `--catalog-only` skips slow payload hashing and explicitly
withholds byte-identity claims.

RR — Really Readable rundown: matching names only prove that two filing
cabinets have labels in common. Matching sizes still do not prove the files are
the same. Even byte-identical retail packages do not automatically prove that a
modified package is safe in both games. Build or validate replacements against
the exact target pair, then prove the target scene in runtime. The oracle always
keeps direct cross-build replacement transfer unauthorized.

### Rebase texture edits onto another retail build

Do not copy a modified package wholesale when the oracle says the source and
target package bytes differ. Keep the target retail XPP as the base and transfer
only the texture edits whose original retail identities match uniquely:

```bash
xpp-tool texture-rebase \
  --source-retail ./source/retail.xpp \
  --source-candidate ./source/hd.xpp \
  --target-psarc ./target/install1/infamous1.psarc_s \
  --target-entry /matching-package.xpp \
  --out ./target/rebased-hd.xpp \
  --json
```

The identity is the exact original retail texture chain plus its format, face
count, dimensions, and mip topology. Descriptor numbers are deliberately not
used as cross-build identities. The command detects both resized textures and
same-size pixel edits, refuses missing or ambiguous matches, rejects cubemap
edits, verifies every unselected target texture remained exact, validates the
rebuilt target, and publishes the output atomically. `--include-index` can limit
the transfer to explicitly changed source records. `--allow-zero-change`
produces an exact target-retail control only when no edit is selected.
An already extracted target may be supplied with `--target-retail` instead of
the `--target-psarc`/`--target-entry` pair.
Without `--include-index`, every changed chain transfers, including same-size
re-encodes; inspect `source_changed_records` before building the profile.

RR — Really Readable rundown: think of the source retail texture as a
fingerprint card. The tool finds exactly one matching original fingerprint in
the target build, then puts the edited picture into that target-owned slot. If
there are zero or several matching slots, it stops. This proves a careful
offline conversion, not that the target game has loaded or displayed it;
profile validation and scene-specific runtime proof still follow.

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
publishes a partial workspace. The slot directories preserve packages that use
the same basename in both archives; neither copy overwrites the other.

### 2. Extract, edit, and repack selected XPPs

Use the existing `extract`, `pack`, `derive`, and `verify` commands. Put only
the finished replacement `.xpp`/`.xpps` files in one replacement directory.
Nested folders are allowed. A flat basename keeps the legacy workflow when it
has exactly one owner across the retail pair. When a basename exists in both
archives, preserve the extracted `install1/` or `install2/` prefix:

```bash
xpp-tool extract --xpp ./workspace/xpp/install1/textures/A21.xpp --outdir ./png
xpp-tool pack \
  --xpp ./workspace/xpp/install1/textures/A21.xpp \
  --from-dir ./png \
  --out ./replacements/A21.xpp
xpp-tool verify --xpp ./replacements/A21.xpp

mkdir -p ./replacements/install1/textures
cp ./workspace/xpp/install1/textures/A21.xpp \
  ./replacements/install1/textures/A21.xpp
```

An exact manifest-relative path wins inside an explicit slot. A slot-local
basename shortcut is accepted only when unique in that archive. The tool fails
before staging if a flat name has several owners, a file names the wrong slot,
two inputs resolve to one retail target, or an inexact name is still ambiguous.

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

Build one or more explicit host-GPU replacement records without enlarging the
XPP the PS3 game sees:

```bash
xpp-tool runtime-bundle \
  --retail ./retail/male_base_Zeke.xpp \
  --candidate ./true2x/male_base_Zeke.xpp \
  --index 1 \
  --outdir ./trace/zeke-one-2x
```

`runtime-bundle` strictly compares the two XPPs, binds the retail upload hash
and exact source shape to the candidate hash and larger shape, writes only the
selected encoded mip prefix, and publishes the bundle atomically. The private
RPCS3 fork refuses a row if any hash, shape, format, byte count, or file is
wrong. RR: the game still budgets a retail texture; only RPCS3's host GPU image
becomes larger. This is experimental and must be A/B tested in a scene proven
to call the selected retail identity.

### 4. Build and audit the installable pair

```bash
xpp-tool profile-build \
  --install1 ./retail/install1/infamous1.psarc_s \
  --install2 ./retail/install2/infamous2.psarc_s \
  --xpp-dir ./replacements \
  --outdir ./profile
```

`profile-build` resolves each replacement to one exact archive/manifest owner,
rejects unknown or ambiguous inputs, performs the same strict retail comparison
on every XPP, and only then builds both outputs in a hidden staging directory.
It then reopens both archives and checks:

- PSARC version, compression, block size, flags, entry count, name digests, and exact manifest bytes/order;
- every replacement payload against the supplied XPP bytes;
- every unchanged payload against the retail source bytes;
- source and output sizes and SHA-256 hashes.

Only a completely verified pair is renamed to `profile/`. `profile.json`
contains the audit counts and hashes. The resulting flat
`infamous1.psarc_s`/`infamous2.psarc_s` directory can be selected directly in
the universal inFAMOUS Mod Manager's matching packed-profile controls.

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

## Rigged characters: report before conversion

Compare two owned builds before trusting descriptor numbers or internal
locations:

```bash
xpp-tool character-oracle \
  --left-xpp ./build-a/male_base_Zeke.xpp \
  --right-xpp ./build-b/male_base_Zeke.xpp \
  --left-label build-a \
  --right-label build-b \
  --json-out ./zeke-cross-build-oracle.json
```

Version 2.9 matches texture descriptors by their complete encoded-content
identity and structural shape, never by list position. It separately matches
character geometry contracts after removing only location fields, then reports
location-delta histograms instead of raw payload bytes. Missing, changed, or
ambiguous members fail closed; an existing output is never overwritten. A
positive report may prove that one exact observer allowlist applies to both
builds, while still keeping cross-build repacking, model export, and injection
unauthorized.

RR — Really Readable rundown: two editions can contain the same Zeke textures
but shuffle every numbered slot. The old slot number is therefore a shelf
address, not the texture's identity. This oracle fingerprints the picture and
its complete mip layout, finds its one matching shelf in the other edition,
and does the same for the packed geometry contracts. A complete match gives us
a universal lookup map. It does not yet tell Blender which packed numbers are
positions, UVs, bones, or weights; that still needs one complete decoded retail
vertex-stream proof.

```bash
xpp-tool character-report \
  --xpp /path/to/male_base_Zeke.xpp \
  --external /path/to/owned/Fallout76/character.nif \
  --json-out ./character-compatibility.json
```

`character-report` is the first fail-closed stage of the rigged-model pipeline.
On an inFAMOUS character XPP it proves paired geometry-heap envelopes, exact
big-endian triangle lists, descriptor-local vertex counts, and the byte extents
of three MSB-first packed streams. On a Fallout 4/76 NIF it validates the
header, block and string tables, block extents, footer roots, skin instances,
bone-data counts, and every referenced `NiNode` before reporting hashes and
counts. Game data is read locally and is never added to the repository.

The intended full chain is:

1. validate the owned source NIF;
2. normalize it as skinned glTF 2.0;
3. map it onto a preserved retail inFAMOUS character template;
4. rebuild XPP streams, pointers, chunks, fixups, and material references;
5. prove an export/import semantic round trip; and
6. validate animation and visibility in a controlled game scene.

The current command intentionally reports `injection_authorized: false`.
Triangle topology is proved, but target positions, normals, UVs, weights,
mesh-local joint palettes, hierarchy bindings, inverse-bind direction,
materials, wrapper/LOD ownership, and runtime visibility are not all decoded.
It will not create a plausible-looking but structurally unsafe character pack.

RR — Really Readable rundown: the Fallout model and Zeke model both have a
skeleton-shaped structure, but they speak different binary languages. The
report is the translator's checklist. It proves which pages we can read, names
the pages we still cannot read, and blocks conversion until every animation-
critical field has an exact mapping. The next proof is one retail character
vertex stream captured before and after the game's decoder; that tells us how
to turn the packed XPP numbers back into real vertex values.

An RPCS3 RSX capture can be rejected or correlated before deeper analysis:

```bash
xpp-tool character-capture-report \
  --xpp /path/to/male_base_Zeke.xpp \
  --rrc /path/to/BCUS98119_capture.rrc.gz \
  --json-out ./zeke-capture-match.json
```

This validates the capture container and serialized maps, hashes
each captured guest-memory payload, and looks for exact copies of the proven
XPP triangle-index streams. It emits only identities, sizes, addresses, and
hashes. An exact hit binds one package topology record to live RSX memory; a
miss only says that this frame did not provide an exact index-buffer match.
Neither result assigns vertex semantics or proves model compatibility.

When an exact index block is referenced by a captured
`NV4097_SET_BEGIN_END(0)` command, the report also records that replay-command
index and every memory block attached to the same draw. Sibling blocks remain
explicitly `unclassified-draw-sibling` candidates: their guest address, size,
and SHA-256 narrow the decoded vertex search without labeling any block as a
position, normal, UV, joint, or weight stream prematurely. Captured bytes are
never written to the JSON report.

RR — Really Readable rundown: finding Zeke's exact triangle list in a capture
is like finding his page number in a live book. Binding that page to a draw
call tells us which small stack of other pages the GPU read at the same time.
Those nearby pages are where the decoded vertices should be, but the tool does
not guess which page means position or skin weight. The next proof compares
each bounded sibling to the known 26-vertex record and accepts a semantic only
when its stride, numeric range, and decode/encode round trip all agree.

For a complete indexed draw, version 2.0 also replays the relevant raw RSX
register writes. It reports the primitive and index ranges, index type and
location, and each enabled vertex attribute's number, component format,
stride, frequency, location, and address. A vertex attribute is structurally
bound only when one sibling memory block exactly matches the address and the
capture extent calculated from the live index range. Attribute numbers remain
semantic-free: even a `float32 x3` attribute is not labeled `POSITION` until a
numeric comparison and round trip prove that meaning.

RR — Really Readable rundown: the first report put all pages used by Zeke's
draw on the table. The RSX descriptor pass now reads the tabs on those pages:
"three floats every 12 bytes," "four normalized bytes every 10 bytes," and so
on. That reduces a pile of 13 mystery blocks to the exact five vertex arrays
used by the 26-vertex piece. It still does not call a tab "position" or
"weights" merely because the label looks familiar.

Version 2.1 takes the next bounded step for vertex arrays whose RSX numeric
format is already defined. It decodes big-endian `float32`, `float16`, and
`unorm8` elements for the exact captured index span, re-encodes them into a
copy of the original strided payload, and requires byte-for-byte and SHA-256
identity. Reports contain only counts, formats, component minima/maxima, and
hashes. Captured bytes and per-vertex values remain local. `cmp32` stays
explicitly unsupported because its component packing has not been proved.

RR — Really Readable rundown: we can now read three kinds of numbered vertex
pages and put every byte back exactly where it came from. That proves the
number reader is correct; it does not yet prove what the numbers mean. A
three-float page may look like positions, but xpp-tool will not label it until
those values are mathematically tied back to one packed XPP stream. Model
export and injection therefore remain blocked.

Version 2.2 correlates those supported arrays back to XPP stream zero. It
searches bound arrays that share a location and stride, requires their numeric
component bytes to cover one complete record with no gaps or overlaps, rebuilds
every record, and compares the result with the bounded stream-zero bytes in the
matched XPP geometry heap. A match is accepted only when it is unique and the
source contains multiple distinct records and byte values, avoiding an
all-zero coincidence. Reports still contain only layout metadata and hashes.

RR — Really Readable rundown: two separate RSX tabs turned out to be one XPP
page split at byte 4. For the visible 26-vertex Zeke piece, attribute 3 supplies
four bytes and attribute 9 supplies six bytes; joining them makes each original
10-byte XPP record exactly. This proves how stream zero is carried to the GPU.
It still does not prove whether either field means UVs, color, weights, joints,
or something else. The remaining three compressed streams and model semantics
must be solved before export or injection can be enabled.

Version 2.3 adds the remaining RSX numeric format observed in that draw:
`cmp32`. It follows RPCS3's maintained vertex-fetch contract: one big-endian
word stores signed 11-, 11-, and 10-bit components, the values are extended
through the RSX 16-bit scale and divided by 32767, and W is 1. Every decoded
word must rebuild exactly. The source contract is available in the
[RPCS3 vertex fetch implementation](https://github.com/RPCS3/rpcs3/blob/8fd2ae954d80d867fd2d58795848c77d1954574b/rpcs3/Emu/RSX/Program/GLSLSnippets/RSXProg/RSXVertexFetch.glsl).

RR — Really Readable rundown: the last two unread tabs each squeeze three
signed numbers into one 32-bit word. xpp-tool now opens all five tabs in the
captured Zeke draw and can close them again without changing a byte. That is
complete numeric coverage, not complete model understanding: the tool still
does not call the two packed tabs normals or tangents, and it still cannot skip
the game's decompression, skinning, or output-compression stages. Export and
injection remain blocked.

Version 2.7 can export one proven draw as a deliberately limited Blender GLB:

```bash
xpp-tool character-diagnostic-export \
  --xpp /path/to/owned/male_base_Zeke.xpp \
  --binding-report /path/to/owned/exact-draw-binding.json \
  --attribute-payload /path/to/owned/attribute-0.bin \
  --position-hypothesis-attribute 0 \
  --output ./zeke-piece-diagnostic.glb \
  --json-out ./zeke-piece-diagnostic.json
```

The command requires exactly one complete RSX draw binding, one exact XPP
topology match, and one explicitly selected `float32x3` attribute. It verifies
the captured payload's exact size and SHA-256, bounds every indexed vertex,
rejects non-finite or fully degenerate geometry, recenters the result, and
writes the retail triangle list unchanged. Repeating the same export produces
the same GLB bytes.

This is an inspection aid, not a rigged-character converter. The GLB embeds a
plain unlit diagnostic material and labels the selected attribute as an
unproved position hypothesis. It contains no UVs, skin, skeleton, inverse bind
matrices, retail material, or injection authorization. Captured payloads and
game packages remain local and must not be committed.

RR — Really Readable rundown: we can now put one small, exact piece of the
visible Zeke draw on Blender's workbench. The triangle connections are proved,
and the candidate coordinates make a real, non-flat shape, but the game has not
yet told us what body part it is or how it attaches to Zeke's bones. A Blender
render proves the export is usable for inspection. It does not prove the full
character, textures, animation, or a safe replacement pack. Those remain the
next gates.

Version 2.8 adds a separate bridge for complete runtime topology bundles that
have not yet been mapped back to one XPP record:

```bash
xpp-tool runtime-topology-diagnostic-export \
  --bundle /path/to/owned/topology-census-output \
  --event 1 \
  --position-hypothesis-attribute 0 \
  --output ./runtime-draw-candidate.glb \
  --json-out ./runtime-draw-candidate.json
```

The command validates the complete bundle as one immutable set: the completion
totals, all event binding files, reconstructed descriptor hashes, exact index
and vertex payload hashes and sizes, and absence of missing, extra, or symlinked
files. The selected event must contain one bounded big-endian u16 triangle list
and one unambiguous zero-frequency `float32x3` attribute. Every source index
must fall inside the captured vertex range. When that range begins after vertex
zero, the exporter subtracts the recorded first vertex to make GLB-local
indices while retaining the original range and index hashes as evidence.
Coordinates must be finite and produce at least one nondegenerate triangle.
Output is refused inside the input bundle.

RR — Really Readable rundown: this lets us quickly put a GPU draw on Blender's
workbench before we know which XPP record produced it. That is useful for
sorting nearby draws into likely hair, face, clothes, hands, or unrelated
scene objects. The tool still labels every result “runtime-only and unowned.”
Looking like Zeke is a clue, not identity proof; XPP correlation, UVs, textures,
bones, and injection remain separate gates. Progress pieces can render quickly
at normal resolution, while 4K is reserved for the assembled full character.

Version 2.10 accepts the stricter texture-bound capture format as a separate
mode. The exact allowlist used by the private runtime capture is required again
at validation time:

```bash
xpp-tool runtime-topology-diagnostic-export \
  --bundle /path/to/owned/texture-bound-topology-output \
  --texture-allowlist /path/to/exact-target-textures.sha256 \
  --event 1 \
  --position-hypothesis-attribute 0 \
  --output ./texture-bound-draw-01.glb \
  --json-out ./texture-bound-draw-01.json
```

The validator proves that the listed texture identity was associated with an
enabled fragment-texture address when RPCS3 captured the draw. It also proves
the exact local payload set, capture-key calculation, bounds, and triangle
topology. It deliberately records `shader_reference_proven=0`: an enabled slot
is not yet proof that the active shader sampled it, and a texture-bound draw is
not automatically a uniquely owned Zeke body part. Visual inspection and XPP
correlation remain required.

RR — Really Readable rundown: the old search recognized a piece only when its
runtime index list exactly matched the retail XPP index list. The new route can
also ask, “Which bounded draw had this exact known Zeke texture switched on?”
That is a stronger clue for reordered or alternate-LOD geometry, but still a
clue. Each partial can be exported and rendered immediately at quick normal
resolution. The 3840×2160 treatment starts when head, hair, body/skin, clothes,
and gear are assembled into the fully textured character; 4× source textures
and a 4K output canvas remain separate concepts.

Version 2.11 raises only the bounded texture-identity input envelope from 64
hashes / 16 KiB to 512 hashes / 40 KiB. A real surface-only cross-build oracle
produced 501 unique whole-chain, face, mip, and mip-prefix identities because
large textures may reach RPCS3 as partial uploads rather than one whole XPP
descriptor. Strict lowercase SHA-256, duplicate, symlink, byte-size, and count
validation is unchanged.

RR — Really Readable rundown: the first live texture-bound draw matched a 1×1
texture because its entire file is one tiny upload. The 28 larger Zeke
descriptor hashes produced zero runtime matches even while Zeke was visible.
That means the next search needs the smaller pieces of those textures—their
mips and prefixes—not a larger guess. The new limit fits that exact 501-hash
evidence set with 11 spare rows; it is not an unbounded memory increase.

Version 2.12 also accepts `if1-texture-bound-topology-v2`. Each captured draw
must include exactly one 8,708-byte RSX vertex-program image (including its
entry point) and one 8,192-byte transform-constant bank. Their filenames,
sizes, SHA-256 values, event numbers, binding rows, completion totals, and the
entire directory file set must reconcile before an export is written. The
older census-v1 and texture-bound-v1 formats remain accepted unchanged.

RR — Really Readable rundown: we now have real Zeke-material-bound geometry,
but the pieces are stored before the GPU places and bends them. The v2 bundle
preserves the small GPU “recipe and ingredient table” used for that placement:
the vertex program says what math to perform, and the constant bank supplies
matrices and other per-draw values. Capturing them does not magically prove
which values are bones, but it gives the next analyzer exact, hash-bound input
instead of forcing it to guess. A completed full Zeke render remains the gate
for the first 3840×2160 image; partial progress can still publish immediately
at quick resolution.

Version 2.13 decodes that v2 transform input without running the game again:

```console
xpp-tool runtime-vertex-transform-census \
  --bundle /path/to/owned/texture-bound-topology-v2 \
  --texture-allowlist /path/to/exact-targets.sha256 \
  --json-out /path/outside-the-bundle/vertex-transform-census.json
```

The command revalidates the entire bundle, walks only reachable RSX vertex
instructions from the captured entry point, reconstructs all three encoded
sources, and reports referenced vertex inputs, fixed constants, indexed
constants, opcodes, output registers, and stable-versus-varying constant
identities across shared programs. It refuses incomplete bundles and existing
outputs. Raw program bytes, constant values, game paths, and game assets are
not copied into the report.

RR — Really Readable rundown: this answers whether a draw uses a fixed stack of
GPU values or an address-indexed array that could be compatible with a bone
palette. It still does not name any value a matrix or bone. The next gate must
replay the actual shader arithmetic and prove which output places vertices on
screen before those pieces can be assembled honestly.

Version 2.14 performs that next bounded arithmetic replay for an explicit event
set:

```console
xpp-tool runtime-position-replay-export \
  --bundle /path/to/owned/texture-bound-topology-v2 \
  --texture-allowlist /path/to/exact-targets.sha256 \
  --events 1,2,3 \
  --projection-event 4 \
  --output /path/outside-the-bundle/cluster.glb \
  --json-out /path/outside-the-bundle/cluster.json
```

It symbolically executes only the supported straight-line affine path from RSX
attribute zero to output zero. The selected projection constants must invert
cleanly and decompose at least two captured program outputs before any GLB is
written. Relative positions are preserved in one pre-projection frame; the
combined result is recentered only for inspection.

RR — Really Readable rundown: the tool is taking the exact GPU placement math
and undoing the final camera projection so nearby pieces can finally be viewed
together. A colorful multi-piece result is still a diagnostic: an enabled Zeke
texture slot does not prove the shader sampled that texture or that every draw
belongs to Zeke. The render is evidence for geometry placement, not yet a full
textured character.

Version 2.15 adds the missing fragment-side filter. A complete
`if1-texture-bound-topology-v3` bundle includes one bounded, hash-bound RSX
fragment program per draw. The validator independently walks its exact
instruction extent, decodes texture opcodes and sampler numbers, reconciles the
derived sampler mask with RPCS3's captured mask, and requires every target
texture slot to appear in that mask:

```console
xpp-tool runtime-fragment-sampler-census \
  --bundle /path/to/owned/texture-bound-topology-v3 \
  --texture-allowlist /path/to/exact-targets.sha256 \
  --json-out /path/outside-the-bundle/fragment-samplers.json
```

This proves a static reference from the captured fragment program to the exact
sampler slot carrying the target texture. It does not prove that a branch ran,
that the texture owns the draw, or that a UV/material semantic is understood.
The v3 filter exists specifically to exclude enabled-but-unused texture slots
before geometry is assembled. The existing vertex-transform census and
position replay accept both v2 and v3 bundles.

Version 2.16 adds the bounded fallback needed when captured vertex shaders use
one combined world/view/projection transform that cannot be split honestly
into a shared camera matrix plus per-draw model matrices:

```console
xpp-tool runtime-screen-position-replay-export \
  --bundle /path/to/owned/texture-bound-topology-v3 \
  --texture-allowlist /path/to/exact-targets.sha256 \
  --events 1,2,3 \
  --output /path/outside-the-bundle/screen-cluster.glb \
  --json-out /path/outside-the-bundle/screen-cluster.json
```

The command replays the exact affine path from captured attribute zero to clip
output zero, performs the checked homogeneous divide, and preserves each draw's
normalized screen position and depth. It writes neutral per-event colors so a
render can be compared directly with the exact gameplay screenshot.

RR — Really Readable rundown: the GPU did not hand us one file called “Zeke.”
It handed us separate draw calls, including his glasses and the book he was
holding. This export puts those pieces back where they appeared on the captured
screen, making it practical to classify them one by one. It does not turn the
pieces into a body, prove that a draw belongs to Zeke, recover world space, or
add UVs, textures, bones, weights, inverse binds, or editable game materials.
A genuinely moddable character still requires those later gates and a verified
reverse import/repack path.

Version 2.17 pages past the 16-draw capture ceiling without changing what a
capture key means. First, generate the exact exclusion manifest from the
complete base v3 bundle:

```console
xpp-tool runtime-capture-key-exclusion \
  --bundle /path/to/owned/page-1-v3 \
  --texture-allowlist /path/to/exact-targets.sha256 \
  --output /path/outside-the-bundle/page-1-keys.tsv \
  --json-out /path/outside-the-bundle/page-1-keys.json
```

The private observer consumes that TSV and writes a v4 bundle. Every v4-aware
validator command requires the same exact file through
`--capture-key-exclusion`; a different byte sequence, count, or captured-key
overlap fails closed. After page 2 validates, combine the two page selections:

```console
xpp-tool runtime-screen-position-page-merge \
  --page-bundle /path/to/owned/page-1-v3 \
  --page-events 1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16 \
  --page-capture-key-exclusion - \
  --page-bundle /path/to/owned/page-2-v4 \
  --page-events 1,2,3 \
  --page-capture-key-exclusion /path/to/page-1-keys.tsv \
  --texture-allowlist /path/to/exact-targets.sha256 \
  --output /path/outside-the-bundles/pages-1-and-2.glb \
  --json-out /path/outside-the-bundles/pages-1-and-2.json
```

RR — Really Readable rundown: page 1 is a tray that filled up after 16 GPU draw
calls. The TSV is the exact list of pieces already on that tray. Page 2 tells
the observer to ignore only those exact pieces, then catches the next unique
ones. The merge tool checks that every later tray excludes the complete earlier
set and that no piece appears twice before placing all selected pieces back at
their captured screen coordinates. A new page may reveal Zeke's missing shoes,
but it is not called footwear until its rendered shape visibly agrees with the
foreground screenshot.

Operator card: both commands are offline and single-process. Inputs must be
complete caller-owned, non-symlink bundles and exact external manifests;
bundles remain read-only. Outputs must be new paths outside every input bundle
and are never overwritten. A page contains at most 16 draws, an exclusion
contains at most 256 unique lowercase SHA-256 keys and 16,705 bytes, and a merge
contains 2 through 17 pages, at most 1,048,576 selected vertices, and at most
3,145,728 selected indices. Repeated runs over identical bytes are deterministic.
The output is diagnostic NDC geometry only: it does not prove character
ownership, feet, UVs, textures, world transforms, bones, weights, materials,
PBR, or a reverse-import path.

Version 2.18 classifies reuse across the complete page chain before another
runtime capture is requested:

```console
xpp-tool runtime-page-family-census \
  --page-bundle /path/to/owned/page-1-v3 \
  --page-capture-key-exclusion - \
  --page-bundle /path/to/owned/page-2-v4 \
  --page-capture-key-exclusion /path/to/page-1-keys.tsv \
  --texture-allowlist /path/to/exact-targets.sha256 \
  --json-out /path/outside-the-bundles/page-families.json
```

The census compares four deliberately separate levels. Exact geometry bytes
mean the index and ordered vertex-stream bytes repeat. Exact vertex-stream
bytes allow the index selection to change. A stable-layout partial-stream
candidate requires the same ordered layout, target texture/vertex-program
identity, and at least one exact stream while another stream changes. A weak
target-texture/vertex-program match is counted but never promoted to a component
family.
Many-to-one and one-to-many strong groups remain explicitly ambiguous.

RR — Really Readable rundown: a new capture key is a new ticket at the GPU
door, not automatically a new body part. Animation, a different triangle
subset, or another render pass can issue a different ticket for geometry we
already saw. This report sorts strong repeated-family evidence from weak
lookalikes so the next capture can move to a view that actually shows missing
geometry instead of filling another tray with the same seated pose.

Operator card: this command is offline, payload-free, deterministic, and
single-process. It accepts 2 through 17 distinct bundles, at most 16 events per
page and 34,816 cross-page comparisons. The JSON is bounded to 256 KiB, must be
written to a new path outside every immutable bundle, and contains no raw
payload bytes. The tiers are correlation evidence only: same source component,
new geometry, Zeke ownership, completeness, UVs, materials, bones, weights,
PBR, and reverse import all remain unproved.

Version 2.19 ties those runtime families back to the editable XPP source without
guessing a body-part name:

```console
xpp-tool runtime-xpp-source-census \
  --xpp /path/to/owned/male_base_Zeke.xpp \
  --page-bundle /path/to/owned/page-1-v3 \
  --page-capture-key-exclusion - \
  --page-bundle /path/to/owned/page-2-v4 \
  --page-capture-key-exclusion /path/to/page-1-keys.tsv \
  --texture-allowlist /path/to/exact-targets.sha256 \
  --json-out /path/outside-the-inputs/xpp-source-census.json
```

The command first requires complete topology coverage for the supplied XPP.
For each bounded runtime block, it applies that block's captured stride and
range start/count to every bounded XPP character stream-zero record and accepts
only one exact byte match. This preserves distinct layouts instead of forcing
every component through one record width. A full index-list match is reported
separately. Zero matches remain unbound; multiple matches remain ambiguous.
Neither is promoted by size, vertex count, appearance, or texture identity.

RR — Really Readable rundown: the GPU gives us Zeke as batches of triangles,
not as one Blender file. This check asks whether a runtime batch contains an
exact strip of bytes from one specific editable XPP record. A unique answer is
a real bridge back to the game package. It still does not tell us whether that
record is a shoe, jacket, glasses, book, or hair, and it does not decode UVs or
bones. Those names and meanings need separate proof.

Operator card: the command is offline and single-process. The XPP is capped at
64 MiB and an observed record stride is capped at 64 bytes; the
page/event/comparison bounds are inherited from the strict page
validator; the JSON is capped at 256 KiB. Inputs must be regular immutable
files/directories, output must be a new path outside every bundle and different
from the XPP, and no raw source or runtime payload bytes enter the report.

### Version 2.36 — strict partial-range material observations

A safe partial-range source receipt can now join an existing strict material
union without pretending that the capture contains every source vertex. Build
the partial lineage with the existing `character-uv-texture-binding` command,
then pass it to either union or export alongside at least one regular full-range
material observation:

```bash
xpp-tool character-material-coverage-export \
  --xpp ./retail/male_base_Zeke.xpp \
  --xpp-sha256 XPP_SHA256 \
  --texture-allowlist ./zeke-surface-identities.sha256 \
  --record-offset 533752 \
  --anchor-lineage ./page2-full-range-lineage.json \
  --anchor-lineage-sha256 ANCHOR_LINEAGE_SHA256 \
  --observation ./page2-material.json REPORT_SHA ./page2-bundle ./page1-keys.tsv \
  --observation ./page3-material.json REPORT_SHA ./page3-bundle ./page1-plus-page2-keys.tsv \
  --partial-observation \
    ./page1-partial-lineage.json PARTIAL_LINEAGE_SHA \
    ./page1-bundle - \
    ./three-page-source-census.json SOURCE_CENSUS_SHA \
    ./character-census.json CHARACTER_CENSUS_SHA \
  --output-glb ./zeke-hair-288.glb \
  --output-report ./zeke-hair-288.json
```

RR — Really Readable rundown: the page-one tray has 183 of the retail hair
record's 184 vertex rows. That sounds incomplete, but its triangle tickets use
only rows 0 through 182; the absent last row is never called. Version 2.36 does
not take that on faith. It reopens the actual index and UV bytes, checks every
triangle against retail, checks every index against the exact captured range,
replays the vertex/fragment shader identity, and independently matches every
named `Zeke_Hair_A/C/N/S` descriptor and mip prefix against the pinned character
census. Only then may the draw contribute material-covered faces.

The first owned result combines page one (**276** faces), page two (**11** new),
and page three (**1** new) into **288 / 294** strict retail-material triangles.
The deterministic GLB remains 179,204 bytes, SHA-256
`f11dc2be73ccba0aaad2576b76ae8e904c7302e8e44f3a6aa18c1cbc81705e3b`.
The six unproved triangles remain a separate orange audit primitive. The exact
four-map names are retained as compatible pass evidence; the full-range `C/N`
anchor still owns the exported display material, and `A`/`S` are not invented
as PBR roles.

Operator card: this extends stable IDs
`xpp-tool.character-uv-texture-binding.v1`,
`xpp-tool.character-material-coverage-union.v1`, and
`xpp-tool.character-material-coverage-export.v1`. Every partial observation is
the eight-value tuple shown above: pinned lineage, immutable bundle and optional
exclusion, pinned source census, and pinned character census. One through 16
total observations are accepted, but at least one regular full-range material
export is mandatory as the texture/UV/export anchor. The command is offline and
single-process; existing 64 MiB XPP/GLB, 1 MiB authority, inherited bundle, and
256 KiB report bounds remain. Inputs are immutable regular files/directories;
outputs are atomic, new-only, outside every bundle, and never overwritten.

This proves a strict material assignment for 288 hair triangles. It does not
prove the remaining six, a complete hair/head/body assembly, original
normals/tangents, bones/weights, authored PBR, 4× textures, RPCS3 round trip, or
native-decomp import. The approved matte/even-brown render remains the visual
baseline; this capability changes evidence coverage, not its lighting recipe.

### Version 2.35 — safe retail coverage from partial-range runtime draws

`runtime-xpp-source-census` now answers one more question before a useful old
draw is discarded: does its exact runtime triangle multiset exist inside the
pinned retail record, and do all referenced vertex indices stay inside the
exact source byte slice that was captured?

The first three-page owned run validates all **45** uniquely source-bound
events and rejects zero. Zeke hair record 533752 has six observations but only
three distinct index payloads. Page one events 15/16 carry 276 retail triangle
occurrences and reference vertices 0 through 182, entirely inside their exact
183-of-184 captured vertex slice. Page two adds 11 new occurrences; page three
adds one more. Their retail-ordered topology union is therefore **288 / 294**,
leaving **6** topology occurrences unobserved.

RR — Really Readable rundown: the older draw was missing one unused seat in its
vertex table, not necessarily a visible piece of hair. This validator checked
every triangle ticket from that draw against the retail hair record and then
checked that none of those tickets asks for the uncaptured last vertex. It is
safe topology evidence and cuts the search space in half. It is not yet a
strict material export: the canonical textured GLB remains **282 / 294** until
the page-one UV/shader/texture lineage is admitted through the material union.
Nothing about the approved matte hair appearance changes.

Operator card: stable ID `xpp-tool.runtime-xpp-source-census.v1`, extended in
2.35.0 and maintained in
`src/infamous_xpp_textures/source_correlation.py`. The existing offline,
single-process command accepts one regular non-symlink XPP capped at 64 MiB,
one exact allowlist, and the inherited chain of at most 17 immutable v3/v4
pages with their exact exclusions. For every unique source-bound event it
reopens the checksum-validated index payload, requires a triangle list,
requires every triangle occurrence to be a retail multiset member, and
requires every referenced vertex to remain inside the exact mapped range.
Rejected index evidence remains explicit and cannot enter a union. The
deterministic payload-free JSON is capped at 256 KiB, written only to a new path
outside all inputs, and includes per-event plus per-record covered/unobserved
counts and hashes. It does not prove UVs, named material roles, full character,
rigging, 4×, authored PBR, RPCS3 round trip, or native-decomp import.

### Version 2.34 — preserve material passes in the canonical character ledger

Attach one or more completed cross-material pass censuses when rebuilding the
canonical character ledger:

```bash
xpp-tool character-component-ledger \
  --title-id infamous-1 \
  --build-id bcus98119-v0100 \
  --candidate-id zeke \
  --material-report ./zeke-hair-union.json \
  --material-report-sha256 UNION_REPORT_SHA256 \
  --material-report ./zeke-jacket.json \
  --material-report-sha256 JACKET_REPORT_SHA256 \
  --visual-receipts ./zeke-render-receipts.json \
  --visual-receipts-sha256 RENDER_RECEIPTS_SHA256 \
  --material-pass-census ./zeke-hair-pass-census.json \
  --material-pass-census-sha256 PASS_CENSUS_SHA256 \
  --output ./zeke-component-ledger.json
```

Repeat the material-report pair for every proved component and the pass-census
pair for every exact cross-pass receipt. A census is admitted only when its XPP,
source record, vertex/triangle topology, retail index, texture family, covered
and unobserved counts, and both union hashes match material evidence already in
the same ledger. Its internal observations, pass groups, pairwise relationships,
canonical IDs, and nonclaims are independently validated as well.

RR — Really Readable rundown: the standalone census told us Zeke hair has three
real material/shader passes but only two geometry selections. Version 2.34 puts
that fact in Zeke's permanent record. The ledger still treats the page-two and
page-three animated position captures separately, then links the pass receipt
to the matching source component. That is like filing three paint recipes with
one body panel instead of manufacturing three copies of the panel. Nothing is
lost, and a future exporter can see the `A/C/N/S` possibility without mistaking
it for the twelve still-unobserved faces.

The first updated ledger is 44,794 bytes and byte-identical under reversed
material-report and equivalent census inputs. It retains five components, five
render receipts, and one accepted matte hair baseline while adding one census,
three pass signatures, and the exact 282/12 hair split. It does not infer PBR
roles or compositing order, merge poses, assign missing faces, finish Zeke,
upscale textures, rig the character, repack RPCS3 content, or import into the
native decomp. This gate creates no render, so no image is withheld.

### Version 2.33 — exact cross-material pass census

One character piece can be drawn several times with different shaders and
texture sets. Those passes may reveal new faces, or they may cover the exact
same faces for a different material stage. Compare them before spending another
foreground capture:

```bash
xpp-tool character-material-pass-census \
  --xpp ./retail/male_base_Zeke.xpp \
  --xpp-sha256 XPP_SHA256 \
  --texture-allowlist ./surface-identities.sha256 \
  --record-offset 533752 \
  --observation ./page2-hair.json REPORT_SHA ./page2-bundle ./page1-keys.tsv \
  --observation ./page3-hair-four-map.json REPORT_SHA ./page3-bundle ./page1-plus-page2-keys.tsv \
  --observation ./page3-hair-two-map.json REPORT_SHA ./page3-bundle ./page1-plus-page2-keys.tsv \
  --output ./zeke-hair-pass-census.json
```

Repeat `--observation REPORT REPORT_SHA256 BUNDLE EXCLUSION_OR_DASH` two
through 32 times. `-` is accepted only when that bundle does not require a
capture-key exclusion. Every report must be the strict `observed-only` result
for one draw. The command reopens the exact runtime index payload and refuses
authority drift, duplicate evidence, out-of-retail triangles, oversized output,
symlink output, and overwrite.

The report gives every exact pass signature, every pairwise triangle-multiset
relationship, and one retail-ordered any-pass union. It serializes hashes and
counts, not vertex, index, shader, or texture payload bytes.

RR — Really Readable rundown: Zeke's hair is genuinely drawn in multiple ways.
The page-three four-map `A/C/N/S` pass and its `C/N` pass use different fragment
programs and texture bindings, but both touch the exact same 275 triangles. They
are two coats of paint over the same boards, not extra boards. Page two and page
three each contribute seven faces the other draw missed, so together we still
have 282 of 294 proved hair triangles and the same 12 unknown faces. We must
preserve all three material passes for eventual faithful reconstruction, but a
new capture is worth doing only when it exposes a genuinely different draw,
pose, or state. Repeating this four-map pass cannot close those twelve.

This command does not decide that `A` means ambient/alpha, `S` means specular,
or how the passes composite. It does not render, upscale, rig, assemble full
Zeke, repack an RPCS3 mod, or import into the native decomp. The approved matte,
even-brown hair render remains the locked appearance regression baseline.

### Version 2.32 — exact repeated-draw material union to Blender GLB

`character-material-coverage-export` closes the gap between a payload-free
coverage receipt and a usable private Blender asset. It fully reruns the 2.31
union validation, retains the exact covered triangle multiset in memory, selects
one checksum-pinned lineage as the position/UV/texture anchor, and writes a
strict GLB plus a payload-free receipt. It does not recover triangle bytes from
the public union JSON and never paints unresolved faces by inference.

```console
timeout 60s xpp-tool character-material-coverage-export \
  --xpp /path/to/owned/male_base_Zeke.xpp \
  --xpp-sha256 RETAIL_XPP_SHA256 \
  --texture-allowlist /path/to/zeke-surface-identities.sha256 \
  --record-offset 533752 \
  --anchor-lineage /path/to/page3-hair-lineage.json \
  --anchor-lineage-sha256 PAGE3_LINEAGE_SHA256 \
  --observation /path/to/page2-hair.json PAGE2_REPORT_SHA256 /path/to/page2-v4 /path/to/page1-keys.tsv \
  --observation /path/to/page3-hair.json PAGE3_REPORT_SHA256 /path/to/page3-v4 /path/to/page1-plus-page2-keys.tsv \
  --output-glb /new/path/zeke-hair-union.glb \
  --output-report /new/path/zeke-hair-union.json
```

The first owned run exports Zeke hair record **533752** as one deterministic
**179,204-byte** GLB, SHA-256
`e4199e6e8e31635bbe7624164bed04b665224771e3a73851599d68b1fc534879`.
Its two compatible observations prove the retail `Zeke_Hair_C/N` material on
**282 / 294** triangle occurrences; the remaining **12** stay in a separate
orange diagnostic primitive. The 5,423-byte receipt repeats byte-identically at
SHA-256 `45bcea0b007be9b164b03168c7582806310ccf4195b58d3bfba892ece3f4566e`.

RR — Really Readable rundown: the old union tool could say, “these two game
views cover 282 hair faces together,” but Blender still received only one
view's 275-face material assignment. This bridge carries the exact combined
face list into the editable GLB. It keeps the approved matte texture and makes
only the twelve still-unproved faces orange. It is a better hair component, not
a complete hairstyle, head, character, rig, 4× texture set, PBR material, or
working game mod.

Operator card: stable ID
`xpp-tool.character-material-coverage-export.v1`. Inputs are one regular
non-symlink retail XPP capped at 64 MiB; its canonical SHA-256; one exact
allowlist; one record offset; one through 16 checksum-pinned strict material
reports with immutable bounded v3/v4 bundles and required exclusions; and one
checksum-pinned anchor lineage that must identify exactly one accepted
observation. The command is offline, single-process, deterministic, and
new-only. GLB output is capped at 64 MiB and the receipt at 256 KiB; destinations
must differ and remain outside every input/bundle. Duplicate, conflicting,
ambiguous-anchor, out-of-retail, hash-drift, dishonest-count, over-bound, symlink,
and overwrite cases fail closed. Public reports contain hashes/counts only; the
GLB and game-derived payloads remain private operator assets.

Two wheel builds with pinned `SOURCE_DATE_EPOCH=1787484000` are byte-identical.
The exact wheel size and SHA-256 live in `CHANGELOG.md` and
`TOOL-INVENTORY.md`, outside the wheel's README metadata, so the artifact never
contains a self-referential hash claim.

### Version 2.31 — exact material coverage across repeated draws

`character-material-coverage-union` answers whether two or more runtime views
actually expose different faces of the same editable retail record. It pins each
strict `observed-only` material report, reopens its immutable runtime bundle,
rechecks the exact index payload and page exclusion, and unions triangle
occurrences as a multiset against the retail XPP topology.

```console
timeout 60s xpp-tool character-material-coverage-union \
  --xpp /path/to/owned/male_base_Zeke.xpp \
  --xpp-sha256 RETAIL_XPP_SHA256 \
  --texture-allowlist /path/to/zeke-surface-identities.sha256 \
  --record-offset 534628 \
  --observation /path/to/page1-material.json PAGE1_REPORT_SHA256 /path/to/page1-v3 - \
  --observation /path/to/page2-material.json PAGE2_REPORT_SHA256 /path/to/page2-v4 /path/to/page1-keys.tsv \
  --output /new/path/zeke-jacket-coverage-union.json
```

The first owned run gives a useful negative answer. Main jacket coverage moves
from **492** to only **493 / 1,002** triangles; the head becomes **185 / 404**;
the packs remain **170 / 302**; and the small jacket detail is the expected
complete duplicate control at **24 / 24**. Repeating the same ordinary pose is
therefore not a path to the missing surfaces. The next capture must deliberately
change animation, camera, clothing/state, or occlusion before it earns another
material observation.

RR — Really Readable rundown: a screenshot can look perfect while still showing
only the same front-facing triangles the last screenshot showed. This tool lays
the two exact face lists on top of each other. It proved the second view added
one jacket triangle, some head triangles, and no new pack triangles. That is why
we are keeping the accepted render as the visual baseline while changing the
capture strategy instead of changing its good-looking material or lighting.

Operator card: stable ID `xpp-tool.character-material-coverage-union.v1`.
Inputs are one retail XPP capped at 64 MiB, one exact texture allowlist, and one
through 16 pinned material reports capped at 1 MiB each with their immutable
v3/v4 bundles and required v4 exclusion manifests. The tool is offline,
single-process, deterministic, input-order independent, and payload-free. It
rejects duplicate observations, source/UV/texture-family conflicts, dishonest
coverage, index payloads outside retail topology, symlinks, over-bound inputs,
and overwrite attempts. Output is capped at 256 KiB and contains counts and
hashes, never model, texture, shader, index, or game payload bytes. Full
character, original normals/tangents, rig/skin, 4×, authored PBR, RPCS3 round
trip, and native import remain separate gates.

### Version 2.30 — canonical multipart character component ledger

`character-component-ledger` is the durable bridge between individual material
exports and the all-asset completion inventory. It consumes repeatable exact
`infamous-character-material-export` reports plus an optional checksum-pinned,
payload-free visual receipt manifest. A friendly texture name is not the
identity: each component is keyed by title, build, candidate, runtime page, and
source record offset. Multiple runtime events may therefore remain explicit
observations of one source component without becoming duplicate work.

```console
timeout 60s xpp-tool character-component-ledger \
  --title-id infamous-1 \
  --build-id bcus98119-v0100 \
  --candidate-id zeke \
  --material-report /path/to/hair-material.json \
  --material-report-sha256 HAIR_REPORT_SHA256 \
  --material-report /path/to/head-material.json \
  --material-report-sha256 HEAD_REPORT_SHA256 \
  --visual-receipts /path/to/zeke-render-receipts.json \
  --visual-receipts-sha256 RENDER_RECEIPTS_SHA256 \
  --output /new/path/zeke-component-ledger.json
```

The first owned run reconciles five distinct Zeke records: hair **533752**,
jacket **534628**, packs **535048**, head **536280**, and jacket detail
**536488**. All five already have exact source record, runtime topology, UV,
retail texture binding, material GLB, and published-render receipts. Only jacket
detail has complete observed material coverage (**24 / 24** triangles). Hair is
**275 / 294**; jacket is **492 / 1,002**; packs are **167 / 302**; and head is
**174 / 404**. Those four remain selected for missing material-draw evidence,
not for redundant re-export. Three final-code outputs are byte-identical at
**28,843 bytes**, SHA-256
`755fb441c735671697953141074e92bce357049addb1bb831388cfccb76e6046`.

RR — Really Readable rundown: this is Zeke's parts checklist. It says, “we have
this exact hair record, this exact head record, these two separate jacket-family
records, and this exact packs record.” It also says what each part still lacks.
The approved matte/unlit hair image is now a protected visual baseline: future
work should not reintroduce fake shine, patchiness, or split-tone lighting. But
a good-looking picture cannot fill nineteen unproved hair faces, add feet, join
the body, create bones, prove 4× textures, invent PBR, or prove a working RPCS3
mod. Those gates stay separate and false.

Operator card: stable ID `xpp-tool.character-component-ledger.v1`. The command
is offline and single-process. It accepts at most 256 material observations,
128 components, and 256 render receipts. Each material report is capped at 1
MiB, the visual receipt manifest at 256 KiB, and output at 1 MiB. Every input is
a regular non-symlink file with an exact SHA-256 pin; duplicate paths/content,
contradictory immutable geometry, mismatched texture families, dishonest
triangle coverage, unknown render targets, and unsupported schemas fail closed.
Output contains counts, names, and hashes—not model, texture, shader, or game
payload bytes—and is atomically published only to a new path. Full character,
rig/skin, 4×, authored PBR, RPCS3 round trip, and native-decomp import remain
false until independent evidence closes each gate.

### Version 2.29 — full-range character material candidate census

`character-material-candidate-census` replaces the repeated manual step of
copying page/event/record triples into separate lineage commands. It reads one
checksum-pinned source census, selects every full-source-range event on one
exact runtime page, removes only explicit completed `EVENT:RECORD_OFFSET`
identities, and runs the maintained shader-lineage proof against every remaining
candidate.

```console
timeout 60s xpp-tool character-material-candidate-census \
  --bundle /path/to/owned/page-2-v4 \
  --texture-allowlist /path/to/zeke-surface-identities.sha256 \
  --capture-key-exclusion /path/to/page-1-keys.tsv \
  --page 2 \
  --source-census /path/to/xpp-source-census.json \
  --source-census-sha256 SOURCE_CENSUS_SHA256 \
  --character-census /path/to/character-census.json \
  --character-census-sha256 CHARACTER_CENSUS_SHA256 \
  --character-side left \
  --exclude-candidate 5:536488 \
  --exclude-candidate 15:533752 \
  --exclude-candidate 16:533752 \
  --output /new/path/page2-material-candidates.json
```

The first final-code run sees **six** eligible page-two full-range events. Three
are already completed and excluded by their exact event/record identities. The
remaining three all pass: event 3 / record 534628 / `Zeke_Jacket`, event 11 /
record 536280 / `Zeke_Head`, and event 13 / record 535048 / `Zeke_Packs`.
Two final reports repeat byte-for-byte at **5,901 bytes**, SHA-256
`6a5b5c88924aa81103ad3c87f259e75d4d43f94ce80150efaf9e1b719b87692b`.
The report includes each accepted lineage-report SHA-256, UV input shape,
fragment coordinate, family, and sampler/name identities; a failed candidate
would remain present with its exact bounded rejection reason.

RR — Really Readable rundown: the old process could accidentally skip a valid
component or redo one we had already finished because the list lived in shell
commands and notes. The permanent census asks the source evidence for the whole
eligible page, subtracts only exact completed identities, and gives every new
candidate a pass or fail receipt. A pass means “safe to write the full lineage
and render this component next.” It does not mean the component is the whole
character, fully material-covered, rigged, PBR, 4×, or ready to inject.

Operator card: one page, at most 16 candidates, at most 272 source event rows,
2 MiB per pinned JSON authority, inherited 64 MiB bundle/payload bounds, one
process, no runtime or network, and a 512 KiB payload-free JSON output. Inputs
must remain immutable; the output must be a new path outside the bundle and all
authorities. Duplicate/unknown completion exclusions and an empty remaining set
fail closed. Repeated identical inputs produce identical bytes; occupied output
refuses overwrite.

### Version 2.28 — complete shader-bound texture-family GLB export

`character-material-export` now preserves every texture selected by one exact
shader-lineage family instead of requiring exactly two. A lineage must still
contain unique samplers, unique short alphanumeric name suffixes, one bounded
family, and required `C` plus `N` descriptors. Two through eight images are
accepted; every encoded runtime-prefix identity is rechecked against the retail
XPP before decoding.

```console
timeout 60s xpp-tool character-material-export \
  --xpp /path/to/owned/male_base_Zeke.xpp \
  --bundle /path/to/owned/page-2-v4 \
  --texture-allowlist /path/to/zeke-surface-identities.sha256 \
  --capture-key-exclusion /path/to/page-1-keys.tsv \
  --lineage /path/to/zeke-jacket-event5-binding.json \
  --lineage-sha256 LINEAGE_SHA256 \
  --material-coverage-mode observed-only \
  --output-glb /new/path/zeke-jacket-event5.glb \
  --output-report /new/path/zeke-jacket-event5.json
```

The first owned run exports record `536488` as **26 vertices / 24 triangles**,
one shader-proved UV layer, generated inspection normals, and four exact retail
images in sampler order:

- sampler 0: `Zeke_Jacket_N.psd` — display-wired as the name-derived normal;
- sampler 1: `Zeke_Jacket_A.psd` — embedded, role unassigned;
- sampler 2: `Zeke_Jacket_S.psd` — embedded, role unassigned;
- sampler 3: `Zeke_Jacket_C.psd` — display-wired as name-derived base color.

The runtime and retail index lists are identical, so all **24/24 triangles** have
exact material coverage. Two final GLBs repeat at **513,796 bytes**, SHA-256
`bd364627c4bd2e57cb7088320859d1839e5b67fa4d3d91acd487646ea83e3c47`;
their payload-free reports repeat at SHA-256
`a208e1335c10ea5fb27585ede1499af26f075ddce57bc491d91ab9ce5815f09e`.

RR — Really Readable rundown: Blender should receive every image the captured
shader selected, but receiving an image is not the same as knowing its modern
PBR meaning. The exporter therefore keeps all four retail maps inside the GLB,
uses only the already-supported `C`/`N` display convention, and writes explicit
`null` display roles for `A` and `S`. Nothing silently turns those letters into
alpha, specular, roughness, or metalness.

Three immediate unlit views show two small, widely separated textured islands,
including a legible `EMPIRE CITY` detail. That proves the UVs address coherent
retail imagery. It does not prove the human-readable name or whole-character
placement of the geometry, and it does not make this record a full jacket.

Operator card: stable ID `xpp-tool.character-material-export.v1`, maintained in
xpp-tool 2.28.0. The 64 MiB XPP/GLB and 256 KiB report bounds, immutable authority
pins, complete topology/material partition, deterministic atomic pair publication,
and no-overwrite behavior remain unchanged. The GLB is private/operator-owned;
the public repo and wiki retain only source, payload-free facts, and review PNGs.

### Version 2.27 — packed three-component character streams

`character-uv-texture-binding` now distinguishes two byte counts that look
similar but are not interchangeable: bytes in the original packed XPP vertex
stream and bytes in a renderer's padded host upload. A three-component
half-float row occupies six packed source bytes even when a host-side upload
later expands it to eight. The source-binding path applies the same packed rule
to three-component unorm8 rows; other formats retain their conservative sizing
until independently proved.

The command still refuses gaps, overlaps, non-finite float rows, and ambiguous
descriptor ordering. It changed the source-storage width calculation; it did
not weaken the existing complete-tiling proof.

```console
timeout 60s xpp-tool character-uv-texture-binding \
  --bundle /path/to/owned/page-2-v4 \
  --texture-allowlist /path/to/zeke-surface-identities.sha256 \
  --capture-key-exclusion /path/to/page-1-keys.tsv \
  --page 2 --event 5 --record-offset 536488 \
  --source-census /path/to/source-census.json \
  --source-census-sha256 SOURCE_CENSUS_SHA256 \
  --character-census /path/to/character-census.json \
  --character-census-sha256 CHARACTER_CENSUS_SHA256 \
  --character-side left \
  --output /new/path/zeke-jacket-event5-binding.json
```

The first owned 2.27 run proves one exact 26-vertex / 24-triangle component:

- source record `536488`, stream zero, 26 rows × 10 bytes;
- one valid complete layout: four-byte attribute 3 at byte 0, then six-byte
  half3 attribute 9 at byte 4;
- attribute 9 XY feeds vertex output 7 and fragment `TEX0`; its third stored
  component is consistently `-1.0`, but this gate assigns no semantic to it;
- samplers 0–3 select unique named `Zeke_Jacket_N`, `A`, `S`, and `C`
  descriptors;
- the runtime index list exactly equals the complete retail component: all 72
  indices / 24 triangles are covered, with no hair-style unobserved remainder.

RR — Really Readable rundown: the earlier tool tried to measure the original
game bytes with the ruler RPCS3 uses after preparing them for the PC GPU. That
made a real ten-byte row appear impossibly twelve bytes wide. We now use the
packed-game-data ruler for the XPP and keep the padded-renderer ruler in its
own lane. The result is a second real wire from Zeke triangles to UVs to named
jacket textures, not a texture chosen because it looked close.

This does not yet tell us whether the 24-triangle piece is a collar, jacket
detail, book-adjacent prop, or another sub-piece. It creates no render and does
not claim a full jacket or full Zeke. The next bounded gate extends the material
exporter from a two-texture hair pair to this four-texture family while keeping
retail name suffixes separate from authored PBR meanings; any progress render
is published as soon as it exists.

Operator card: stable ID `xpp-tool.character-uv-texture-binding.v1`, introduced
in 2.25 and maintained in xpp-tool 2.28.0. Inputs, bounds, payload-free output, SHA-256 pins, atomic
new-only publication, and no-runtime/no-network behavior remain unchanged from
2.25. Packed source widths are now explicit and regression-tested; full
character, PBR/4x, RPCS3 repack, and native-decomp import remain separate gates.

### Version 2.26 — permanent retail-material component GLB export

`character-material-export` turns one checksum-pinned 2.25 lineage into a
deterministic, inspectable GLB. It rereads the immutable runtime bundle and
retail XPP, verifies the full retail topology and both encoded texture-prefix
identities, decodes the retail color/normal mip-zero images, and writes the
proved half-float rows as `TEXCOORD_0`.

```console
timeout 60s xpp-tool character-material-export \
  --xpp /path/to/owned/male_base_Zeke.xpp \
  --bundle /path/to/owned/page-2-v4 \
  --texture-allowlist /path/to/zeke-surface-identities.sha256 \
  --capture-key-exclusion /path/to/page-1-keys.tsv \
  --lineage /path/to/zeke-hair-binding.json \
  --lineage-sha256 LINEAGE_SHA256 \
  --output-glb /new/path/zeke-hair-retail-material.glb \
  --output-report /new/path/zeke-hair-retail-material.json
```

The default `--material-coverage-mode observed-only` is the audit-safe export.
It proves the captured retail material on **275 of 294 triangles** and writes
the remaining **19 material-unobserved triangles** as a separate orange,
unlit diagnostic primitive. It repeats at 178,024 bytes, SHA-256
`1a2a3eaa8229a2c870b99793996dd948db108397c42f83db5360f0ca018f0b68`;
its payload-free receipt repeats at SHA-256
`81e24c77402524c7d7739dfdec4d627158df99eb9b7afaaf46b515f004f3944f`.

For a clean progress image, add
`--material-coverage-mode preview-full-record`. That preview applies the
observed material to all 294 retail triangles without orange clay, but both
the GLB metadata and receipt keep `full_topology_material_coverage=false` and
`unobserved_material_preview_extrapolated=true`. It repeats at 177,084 bytes,
SHA-256
`39d8773b4deecaff5ebcf6cabae3b5e5b19b14a6030f47d3b3e56f95c0e71f6b`;
its receipt repeats at SHA-256
`d1261f53a968e3720b805796ac50db39e84e5ef7e8b3b6c88f8e7651e7e928a8`.
Both forms contain 184 vertices, the full 294-triangle retail topology, one
proved UV layer, and two embedded retail PNGs.

RR — Really Readable rundown: this is the first time one recovered Zeke piece
contains the edit-facing trio in one file: triangles, UV coordinates, and the
exact retail color/normal images the captured shaders selected. The runtime
draw proves where that material belongs for 275 triangles. It does not yet
prove the other 19, so the strict file shows them plainly and the clean file
labels their material as a preview extrapolation. Both are useful for Blender
today, a retail reverse packer next, and a native importer later.

It is still a diagnostic hair component. Vertex attribute 0 remains an
explicitly unproved position semantic; the exported normals are generated from
triangles for inspection, not recovered retail normals; and `C`/`N` material
roles follow retail names rather than a proved native PBR contract. The first
published views were also artificially shiny because the old review renderer
replaced the imported material with metallic 0.18 / roughness 0.48; those
views are rejected historical evidence. The maintained renderer now has an
explicit imported-material-preservation mode, and the current clean preview is
matte. The command preserves the exact 275/19 evidence and leaves coordinate
convention, diagnostic position meaning, alpha/material behavior, and the
missing 19-face material assignment as separate gates instead of adjusting the
asset by eye.

The delivery paths remain independent. Near term, this canonical component is
input to a validated retail XPP/PSARC round trip in RPCS3. Long term, the same
mesh/UV/material manifest can feed a native decomp importer once its asset
runtime and renderer exist. The native infrastructure is the larger up-front
gate; each later native mod should need less retail-container archaeology.

Operator card: stable ID `xpp-tool.character-material-export.v1`. Inputs are
immutable and checksum-pinned through the lineage/bundle contracts; the XPP is
capped at 64 MiB, GLB at 64 MiB, and JSON receipt at 256 KiB. The command is
offline, single-process, new-only, and publishes the GLB/report pair together.
The report contains hashes, counts, names, bounds, and gate states—not texture,
vertex, index, shader, or game payload bytes. A render is published whenever it
exists and never gates the evidence/tool release.

### Version 2.25 — permanent character UV-to-texture shader lineage

`character-uv-texture-binding` closes one exact material-binding chain at a
time. It consumes an immutable v3/v4 draw bundle plus checksum-pinned source
and character censuses, follows the fragment sampler back through the vertex
shader at component level, and reconciles the selected vertex input with the
exact XPP source stream and named texture descriptors.

```console
timeout 60s xpp-tool character-uv-texture-binding \
  --bundle /path/to/owned/page-2-v4 \
  --texture-allowlist /path/to/zeke-surface-identities.sha256 \
  --capture-key-exclusion /path/to/page-1-keys.tsv \
  --page 2 --event 16 --record-offset 533752 \
  --source-census /path/to/source-census.json \
  --source-census-sha256 SOURCE_CENSUS_SHA256 \
  --character-census /path/to/character-census.json \
  --character-census-sha256 CHARACTER_CENSUS_SHA256 \
  --character-side left \
  --output /new/path/zeke-hair-uv-texture-binding.json
```

The first owned run proves this chain for the 184-vertex hair record:

- exact XPP record `533752`, stream zero, 184 rows × 8 bytes;
- one valid complete packed layout, with attribute 3 at byte 0 and two-component
  half-float attribute 9 at byte 4;
- vertex attribute 9 XY feeds vertex output register 7, which RPCS3 maps to
  `TEX0`;
- both target fragment `TXB` instructions read that `TEX0` coordinate;
- sampler 0 resolves uniquely to the 174,760-byte mip prefix of
  `Zeke_Hair_N.psd`, while sampler 2 resolves uniquely to the 87,376-byte mip
  prefix of `Zeke_Hair_C.psd`;
- observed UV bounds are U `0.0051002502–0.9941406250` and V
  `0.0287017822–0.7763671875`.

RR — Really Readable rundown: before this, we had the correct hair triangles
and the correct hair textures sitting beside each other, but no proved wire
between them. This command follows the wire the game actually uses: two packed
numbers enter vertex attribute 9, the vertex shader passes their X and Y into
`TEX0`, and the fragment shader uses `TEX0` to sample the named hair color and
normal textures. That is why this is real progress toward a texturable Blender
piece rather than another visual guess.

The old capture did not directly store each descriptor's byte offset. The
command says so. It reconstructs byte 4 because the two descriptors exactly
tile the eight-byte stride and only one ordering has finite float components;
the reversed order decodes the leading `0xff` bytes as non-finite half floats.
This is a deterministic, fail-closed reconstruction—not a claim that the byte
offset came directly from the capture log.

This gate does not create a render. One hair mesh is not full Zeke, the `C`/`N`
name suffixes are not presented as native PS3 PBR metadata, and 4x/PBR,
remaining material slots, Blender assembly, RPCS3 repack, and native-decomp
import stay separate. The next gate writes the proved half-float rows as
`TEXCOORD_0`, attaches retail color/normal materials, and publishes the first
correctly textured progress render immediately.

Operator card: stable ID `xpp-tool.character-uv-texture-binding.v1`. The command
is offline and single-process; each JSON authority and the output are capped at
2 MiB and 256 KiB respectively, the bundle inherits the strict 16-event / 64
MiB payload contract, every external authority is SHA-256 pinned, and output is
new-only with atomic publication. It serializes hashes, counts, names, bounded
numeric summaries, and lineage tokens—not shader, vertex, index, texture, or
game bytes. The same report feeds the near-term RPCS3 material/repack path and
the later native-decomp asset importer; neither delivery path is promoted by
this evidence alone.

### Version 2.24 — canonical completion inventory and dual-output manifest

`asset-completion-inventory` is the permanent gate before any corpus-wide
character or item batch. It consumes four exact, checksum-pinned authorities:
the decomp graphics/assets tally, the retail static-GLB manifest, a metadata-only
gallery snapshot, and one character/item census selected as the first unfinished
batch.

```console
xpp-tool asset-completion-inventory \
  --decomp-tally /path/to/GRAPHICS-ASSETS-TALLY.md \
  --decomp-tally-sha256 TALLY_SHA256 \
  --static-glb-manifest /path/to/MANIFEST.json \
  --static-glb-manifest-sha256 GLB_MANIFEST_SHA256 \
  --gallery-snapshot /path/to/gallery-drive-snapshot.json \
  --gallery-snapshot-sha256 GALLERY_SNAPSHOT_SHA256 \
  --character-census /path/to/character-census.json \
  --character-census-sha256 CENSUS_SHA256 \
  --candidate-id zeke \
  --output /new/path/canonical-asset-inventory.json
```

The first owned run reconciles **57** successful retail static GLB exports,
**19** unique 8K asset renders, **1** gameplay screenshot, **1** duplicate Drive
file entry, **15,437** corrected texture records, and **0** character renders.
Nine render subjects join one static GLB by the deliberately narrow normalized
identity rule; ten remain unresolved instead of being guessed. The resulting
inventory has 68 records: 0 fully complete, 58 partial, and 10 unknown. It skips
only the work already proved: 57 retail static exports and 19 existing renders.

The first unfinished batch is Zeke because the input census—not a filename-only
search—proves the same multipart target in both builds, 31 named cross-build
texture identities, and 16 packed geometry contracts per build while every
complete-model and delivery gate remains false. The short exit is an editable,
correctly assembled asset that round-trips through retail XPP/PSARC in RPCS3.
The long exit uses the same canonical record in the native decomp. Native import
does not block the emulator path.

RR — Really Readable rundown: this is the project checklist that stops us from
doing finished work twice or calling a pretty picture a finished mod. A GLB says
“we exported this exact static model once.” An 8K PNG says “this exact picture
exists.” Neither says the object is correctly aligned, fully textured, safely
repackable, accepted by RPCS3, or loadable by the future native engine. For the
native decomp, mod authoring should become easier after its asset loader and
renderer exist because we control that loader and can use this stable manifest;
the hard wait is building that native runtime, not repacking each later mod.

Inputs are regular non-symlink files and must match their SHA-256 pins. Counts,
hashes, safe relative GLB paths, duplicate claims, candidate identity, and source
reconciliation fail closed. Private contact paths and payload bytes are never
serialized. Output is deterministic, capped at 4 MiB, and published only to a
new path. A partial render or turntable may publish immediately and never blocks
other evidence, but it cannot change a completion gate by itself.

### Version 2.23 — multipart character and item asset census

`character-asset-census` is the permanent profile-wide discovery primitive for
complete character/item extraction. It does not assume one filename is the
whole asset and it does not hard-code Zeke. One invocation audits a target XPP
across two complete extracted profiles and their exact ordinal OID manifests:

```console
xpp-tool character-asset-census \
  --left-profile /path/to/bcus-profile \
  --right-profile /path/to/npua-profile \
  --left-workspace-sha256 LEFT_WORKSPACE_SHA256 \
  --right-workspace-sha256 RIGHT_WORKSPACE_SHA256 \
  --left-oid-manifest /path/to/bcus/oid_manifest.txt \
  --right-oid-manifest /path/to/npua/oid_manifest.txt \
  --left-oid-manifest-sha256 LEFT_OID_SHA256 \
  --right-oid-manifest-sha256 RIGHT_OID_SHA256 \
  --left-target xpp/install1/male_base_Zeke.xpp \
  --right-target xpp/install1/male_base_Zeke.xpp \
  --anchor male_base_Zeke.xml --name-token zeke \
  --output /new/path/zeke-asset-census.json
```

The command verifies every workspace entry against its declared byte count and
SHA-256 while streaming the complete package corpus. It records:

- exact descriptor OID-to-name bindings and texture-family groupings;
- aligned manifest-OID references separated by XPP chunk type;
- proved packed character topology counts without inventing per-piece names;
- exact descriptor sharing, substantial mip/prefix sharing, and tiny shared-mip
  counts across both profiles;
- location-independent cross-build descriptor mapping without trusting index;
- separate false gates for piece completeness, orientation, alignment, UVs,
  materials, LOD/state/flavor selection, Blender completion, RPCS3 mod round
  trip, and native-decomp import.

The first owned audit read **5,038 packages / 3,769,944,736 declared bytes** and
**31,557 texture descriptors**. BCUS and NPUA each contain 31 target textures
and 16 packed geometry contracts. All 31 texture identities match uniquely,
but all 31 descriptor positions are reordered. No substantial target texture
is shared by another package and no substantial partial mip/prefix match was
found. The only exact external sharing is the same 8-byte, 1×1 utility texture
in five other packages per build.

RR — Really Readable rundown: Zeke really is built from many named parts and
texture sheets. His XPP directly names jacket, pants, head, hair, packs, a
combined hands/feet/collar/glasses family, an eye, glasses lookup strip, and
comic-book texture. The package also references named objects such as head,
jacket, hair, shoes, glasses, straps, clasps, firearm, and book. The full scan
does **not** find another package supplying his substantial texture bytes. The
remaining blocker is the hard one: assigning each of the 16 packed mesh records
to the correct named object and then proving which material/UV binding selects
which named texture family. Names prove the multipart inventory; they do not by
themselves assemble or texture the Blender model.

The command is a reusable per-asset primitive. Corpus batching must first
consume a verified completion inventory so already finished assets are skipped.
The short product goal is a validated Blender edit that round-trips into RPCS3;
the same canonical asset manifest is retained for the later native decomp.

Bounds: two profiles of at most 4,096 packages / 8 GiB each; individual package
256 MiB; workspace and OID manifest 16 MiB each; manifest anchor window at most
512 rows per side; detailed match count 20,000; JSON report 2 MiB; new output
only. Inputs are regular non-symlink files and every package is hash checked.

### Version 2.22 — proper-similarity decoder discriminator

The same permanent `character-source-runtime-correlate` command now reports two
narrower transform classes for every numeric family in addition to its
unrestricted affine metrics:

- **proper similarity:** rotation + translation + one positive uniform scale;
- **mirrored similarity:** one fixed reflection followed by the same proper
  rotation/translation/uniform-scale fit.

The implementation uses a bounded standard-library symmetric-eigen/quaternion
solver. It needs no NumPy, Blender, emulator, network, or game runtime, and it
does not serialize the fitted rotation, translation, scale, or vertex values.

On the independently proved 184-vertex hair record,
`scale-offset-unsigned` preserves the near-exact source/runtime relationship
under a proper transform (`R² 0.9999999971`, normalized RMSE
`0.0000091454`). The next proper candidate falls to `R² 0.3620036761`, a
margin of `0.6379963210`. The 26-vertex visible fragment independently ranks
the same family first (`R² 0.9912978824`, normalized RMSE `0.0222147720`),
ahead by `0.0192858807` R-squared.

RR — Really Readable rundown: the earlier free affine fit could stretch each
axis independently, so every formula could be made to look equally good. The
new test ties the axes together: it allows the kind of rotation, movement, and
single scale expected for a rigid component, but it does not allow arbitrary
shape warping. Under that stricter rule, one formula wins on both records and
is almost exact on proved hair. That makes `scale-offset-unsigned` the strongest
decoder candidate so far. It is still a candidate—not a promoted semantic—until
the executed retail arithmetic or another equally direct authority proves it.

Operator card: the stable inventory ID remains
`xpp-tool.character-source-runtime-correlate.v1`; the invocation and bounds are
unchanged from 2.21. Reports are now schema version 2 and preserve all 2.21
unrestricted-affine fields while adding proper/mirrored family fits, rankings,
and margins. Numeric-family selection, position meaning, object space,
ownership, UV, material, rigging, full-character, PBR, and injection gates stay
false. This metric-only update creates no render and does not gate publication
of any other evidence.

### Version 2.21 — permanent packed-source/runtime correlation

The source/runtime decoder probe is now a permanent, payload-free command:

```console
timeout 60s xpp-tool character-source-runtime-correlate \
  --xpp /path/to/owned/male_base_Zeke.xpp \
  --record-offset 536488 \
  --runtime-index ./exact-index.bin \
  --runtime-index-sha256 EXACT_INDEX_SHA256 \
  --runtime-positions ./exact-float32x3.bin \
  --runtime-positions-sha256 EXACT_POSITIONS_SHA256 \
  --runtime-byte-order big \
  --runtime-first-row 0 \
  --output ./source-runtime-correlation.json
```

The command proves the selected runtime index bytes are exactly the chosen XPP
record's triangle list, hashes the complete runtime position buffer, records an
explicit contiguous row window, and compares every eligible packed
three-component stream with those rows. Each result reports source rank,
R-squared, RMSE, normalized RMSE, maximum point residual, and cross-stream
ranking. It does not write source or runtime vertex values into the JSON.

Two independent owned records now select stream 1. The 184-vertex hair record
is an effectively exact affine match (`R² 0.9999999972`, normalized RMSE
`0.0000089894`); the 26-vertex visible fragment is a strong but imperfect match
(`R² 0.9943533660`, normalized RMSE `0.0178946912`). Both source streams have
full three-axis rank.

RR — Really Readable rundown: the flat-looking side image was a thin captured
draw viewed after the game had transformed it, not proof that Zeke is stored in
one dimension. This new test walks backward to the packed XPP. The hair result
shows that one packed stream contains a real three-axis shape which maps almost
perfectly to what the GPU used. That is a major bridge toward an editable model,
but it is not yet the whole character: we still must prove the canonical
scale/offset formula, coordinate space, UV stream, material bindings, bones,
weights, assembly, and reverse import.

Operator card: stable ID
`xpp-tool.character-source-runtime-correlate.v1`. The command is offline and
single-process; XPP input is capped at 64 MiB, each runtime payload at 16 MiB,
and JSON at 256 KiB. Inputs must be immutable regular non-symlink files with
exact SHA-256 pins. Output must be a new path and atomic publication preserves
a concurrent writer. Numeric families that differ only by affine scale/offset
remain intentionally indistinguishable, so all semantic, ownership, UV,
material, rigging, full-character, PBR, and injection gates remain false.

### Version 2.20 — permanent packed-source diagnostic export

The packed-source visual probe is now a permanent command instead of a
disposable analysis script:

```console
timeout 60s xpp-tool character-source-diagnostic-export \
  --xpp /path/to/owned/male_base_Zeke.xpp \
  --record-offset 536488 \
  --stream-index 1 \
  --numeric-family endpoint-unsigned \
  --output ./zeke-record-536488-source.glb \
  --json-out ./zeke-record-536488-source.json
```

The command selects exactly one proved character record and one of its three
descriptor-backed packed streams. It verifies the stream and triangle-list
hashes, unpacks the exact MSB-first integers, applies only the explicitly named
numeric hypothesis, requires finite nondegenerate geometry, and writes a
deterministic GLB with the retail topology unchanged. A PSARC source is also
accepted through `--psarc ARCHIVE --entry /path/to/package.xpp`.

RR — Really Readable rundown: this is a permanent magnifying glass for packed
character records. It lets us repeat the same source-space test on Zeke's
glasses, book, clothing, or any later character record without rebuilding a
throwaway script. The triangle connections and selected source bytes are
proved. The formula used to turn packed integers into coordinates is still a
hypothesis, so a recognizable shape is evidence—not yet an editable position
stream. UVs, textures, bones, weights, a skeleton, full-body completeness,
PBR, and reverse injection all remain separate gates.

Operator card: stable ID `xpp-tool.character-source-diagnostic-export.v1`.
Python 3.10+ is the only tool dependency; the command is offline and
single-process. The XPP payload and GLB are each capped at 64 MiB. The copyable
operator command uses a 60-second wall-clock bound. Inputs stay read-only;
outputs must be new, distinct paths and are never overwritten. Exit status is
zero only after the GLB is fully published and the deterministic JSON report
is emitted; validation, identity, bounds, nonfinite values, degenerate
topology, aliasing, and overwrite attempts fail nonzero. Repeated runs with the
same input bytes and options produce byte-identical GLBs and JSON. The command
does not prove position semantics, ownership, completeness, rigging,
materials, PBR, Blender readiness beyond diagnostic import, or safe game
injection.

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

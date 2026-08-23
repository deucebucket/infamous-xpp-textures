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

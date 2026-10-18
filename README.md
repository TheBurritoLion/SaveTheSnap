# SaveTheSnap

Keep your Snapchat exports local. Preserve the originals, prepare checked copies,
and repair only what the evidence supports.

**Working alpha, v0.1.** The browser app handles scanning and copy preparation.
Joining, overlay rendering, and verified JPEG metadata repair are command-line
features today. This is not yet a one-click complete migration.

## Try it

Requires Python 3.11 or newer. No Python packages need installing.

On Windows, double-click **Try-Demo.cmd** to try synthetic files, or
**Start-SaveTheSnap.cmd** to choose your own export. On any supported Python host:

```sh
python -m savethesnap ui --demo
python -m savethesnap ui
```

Run these from the project folder. The app opens a local browser page. Paste the
folder containing **all** your Snapchat ZIP parts, scan, then choose an output
folder. Each batch gets its own review page and provenance records. The next
batch continues through the remaining items during the same app session.
Rescanning or restarting resets that cursor; existing outputs are never replaced.
The demo uses temporary synthetic files which are removed when you stop it.

[FFmpeg and FFprobe](https://ffmpeg.org/download.html) must be on PATH for media
validation, joining, and rendering. Scanning works without them; preparation
preserves copies but marks items for attention if decoding is unavailable.
JPEG metadata repair additionally uses an existing [ExifTool](https://exiftool.org/).
Tools are not downloaded, installed, or bundled automatically.

## What works

- Read exported folders and multipart ZIP collections without extracting them
  wholesale or changing their contents.
- Parse `memories_history.json` and `.html`, retaining precision and explicit
  filename/path associations. Signed download URLs are discarded after checking
  for their presence. JavaScript in exports is never executed.
- Prepare bounded batches with verified byte copies, original source/member
  mappings, hashes, metadata evidence, decoding checks, and a local review page.
- Preserve separate overlays. Ambiguous pairs and missing history associations
  stay visible; an incomplete base is never called a complete Memory.
- Join an explicitly selected, ordered list of compatible video segments without
  encoding. Check full decoding, duration, and encoded video/audio packets in order.
- Render an explicitly paired, transparent static PNG overlay of equal dimensions.
  Video rendering requires permission to encode; encoded audio stays unchanged.
- Write exact, offset-bearing capture dates and valid GPS into a **new JPEG**,
  then read them back and verify the JPEG image content. Day-only, minute-only,
  and timezone-less dates remain sidecar evidence without invented precision.

No upload, account login, telemetry, original deletion, automatic deduplication,
timestamp-based segment grouping, or guess-by-row-order matching.

## Commands

Use your own local paths. All outputs should be in a separate local directory
outside a Git checkout or cloud-synced folder.

```sh
# Read-only summary; no output files.
python -m savethesnap scan "/path/to/export"

# A true dry run: no copies, moves, renames, metadata edits, or output directory.
python -m savethesnap prepare "/path/to/export" --output "/path/to/copies" --dry-run

# Copy and validate 20 items. --start 20 selects the following batch.
python -m savethesnap prepare "/path/to/export" --output "/path/to/copies" --limit 20

# You supply the complete segments in the correct playback order.
python -m savethesnap join "/path/to/part1.mp4" "/path/to/part2.mp4" --output "/path/to/copies" --confirm-order

# Pairing is explicit; unsupported positioning/scaling is held for attention.
python -m savethesnap overlay "/path/to/base.mp4" "/path/to/overlay.png" --output "/path/to/copies" --confirm-pair --allow-reencode

# This uses the linked row in a prepared item's provenance file. JPEG only.
python -m savethesnap tag "/path/to/batch/item/provenance.json" --output "/path/to/copies" --exiftool "/path/to/exiftool"
```

For joining/rendering/tagging, a `complete.json` marker means the declared checks
passed. An output without that marker is unfinished and should not be imported.
The preparation workflow instead records each item's status in `manifest.json`.
`copy_verified` means the exported file was copied and decoded; it does not prove
the export contains the whole original Memory.

## What this cannot recover

Some Snapchat exports contain dates/GPS in history rows but no stable media
identifier, filename, or working link connecting those rows to the bundled files.
Having the same number of records or a nearby timestamp does not establish that
connection. SaveTheSnap leaves those dates and locations unassigned.

A playable video can be only one segment. This tool cannot manufacture missing
footage. Supply the remaining segments explicitly; overlays that change across
segments must be rendered per segment before any join. Incompatible rotations,
codec settings, color metadata, or stream layouts cause a conservative refusal.
HDR/display-transform compositing and unequal overlay geometry are unsupported.
JPEG metadata writing is verified; video metadata writing is not implemented.
Cloud upload, cloud quality, and destination metadata acceptance are not tested.

The article [Fixing Snapchat’s Memories Export](https://medium.com/@shuchawl/fixing-snapchats-memories-export-yes-including-capture-time-and-location-4663ac9a24e5)
demonstrates a useful approach: download each media response from its own history
row and retain that association immediately. That requires a fresh export with
populated, usable links. **Network downloading is not implemented in this alpha.**

## Development and privacy

```sh
python -m unittest discover -s tests -v
```

Tests generate synthetic media. FFmpeg-dependent tests skip if the tools are
missing. Set `SAVETHESNAP_TEST_EXIFTOOL` to an installed executable to enable the
real JPEG tagging integration. No tests require a Snapchat login or user export.

The UI binds only to `127.0.0.1` on a random port, uses a per-launch token, and
rejects foreign origins. It loads no remote assets. FFmpeg/FFprobe are restricted
to local file/pipe protocols. Do not expose the local server to a network.

Never commit personal exports, media, generated manifests, screenshots, logs,
coordinates, or signed URLs. Reports stay in the chosen private output folder.
When filing an issue, describe the shape of the problem and use a synthetic
example. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Roadmap

1. Bring joining, overlay repair, and JPEG tagging into the browser workflow.
2. Add an opt-in downloader that preserves row-to-response provenance and resumes
   safely, with fixture-based tests before any real-account use.
3. Add verified video metadata writing and additional export-gallery geometries.
4. Add persistent batch progress and portable packaged releases.

## Attribution and license

MIT licensed; see [LICENSE](LICENSE). This is an independent implementation.
Research references include the article above, its author's
[downloader](https://github.com/shubham1chawla/snapchat-memories-downloader),
[Paper319/exportsnapmemories](https://github.com/Paper319/exportsnapmemories), and
[andrefecto/Snapchat-Memories-Downloader](https://github.com/andrefecto/Snapchat-Memories-Downloader).
The first two repositories had no license file when inspected; their code is
not incorporated here. FFmpeg and ExifTool remain separately distributed tools.
SaveTheSnap is an unofficial project, unaffiliated with Snap Inc.

# Alpha validation

Validated on Windows with Python 3.14, FFmpeg/FFprobe 8.1.2, and an existing
ExifTool 13.59 installation. All fixtures were synthetic; no personal export was
processed for this validation.

`python -m unittest discover -s tests` with `SAVETHESNAP_TEST_EXIFTOOL` set:
**58 tests: 57 passed, 1 skipped.** The skipped test requires real Windows symlink
creation privileges. A separate mocked link-refusal regression passed.

Coverage includes read-only scanning and dry runs, duplicate filename ambiguity,
conflicting history evidence, archive path traversal, changed sources, bounded
copying, corrupt-media isolation, per-item hashes, next-batch progression, stale
source rejection, token/origin checks, exact encoded video/audio packet order,
incompatible audio/rotation rejection, static overlay rendering, video duration
and audio preservation, and JPEG metadata readback/content verification.

REA performed the synthetic browser preparation workflow in a separately launched
Edge/Chromium profile. The final capture showed 3 prepared items (2 verified
copies, 1 overlay needing attention), the review button, and the exhausted batch
cursor. Both declared actions completed. There were no captured page errors,
no dropped events, and observed request origins were loopback only. REA reported
complete requested capture coverage and terminated its owned browser process.

Evidence: `ev_c756b794991e5543da99f50a04e4b2000b5f0ea0974155889efdc14ca5cd1c68`.
Raw captures are excluded from this repository because even synthetic UI captures
can contain host filesystem paths.

REA limitations: no shipped-artifact identity was established; host architecture
was not observed; event order is receipt order, not browser causality. Network
bodies and headers were not requested. This proves the declared local demo flow,
not Snapchat connectivity, a full-library migration, cloud acceptance, arbitrary
media compatibility, or all UI states.

The CI matrix separately targets Python 3.11/3.14 on Linux and Windows. Windows
runners may skip optional media integrations when their tools are absent; Linux
jobs install the media tools and enable the real ExifTool test.

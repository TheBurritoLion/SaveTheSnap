# Contributing

Use synthetic files for reproductions and tests. Do not attach real Memories,
export histories, signed download links, GPS, account identifiers, or generated
private manifests to public issues or pull requests.

Useful contributions improve supported export schemas, simplify the local UI,
or add verifiable recovery methods. Include a regression for the actual failure.
Keep input data read-only, make new output paths, and preserve the association
between each output and its exact source. Unknown provenance stays unknown.

Before opening a pull request, run `python -m unittest discover -s tests -v` and
state which optional media tools were present. New network or account features
must be opt-in and tested against local fixtures. Do not infer capture dates,
GPS, deduplication, or segment membership from file order or timestamps.

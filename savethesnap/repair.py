"""Apply verified history metadata to a new copy from a prepared item."""
import json
from pathlib import Path
import shutil
import uuid

from . import core
from .tagging import tag_copy


def repair_metadata(provenance, destination, exiftool=None):
    provenance = core.safe_path(provenance)
    row = json.loads(provenance.read_text(encoding='utf-8'))
    record = row.get('metadata')
    originals = [f for f in row.get('files', []) if f.get('role') == 'original']
    if row.get('metadata_status') != 'explicit_unique' or not record or len(originals) != 1:
        raise ValueError('This item does not have a unique explicit history association.')
    file = originals[0]
    if Path(file['name']).name != file['name']:
        raise ValueError('Unexpected source filename in provenance.')
    source = core.safe_path(provenance.parent / file['name'])
    if core.sha256(source) != file['sha256']:
        raise ValueError('Prepared source no longer matches its recorded hash.')
    if source.suffix.lower() not in {'.jpg', '.jpeg'}:
        raise ValueError('Verified embedded metadata repair currently supports JPEG only.')
    parent = core.output_parent(destination, provenance.parent)
    parent.mkdir(parents=True, exist_ok=True)
    run = parent / ('metadata-' + uuid.uuid4().hex[:12])
    run.mkdir()
    result = tag_copy(source, run / 'repaired.jpg', record, exiftool or shutil.which('exiftool') or '')
    core.write_json(run / 'complete.json', {'status': 'jpeg_metadata_verified', 'source_provenance_sha256': core.sha256(provenance)})
    return {'folder': str(run), **result}

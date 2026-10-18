"""Offline inventory and copy-on-write preparation. No downloader or deletion API."""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
import zipfile
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from .metadata import parse_history, date_evidence, gps_evidence

IMAGES = {'.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif', '.avif', '.gif'}
VIDEOS = {'.mp4', '.mov', '.m4v', '.3gp', '.mkv', '.avi'}
MEDIA = IMAGES | VIDEOS
MAX_MEMBER = 4 * 1024**3
MAX_METADATA = 32 * 1024**2


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def linked(path):
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def safe_path(path):
    path = Path(path).expanduser().absolute()
    if any(linked(p) for p in (path, *path.parents)):
        raise ValueError('Choose an ordinary local path without symbolic links or junctions.')
    return path.resolve()


def output_parent(path, source=None):
    path = safe_path(path)
    if source and (path == Path(source) or path.is_relative_to(Path(source))):
        raise ValueError('Choose an output folder outside the source.')
    if any((p / '.git').exists() for p in (path, *path.parents)):
        raise ValueError('Keep personal output outside a Git repository.')
    for key, value in os.environ.items():
        if key.lower().startswith('onedrive') and value and path.is_relative_to(Path(value).resolve()):
            raise ValueError('Choose a local output folder outside OneDrive.')
    return path


def safe_member(name):
    name = name.replace('\\', '/')
    parts = PurePosixPath(name).parts
    return bool(parts) and not name.startswith('/') and not any(
        p in {'.', '..'} or ':' in p or p.endswith((' ', '.')) or
        (os.name == 'nt' and (re.search(r'[<>"|?*\x00-\x1f]', p) or
         re.match(r'^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)', p, re.I))) for p in parts)


def snapshot(path):
    info = path.stat()
    return {'size': info.st_size, 'mtime_ns': info.st_mtime_ns}


@contextmanager
def open_entry(entry):
    source = safe_path(entry['container'])
    if snapshot(source) != entry['snapshot']:
        raise ValueError('Source changed after scanning; scan again.')
    if entry['index'] is None:
        with source.open('rb') as stream:
            yield stream
    else:
        with zipfile.ZipFile(source) as archive:
            info = archive.infolist()[entry['index']]
            if info.filename != entry['name'] or info.file_size != entry['size']:
                raise ValueError('Archive entry changed after scanning.')
            with archive.open(info) as stream:
                yield stream


def read_small(entry):
    if entry['size'] > MAX_METADATA:
        raise ValueError('Metadata exceeds the 32 MiB limit.')
    with open_entry(entry) as stream:
        value = stream.read(MAX_METADATA + 1)
    if len(value) > MAX_METADATA:
        raise ValueError('Metadata exceeds the 32 MiB limit.')
    return value


def scan(source):
    """Read directory/ZIP indexes and histories only; never change source files."""
    source = safe_path(source)
    if not source.exists():
        raise ValueError('Source does not exist.')
    if source.is_file() and source.suffix.lower() != '.zip':
        raise ValueError('Choose a ZIP archive or a folder containing your export.')
    files, entries, issues = [], [], []
    if source.is_file():
        files = [source]
    else:
        for root, dirs, names in os.walk(source, followlinks=False):
            for name in list(dirs):
                if linked(Path(root) / name) or name in {'.git', '__MACOSX'}:
                    dirs.remove(name)
            for name in names:
                path = Path(root) / name
                if linked(path):
                    issues.append('Symbolic link skipped.')
                    continue
                if path.suffix.lower() in MEDIA | {'.zip', '.html', '.json'}:
                    files.append(path)
    for path in sorted(files):
        file_snapshot = snapshot(path)
        container_key = str(path.relative_to(source)) if source.is_dir() else path.name
        def add(name, size, index):
            if not safe_member(name) or size > MAX_MEMBER:
                issues.append('Unsafe or oversized archive entry skipped.')
                return
            extension = PurePosixPath(name).suffix.lower()
            kind = ('overlay' if re.search(r'-overlay\.[^.]+$', name, re.I) else 'main') if extension in MEDIA else 'metadata'
            ident = hashlib.sha256(f'{container_key}\0{index}\0{name}'.encode()).hexdigest()[:20]
            entries.append({'id': ident, 'container': str(path), 'name': name, 'size': size,
                            'index': index, 'kind': kind, 'snapshot': file_snapshot})
        if path.suffix.lower() == '.zip':
            try:
                with zipfile.ZipFile(path) as archive:
                    for index, info in enumerate(archive.infolist()):
                        if info.is_dir():
                            continue
                        mode = info.external_attr >> 16
                        if stat.S_ISLNK(mode) or info.flag_bits & 1:
                            issues.append('Linked or encrypted archive entry skipped.')
                        elif PurePosixPath(info.filename).suffix.lower() in MEDIA | {'.html', '.json'}:
                            add(info.filename, info.file_size, index)
            except (OSError, zipfile.BadZipFile):
                issues.append('An archive could not be read.')
        else:
            add(path.relative_to(source).as_posix(), file_snapshot['size'], None)
    records = []
    for entry in entries:
        if entry['kind'] == 'metadata' and 'memories_history' in entry['name'].lower():
            try:
                for row in parse_history(read_small(entry), entry['name']):
                    records.append({**row, 'history': entry['id']})
            except (ValueError, UnicodeError, OSError, zipfile.BadZipFile):
                issues.append('A history could not be parsed; its metadata will not be assigned.')
    mains = [e for e in entries if e['kind'] == 'main']
    overlays = [e for e in entries if e['kind'] == 'overlay']
    by_name = defaultdict(list)
    for entry in mains:
        by_name[entry['name'].replace('\\', '/')].append(entry['id'])
        base = PurePosixPath(entry['name']).name
        if base != entry['name']:
            by_name[base].append(entry['id'])
    by_main_stem, by_overlay_stem = defaultdict(list), defaultdict(list)
    for entry in mains:
        if re.search(r'-main\.[^.]+$', entry['name'], re.I):
            by_main_stem[re.sub(r'-main\.[^.]+$', '', entry['name'], flags=re.I)].append(entry)
    for entry in overlays:
        by_overlay_stem[re.sub(r'-overlay\.[^.]+$', '', entry['name'], flags=re.I)].append(entry)
    items = []
    for main in mains:
        matching = []
        for row in records:
            reference = (row.get('reference') or '').replace('\\', '/')
            if reference and by_name.get(reference) == [main['id']]:
                matching.append(row)
        # HTML and JSON may repeat the same row. Conflicting evidence is held.
        signatures = {json.dumps({k: r.get(k) for k in ('date', 'location', 'media_type')}, sort_keys=True): r for r in matching}
        metadata = next(iter(signatures.values())) if len(signatures) == 1 else None
        if metadata:
            metadata = {**metadata, 'association': 'explicit_unique',
                        'date_evidence': date_evidence(metadata.get('date')),
                        'gps_evidence': gps_evidence(metadata.get('location'))}
        stem = re.sub(r'-main\.[^.]+$', '', main['name'], flags=re.I)
        candidates = by_overlay_stem.get(stem, []) if stem in by_main_stem else []
        pair = candidates[0] if len(candidates) == 1 and len(by_main_stem[stem]) == 1 else None
        items.append({'id': main['id'], 'name': main['name'], 'main': main['id'],
                      'overlay': pair['id'] if pair else None,
                      'overlay_candidates': [e['id'] for e in candidates],
                      'metadata': metadata, 'metadata_status': 'explicit_unique' if metadata else
                      ('conflicting' if matching else 'unlinked'),
                      'video': PurePosixPath(main['name']).suffix.lower() in VIDEOS})
    paired = {item['overlay'] for item in items if item['overlay']}
    orphan = [e['id'] for e in overlays if e['id'] not in paired]
    return {'version': 1, 'source': str(source), 'entries': entries, 'items': items,
            'orphan_overlays': orphan, 'issues': issues,
            'summary': {'media': len(mains), 'videos': sum(i['video'] for i in items),
                        'overlays': len(overlays), 'paired_overlays': len(paired),
                        'unpaired_overlays': len(orphan), 'history_rows': len(records),
                        'rows_with_download_links': sum(bool(r.get('url_present')) for r in records),
                        'linked_metadata': sum(i['metadata'] is not None for i in items),
                        'unlinked_metadata': sum(i['metadata'] is None for i in items)}}


def run_tool(arguments, timeout=300):
    result = subprocess.run([str(x) for x in arguments], stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        # Tool stderr often contains private filenames. Keep it out of public logs.
        raise ValueError(f'{Path(arguments[0]).stem} could not validate this media.')
    return result.stdout


def tool(name):
    path = shutil.which(name)
    if not path:
        raise ValueError(f'{name} is needed for media validation. Install it separately and reopen the app.')
    return path


def probe(path):
    return json.loads(run_tool([tool('ffprobe'), '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_streams', '-show_format',
                               '-show_data_hash', 'sha256', '-of', 'json', path]))


def validate(path):
    info = probe(path)
    if not any(s['codec_type'] == 'video' for s in info['streams']):
        raise ValueError('No image or video stream was found.')
    run_tool([tool('ffmpeg'), '-v', 'error', '-xerror', '-protocol_whitelist', 'file,pipe', '-i', path,
              '-map', '0:v:0', '-map', '0:a?', '-f', 'null', '-'])
    return info


def packet_fingerprints(paths):
    """Hash encoded packet payloads per stream, retaining explicit source order."""
    digests = {}
    counts = Counter()
    for path in paths:
        raw = run_tool([tool('ffprobe'), '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_packets', '-show_data_hash', 'sha256',
                        '-show_entries', 'packet=stream_index,data_hash', '-of', 'json', path])
        for packet in json.loads(raw).get('packets', []):
            payload_hash = packet.get('data_hash', '')
            if not re.fullmatch(r'SHA256:[0-9a-fA-F]{64}', payload_hash):
                raise ValueError('A packet payload hash was unavailable.')
            index = packet['stream_index']
            digests.setdefault(index, hashlib.sha256()).update((payload_hash + '\n').encode('ascii'))
            counts[index] += 1
    if not counts:
        raise ValueError('Encoded packet verification was unavailable.')
    return {index: {'sha256': digest.hexdigest(), 'packets': counts[index]} for index, digest in digests.items()}


def copy_entry(entry, target):
    digest = hashlib.sha256()
    count = 0
    with open_entry(entry) as stream, target.open('xb') as out:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            count += len(chunk)
            if count > MAX_MEMBER:
                raise ValueError('Media exceeds the size limit.')
            out.write(chunk)
            digest.update(chunk)
    if count != entry['size'] or sha256(target) != digest.hexdigest():
        raise ValueError('Copy verification failed.')
    return digest.hexdigest()


def write_json(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def prepare(inventory, destination, selected=None, limit=20):
    """Make a new, bounded run. Unknown dates remain unknown; inputs stay intact."""
    if not isinstance(limit, int) or not 1 <= limit <= 500:
        raise ValueError('Choose a batch size from 1 to 500.')
    entries = {e['id']: e for e in inventory['entries']}
    if selected is not None and (len(set(selected)) != len(selected) or not set(selected) <= {i['id'] for i in inventory['items']}):
        raise ValueError('Selection contains duplicate or unknown items.')
    items = [i for i in inventory['items'] if selected is None or i['id'] in selected][:limit]
    if not items:
        raise ValueError('No media selected.')
    parent = output_parent(destination, inventory['source'])
    parent.mkdir(parents=True, exist_ok=True)
    run = parent / ('batch-' + uuid.uuid4().hex[:12])
    run.mkdir()
    results = []
    for item in items:
        folder = run / item['id']
        folder.mkdir()
        row = {'id': item['id'], 'source_name': item['name'], 'files': [],
               'metadata': item['metadata'], 'metadata_status': item['metadata_status'],
               'date_location_written': False, 'full_memory_completeness': 'unproven',
               'status': 'needs_attention', 'notes': []}
        try:
            for ident in dict.fromkeys([item['main'], *item['overlay_candidates']]):
                entry = entries[ident]
                extension = PurePosixPath(entry['name']).suffix.lower()
                name = ('original' if ident == item['main'] else 'overlay-' + ident) + extension
                copied = folder / name
                digest = copy_entry(entry, copied)
                row['files'].append({'name': name, 'source_entry': ident,
                                     'source': {'container': entry['container'], 'member': entry['name'], 'member_index': entry['index']},
                                     'sha256': digest,
                                     'bytes': copied.stat().st_size, 'role': 'original' if ident == item['main'] else 'overlay'})
            info = validate(folder / row['files'][0]['name'])
            row['validation'] = {'decoded': True, 'duration': info.get('format', {}).get('duration'),
                                 'audio_streams': sum(s['codec_type'] == 'audio' for s in info['streams'])}
            if item['overlay_candidates']:
                row['notes'].append('Separate overlay components preserved. A rendered copy needs a geometry and appearance check.')
            else:
                row['status'] = 'copy_verified'
            if not item['metadata']:
                row['notes'].append('History metadata is not linked. Existing embedded metadata was preserved unchanged.')
            if item['video']:
                row['notes'].append('This exported file decodes, but it may be only one segment of a longer Memory.')
        except (ValueError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
            row['notes'].append(str(exc) if isinstance(exc, ValueError) else 'This item could not be prepared; its original is intact.')
        write_json(folder / 'provenance.json', row)
        results.append(row)
    report = {'version': 1, 'source': inventory['source'], 'results': results,
              'summary': dict(Counter(r['status'] for r in results)),
              'scope': 'Local copies only. No uploads, deletion, automatic joins, or inferred dates/GPS.'}
    write_json(run / 'manifest.json', report)
    write_review(run, report)
    return {'folder': str(run), 'summary': report['summary'], 'count': len(results)}


def write_review(run, report):
    cards = []
    for item in report['results']:
        media = ''
        for file in item['files']:
            url = item['id'] + '/' + file['name']
            if Path(file['name']).suffix in VIDEOS:
                media += f'<video controls preload="metadata" src="{url}"></video>'
            else:
                media += f'<img loading="lazy" src="{url}" alt="Preserved component">'
        notes = ''.join('<li>' + html.escape(n) + '</li>' for n in item['notes'])
        cards.append(f'<article><h2>{html.escape(item["source_name"])}</h2><b>{item["status"]}</b><div>{media}</div><ul>{notes}</ul></article>')
    (run / 'review.html').write_text('<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer">'
        '<title>SaveTheSnap batch review</title><style>body{font:17px system-ui;margin:40px auto;max-width:1000px;padding:20px;background:#f4f3ed;color:#202623}'
        'article{background:white;padding:24px;margin:20px 0;border-radius:18px}video,img{max-width:320px;max-height:300px;margin:12px}h2{overflow-wrap:anywhere;font-size:20px}</style>'
        '<h1>Your local copies</h1><p>Original bytes are preserved with hashes. Date and location evidence is in the sidecars; '
        'nothing was guessed or uploaded. Video completeness still needs a reference when a Memory has multiple segments.</p>' + ''.join(cards), encoding='utf-8')


def join_segments(paths, destination, confirmed=False):
    """Join only the explicit, owner-ordered list. Never infer groups from time."""
    if not confirmed:
        raise ValueError('Confirm that these are the complete segments in playback order.')
    paths = [safe_path(p) for p in paths]
    if len(paths) < 2 or len(set(paths)) != len(paths):
        raise ValueError('Choose at least two different segments.')
    if any(not p.is_file() or p.suffix.lower() not in VIDEOS for p in paths):
        raise ValueError('Every segment must be an ordinary video file.')
    infos = [probe(p) for p in paths]
    def signature(info):
        keys = ('codec_type', 'codec_name', 'codec_tag_string', 'profile', 'level', 'width', 'height',
                'pix_fmt', 'sample_rate', 'channels', 'channel_layout', 'time_base', 'extradata_size', 'extradata_hash')
        return [{**{k: stream.get(k) for k in keys},
                 'display_side_data': stream.get('side_data_list'),
                 'color': {k: stream.get(k) for k in ('color_range', 'color_space', 'color_transfer', 'color_primaries')},
                 'rotation_tag': stream.get('tags', {}).get('rotate')}
                for stream in info['streams']]
    if any(signature(i) != signature(infos[0]) for i in infos[1:]):
        raise ValueError('Segment formats differ. Lossless joining is unavailable; originals were preserved.')
    expected = sum(float(i['format']['duration']) for i in infos)
    parent = output_parent(destination)
    if any(parent == p.parent or parent.is_relative_to(p.parent) for p in paths):
        raise ValueError('Choose a separate output folder for the joined copy.')
    parent.mkdir(parents=True, exist_ok=True)
    run = parent / ('joined-' + uuid.uuid4().hex[:12])
    run.mkdir()
    records = []
    write_json(run / 'started.json', {'status': 'started', 'success_marker': 'complete.json'})
    with tempfile.TemporaryDirectory(prefix='savethesnap-join-') as temporary:
        work = Path(temporary)
        for n, path in enumerate(paths):
            target = work / f'{n:04d}{path.suffix.lower()}'
            before = sha256(path)
            shutil.copyfile(path, target)
            if sha256(target) != before:
                raise ValueError('Segment copy failed validation.')
            records.append({'path': str(path), 'sha256': before, 'order': n})
        (work / 'segments.txt').write_text(''.join(f"file '{n:04d}{p.suffix.lower()}'\n" for n, p in enumerate(paths)), encoding='ascii')
        output = run / 'full-video.mp4'
        run_tool([tool('ffmpeg'), '-v', 'error', '-n', '-protocol_whitelist', 'file,pipe', '-f', 'concat', '-safe', '1', '-auto_convert', '0', '-i', work / 'segments.txt',
                  '-map', '0', '-c', 'copy', '-movflags', '+faststart', output])
        result = validate(output)
        actual = float(result['format']['duration'])
        if abs(actual - expected) > max(.25, expected * .005):
            raise ValueError('Joined duration did not match the sum of the segments; do not import this output.')
        packets = packet_fingerprints(paths)
        if packet_fingerprints([output]) != packets:
            raise ValueError('Encoded video/audio packet verification failed; do not import this output.')
        if any(sha256(path) != record['sha256'] for path, record in zip(paths, records)):
            raise ValueError('A source changed during joining; do not import this output.')
    write_json(run / 'provenance.json', {'segments': records, 'output_sha256': sha256(output),
        'expected_duration': expected, 'actual_duration': actual, 'encoding': 'stream_copy', 'encoded_packets': packets,
        'full_memory_completeness': 'owner_confirmed_segment_selection',
        'date_location_written': False, 'overlay_composition': 'not performed'})
    # A failed join never receives the success marker.
    write_json(run / 'complete.json', {'status': 'verified_stream_copy', 'output': output.name})
    return {'folder': str(run), 'duration': actual, 'encoding': 'stream_copy'}

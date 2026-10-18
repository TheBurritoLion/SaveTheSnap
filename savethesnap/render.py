"""Conservative compositing of explicitly selected static PNG overlays."""
from pathlib import Path
import uuid

from . import core


def render_overlay(main, overlay, destination, confirmed=False, allow_reencode=False):
    if not confirmed:
        raise ValueError('Confirm that this overlay belongs to this media file.')
    main, overlay = core.safe_path(main), core.safe_path(overlay)
    if main == overlay or overlay.suffix.lower() != '.png':
        raise ValueError('Choose a separate static PNG overlay.')
    before = {str(p): core.sha256(p) for p in (main, overlay)}
    base, top = core.validate(main), core.validate(overlay)
    video = main.suffix.lower() in core.VIDEOS
    if video and not allow_reencode:
        raise ValueError('Rendering video captions encodes the video. Use --allow-reencode to make a separate copy.')
    streams = [s for s in base['streams'] if s['codec_type'] == 'video']
    overlays = [s for s in top['streams'] if s['codec_type'] == 'video']
    if len(streams) != 1 or len(overlays) != 1:
        raise ValueError('Only one base picture and one static overlay are supported.')
    a, b = streams[0], overlays[0]
    if (a['width'], a['height']) != (b['width'], b['height']):
        raise ValueError('Overlay dimensions differ. Position and scale need explicit evidence; no composite was created.')
    if b.get('nb_frames', '1') not in ('1', None) or b['codec_name'] != 'png':
        raise ValueError('Animated or non-PNG overlays are not supported.')
    if a.get('side_data_list') or a.get('tags', {}).get('rotate'):
        raise ValueError('Base has display-transform metadata. Resolve its intended geometry before compositing.')
    if a['width'] * a['height'] > 50_000_000:
        raise ValueError('Image dimensions exceed this alpha version’s limit.')
    alpha = core.run_tool([core.tool('ffmpeg'), '-v', 'error', '-protocol_whitelist', 'file,pipe', '-i', overlay, '-vf',
        'format=rgba,alphaextract', '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'gray', '-'])
    if not alpha or min(alpha) == 255:
        raise ValueError('Overlay is opaque; it would hide the original picture.')
    parent = core.output_parent(destination)
    if any(parent == p.parent or parent.is_relative_to(p.parent) for p in (main, overlay)):
        raise ValueError('Choose an output folder separate from both sources.')
    parent.mkdir(parents=True, exist_ok=True)
    run = parent / ('overlay-' + uuid.uuid4().hex[:12])
    run.mkdir()
    core.write_json(run / 'started.json', {'success_marker': 'complete.json'})
    output = run / ('rendered.mp4' if video else 'rendered.png')
    args = [core.tool('ffmpeg'), '-v', 'error', '-n', '-protocol_whitelist', 'file,pipe', '-i', main]
    args += ['-loop', '1', '-protocol_whitelist', 'file,pipe', '-i', overlay] if video else ['-protocol_whitelist', 'file,pipe', '-i', overlay]
    args += ['-filter_complex', '[0:v][1:v]overlay=0:0:shortest=1[v]', '-map', '[v]', '-map', '0:a?']
    args += ['-c:v', 'libx264', '-crf', '18', '-preset', 'medium', '-pix_fmt', 'yuv420p', '-c:a', 'copy'] if video else ['-frames:v', '1']
    args += ['-map_metadata', '0', output]
    core.run_tool(args)
    result = core.validate(output)
    if video:
        expected, actual = float(base['format']['duration']), float(result['format']['duration'])
        if abs(expected - actual) > max(.1, expected * .005):
            raise ValueError('Rendered duration changed; do not import this output.')
        original_packets = core.packet_fingerprints([main])
        output_packets = core.packet_fingerprints([output])
        source_audio = [original_packets.get(n) for n, s in enumerate(base['streams']) if s['codec_type'] == 'audio']
        rendered_audio = [output_packets.get(n) for n, s in enumerate(result['streams']) if s['codec_type'] == 'audio']
        if source_audio != rendered_audio:
            raise ValueError('Audio payloads changed; do not import this output.')
    if before != {str(p): core.sha256(p) for p in (main, overlay)}:
        raise ValueError('Source changed during rendering; do not import this output.')
    core.write_json(run / 'provenance.json', {'sources': before, 'output_sha256': core.sha256(output),
        'video_reencoded': video, 'overlay_geometry': 'same_dimensions_top_left',
        'original_snapchat_appearance': 'requires_reference_comparison', 'date_location_written': False})
    core.write_json(run / 'complete.json', {'status': 'rendered_for_review', 'output': output.name})
    return {'folder': str(run), 'video_reencoded': video, 'status': 'rendered_for_review'}

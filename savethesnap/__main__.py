import argparse
import json
import tempfile
from pathlib import Path

from . import core


def main():
    parser = argparse.ArgumentParser(description='SaveTheSnap — local, non-destructive export repair')
    sub = parser.add_subparsers(dest='command')
    ui = sub.add_parser('ui', help='Open the local app')
    ui.add_argument('--source')
    ui.add_argument('--output')
    ui.add_argument('--demo', action='store_true')
    ui.add_argument('--no-browser', action='store_true')
    ui.add_argument('--port', type=int, default=0)
    scan = sub.add_parser('scan', help='Read-only export summary')
    scan.add_argument('source')
    prep = sub.add_parser('prepare', help='Prepare a bounded batch in a separate folder')
    prep.add_argument('source')
    prep.add_argument('--output', required=True)
    prep.add_argument('--limit', type=int, default=20)
    prep.add_argument('--start', type=int, default=0, help='Zero-based item offset for the next batch')
    prep.add_argument('--select', nargs='+', help='Exact item IDs; run the UI/scan first')
    prep.add_argument('--dry-run', action='store_true', help='Print counts only; no output files or changes')
    join = sub.add_parser('join', help='Losslessly join explicitly selected full-video segments in order')
    join.add_argument('segments', nargs='+')
    join.add_argument('--output', required=True)
    join.add_argument('--confirm-order', action='store_true')
    overlay = sub.add_parser('overlay', help='Render one explicitly paired static overlay into a separate copy')
    overlay.add_argument('main')
    overlay.add_argument('overlay')
    overlay.add_argument('--output', required=True)
    overlay.add_argument('--confirm-pair', action='store_true')
    overlay.add_argument('--allow-reencode', action='store_true')
    tagging = sub.add_parser('tag', help='Write verified date/GPS into a new JPEG from a prepared item')
    tagging.add_argument('provenance', help='Path to a prepared item’s provenance.json')
    tagging.add_argument('--output', required=True)
    tagging.add_argument('--exiftool', help='Path to an existing ExifTool executable')
    args = parser.parse_args()
    try:
        if args.command in {None, 'ui'}:
            from .server import serve
            from .demo import create_demo
            if getattr(args, 'demo', False):
                with tempfile.TemporaryDirectory(prefix='savethesnap-demo-') as tmp:
                    source = create_demo(Path(tmp) / 'synthetic-export')
                    serve(source, Path(tmp) / 'copies', demo=True,
                          open_browser=not args.no_browser, port=args.port)
            else:
                serve(getattr(args, 'source', None), getattr(args, 'output', None),
                      open_browser=not getattr(args, 'no_browser', False), port=getattr(args, 'port', 0))
        elif args.command == 'scan':
            inv = core.scan(args.source)
            print(json.dumps({'summary': inv['summary'], 'issues': inv['issues']}, indent=2))
        elif args.command == 'prepare':
            inv = core.scan(args.source)
            if args.dry_run:
                print(json.dumps({'dry_run': True, 'summary': inv['summary'], 'writes': 0}, indent=2))
            else:
                if args.start < 0:
                    raise ValueError('Start offset cannot be negative.')
                selected = args.select if args.select is not None else [i['id'] for i in inv['items'][args.start:args.start + args.limit]]
                print(json.dumps(core.prepare(inv, args.output, selected, args.limit), indent=2))
        elif args.command == 'join':
            print(json.dumps(core.join_segments(args.segments, args.output, args.confirm_order), indent=2))
        elif args.command == 'overlay':
            from .render import render_overlay
            print(json.dumps(render_overlay(args.main, args.overlay, args.output,
                args.confirm_pair, args.allow_reencode), indent=2))
        elif args.command == 'tag':
            from .repair import repair_metadata
            print(json.dumps(repair_metadata(args.provenance, args.output, args.exiftool), indent=2))
    except (ValueError, OSError) as exc:
        parser.exit(2, f'SaveTheSnap: {exc}\n')


if __name__ == '__main__':
    main()

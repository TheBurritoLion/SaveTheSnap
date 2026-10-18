"""Entirely synthetic demonstration export; no user media or identifiers."""
import json
import struct
import zlib
from pathlib import Path


def png(width=96, height=64, rgba=(25, 130, 100, 255)):
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', zlib.crc32(kind + payload) & 0xffffffff)
    raw = b''.join(b'\0' + bytes(rgba) * width for _ in range(height))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')


def create_demo(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    (folder / 'example-main.png').write_bytes(png())
    (folder / 'caption-main.png').write_bytes(png(rgba=(60, 80, 140, 255)))
    (folder / 'caption-overlay.png').write_bytes(png(rgba=(255, 190, 0, 100)))
    (folder / 'unknown-date.png').write_bytes(png(rgba=(110, 60, 140, 255)))
    (folder / 'memories_history.json').write_text(json.dumps({'Saved Media': [
        {'File Name': 'example-main.png', 'Date': '2020-01-02 03:04:05 UTC', 'Media Type': 'Image'},
        {'File Name': 'caption-main.png', 'Date': '2020-01-03', 'Media Type': 'Image'},
        {'Date': '2020-01-04 05:06:07 UTC', 'Location': 'Unknown', 'Media Type': 'Image'}
    ]}), encoding='utf-8')
    return folder

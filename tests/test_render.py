import json
from pathlib import Path
import shutil
import tempfile
import unittest

from savethesnap import core
from savethesnap.demo import png
from savethesnap.render import render_overlay


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg/FFprobe not installed')
class RenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='savethesnap-render-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / 'source'
        self.input.mkdir()
        self.main, self.overlay = self.input / 'main.png', self.input / 'overlay.png'
        self.main.write_bytes(png(rgba=(20, 80, 140, 255)))
        self.overlay.write_bytes(png(rgba=(250, 140, 10, 120)))

    def test_static_overlay_creates_review_copy_and_keeps_both_originals(self):
        before = [core.sha256(p) for p in (self.main, self.overlay)]
        result = render_overlay(self.main, self.overlay, self.root / 'out', confirmed=True)
        output = Path(result['folder']) / 'rendered.png'
        self.assertTrue(output.exists())
        self.assertEqual(result['status'], 'rendered_for_review')
        self.assertEqual(before, [core.sha256(p) for p in (self.main, self.overlay)])
        self.assertNotEqual(core.sha256(output), before[0])
        self.assertTrue((output.parent / 'complete.json').exists())

    def test_opaque_and_mismatched_overlays_are_held(self):
        for payload in [png(rgba=(0, 0, 0, 255)), png(width=32)]:
            self.overlay.write_bytes(payload)
            with self.assertRaises(ValueError):
                render_overlay(self.main, self.overlay, self.root / 'out', confirmed=True)
        self.assertFalse((self.root / 'out').exists())

    def test_video_reencoding_requires_explicit_choice_and_audio_is_preserved(self):
        video = self.input / 'main.mp4'
        core.run_tool([core.tool('ffmpeg'), '-v', 'error', '-n', '-f', 'lavfi', '-i',
            'color=c=green:s=96x64:r=30:d=0.6', '-f', 'lavfi', '-i', 'sine=frequency=500:duration=0.6',
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', video])
        with self.assertRaisesRegex(ValueError, 'encodes the video'):
            render_overlay(video, self.overlay, self.root / 'out', confirmed=True)
        result = render_overlay(video, self.overlay, self.root / 'out', confirmed=True, allow_reencode=True)
        self.assertTrue(result['video_reencoded'])
        info = json.loads((Path(result['folder']) / 'provenance.json').read_text())
        self.assertTrue(info['video_reencoded'])


if __name__ == '__main__':
    unittest.main()

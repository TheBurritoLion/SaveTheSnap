import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from savethesnap import core
from savethesnap.demo import create_demo, png


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='savethesnap-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = create_demo(self.root / 'input')

    def evidence(self):
        return {p.relative_to(self.source).as_posix(): (core.sha256(p), p.stat().st_mtime_ns)
                for p in self.source.rglob('*') if p.is_file()}

    def test_read_only_scan_and_unlinked_rows_never_gain_metadata(self):
        before = self.evidence()
        inv = core.scan(self.source)
        self.assertEqual(inv['summary']['media'], 3)
        self.assertEqual(inv['summary']['linked_metadata'], 2)
        self.assertEqual(inv['summary']['paired_overlays'], 1)
        unknown = next(i for i in inv['items'] if i['name'] == 'unknown-date.png')
        self.assertIsNone(unknown['metadata'])
        self.assertEqual(before, self.evidence())

    def test_zip_duplicates_do_not_gain_associations(self):
        archive = self.root / 'two.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('a/same.png', png())
            z.writestr('b/same.png', png())
            z.writestr('memories_history.json', json.dumps({'Saved Media': [
                {'File Name': 'same.png', 'Date': '2020-01-02 03:04:05 UTC'}]}))
        inv = core.scan(archive)
        self.assertEqual(inv['summary']['linked_metadata'], 0)
        self.assertEqual(len(inv['items']), 2)

    def test_same_full_path_in_multiple_archives_is_ambiguous(self):
        for n in range(2):
            with zipfile.ZipFile(self.source / f'part{n}.zip', 'w') as z:
                z.writestr('same.png', png())
                if n == 0:
                    z.writestr('memories_history.json', json.dumps({'Saved Media': [
                        {'File Name': 'same.png', 'Date': '2020-01-02 03:04:05 UTC'}]}))
        inv = core.scan(self.source)
        self.assertTrue(all(i['metadata'] is None for i in inv['items'] if i['name'] == 'same.png'))

    def test_conflicting_metadata_is_held(self):
        (self.source / 'second-memories_history.json').write_text(json.dumps({'Saved Media': [
            {'File Name': 'example-main.png', 'Date': '2022-02-02 03:04:05 UTC'}]}))
        inv = core.scan(self.source)
        item = next(i for i in inv['items'] if i['name'] == 'example-main.png')
        self.assertEqual(item['metadata_status'], 'conflicting')
        self.assertIsNone(item['metadata'])

    def test_hostile_archive_paths_skipped_without_extraction(self):
        archive = self.root / 'bad.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            for name in ['../escape.png', '/absolute.png', 'C:/drive.png', 'good.png']:
                z.writestr(name, png())
            info = zipfile.ZipInfo('linked.png')
            info.external_attr = 0o120777 << 16
            z.writestr(info, 'target')
        inv = core.scan(archive)
        self.assertEqual([i['name'] for i in inv['items']], ['good.png'])
        self.assertFalse((self.root / 'escape.png').exists())

    def test_dry_run_writes_nothing(self):
        before = self.evidence()
        result = subprocess.run([os.sys.executable, '-m', 'savethesnap', 'prepare', str(self.source),
            '--output', str(self.root / 'never-created'), '--dry-run'], capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout)['writes'], 0)
        self.assertFalse((self.root / 'never-created').exists())
        self.assertEqual(before, self.evidence())

    def test_output_must_be_separate_and_outside_git(self):
        with self.assertRaises(ValueError):
            core.output_parent(self.source / 'output', self.source)
        repo = self.root / 'repo'
        (repo / '.git').mkdir(parents=True)
        with self.assertRaises(ValueError):
            core.output_parent(repo / 'output', self.source)

    def test_changed_source_is_not_copied(self):
        inv = core.scan(self.source)
        entry = next(e for e in inv['entries'] if e['name'] == 'example-main.png')
        (self.source / 'example-main.png').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            core.copy_entry(entry, self.root / 'no.png')
        self.assertFalse((self.root / 'no.png').exists())

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg/FFprobe not installed')
    def test_preparation_preserves_all_sources_and_component_bytes(self):
        before = self.evidence()
        result = core.prepare(core.scan(self.source), self.root / 'output')
        run = Path(result['folder'])
        report = json.loads((run / 'manifest.json').read_text())
        self.assertEqual(result['count'], 3)
        self.assertEqual(result['summary'], {'needs_attention': 1, 'copy_verified': 2})
        self.assertEqual(before, self.evidence())
        self.assertTrue((run / 'review.html').exists())
        for row in report['results']:
            self.assertFalse(row['date_location_written'])
            for file in row['files']:
                self.assertEqual(core.sha256(run / row['id'] / file['name']), file['sha256'])
                self.assertEqual(file['sha256'], core.sha256(self.source / file['source']['member']))

    def test_broken_media_does_not_block_other_items(self):
        (self.source / 'broken.mp4').write_bytes(b'broken')
        result = core.prepare(core.scan(self.source), self.root / 'output', limit=4)
        self.assertEqual(result['count'], 4)
        self.assertGreaterEqual(result['summary']['needs_attention'], 2)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg/FFprobe not installed')
class JoinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='savethesnap-join-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / 'input'
        self.inputs.mkdir()
        self.paths = []
        for n, color in enumerate(['red', 'blue']):
            path = self.inputs / f'clip{n}.mp4'
            core.run_tool([core.tool('ffmpeg'), '-v', 'error', '-n', '-f', 'lavfi', '-i',
                f'color=c={color}:s=96x64:r=30:d=0.6', '-f', 'lavfi', '-i',
                f'sine=frequency={400+n*200}:duration=0.6', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-shortest', path])
            self.paths.append(path)

    def packet_hashes(self, path):
        data = json.loads(core.run_tool([core.tool('ffprobe'), '-v', 'error', '-show_packets',
            '-show_data_hash', 'sha256', '-show_entries', 'packet=stream_index,data_hash', '-of', 'json', path]))
        return {index: [p['data_hash'] for p in data['packets'] if p['stream_index'] == index] for index in (0, 1)}

    def test_lossless_join_preserves_video_and_audio_packets_in_order(self):
        before = [core.sha256(p) for p in self.paths]
        result = core.join_segments(self.paths, self.root / 'output', confirmed=True)
        output = Path(result['folder']) / 'full-video.mp4'
        hashes = self.packet_hashes(output)
        for index in (0, 1):
            expected = sum((self.packet_hashes(p)[index] for p in self.paths), [])
            self.assertEqual(hashes[index], expected)
        self.assertEqual(before, [core.sha256(p) for p in self.paths])
        self.assertTrue((output.parent / 'complete.json').exists())

    def test_duplicate_or_unconfirmed_join_is_refused_before_output(self):
        for paths, confirmed in [(self.paths, False), ([self.paths[0]] * 2, True)]:
            with self.assertRaises(ValueError):
                core.join_segments(paths, self.root / 'output', confirmed)
        self.assertFalse((self.root / 'output').exists())

    def test_incompatible_audio_does_not_get_silently_dropped(self):
        silent = self.inputs / 'silent.mp4'
        core.run_tool([core.tool('ffmpeg'), '-v', 'error', '-n', '-i', self.paths[0], '-an', '-c:v', 'copy', silent])
        with self.assertRaisesRegex(ValueError, 'formats differ'):
            core.join_segments([self.paths[0], silent], self.root / 'output', confirmed=True)
        self.assertFalse((self.root / 'output').exists())

    def test_different_rotation_is_not_silently_inherited(self):
        rotated = self.inputs / 'rotated.mp4'
        core.run_tool([core.tool('ffmpeg'), '-v', 'error', '-n', '-display_rotation:v:0', '90',
            '-i', self.paths[1], '-map', '0', '-c', 'copy', rotated])
        self.assertTrue(core.probe(rotated)['streams'][0].get('side_data_list'))
        with self.assertRaisesRegex(ValueError, 'formats differ'):
            core.join_segments([self.paths[0], rotated], self.root / 'output', confirmed=True)
        self.assertFalse((self.root / 'output').exists())


if __name__ == '__main__':
    unittest.main()

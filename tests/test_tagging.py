"""Copy-tagging tests use a generated 2-by-2 blue JPEG, never personal media."""

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from savethesnap.tagging import _jpeg_essence, tag_copy


JPEG = base64.b64decode(
    '/9j/4AAQSkZJRgABAgAAAQABAAD//gAQTGF2YzYyLjI4LjEwMgD/2wBDAAgKCgsKCw0NDQ0NDRAPEBAQEBAQEBAQEBASEhIVFRUSEhIQEBISFBQVFRcXFxUVFRUXFxkZGR4eHBwjIyQrKzP/xABMAAEBAAAAAAAAAAAAAAAAAAAABwEBAQAAAAAAAAAAAAAAAAAABQcQAQAAAAAAAAAAAAAAAAAAAAARAQAAAAAAAAAAAAAAAAAAAAD/wAARCAACAAIDASIAAhEAAxEA/9oADAMBAAIRAxEAPwCOAL+Kf//Z'
)


def history(**extra):
    return {"association": "explicit_unique", "reference": "synthetic.jpg",
            "date": "2024-01-02", "location": None, **extra}


class TaggingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.jpg"
        self.source.write_bytes(JPEG)
        self.destination = self.root / "output.jpg"

    def tearDown(self):
        self.temporary.cleanup()

    def test_day_minute_and_missing_zone_are_sidecar_only(self):
        for index, date in enumerate(("2024-01-02", "2024-01-02 12:34 UTC", "2024-01-02 12:34:56")):
            with self.subTest(date=date), patch("savethesnap.tagging.subprocess.run") as run:
                destination = self.root / f"output-{index}.jpg"
                result = tag_copy(self.source, destination, history(date=date), "unused")
                run.assert_not_called()
                self.assertFalse(result["date_written"])
                self.assertFalse(result["gps_written"])
                self.assertEqual(self.source.read_bytes(), JPEG)
                self.assertEqual(destination.read_bytes(), JPEG)
                sidecar = json.loads(Path(result["sidecar"]).read_text("utf-8"))
                self.assertEqual(sidecar["date_raw"], date)
                self.assertEqual(result["source_sha256"], hashlib.sha256(JPEG).hexdigest())

    def test_explicit_association_is_required(self):
        for association in (None, "date_order", "ambiguous", "owner_guess"):
            with self.subTest(association=association), self.assertRaisesRegex(ValueError, "explicit"):
                tag_copy(self.source, self.destination, history(association=association), "unused")
        self.assertFalse(self.destination.exists())

    def test_existing_output_or_sidecar_is_never_overwritten(self):
        for target in (self.destination, self.destination.with_name("output.jpg.metadata.json")):
            with self.subTest(target=target.name):
                target.write_bytes(b"KEEP")
                with self.assertRaises(FileExistsError):
                    tag_copy(self.source, self.destination, history(), "unused")
                self.assertEqual(target.read_bytes(), b"KEEP")
                target.unlink()

    def test_video_is_explicitly_refused(self):
        video = self.root / "source.mp4"
        video.write_bytes(b"synthetic unsupported video")
        with self.assertRaisesRegex(ValueError, "video"):
            tag_copy(video, self.root / "output.mp4", history(), "unused")
        self.assertEqual(video.read_bytes(), b"synthetic unsupported video")

    def test_source_and_parent_symlinks_are_refused(self):
        link = self.root / "linked.jpg"
        try:
            link.symlink_to(self.source)
        except OSError:
            self.skipTest("This Windows account cannot create symbolic links")
        with self.assertRaisesRegex(ValueError, "links"):
            tag_copy(link, self.destination, history(), "unused")
        parent = self.root / "parent-link"
        parent.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "links"):
            tag_copy(self.source, parent / "output.jpg", history(), "unused")

    def test_link_checks_run_even_when_account_cannot_create_links(self):
        original = Path.is_symlink
        def is_symlink(path):
            return path == self.root or original(path)
        with patch.object(Path, "is_symlink", is_symlink):
            with self.assertRaisesRegex(ValueError, "links"):
                tag_copy(self.source, self.destination, history(), "unused")

    def test_parent_traversal_does_not_bypass_link_checks(self):
        with self.assertRaisesRegex(ValueError, "traversal"):
            tag_copy(self.source, self.root / "unverified" / ".." / "output.jpg", history(), "unused")

    def test_invalid_jpeg_is_not_published(self):
        self.source.write_bytes(b"not jpeg")
        with self.assertRaisesRegex(ValueError, "JPEG"):
            tag_copy(self.source, self.destination, history(), "unused")
        self.assertFalse(self.destination.exists())

    def test_failed_exiftool_does_not_publish_or_mutate_source(self):
        with patch("savethesnap.tagging.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "private output")):
            with self.assertRaisesRegex(ValueError, "ExifTool failed") as error:
                tag_copy(self.source, self.destination, history(date="2024-01-02T12:34:56Z"), "exiftool")
        self.assertNotIn("private", str(error.exception))
        self.assertEqual(self.source.read_bytes(), JPEG)
        self.assertFalse(self.destination.exists())
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["source.jpg"])

    def test_readback_mismatch_prevents_publication(self):
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, "[{}]" if "-j" in command else "1 image updated", "")
        with patch("savethesnap.tagging.subprocess.run", side_effect=run):
            with self.assertRaisesRegex(ValueError, "readback"):
                tag_copy(self.source, self.destination, history(date="2024-01-02T12:34:56Z"), "exiftool")
        self.assertFalse(self.destination.exists())

    def test_exact_offset_and_zero_gps_use_argument_array_and_readback(self):
        commands = []
        def run(command, **kwargs):
            commands.append(command)
            self.assertIsInstance(command, list)
            self.assertFalse(kwargs["shell"])
            self.assertNotIn(str(self.source), command)
            metadata = {"ExifIFD:DateTimeOriginal": "2024:01:02 12:34:56", "ExifIFD:OffsetTimeOriginal": "-07:00",
                        "ExifIFD:SubSecTimeOriginal": "120", "GPS:GPSLatitude": 0,
                        "GPS:GPSLongitude": 0, "GPS:GPSLatitudeRef": "N", "GPS:GPSLongitudeRef": "E"}
            return subprocess.CompletedProcess(command, 0, json.dumps([metadata]) if "-j" in command else "1 image updated", "")
        with patch("savethesnap.tagging.subprocess.run", side_effect=run):
            result = tag_copy(self.source, self.destination,
                              history(date="2024-01-02T12:34:56.120-07:00", location={"latitude": 0, "longitude": 0}), "exiftool")
        self.assertTrue(result["date_written"])
        self.assertTrue(result["gps_written"])
        self.assertIn("-EXIF:OffsetTimeOriginal=-07:00", commands[0])
        self.assertIn("-GPS:GPSLatitude=0.0", commands[0])
        self.assertEqual(self.source.read_bytes(), JPEG)

    def test_scan_or_color_metadata_change_prevents_publication(self):
        def run(command, **kwargs):
            if "-j" not in command:
                stage = Path(command[-1])
                changed = bytearray(stage.read_bytes())
                changed[-3] ^= 1
                stage.write_bytes(changed)
                return subprocess.CompletedProcess(command, 0, "1 image updated", "")
            return subprocess.CompletedProcess(command, 0, json.dumps([{
                "ExifIFD:DateTimeOriginal": "2024:01:02 12:34:56", "ExifIFD:OffsetTimeOriginal": "+00:00",
            }]), "")
        with patch("savethesnap.tagging.subprocess.run", side_effect=run):
            with self.assertRaisesRegex(ValueError, "payload"):
                tag_copy(self.source, self.destination, history(date="2024-01-02T12:34:56Z"), "exiftool")
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.source.read_bytes(), JPEG)

    def test_invalid_gps_never_becomes_zero_fallback(self):
        for index, location in enumerate((None, "not supplied", {"latitude": 91, "longitude": 0})):
            with self.subTest(location=location), patch("savethesnap.tagging.subprocess.run") as run:
                result = tag_copy(self.source, self.root / f"output-{index}.jpg", history(location=location), "unused")
                run.assert_not_called()
                self.assertFalse(result["gps_written"])


@unittest.skipUnless(os.environ.get("SAVETHESNAP_TEST_EXIFTOOL"), "Set SAVETHESNAP_TEST_EXIFTOOL to run installed-tool integration")
class ExifToolIntegration(unittest.TestCase):
    def test_real_tagging_preserves_image_source_and_unrelated_metadata(self):
        exiftool = os.environ["SAVETHESNAP_TEST_EXIFTOOL"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "synthetic.jpg"
            source.write_bytes(JPEG)
            subprocess.run([exiftool, "-overwrite_original", "-EXIF:Artist=Synthetic fixture", str(source)],
                           check=True, capture_output=True, shell=False)
            original_bytes = source.read_bytes()
            essence = _jpeg_essence(source)
            result = tag_copy(source, root / "tagged.jpg", history(
                date="2024-01-02T12:34:56.120-07:00", location={"latitude": 0, "longitude": -105}), exiftool)
            self.assertEqual(source.read_bytes(), original_bytes)
            self.assertEqual(_jpeg_essence(root / "tagged.jpg"), essence)
            self.assertTrue(result["date_written"])
            self.assertTrue(result["gps_written"])
            readback = subprocess.run([exiftool, "-j", "-EXIF:Artist", str(root / "tagged.jpg")],
                                      check=True, capture_output=True, text=True, shell=False)
            self.assertEqual(json.loads(readback.stdout)[0]["Artist"], "Synthetic fixture")


if __name__ == "__main__":
    unittest.main()

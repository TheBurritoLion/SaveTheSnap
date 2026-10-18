"""Verified JPEG copy tagging for explicitly associated export-history rows.

The source is never passed to ExifTool. Videos are deliberately refused until
packet-level media verification is implemented. Dates with missing precision or
offset remain in a separate JSON sidecar; existing native tags stay untouched.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

from .metadata import date_evidence, gps_evidence


def _guard_path(path: Path, *, source: bool = False) -> Path:
    if ".." in path.parts:
        raise ValueError("Parent traversal is not supported")
    path = Path(os.path.abspath(path))
    if getattr(os.path, "isreserved", lambda value: False)(str(path)):
        raise ValueError("Reserved filesystem names are not supported")
    for item in (path, *path.parents):
        is_junction = getattr(item, "is_junction", lambda: False)
        if item.is_symlink() or is_junction():
            raise ValueError("Symbolic links and junctions are not supported")
    if source:
        if not path.is_file():
            raise ValueError("Source must be an existing regular file")
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError("Source must be an existing regular file")
    else:
        if path.exists():
            raise FileExistsError("Output already exists")
        if not path.parent.is_dir():
            raise ValueError("Output parent must already exist")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _jpeg_essence(path: Path) -> str:
    """Hash JPEG scans, decoder tables, and non-EXIF segments in sequence.

    Only APP1 segments explicitly identified as EXIF may differ. This is stricter
    than hashing scan bytes alone: dimensions, ICC profiles, comments, XMP, and
    decoder tables must also remain unchanged.
    """
    data = path.read_bytes()
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("Input is not a JPEG")
    digest = hashlib.sha256(b"\xff\xd8")
    position = 2
    saw_scan = False
    saw_frame = False
    frame_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while position < len(data):
        if data[position] != 0xFF:
            raise ValueError("Malformed JPEG marker")
        while position < len(data) and data[position] == 0xFF:
            position += 1
        if position >= len(data):
            raise ValueError("Truncated JPEG marker")
        marker = data[position]
        position += 1
        if marker == 0xD9:
            if not saw_scan or not saw_frame:
                raise ValueError("JPEG has no complete image scan")
            digest.update(b"\xff\xd9")
            digest.update(data[position:])
            return digest.hexdigest()
        if marker in (0x00, 0xD8) or 0xD0 <= marker <= 0xD7:
            raise ValueError("Unexpected JPEG marker")
        if marker == 0x01:
            digest.update(b"\xff\x01")
            continue
        if position + 2 > len(data):
            raise ValueError("Truncated JPEG segment")
        length = int.from_bytes(data[position:position + 2], "big")
        if length < 2 or position + length > len(data):
            raise ValueError("Invalid JPEG segment length")
        segment = data[position:position + length]
        if not (marker == 0xE1 and segment[2:].startswith(b"Exif\x00\x00")):
            digest.update(bytes((0xFF, marker)))
            digest.update(segment)
        saw_frame = saw_frame or marker in frame_markers
        position += length
        if marker != 0xDA:
            continue
        saw_scan = True
        scan_start = position
        while position < len(data):
            if data[position] != 0xFF:
                position += 1
                continue
            if position + 1 >= len(data):
                raise ValueError("Truncated JPEG scan")
            following = data[position + 1]
            if following == 0x00 or 0xD0 <= following <= 0xD7:
                position += 2
            elif following == 0xFF:
                position += 1
            else:
                break
        digest.update(data[scan_start:position])
    raise ValueError("JPEG has no end marker")


def _run(command: list[str]) -> subprocess.CompletedProcess:
    command = [command[0], '-config', '', *command[1:]]
    try:
        result = subprocess.run(command, shell=False, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("ExifTool could not complete; no output was published") from None
    if result.returncode:
        raise ValueError("ExifTool failed; no output was published")
    return result


def _verify_readback(exiftool: str, path: Path, expected_date: dict, gps: list[float] | None) -> None:
    result = _run([
        exiftool, "-charset", "filename=UTF8", "-j", "-n", "-G1",
        "-EXIF:DateTimeOriginal", "-EXIF:OffsetTimeOriginal", "-EXIF:SubSecTimeOriginal",
        "-GPS:GPSLatitude", "-GPS:GPSLatitudeRef", "-GPS:GPSLongitude", "-GPS:GPSLongitudeRef",
        "--", str(path),
    ])
    try:
        values = json.loads(result.stdout)
        if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
            raise ValueError
        values = values[0]
        if expected_date["status"] == "second":
            expected = datetime.fromisoformat(expected_date["value"])
            stamp = values["ExifIFD:DateTimeOriginal"]
            offset = values["ExifIFD:OffsetTimeOriginal"]
            fraction = values.get("ExifIFD:SubSecTimeOriginal", "")
            if not isinstance(stamp, str) or not isinstance(offset, str):
                raise ValueError
            iso = stamp[:10].replace(":", "-") + "T" + stamp[11:]
            if fraction:
                iso += "." + str(fraction)
            observed = datetime.fromisoformat(iso + offset)
            expected_fraction = expected_date["value"].split(".", 1)[1][:-6] if "." in expected_date["value"] else ""
            if (observed.tzinfo is None or observed != expected or
                    observed.utcoffset() != expected.utcoffset() or str(fraction) != expected_fraction):
                raise ValueError
        if gps is not None:
            latitude = float(values["GPS:GPSLatitude"])
            longitude = float(values["GPS:GPSLongitude"])
            latitude_ref = values["GPS:GPSLatitudeRef"]
            longitude_ref = values["GPS:GPSLongitudeRef"]
            if latitude_ref not in ("N", "S") or longitude_ref not in ("E", "W"):
                raise ValueError
            latitude = abs(latitude) * (-1 if latitude_ref == "S" else 1)
            longitude = abs(longitude) * (-1 if longitude_ref == "W" else 1)
            if not all(math.isfinite(value) for value in (latitude, longitude)):
                raise ValueError
            if abs(latitude - gps[0]) > 1e-7 or abs(longitude - gps[1]) > 1e-7:
                raise ValueError
    except (ValueError, KeyError, TypeError, IndexError):
        raise ValueError("Written metadata did not pass readback verification") from None


def _write_new(path: Path, content: bytes) -> os.stat_result:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    created = os.fstat(descriptor)
    if not stat.S_ISREG(created.st_mode):
        os.close(descriptor)
        raise ValueError("Output must be a regular file")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        _remove_owned(path, created)
        raise
    return created


def _remove_owned(path: Path, created: os.stat_result) -> None:
    try:
        current = path.lstat()
        if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
            path.unlink()
    except FileNotFoundError:
        pass


def tag_copy(source: Path, destination: Path, record: dict, exiftool: str) -> dict:
    """Create a verified tagged JPEG and JSON sidecar; never overwrite inputs.

    The caller must first establish a unique explicit media/history association.
    The sidecar is ``destination.name + '.metadata.json'``. Both output paths
    must be absent and their existing parents must contain no links or junctions.
    ExifTool must already be installed; this function does not install tools.
    """
    if not isinstance(record, dict) or record.get("association") != "explicit_unique":
        raise ValueError("A unique explicit history association is required")
    source = _guard_path(Path(source), source=True)
    destination = _guard_path(Path(destination))
    if source.suffix.lower() not in (".jpg", ".jpeg") or destination.suffix.lower() not in (".jpg", ".jpeg"):
        raise ValueError("Only JPEG tagging is verified; video and other formats are not supported")
    sidecar = _guard_path(destination.with_name(destination.name + ".metadata.json"))
    date = date_evidence(record.get("date"))
    gps = gps_evidence(record.get("location"))
    source_hash = _sha256(source)
    essence = _jpeg_essence(source)
    written_date = date["status"] == "second"
    arguments = []
    if written_date:
        timestamp = datetime.fromisoformat(date["value"])
        fraction = date["value"].split(".", 1)[1][:-6] if "." in date["value"] else ""
        arguments.extend([
            "-EXIF:DateTimeOriginal=" + timestamp.strftime("%Y:%m:%d %H:%M:%S"),
            "-EXIF:OffsetTimeOriginal=" + date["value"][-6:],
            "-EXIF:SubSecTimeOriginal=" + fraction,
        ])
    if gps is not None:
        arguments.extend([
            "-GPS:GPSLatitude=" + repr(abs(gps[0])),
            "-GPS:GPSLatitudeRef=" + ("S" if gps[0] < 0 else "N"),
            "-GPS:GPSLongitude=" + repr(abs(gps[1])),
            "-GPS:GPSLongitudeRef=" + ("W" if gps[1] < 0 else "E"),
            "-GPS:GPSMapDatum=WGS-84",
        ])
    stage = None
    published = []
    try:
        descriptor, stage_name = tempfile.mkstemp(prefix=".savethesnap-", suffix=".jpg", dir=destination.parent)
        stage = Path(stage_name)
        with os.fdopen(descriptor, "wb") as target, source.open("rb") as original:
            shutil.copyfileobj(original, target)
        if _sha256(stage) != source_hash:
            raise ValueError("Source changed while being copied")
        if arguments:
            if not isinstance(exiftool, str) or not exiftool.strip():
                raise ValueError("An existing ExifTool executable is required")
            _run([exiftool, "-charset", "filename=UTF8", "-overwrite_original", *arguments, "--", str(stage)])
            _guard_path(stage, source=True)
            _verify_readback(exiftool, stage, date, gps)
        if _jpeg_essence(stage) != essence:
            raise ValueError("JPEG image payload or unrelated metadata changed")
        if _sha256(source) != source_hash:
            raise ValueError("Source changed during verification")
        output_hash = _sha256(stage)
        evidence = {
            "schema_version": 1,
            "association": "explicit_unique",
            "reference": record.get("reference"),
            "date_raw": record.get("date"),
            "location_raw": record.get("location"),
            "date_evidence": date,
            "gps_evidence": gps,
            "date_written": written_date,
            "gps_written": gps is not None,
            "source_sha256": source_hash,
            "output_sha256": output_hash,
            "jpeg_essence_sha256": essence,
        }
        sidecar_content = (json.dumps(evidence, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        _guard_path(destination)
        _guard_path(sidecar)
        published.append((destination, _write_new(destination, stage.read_bytes())))
        published.append((sidecar, _write_new(sidecar, sidecar_content)))
        return {
            "destination": str(destination), "sidecar": str(sidecar),
            "date_written": written_date, "gps_written": gps is not None,
            "source_sha256": source_hash, "output_sha256": output_hash,
            "jpeg_essence_sha256": essence,
        }
    except BaseException:
        for path, created in reversed(published):
            _remove_owned(path, created)
        raise
    finally:
        if stage is not None:
            stage.unlink(missing_ok=True)

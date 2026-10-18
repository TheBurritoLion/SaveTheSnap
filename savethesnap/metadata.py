"""Parse export evidence without evaluating HTML or guessing missing metadata.

Download URLs are deliberately reduced to a boolean. This module does not
download media, resolve history associations, or assign a local timezone.
"""

from __future__ import annotations

from datetime import date, datetime
from html.parser import HTMLParser
import json
import math
import re
from pathlib import PurePosixPath
from urllib.parse import urlsplit


_REFERENCE_KEYS = {"file", "filename", "mediapath", "path"}
_DATE_KEYS = {"date", "capturedate", "capturetime"}
_MEDIA_KEYS = {"mediatype", "type"}
_LOCATION_KEYS = {"location"}
_DOWNLOAD_KEYS = {"download", "downloadlink", "downloadurl", "url"}
_KNOWN_KEYS = _REFERENCE_KEYS | _DATE_KEYS | _MEDIA_KEYS | _LOCATION_KEYS | _DOWNLOAD_KEYS
_MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".bmp",
    ".tif", ".tiff", ".mp4", ".mov", ".m4v", ".3gp", ".avi", ".mkv",
    ".webm", ".mpeg", ".mpg", ".mts", ".m2ts",
}


def _key(value: str) -> str:
    return "".join(char for char in value.lower() if char.isalnum())


def _local_reference(value: str, *, require_media_extension: bool = False) -> str | None:
    value = value.strip()
    if not value or value.startswith(("#", "//", "\\\\")):
        return None
    # A Windows drive is a path; other URI schemes are not local references.
    is_drive = len(value) >= 3 and value[0].isalpha() and value[1:3] in (":\\", ":/")
    if not is_drive:
        try:
            if urlsplit(value).scheme:
                return None
        except ValueError:
            return None
    if "?" in value or "#" in value or any(ord(char) < 32 for char in value):
        return None
    if require_media_extension and PurePosixPath(value.replace("\\", "/")).suffix.lower() not in _MEDIA_EXTENSIONS:
        return None
    return value


def _field(row: dict, names: set[str], index: int):
    values = [value for key, value in row.items() if _key(key) in names]
    if not values:
        return None
    if any(value != values[0] for value in values[1:]):
        raise ValueError(f"History row {index}: conflicting fields")
    return values[0]


def _normal_row(row: dict, index: int) -> dict:
    if not row or not all(isinstance(key, str) for key in row):
        raise ValueError(f"History row {index}: expected named fields")
    if not any(_key(key) in _KNOWN_KEYS for key in row):
        raise ValueError(f"History row {index}: no recognized export fields")
    reference = _field(row, _REFERENCE_KEYS, index)
    raw_date = _field(row, _DATE_KEYS, index)
    location = _field(row, _LOCATION_KEYS, index)
    media_type = _field(row, _MEDIA_KEYS, index)
    download = _field(row, _DOWNLOAD_KEYS, index)
    for value, label in ((reference, "reference"), (raw_date, "date"), (media_type, "media type")):
        if value is not None and not isinstance(value, str):
            raise ValueError(f"History row {index}: invalid {label} field")
    if location is not None and not isinstance(location, (str, dict)):
        raise ValueError(f"History row {index}: invalid location field")
    if download is not None and not isinstance(download, (str, bool)):
        raise ValueError(f"History row {index}: invalid download field")
    return {
        "reference": _local_reference(reference) if reference else None,
        "date": raw_date.strip() or None if raw_date is not None else None,
        "location": location if location else None,
        "media_type": media_type.strip() or None if media_type is not None else None,
        "url_present": download.strip().lower().startswith(('https://', 'http://')) if isinstance(download, str) else bool(download),
        "row_index": index,
    }


class _HistoryTables(HTMLParser):
    """Collect table cells and link presence, never JavaScript or link contents."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[dict]]] = []
        self.stack: list[dict] = []
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.ignored += 1
            return
        if self.ignored:
            return
        if tag == "table":
            frame = {"rows": [], "row": None, "cell": None}
            self.stack.append(frame)
            return
        if not self.stack:
            return
        frame = self.stack[-1]
        if tag == "tr":
            if frame["row"] is not None:
                raise ValueError("History HTML: overlapping table rows")
            frame["row"] = []
        elif tag in ("td", "th"):
            if frame["row"] is None or frame["cell"] is not None:
                raise ValueError("History HTML: malformed table cell")
            frame["cell"] = {"text": [], "references": [], "url_present": False}
        cell = frame["cell"]
        if cell is None:
            return
        attributes = dict(attrs)
        # Merely detect an export callback; never parse or execute its argument.
        callback = attributes.get("onclick") or ""
        match = re.search(r"downloadMemories\(\s*(['\"])(.*?)\1", callback, re.IGNORECASE)
        if match and match[2].strip().lower().startswith(('https://', 'http://')):
            cell["url_present"] = True
        if tag == "a":
            href = (attributes.get("href") or "").strip()
            local = _local_reference(href, require_media_extension=True)
            if local:
                cell["references"].append(local)
            elif href.lower().startswith(('https://', 'http://')):
                cell["url_present"] = True
        if tag == "br":
            cell["text"].append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.ignored = max(0, self.ignored - 1)
            return
        if self.ignored or not self.stack:
            return
        frame = self.stack[-1]
        if tag in ("td", "th"):
            if frame["cell"] is None:
                raise ValueError("History HTML: unmatched table cell")
            frame["row"].append(frame["cell"])
            frame["cell"] = None
        elif tag == "tr":
            if frame["row"] is None or frame["cell"] is not None:
                raise ValueError("History HTML: incomplete table row")
            frame["rows"].append(frame["row"])
            frame["row"] = None
        elif tag == "table":
            if frame["row"] is not None or frame["cell"] is not None:
                raise ValueError("History HTML: incomplete table")
            self.tables.append(frame["rows"])
            self.stack.pop()

    def handle_data(self, data):
        if not self.ignored and self.stack and self.stack[-1]["cell"] is not None:
            self.stack[-1]["cell"]["text"].append(data)


def _cell_text(cell: dict) -> str:
    return " ".join("".join(cell["text"]).split())


def _parse_html(text: str) -> list[dict]:
    parser = _HistoryTables()
    parser.feed(text)
    parser.close()
    if parser.stack:
        raise ValueError("History HTML: unclosed table")
    results = []
    recognized_table = False
    for table in parser.tables:
        header_index = None
        headers = None
        for index, cells in enumerate(table):
            names = [_key(_cell_text(cell)) for cell in cells]
            if any(name in _DATE_KEYS for name in names) and any(name in _MEDIA_KEYS for name in names):
                header_index, headers = index, names
                recognized_table = True
                break
        if headers is None:
            continue
        for cells in table[header_index + 1:]:
            index = len(results)
            if len(cells) != len(headers):
                raise ValueError(f"History row {index}: table column count differs from header")
            row = {}
            references = []
            url_present = False
            for name, cell in zip(headers, cells):
                if name in _KNOWN_KEYS:
                    value = _cell_text(cell)
                    if name in _DOWNLOAD_KEYS:
                        # A label such as "Download" alone does not prove a link.
                        value = cell["url_present"] or value.lower().startswith(("https://", "http://"))
                    elif name in _REFERENCE_KEYS and len(set(cell["references"])) == 1:
                        linked_reference = cell["references"][0]
                        named_reference = _local_reference(value, require_media_extension=True)
                        if named_reference and named_reference != linked_reference:
                            raise ValueError(f"History row {index}: conflicting local media references")
                        value = linked_reference
                    if name in row and row[name] != value:
                        raise ValueError(f"History row {index}: duplicate table field")
                    row[name] = value
                references.extend(cell["references"])
                url_present = url_present or cell["url_present"]
            # A local media anchor is explicit evidence even without a File column.
            if references:
                if len(set(references)) != 1:
                    raise ValueError(f"History row {index}: conflicting local media references")
                existing = _field(row, _REFERENCE_KEYS, index)
                if existing and _local_reference(existing) != references[0]:
                    raise ValueError(f"History row {index}: conflicting local media references")
                row["file"] = references[0]
            normalized = _normal_row(row, index)
            normalized["url_present"] = normalized["url_present"] or url_present
            results.append(normalized)
    if not recognized_table:
        raise ValueError("History HTML: no Snapchat history table with Date and Media Type headers")
    return results


def parse_history(data: bytes, name: str) -> list[dict]:
    """Read Saved Media JSON or a memories_history HTML table.

    Row indexes are zero based. Invalid rows fail explicitly instead of reducing
    the apparent row count. Error messages do not include field contents.
    """
    if not isinstance(data, bytes):
        raise ValueError("History input must be bytes")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        raise ValueError("History input must be UTF-8") from None
    suffix = PurePosixPath(str(name).replace("\\", "/")).suffix.lower()
    if suffix in (".html", ".htm") or text.lstrip().startswith("<"):
        return _parse_html(text)
    try:
        document = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        raise ValueError("History input is not valid JSON or HTML") from None
    if not isinstance(document, dict):
        raise ValueError("History JSON: expected a Saved Media object")
    keys = [key for key in document if _key(key) == "savedmedia"]
    if len(keys) != 1 or not isinstance(document[keys[0]], list):
        raise ValueError("History JSON: expected a Saved Media list")
    results = []
    for index, row in enumerate(document[keys[0]]):
        if not isinstance(row, dict):
            raise ValueError(f"History row {index}: expected an object")
        results.append(_normal_row(row, index))
    return results


_DATE_TIME = re.compile(
    r"^(?P<day>\d{4}-\d{2}-\d{2})(?:T|\s+)"
    r"(?P<time>\d{2}:\d{2}(?P<seconds>:\d{2}(?:\.\d{1,6})?)?)"
    r"\s*(?P<zone>Z|UTC|GMT|[+-]\d{2}:?\d{2})?$", re.IGNORECASE
)


def date_evidence(raw) -> dict:
    """Describe stated date precision; never infer an offset or missing seconds."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {"status": "unknown", "value": None}
    if not isinstance(raw, str):
        return {"status": "invalid", "value": None}
    value = raw.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            date.fromisoformat(value)
        except ValueError:
            return {"status": "invalid", "value": None}
        return {"status": "date_only", "value": value}
    match = _DATE_TIME.fullmatch(value)
    if not match:
        return {"status": "invalid", "value": None}
    zone = match["zone"]
    if zone and zone.upper() in ("Z", "UTC", "GMT"):
        zone = "+00:00"
    elif zone and ":" not in zone:
        zone = zone[:3] + ":" + zone[3:]
    if zone and (int(zone[1:3]) >= 24 or int(zone[4:6]) >= 60):
        return {"status": "invalid", "value": None}
    iso = match["day"] + "T" + match["time"] + (zone or "")
    try:
        datetime.fromisoformat(iso)
    except ValueError:
        return {"status": "invalid", "value": None}
    status = "timezone_missing" if zone is None else ("second" if match["seconds"] else "minute")
    return {"status": status, "value": iso}


def gps_evidence(raw) -> list[float] | None:
    """Return explicitly stated, finite WGS84 coordinate values, including zero."""
    if isinstance(raw, dict):
        values = {_key(key): value for key, value in raw.items() if isinstance(key, str)}
        latitude, longitude = values.get("latitude"), values.get("longitude")
    elif isinstance(raw, str):
        match = re.fullmatch(r"\s*Latitude\s*,\s*Longitude\s*:\s*([^,]+),\s*([^,]+)\s*", raw, re.IGNORECASE)
        if not match:
            return None
        latitude, longitude = match.groups()
    else:
        return None
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        return None
    try:
        latitude, longitude = float(latitude), float(longitude)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return [latitude, longitude]

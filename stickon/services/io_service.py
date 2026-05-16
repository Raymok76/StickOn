from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

MAGIC = b"STCKON01"
VERSION = 1


def _require_available(data: bytes, pos: int, size: int, what: str) -> None:
    if pos < 0 or size < 0 or pos + size > len(data):
        raise ValueError(f"Corrupt .sti: truncated {what}")


def _encode_blob_table(blobs: dict[str, bytes]) -> bytes:
    """Table: u32 count, then for each: u16 key_len, key utf8, u32 len, data."""
    parts: list[bytes] = []
    parts.append(struct.pack("<I", len(blobs)))
    for k, data in blobs.items():
        kb = k.encode("utf-8")
        parts.append(struct.pack("<H", len(kb)))
        parts.append(kb)
        parts.append(struct.pack("<I", len(data)))
        parts.append(data)
    return b"".join(parts)


def _decode_blob_table(data: bytes, offset: int) -> tuple[dict[str, bytes], int]:
    pos = offset
    _require_available(data, pos, 4, "blob table header")
    (count,) = struct.unpack_from("<I", data, pos)
    pos += 4
    blobs: dict[str, bytes] = {}
    # Minimum bytes per blob entry is key_len(u16) + data_len(u32).
    if count > (len(data) - pos) // 6:
        raise ValueError("Corrupt .sti: blob entry count exceeds data size")
    for _ in range(count):
        _require_available(data, pos, 2, "blob key length")
        (kl,) = struct.unpack_from("<H", data, pos)
        pos += 2
        _require_available(data, pos, kl, "blob key")
        try:
            key = data[pos : pos + kl].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Corrupt .sti: invalid blob key encoding") from exc
        pos += kl
        _require_available(data, pos, 4, "blob data length")
        (dl,) = struct.unpack_from("<I", data, pos)
        pos += 4
        _require_available(data, pos, dl, "blob data")
        blobs[key] = data[pos : pos + dl]
        pos += dl
    return blobs, pos


def save_pur(path: str | Path, manifest: dict[str, Any], blobs: dict[str, bytes]) -> None:
    path = Path(path)
    j = json.dumps(manifest, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    blob_bytes = _encode_blob_table(blobs)
    header = MAGIC + struct.pack("<II", VERSION, len(j))
    path.write_bytes(header + j + blob_bytes)


def load_pur(path: str | Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    path = Path(path)
    raw = path.read_bytes()
    if len(raw) < len(MAGIC) + 8:
        raise ValueError("Invalid file")
    if raw[: len(MAGIC)] != MAGIC:
        raise ValueError("Bad magic")
    ver, jlen = struct.unpack_from("<II", raw, len(MAGIC))
    if ver != VERSION:
        raise ValueError(f"Unsupported version {ver}")
    jstart = len(MAGIC) + 8
    jend = jstart + jlen
    if jend > len(raw):
        raise ValueError("Corrupt .sti: manifest length exceeds file size")
    try:
        manifest = json.loads(raw[jstart:jend].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Corrupt .sti: invalid manifest JSON") from exc
    blobs, end = _decode_blob_table(raw, jend)
    if end != len(raw):
        raise ValueError("Corrupt .sti: trailing bytes after blob table")
    return manifest, blobs

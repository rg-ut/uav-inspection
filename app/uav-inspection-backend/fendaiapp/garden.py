"""Prepare the cropped display model for the calibrated Fendai release."""
from functools import lru_cache
from itertools import pairwise
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import struct
import tempfile
from threading import Lock

SOURCE_BYTES = 98_735_796
SOURCE_SHA256 = "101b60a7033cbf7d740c74b33d8be3f9ce358134e6de6521a03e7d42c0ce32f8"
GARDEN_MODEL = "fendai-garden-v1.ksplat"
GARDEN = json.loads(Path(__file__).with_name("garden.json").read_text(encoding="utf-8"))
LOGGER = logging.getLogger(__name__)
PREPARE_LOCK = Lock()
HEADER_SIZE = 4096
BODY_OFFSET = 5120
RECORD_SIZE = 72
POINT = struct.Struct("<3H3e")


def crop_model(data: bytes) -> tuple[bytearray, int]:
    """Filter the known single-section, compression-1 KSplat without re-quantizing it."""
    count, _, bucket_size, buckets, block, _, quant, _, full, partial, _ = (
        struct.unpack_from("<IIIIfH2xIIIIH", data, HEADER_SIZE)
    )
    partial_lengths = struct.unpack_from(f"<{partial}I", data, BODY_OFFSET)
    centers_offset = BODY_OFFSET + partial * 4
    record_offset = centers_offset + buckets * 12
    if full + partial != buckets or record_offset + count * RECORD_SIZE != len(data):
        raise ValueError("Unexpected Fendai KSplat section layout")

    polygon = GARDEN["crop"]
    edges = [
        (x1, z1, x2 - x1, z2 - z1, 1 / math.hypot(x2 - x1, z2 - z1))
        for (x1, z1), (x2, z2) in pairwise(polygon + polygon[:1])
    ]
    min_x, max_x = min(p[0] for p in polygon), max(p[0] for p in polygon)
    min_z, max_z = min(p[1] for p in polygon), max(p[1] for p in polygon)
    heights = GARDEN["heights"]
    origin_x, origin_z = GARDEN["origin"]
    step = GARDEN["step"]
    max_column, max_row = len(heights[0]) - 1, len(heights) - 1

    quant = quant or 32767
    scale = GARDEN["modelScale"]
    position_x, position_y, position_z = GARDEN["modelPosition"]
    qx, _, qz, qw = GARDEN["modelRotation"]
    # Transform each bucket once, then apply the same linear transform to quantized offsets.
    matrix = (
        (1 - 2 * qz * qz, -2 * qz * qw, -2 * qx * qz),
        (2 * qz * qw, 1 - 2 * (qx * qx + qz * qz), 2 * qx * qw),
        (2 * qx * qz, 2 * qx * qw, -(1 - 2 * qx * qx)),
    )
    offset_scale = block / (2 * quant) * scale

    full_centers = bytearray()
    partial_centers = bytearray()
    full_records = bytearray()
    partial_records = bytearray()
    kept_lengths = []
    kept = 0
    for bucket in range(buckets):
        length = bucket_size if bucket < full else partial_lengths[bucket - full]
        center_at = centers_offset + bucket * 12
        cx, cy, cz = struct.unpack_from("<3f", data, center_at)
        bx = position_x + scale * sum(a * b for a, b in zip(matrix[0], (cx, cy, cz)))
        by = position_y + scale * sum(a * b for a, b in zip(matrix[1], (cx, cy, cz)))
        bz = position_z + scale * sum(a * b for a, b in zip(matrix[2], (cx, cy, cz)))
        records = bytearray()
        for at in range(record_offset, record_offset + length * RECORD_SIZE, RECORD_SIZE):
            alpha = data[at + 23]
            if alpha < 5:
                continue
            i, j, k, sx, sy, sz = POINT.unpack_from(data, at)
            major = max(sx, sy, sz) * scale
            if not math.isfinite(major) or major > 5.5:
                continue
            u, v, w = (i - quant) * offset_scale, (j - quant) * offset_scale, (k - quant) * offset_scale
            x = bx + matrix[0][0] * u + matrix[0][1] * v + matrix[0][2] * w
            z = bz + matrix[2][0] * u + matrix[2][1] * v + matrix[2][2] * w
            if x <= min_x or x >= max_x or z <= min_z or z >= max_z:
                continue
            # The crop is convex and oriented counterclockwise in XZ.
            edge = min((dx * (z - pz) - dz * (x - px)) * norm for px, pz, dx, dz, norm in edges)
            if edge <= 0:
                continue
            y = by + matrix[1][0] * u + matrix[1][1] * v + matrix[1][2] * w
            row = min(max(round((z - origin_z) / step), 0), max_row)
            column = min(max(round((x - origin_x) / step), 0), max_column)
            height = y - heights[row][column]
            if not -2.7 <= height <= 10:
                continue
            # Preserve large, thin ground splats that reconstruct the path. Large foliage
            # splats above the surface are the main source of floating blurry patches.
            if major > 2.25 and (height > 0.75 or min(sx, sy, sz) * scale > 0.24):
                continue
            if height > 3.4 and major > 0.95:
                continue
            # Taper large splats farther from the cut to limit smearing across the edge.
            weight = min(1, edge / max(2.8, major * 2.6))
            if major > 0.9 and -1.6 < height < 1.5:
                red, green, blue = data[at + 20:at + 23]
                # Large coral ground splats smear the track at grazing angles.
                # The aligned path surface fills the coverage they leave behind.
                if red > 155 and red - green > 32 and red - blue > 42 and green - blue < 60:
                    weight *= max(0, (1.6 - major) / 0.7)
            opacity = round(alpha * weight)
            if opacity < 5:
                continue
            records += data[at:at + 23] + bytes((opacity,)) + data[at + 24:at + RECORD_SIZE]

        record_offset += length * RECORD_SIZE
        retained = len(records) // RECORD_SIZE
        kept += retained
        if retained == bucket_size:
            full_centers += data[center_at:center_at + 12]
            full_records += records
        elif retained:
            partial_centers += data[center_at:center_at + 12]
            partial_records += records
            kept_lengths.append(retained)

    full_count = len(full_centers) // 12
    partial_count = len(kept_lengths)
    parts = (
        struct.pack(f"<{partial_count}I", *kept_lengths), full_centers, partial_centers,
        full_records, partial_records,
    )
    header = bytearray(data[:BODY_OFFSET])
    struct.pack_into("<II", header, 12, kept, kept)
    struct.pack_into("<II", header, HEADER_SIZE, kept, kept)
    struct.pack_into("<I", header, HEADER_SIZE + 12, full_count + partial_count)
    struct.pack_into("<III", header, HEADER_SIZE + 28, sum(map(len, parts)), full_count, partial_count)
    for part in parts:
        header.extend(part)
    return header, kept


@lru_cache(maxsize=4)
def _digest(path: Path, size: int, mtime: int, ctime: int) -> str:
    # The stat values invalidate the cached digest after an asset replacement.
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _matches(path: Path, size: int, digest: str) -> bool:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return False
    return stat.st_size == size and _digest(path, size, stat.st_mtime_ns, stat.st_ctime_ns) == digest


def prepare_garden_model(source: Path) -> tuple[Path, int] | None:
    with PREPARE_LOCK:
        target = source.parent.parent / ".cache" / GARDEN_MODEL
        pending = None
        try:
            if not _matches(source, SOURCE_BYTES, SOURCE_SHA256):
                return None
            expected_count = GARDEN["pointCount"]
            expected_digest = GARDEN["modelSha256"]
            if _matches(target, GARDEN["modelBytes"], expected_digest):
                return target, expected_count

            data = source.read_bytes()
            if hashlib.sha256(data).hexdigest() != SOURCE_SHA256:
                return None
            cropped, count = crop_model(data)
            if count != expected_count or hashlib.sha256(cropped).hexdigest() != expected_digest:
                raise ValueError("Generated Fendai model does not match its calibrated profile")
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".fendai-", suffix=".tmp", delete=False) as stream:
                pending = Path(stream.name)
                stream.write(cropped)
            os.replace(pending, target)
            return target, count
        except (OSError, ValueError) as exc:
            LOGGER.warning("Cannot prepare the cropped Fendai model: %s", exc)
            return None
        finally:
            if pending is not None:
                pending.unlink(missing_ok=True)

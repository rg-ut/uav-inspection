import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fendaiapp import garden


class FendaiGardenTests(unittest.TestCase):
    def test_cropping_rewrites_full_and_partial_bucket_offsets(self):
        header = bytearray(garden.BODY_OFFSET)
        struct.pack_into("<IIIIfH2xIIIIH", header, garden.HEADER_SIZE,
                         5, 5, 2, 3, 5.0, 12, 32767, 400, 2, 1, 2)
        # Identity-space fixture. The second and third buckets each retain one record.
        centers = struct.pack("<9f", 5, 0, 5, 8, 0, 8, 6, 0, 6)
        point = struct.pack("<3H3e", 32767, 32767, 32767, 0.5, 0.1, 0.5)
        records = [point.ljust(23, b"\0") + bytes([alpha]) + bytes([i]) * 48
                   for i, alpha in enumerate((200, 200, 200, 0, 200), start=1)]
        original = bytes(header) + struct.pack("<I", 1) + centers + b"".join(records)
        with patch.object(garden, "GARDEN", {
            "crop": [[0, 0], [20, 0], [20, 20], [0, 20]],
            "heights": [[0]], "origin": [0, 0], "step": 1,
            "modelScale": 1, "modelRotation": [0, 0, 0, 1], "modelPosition": [0, 0, 20],
        }):
            cropped, count = garden.crop_model(original)

        self.assertEqual(count, 4)
        self.assertEqual(struct.unpack_from("<II", cropped, 12), (4, 4))
        self.assertEqual(struct.unpack_from("<III", cropped, garden.HEADER_SIZE + 28), (332, 1, 2))
        self.assertEqual(struct.unpack_from("<II", cropped, garden.BODY_OFFSET), (1, 1))
        record_at = garden.BODY_OFFSET + 8 + len(centers)
        self.assertEqual(cropped[record_at:], b"".join(records[i] for i in (0, 1, 2, 4)))

    def test_cache_reuses_a_verified_crop_and_recovers_a_missing_cache(self):
        source_data = b"released Fendai fixture"
        output = bytearray(b"processed Fendai fixture")
        profile = {
            "pointCount": 7, "modelBytes": len(output),
            "modelSha256": hashlib.sha256(output).hexdigest(),
        }
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "scenes" / "fendai-flower.ksplat"
            source.parent.mkdir()
            source.write_bytes(source_data)
            with (patch.object(garden, "SOURCE_BYTES", len(source_data)),
                  patch.object(garden, "SOURCE_SHA256", hashlib.sha256(source_data).hexdigest()),
                  patch.object(garden, "GARDEN", profile),
                  patch.object(garden, "crop_model", return_value=(output, 7)) as crop):
                target, count = garden.prepare_garden_model(source)
                self.assertEqual(count, 7)
                self.assertEqual(target.read_bytes(), output)
                self.assertEqual(source.read_bytes(), source_data)
                self.assertEqual(garden.prepare_garden_model(source), (target, 7))
                crop.assert_called_once()

                target.unlink()
                self.assertEqual(garden.prepare_garden_model(source), (target, 7))
                self.assertEqual(crop.call_count, 2)
                source.write_bytes(b"x" * len(source_data))
                self.assertIsNone(garden.prepare_garden_model(source))

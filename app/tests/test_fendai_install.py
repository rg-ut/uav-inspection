from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_fendai_scene.py"
SPEC = importlib.util.spec_from_file_location("install_fendai_scene", SCRIPT)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class FendaiInstallerTests(unittest.TestCase):
    def test_verified_install_is_repeatable_without_changing_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            content = b"binary-scene-fixture"
            source = root / "download.ksplat"
            source.write_bytes(content)
            manifest = {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            target = installer.install(root / "data", manifest, source)
            self.assertEqual(target.read_bytes(), content)
            self.assertEqual(installer.install(root / "data", manifest, source), target)
            self.assertEqual(source.read_bytes(), content)

    def test_corruption_and_unconfirmed_overwrite_preserve_installed_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "download.ksplat"
            source.write_bytes(b"invalid")
            manifest = {"bytes": 7, "sha256": hashlib.sha256(b"correct").hexdigest()}
            with self.assertRaises(ValueError):
                installer.install(root / "data", manifest, source)
            directory = root / "data/uav-inspection-backend/fendaidata/scenes"
            self.assertEqual(list(directory.iterdir()), [])
            target = directory / "fendai-flower.ksplat"
            target.write_bytes(b"keep-this-version")
            with self.assertRaises(ValueError):
                installer.install(root / "data", manifest, source)
            self.assertEqual(target.read_bytes(), b"keep-this-version")
            source.write_bytes(b"correct")
            installer.install(root / "data", manifest, source, overwrite=True)
            self.assertEqual(target.read_bytes(), b"correct")


if __name__ == "__main__":
    unittest.main()

"""
Tests for Tiki Rehearsal Lounge configuration manager.
"""
import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch

from config import (
    load_studio_config,
    save_studio_config,
    STUDIO_CONFIG_DIR,
    STUDIO_CONFIG_PATH,
    STUDIO_VERSION
)


class TestStudioConfig(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.mock_config_path = Path(self.temp_dir.name) / "config.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_version_present(self):
        self.assertTrue(isinstance(STUDIO_VERSION, str))
        self.assertGreater(len(STUDIO_VERSION), 0)

    def test_load_default_config_structure(self):
        with patch("config.STUDIO_CONFIG_PATH", self.mock_config_path):
            cfg = load_studio_config()
            self.assertIn("storage_mode", cfg)
            self.assertIn("library_dir", cfg)
            self.assertIn("data_dir", cfg)
            self.assertIn("recordings_dir", cfg)
            self.assertIn("target_lufs", cfg)
            self.assertIn("video_quality_profile", cfg)
            self.assertIn(cfg["storage_mode"], ("integrated", "standalone"))

    def test_save_and_reload_config(self):
        with patch("config.STUDIO_CONFIG_PATH", self.mock_config_path):
            custom_cfg = {
                "storage_mode": "standalone",
                "library_dir": str(Path(self.temp_dir.name) / "custom_lib"),
                "data_dir": str(Path(self.temp_dir.name) / "custom_data"),
                "recordings_dir": str(Path(self.temp_dir.name) / "custom_recs"),
                "target_lufs": -23.0,
                "video_quality_profile": "balanced_720p"
            }
            ok = save_studio_config(custom_cfg)
            self.assertTrue(ok)
            self.assertTrue(self.mock_config_path.exists())

            loaded = load_studio_config()
            self.assertEqual(loaded["storage_mode"], "standalone")
            self.assertEqual(loaded["library_dir"], custom_cfg["library_dir"])
            self.assertEqual(loaded["target_lufs"], -23.0)
            self.assertEqual(loaded["video_quality_profile"], "balanced_720p")


if __name__ == "__main__":
    unittest.main()

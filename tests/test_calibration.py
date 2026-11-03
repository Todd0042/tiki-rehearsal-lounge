"""
Tests for Tiki Rehearsal Lounge Vocal Calibration & Pitch Tracking.
"""
import unittest
import math
import tempfile
import numpy as np
from pathlib import Path

from audio.calibration import (
    hz_to_midi,
    midi_to_hz,
    midi_to_note_name,
    hz_to_note_name,
    cents_deviation,
    extract_pitch_contour,
    VocalProfile,
    VocalCalibrationManager
)


class TestVocalCalibration(unittest.TestCase):
    def test_frequency_midi_conversions(self):
        # A4 = 440Hz = MIDI 69
        self.assertAlmostEqual(hz_to_midi(440.0), 69.0, places=2)
        self.assertAlmostEqual(midi_to_hz(69.0), 440.0, places=2)

        # C4 = middle C = approx 261.63 Hz = MIDI 60
        self.assertAlmostEqual(hz_to_midi(261.63), 60.0, places=1)
        self.assertAlmostEqual(midi_to_hz(60.0), 261.63, delta=0.5)

    def test_note_naming(self):
        self.assertEqual(midi_to_note_name(60), "C4")
        self.assertEqual(midi_to_note_name(69), "A4")
        self.assertEqual(midi_to_note_name(57), "A3")
        self.assertEqual(hz_to_note_name(440.0), "A4")
        self.assertEqual(hz_to_note_name(0.0), "N/A")

    def test_cents_deviation(self):
        # Exactly on pitch
        self.assertAlmostEqual(cents_deviation(440.0, 69), 0.0, places=2)
        # Sharp by approx 25 cents
        f_sharp = 440.0 * (2.0 ** (25.0 / 1200.0))
        self.assertAlmostEqual(cents_deviation(f_sharp, 69), 25.0, places=1)

    def test_pitch_contour_autocorrelation(self):
        sr = 22050
        duration = 0.5  # 500 ms
        t = np.linspace(0, duration, int(sr * duration), endpoint=False)
        target_hz = 220.0  # A3
        audio = 0.8 * np.sin(2.0 * np.pi * target_hz * t).astype(np.float32)

        times, pitches = extract_pitch_contour(audio, sr, min_hz=100.0, max_hz=400.0)
        voiced_pitches = pitches[pitches > 0]
        self.assertGreater(len(voiced_pitches), 5)
        median_pitch = float(np.median(voiced_pitches))
        self.assertAlmostEqual(median_pitch, target_hz, delta=4.0)

    def test_vocal_profile_persistence(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            profile_path = Path(tmp_dir) / "vocal_profile.json"
            mgr = VocalCalibrationManager(profile_path=profile_path)
            
            profile = VocalProfile(
                anchor_hz=130.81,  # C3
                anchor_note="C3",
                range_min_hz=82.41,   # E2
                range_min_note="E2",
                range_max_hz=392.00, # G4
                range_max_note="G4",
                range_semitones=27.0,
                tessitura_min_hz=155.56,  # D#3
                tessitura_min_note="D#3",
                tessitura_max_hz=293.66, # D4
                tessitura_max_note="D4",
                vocal_type="Baritone",
                calibrated_at="2026-11-03T10:00:00"
            )
            mgr.save_profile(profile)
            self.assertTrue(profile_path.exists())

            loaded = mgr.load_profile()
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.vocal_type, "Baritone")
            self.assertEqual(loaded.anchor_note, "C3")
            self.assertAlmostEqual(loaded.range_max_hz, 392.00, places=1)


if __name__ == "__main__":
    unittest.main()

"""
Tests for Tiki Rehearsal Lounge Multi-Instrument Tone Generator & Warmup Synthesizer.
"""
import unittest
import numpy as np

from audio.tone_generator import (
    note_name_to_midi,
    note_name_to_hz,
    transpose_note,
    clamp_note_to_range,
    parse_target_notes,
    get_sorted_instruments,
    ToneSynthesizer,
    INSTRUMENT_DEFS
)


class TestToneGenerator(unittest.TestCase):
    def test_note_name_to_midi(self):
        self.assertEqual(note_name_to_midi("C4"), 60)
        self.assertEqual(note_name_to_midi("A4"), 69)
        self.assertEqual(note_name_to_midi("C3"), 48)
        self.assertEqual(note_name_to_midi("F#3"), 54)
        self.assertEqual(note_name_to_midi("Bb3"), 58)
        self.assertIsNone(note_name_to_midi("Invalid"))

    def test_note_name_to_hz(self):
        self.assertAlmostEqual(note_name_to_hz("A4"), 440.0, places=1)
        self.assertAlmostEqual(note_name_to_hz("A3"), 220.0, places=1)
        self.assertIsNone(note_name_to_hz("Z9"))

    def test_transpose_note(self):
        self.assertEqual(transpose_note("C4", 2), "D4")
        self.assertEqual(transpose_note("C4", 12), "C5")
        self.assertEqual(transpose_note("C4", -1), "B3")
        self.assertEqual(transpose_note("A4", -12), "A3")

    def test_parse_target_notes(self):
        text = "Start at C3, glide up through E3 and hit G3 smoothly."
        notes = parse_target_notes(text)
        self.assertEqual(notes, ["C3", "E3", "G3"])

    def test_sorted_instruments(self):
        all_inst = get_sorted_instruments()
        self.assertGreaterEqual(len(all_inst), 5)
        self.assertIn("piano", INSTRUMENT_DEFS)
        # Check specific vocal preference sorting
        bass_inst = get_sorted_instruments("Bass")
        self.assertEqual(bass_inst[0]["key"], "strings")

    def test_tone_synthesis(self):
        synth = ToneSynthesizer()
        audio = synth.synthesize_wave(
            freq_hz=440.0,
            instrument="piano",
            duration_s=0.2,
            sample_rate=22050
        )
        self.assertIsInstance(audio, np.ndarray)
        self.assertEqual(len(audio), int(22050 * 0.2))
        self.assertGreater(np.max(np.abs(audio)), 0.01)


if __name__ == "__main__":
    unittest.main()

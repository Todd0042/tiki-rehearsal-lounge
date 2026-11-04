"""
Tests for Tiki Rehearsal Lounge 100% Offline Vocal Coach.
"""
import unittest
from ai.offline_coach import OfflineVocalCoach


class TestOfflineCoach(unittest.TestCase):
    def setUp(self):
        self.coach = OfflineVocalCoach()

    def test_warmup_routine_generation(self):
        profile = {
            "vocal_type": "Tenor",
            "anchor_note": "D3",
            "tessitura_max_note": "A4"
        }
        routine = self.coach.generate_warmup_routine(profile_data=profile, key_shift=0)
        self.assertIsInstance(routine, list)
        self.assertGreaterEqual(len(routine), 3)

        first_step = routine[0]
        self.assertIn("title", first_step)
        self.assertIn("target_notes", first_step)
        self.assertIn("purpose", first_step)

    def test_rule_based_debrief(self):
        report = {
            "overall_score": 88,
            "pitch_accuracy_pct": 91.2,
            "intonation_cents": 3.5,
            "breath_support_pct": 82.0,
            "resonance_verdict": "Forward & Bright",
            "current_key_shift": 0,
            "recommended_key_shift": -1,
            "phrase_highlights": [
                {"timestamp": "0:14", "note_span": "C4 -> E4", "detail": "Locked into pitch center with solid breath support."}
            ],
            "phrase_struggles": [
                {"timestamp": "0:32", "note_span": "G4", "detail": "Vocal pushed sharp on high belting vowel.", "cue": "Drop the jaw and widen the pharynx."}
            ]
        }
        debrief = self.coach._generate_rule_based_debrief(report, track_info=None, profile=None)
        self.assertIn("88/100", debrief)
        self.assertIn("What Clicked", debrief)
        self.assertIn("Where It Slipped", debrief)
        self.assertIn("Drop the jaw", debrief)
        self.assertIn("Transposing from +0 to **-1**", debrief)

    def test_server_binary_resolution(self):
        """Verifies that _find_llama_server_binary locates bundled bin/llama-server if present."""
        bin_path = self.coach._find_llama_server_binary()
        if bin_path:
            self.assertTrue(bin_path.exists())
            self.assertTrue("llama-server" in bin_path.name.lower())

    def test_available_models_catalog(self):
        """Verifies that 7B, 3B, and 1.5B models are present with complete metadata."""
        from ai.offline_coach import AVAILABLE_MODELS
        self.assertIn("7b", AVAILABLE_MODELS)
        self.assertIn("3b", AVAILABLE_MODELS)
        self.assertIn("1.5b", AVAILABLE_MODELS)

        m7b = AVAILABLE_MODELS["7b"]
        self.assertTrue(m7b["name"].startswith("Qwen2.5-7B"))
        self.assertGreater(m7b["size_bytes"], 4000000000)
        self.assertTrue(m7b["url"].startswith("https://huggingface.co/"))
        self.assertTrue(m7b["filename"].endswith(".gguf"))

    def test_acceleration_mode_toggle(self):
        """Verifies getting and setting acceleration mode between GPU and CPU."""
        original_mode = self.coach.get_acceleration_mode()
        try:
            self.coach.set_acceleration_mode("cpu")
            self.assertEqual(self.coach.get_acceleration_mode(), "cpu")

            self.coach.set_acceleration_mode("gpu")
            self.assertEqual(self.coach.get_acceleration_mode(), "gpu")
        finally:
            self.coach.set_acceleration_mode(original_mode)

    def test_preferred_model_selection(self):
        """Verifies that setting a preferred model key succeeds."""
        self.assertTrue(self.coach.set_preferred_model("7b"))
        self.assertTrue(self.coach.set_preferred_model("3b"))
        self.assertFalse(self.coach.set_preferred_model("invalid_model_key"))

    def tearDown(self):
        self.coach.shutdown_engine()


if __name__ == "__main__":
    unittest.main()

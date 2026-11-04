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
        self.assertIn("Acoustic Diagnosis", debrief)
        self.assertIn("Targeted Micro-Drill", debrief)
        self.assertIn("Priority Directive for Take #2", debrief)
        self.assertIn("Transposing from +0 to **-1**", debrief)
        # Verify the raw highlight / struggle cards were NOT duplicated in the debrief text
        self.assertNotIn("🌟 **What Clicked**", debrief)
        self.assertNotIn("⚠️ **Where It Slipped**", debrief)

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

    def test_chat_prompt_assembly(self):
        """Verifies that chat prompt includes rhythm latency, song identity, debrief memory, and avoids duplicate user turns."""
        report = {
            "overall_score": 82,
            "pitch_accuracy_pct": 89.0,
            "intonation_cents": -4.2,
            "intonation_verdict": "Flat",
            "rhythm_offset_ms": 28.5,
            "rhythm_verdict": "Dragging Behind",
            "resonance_verdict": "Ring Present",
            "singers_formant_ratio": 0.42,
            "breath_support_pct": 78.0,
            "breath_support_verdict": "Stable",
            "onset_attack_verdict": "Clean Onset",
            "vibrato_present": True,
            "vibrato_rate_hz": 5.8,
            "vibrato_depth_cents": 45.0,
            "energy_steadiness_pct": 86.0,
            "dynamics_verdict": "Even",
            "take_min_note": "A2",
            "take_max_note": "F4",
            "take_range_semitones": 20.0,
            "current_key_shift": 0,
            "recommended_key_shift": -1,
            "coach_feedback": ["Attack onsets lagged slightly behind the beat transients."],
            "phrase_highlights": [{"timestamp": "0:15", "note_span": "C3 -> E3", "detail": "Locked into the pocket."}],
            "phrase_struggles": [{"timestamp": "0:42", "note_span": "F4", "detail": "Started line late.", "cue": "Anticipate onset on 4-and."}],
            "pitch_scoop_pct": 33.3,
            "attack_velocity_verdict": "Stylistic Occasional Scooping",
            "dynamic_contrast_db": 14.5,
            "dynamic_arc_verdict": "Wide Dynamic Story Arc",
            "vibrato_bloom_ms": 380.0,
            "vibrato_style_verdict": "Contemporary Terminal Bloom"
        }
        track = {"title": "Don't Stop Believin'", "artist": "Journey"}
        user_msg = "how was my timing for lines?"
        history = [
            {"role": "user", "content": "hello coach"},
            {"role": "assistant", "content": "Ready when you are!"},
            {"role": "user", "content": user_msg}
        ]
        debrief_txt = "🔬 Acoustic Diagnosis: Solid support. 🛠️ Targeted Micro-Drill: Staccato."

        lyric_alignment = {
            "status": "aligned",
            "word_accuracy_pct": 95.0,
            "total_lines": 8,
            "lines_covered": 8,
            "summary_text": "All 8 lines delivered on cue.",
            "line_details": [
                {"timestamp": "0:12", "line": "Just a small town girl", "sung": True, "sync_offset_ms": -10.0, "sync_verdict": "Pocket"}
            ]
        }

        prompt = self.coach._build_chat_prompt(
            user_msg,
            history,
            report,
            track,
            debrief_text=debrief_txt,
            lyric_alignment=lyric_alignment
        )

        # 1. Timing & Performer telemetry metrics must be present
        self.assertIn("+28.5 ms", prompt)
        self.assertIn("Dragging Behind", prompt)
        self.assertIn("33.3%", prompt)
        self.assertIn("14.5 dB", prompt)
        self.assertIn("Contemporary Terminal Bloom", prompt)

        # 2. Track identity and debrief memory must be present
        self.assertIn("Don't Stop Believin'", prompt)
        self.assertIn("Journey", prompt)
        self.assertIn("Acoustic Diagnosis: Solid support", prompt)

        # 3. Partitioned Lyrics & Grounding Directive must be present
        self.assertIn("[PARTITION: LYRICS_ALIGNMENT]", prompt)
        self.assertIn("Just a small town girl", prompt)
        self.assertIn("CRITICAL GROUNDING DIRECTIVE", prompt)

        # 4. User message must NOT be duplicated consecutively
        self.assertEqual(prompt.count("how was my timing for lines?"), 1)

    def test_debrief_prompt_assembly(self):
        """Verifies that debrief prompt includes rhythm metrics, vibrato, and key telemetry."""
        report = {
            "overall_score": 85,
            "pitch_accuracy_pct": 90.0,
            "intonation_cents": 2.0,
            "intonation_verdict": "On Pitch",
            "rhythm_offset_ms": -12.0,
            "rhythm_verdict": "Rushing Ahead",
            "resonance_verdict": "Ring Present",
            "singers_formant_ratio": 0.45,
            "breath_support_pct": 85.0,
            "breath_support_verdict": "Solid",
            "onset_attack_verdict": "Balanced",
            "vibrato_present": True,
            "vibrato_rate_hz": 6.0,
            "vibrato_depth_cents": 50.0,
            "vibrato_style_verdict": "Contemporary Terminal Bloom",
            "dynamic_contrast_db": 12.0,
            "dynamic_arc_verdict": "Natural Dynamic Arc",
            "energy_steadiness_pct": 90.0,
            "dynamics_verdict": "Steady",
            "sibilance_verdict": "Clean",
            "vocal_fry_pct": 3.0,
            "vocal_fry_verdict": "Low",
            "take_min_note": "C3",
            "take_max_note": "G4",
            "current_key_shift": 0,
            "recommended_key_shift": 0,
            "coach_feedback": ["Solid pitch accuracy."],
            "phrase_highlights": [],
            "phrase_struggles": []
        }
        track = {"title": "Sweet Caroline", "artist": "Neil Diamond"}
        prompt = self.coach._build_debrief_prompt(report, track, profile=None)
        self.assertIn("-12.0 ms", prompt)
        self.assertIn("Rushing Ahead", prompt)
        self.assertIn("6.0 Hz", prompt)
        self.assertIn("Sweet Caroline", prompt)
        self.assertIn("12.0 dB", prompt)
        self.assertIn("Contemporary Terminal Bloom", prompt)

    def tearDown(self):
        self.coach.shutdown_engine()


if __name__ == "__main__":
    unittest.main()

"""
Tests for Tiki Rehearsal Lounge Vocal Take Analyzer & Smart Key Recommendation Engine.
"""
import unittest
from audio.calibration import VocalProfile
from audio.vocal_analyzer import VocalTakeAnalyzer, TakeAnalysisReport


class TestVocalTakeAnalyzer(unittest.TestCase):
    def setUp(self):
        # Calibrated Tenor profile with comfortable tessitura ceiling at A4 (MIDI 69)
        self.profile = VocalProfile(
            anchor_hz=146.83,  # D3
            anchor_note="D3",
            range_min_hz=110.0,  # A2
            range_min_note="A2",
            range_max_hz=523.25, # C5
            range_max_note="C5",
            range_semitones=27.0,
            tessitura_min_hz=196.0,  # G3
            tessitura_min_note="G3",
            tessitura_max_hz=440.0, # A4
            tessitura_max_note="A4",
            vocal_type="Tenor",
            calibrated_at="2026-11-03T10:00:00"
        )
        self.analyzer = VocalTakeAnalyzer(vocal_profile=self.profile)

    def test_smart_key_recommendation_original_key(self):
        # Song melody peak matches singer's tessitura ceiling (A4)
        shift, reason = self.analyzer.recommend_key_for_track({"max_midi": 69.0})
        self.assertEqual(shift, 0)
        self.assertIn("Original key is optimal", reason)

    def test_smart_key_recommendation_pitch_down(self):
        # Song melody peaks at C5 (MIDI 72), 3 semitones above singer's A4 ceiling
        shift, reason = self.analyzer.recommend_key_for_track({"max_midi": 72.0})
        self.assertEqual(shift, -3)
        self.assertIn("Shift -3", reason)

    def test_smart_key_recommendation_clamped_bound(self):
        # Song melody peaks at E5 (MIDI 76), 7 semitones higher
        # Must be strictly clamped to -3 semitones
        shift, reason = self.analyzer.recommend_key_for_track({"max_midi": 76.0})
        self.assertEqual(shift, -3)

    def test_smart_key_recommendation_pitch_up(self):
        # Song melody peaks low at F4 (MIDI 65), 4 semitones below A4
        # Recommends shifting up, clamped to +3
        shift, reason = self.analyzer.recommend_key_for_track({"max_midi": 65.0})
        self.assertEqual(shift, 3)
        self.assertIn("Shift +3", reason)

    def test_report_card_serialization(self):
        report = TakeAnalysisReport(
            pitch_accuracy_pct=88.5,
            intonation_cents=4.2,
            intonation_verdict="In Tune",
            rhythm_offset_ms=12.0,
            rhythm_verdict="Locked On",
            vibrato_rate_hz=5.8,
            vibrato_depth_cents=45.0,
            vibrato_present=True,
            take_min_note="G3",
            take_max_note="A4",
            take_range_semitones=14.0,
            current_key_shift=0,
            recommended_key_shift=-1,
            key_recommendation_reason="Lowering 1 semitone protects high vocal peak",
            resonance_verdict="Warm & Balanced",
            singers_formant_ratio=0.85,
            breath_support_pct=85.0,
            breath_support_verdict="Rock Solid",
            onset_attack_verdict="Balanced Clean Onsets",
            balanced_onsets_pct=80.0,
            energy_steadiness_pct=90.0,
            dynamics_verdict="Even & Controlled Dynamics",
            sibilance_ratio=0.15,
            sibilance_verdict="Crisp & Articulate",
            vocal_fry_pct=5.0,
            vocal_fry_verdict="Clean Core Phonation",
            overall_score=86,
            coach_feedback=["Great breath control", "Consistent pitch accuracy"],
            pitch_scoop_pct=12.5,
            attack_velocity_verdict="Clean Direct Attacks (Precise Onsets)",
            dynamic_contrast_db=15.2,
            dynamic_arc_verdict="Wide Dynamic Story Arc (High Emotional Contrast)",
            vibrato_bloom_ms=360.0,
            vibrato_style_verdict="Contemporary Terminal Bloom (Straight into Vibrato)"
        )
        d = report.to_dict()
        self.assertEqual(d["overall_score"], 86)
        self.assertEqual(d["pitch_accuracy_pct"], 88.5)
        self.assertEqual(d["recommended_key_shift"], -1)
        self.assertEqual(d["pitch_scoop_pct"], 12.5)
        self.assertEqual(d["dynamic_contrast_db"], 15.2)
        self.assertEqual(d["vibrato_bloom_ms"], 360.0)
        self.assertEqual(d["vibrato_style_verdict"], "Contemporary Terminal Bloom (Straight into Vibrato)")

    def test_extended_telemetry_dsp_methods(self):
        import numpy as np
        # 1. Test clean attack (no scooping)
        clean_pitches = np.array([440.0] * 30 + [0.0] * 10 + [440.0] * 30)
        scoop_pct, verdict = self.analyzer._analyze_attack_velocity_and_scoop(clean_pitches)
        self.assertEqual(scoop_pct, 0.0)
        self.assertIn("Clean Direct Attacks", verdict)

        # 2. Test dynamic contrast
        sr = 22050
        audio = np.concatenate([np.ones(sr) * 0.05, np.ones(sr) * 0.5])
        pitches = np.ones(len(audio) // 330) * 440.0
        contrast_db, arc_verdict = self.analyzer._analyze_dynamic_arc(audio, sr, pitches, hop_ms=15.0)
        self.assertGreater(contrast_db, 10.0)

        # 3. Test vibrato bloom detection
        # Straight-tone
        delay_ms, bloom_verdict = self.analyzer._analyze_vibrato_bloom(clean_pitches, vib_present=False)
        self.assertEqual(delay_ms, 0.0)
        self.assertIn("Straight-Tone", bloom_verdict)


if __name__ == "__main__":
    unittest.main()

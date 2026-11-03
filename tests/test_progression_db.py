"""
Tests for Tiki Rehearsal Lounge Rehearsal Progression Database & Auto-Pruner.
"""
import unittest
import tempfile
import shutil
from pathlib import Path

from db.progression_db import (
    ProgressionDatabase,
    MAX_TAKES_PER_SONG,
    MAX_TOTAL_TAKES
)


class TestProgressionDatabase(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.db_path = self.test_dir / "test_progression.db"
        self.db = ProgressionDatabase(db_path=self.db_path)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_schema_and_take_insertion(self):
        take_id = self.db.record_take(
            track_title="Blue Hawaii",
            overall_score=85,
            pitch_accuracy_pct=88.5,
            intonation_cents=4.2,
            rhythm_offset_ms=-12.0,
            breath_support_pct=80.0,
            singers_formant_ratio=1.45,
            balanced_onsets_pct=90.0,
            track_id=1,
            track_artist="Elvis Presley",
            key_shift=1
        )
        self.assertGreater(take_id, 0)

        history = self.db.get_song_history("Blue Hawaii")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["overall_score"], 85)
        self.assertEqual(history[0]["track_artist"], "Elvis Presley")

    def test_per_song_auto_pruning(self):
        # Insert 18 takes for the same song (limit is 15)
        for i in range(1, 19):
            self.db.record_take(
                track_title="Tiny Bubbles",
                overall_score=60 + i,
                pitch_accuracy_pct=70.0 + i,
                intonation_cents=0.0
            )

        count = self.db.get_song_take_count("Tiny Bubbles")
        self.assertEqual(count, MAX_TAKES_PER_SONG)

        # Ensure the oldest takes were pruned (the latest takes remain)
        history = self.db.get_song_history("Tiny Bubbles", limit=20)
        self.assertEqual(len(history), MAX_TAKES_PER_SONG)
        # Oldest remaining should be take 4 (score 64)
        self.assertEqual(history[0]["overall_score"], 64)
        # Newest should be take 18 (score 78)
        self.assertEqual(history[-1]["overall_score"], 78)

    def test_build_llm_progression_context(self):
        # Baseline take
        ctx1 = self.db.build_llm_progression_context(
            track_title="Beyond the Reef",
            current_score=72,
            current_pitch_acc=75.0,
            current_breath=70.0
        )
        self.assertEqual(ctx1["song_take_number"], 1)
        self.assertFalse(ctx1["has_prior_history"])
        self.assertEqual(ctx1["delta_score"], 0)

        # Record take 1
        self.db.record_take(
            track_title="Beyond the Reef",
            overall_score=72,
            pitch_accuracy_pct=75.0,
            intonation_cents=0.0,
            breath_support_pct=70.0
        )

        # Take 2 context
        ctx2 = self.db.build_llm_progression_context(
            track_title="Beyond the Reef",
            current_score=80,
            current_pitch_acc=82.5,
            current_breath=80.0
        )
        self.assertEqual(ctx2["song_take_number"], 2)
        self.assertTrue(ctx2["has_prior_history"])
        self.assertEqual(ctx2["delta_score"], 8)
        self.assertEqual(ctx2["delta_pitch_pct"], 7.5)
        self.assertEqual(len(ctx2["recent_take_summaries"]), 1)

    def test_clear_history(self):
        self.db.record_take("Song A", 80, 85.0, 0.0)
        self.db.record_take("Song B", 90, 95.0, 0.0)

        stats = self.db.get_lifetime_stats()
        self.assertEqual(stats["total_takes"], 2)
        self.assertEqual(stats["unique_songs"], 2)

        # Clear only Song A
        self.db.clear_history("Song A")
        self.assertEqual(self.db.get_song_take_count("Song A"), 0)
        self.assertEqual(self.db.get_song_take_count("Song B"), 1)

        # Clear all
        self.db.clear_history()
        stats = self.db.get_lifetime_stats()
        self.assertEqual(stats["total_takes"], 0)


if __name__ == "__main__":
    unittest.main()

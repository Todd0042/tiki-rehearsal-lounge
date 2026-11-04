"""
Tests for Tiki Rehearsal Lounge Lyric Manager & Singer Alignment Engine.
"""
import unittest
import tempfile
from pathlib import Path
import numpy as np

from audio.lyric_manager import LyricManager, LyricTrack, LyricLine, LyricAlignmentReport


class TestLyricManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_lyrics.db"
        self.manager = LyricManager(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_lrc_parsing(self):
        sample_lrc = """
[00:12.50] Where it began, I can't begin to knowin'
[00:18.20] But then I know it's growin' strong
[00:24.00] Was in the spring, and spring became the summer
[00:30.15] Who'd have believed you'd come along
        """
        lines = self.manager.parse_lrc_string(sample_lrc)
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[0].time_ms, 12500)
        self.assertEqual(lines[0].text, "Where it began, I can't begin to knowin'")
        self.assertEqual(lines[1].time_ms, 18200)
        self.assertEqual(lines[3].time_ms, 30150)

    def test_sqlite_lyrics_cache_persistence(self):
        track_key = "neil_diamond__sweet_caroline"
        lt = LyricTrack(
            track_key=track_key,
            title="Sweet Caroline",
            artist="Neil Diamond",
            lines=[
                LyricLine(time_ms=12000, text="Where it began"),
                LyricLine(time_ms=16000, text="I can't begin to knowin'")
            ],
            plain_lyrics="Where it began\nI can't begin to knowin'",
            source="test_source"
        )
        self.manager.save_lyrics_to_cache(lt)

        # Retrieve from fresh manager instance sharing same SQLite DB
        fresh_mgr = LyricManager(db_path=self.db_path)
        cached = fresh_mgr.get_cached_lyrics(track_key)
        self.assertIsNotNone(cached)
        self.assertEqual(cached.title, "Sweet Caroline")
        self.assertEqual(cached.artist, "Neil Diamond")
        self.assertEqual(len(cached.lines), 2)
        self.assertEqual(cached.lines[0].time_ms, 12000)
        self.assertEqual(cached.lines[1].text, "I can't begin to knowin'")

    def test_align_singer_take_coverage(self):
        # Create synthetic lyric track
        lt = LyricTrack(
            track_key="test_song",
            title="Test Song",
            artist="Singer",
            lines=[
                LyricLine(time_ms=1000, text="Line One", duration_ms=2000),
                LyricLine(time_ms=5000, text="Line Two", duration_ms=2000),
                LyricLine(time_ms=9000, text="Line Three", duration_ms=2000)
            ],
            source="mock"
        )

        # Singer audio with voiced phrases at Line 1 (1.1s - 2.8s) and Line 2 (5.0s - 6.5s)
        # Missed Line 3 (silence from 7.0s onwards)
        sr = 22050
        duration_s = 12.0
        s_times = np.arange(0, duration_s, 0.015)
        s_pitches = np.zeros_like(s_times)

        # Voiced at 1.1s to 2.8s (Line 1, 100ms lag)
        mask1 = (s_times >= 1.1) & (s_times <= 2.8)
        s_pitches[mask1] = 440.0

        # Voiced at 4.95s to 6.8s (Line 2, -50ms anticipation)
        mask2 = (s_times >= 4.95) & (s_times <= 6.8)
        s_pitches[mask2] = 440.0

        singer_audio = np.zeros(int(sr * duration_s))

        report = self.manager.align_singer_take(
            singer_audio=singer_audio,
            sr=sr,
            s_times=s_times,
            s_pitches=s_pitches,
            lyric_track=lt
        )

        self.assertEqual(report.status, "aligned")
        self.assertEqual(report.total_lines, 3)
        self.assertEqual(report.lines_covered, 2)
        self.assertAlmostEqual(report.word_accuracy_pct, 66.7, places=1)
        self.assertEqual(len(report.missed_phrases), 1)
        self.assertIn("Line Three", report.missed_phrases[0])
        self.assertIn("Line One", report.line_details[0]["line"])
        self.assertTrue(report.line_details[0]["sung"])
        self.assertFalse(report.line_details[2]["sung"])

    def test_stt_engine_availability_and_structure(self):
        from audio.stt_engine import is_stt_available, find_whisper_binary
        binary = find_whisper_binary()
        self.assertIsNotNone(binary)
        self.assertTrue(is_stt_available())


if __name__ == "__main__":
    unittest.main()

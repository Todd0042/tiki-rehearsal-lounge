"""
Tests for Tiki Rehearsal Lounge Adaptive Parallel Analyzer & Partitioned Storage.
"""
import unittest
import tempfile
from pathlib import Path
import wave
import struct
import numpy as np

from audio.parallel_analyzer import ParallelVocalTakeAnalyzer, get_optimal_worker_count
from audio.lyric_manager import LyricTrack, LyricLine
from storage.take_partitions import (
    save_take_partitions,
    load_take_toc,
    load_take_partition,
    get_most_recent_take_dir,
    load_take_report_and_lyrics
)


def create_synthetic_wav(file_path: Path, duration_s: float = 3.0, freq_hz: float = 440.0, sr: int = 22050):
    total_samples = int(duration_s * sr)
    with wave.open(str(file_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        for i in range(total_samples):
            val = int(32767.0 * 0.5 * np.sin(2.0 * np.pi * freq_hz * i / sr))
            wf.writeframes(struct.pack("<h", val))


class TestParallelAnalyzer(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.analyzer = ParallelVocalTakeAnalyzer(max_workers=2)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_worker_count_bounds(self):
        workers = get_optimal_worker_count()
        self.assertGreaterEqual(workers, 1)
        self.assertLessEqual(workers, 24)

    def test_analyze_take_parallel_synthetic(self):
        wav_path = self.temp_path / "test_singer.wav"
        create_synthetic_wav(wav_path, duration_s=2.5, freq_hz=440.0)

        lt = LyricTrack(
            track_key="test_song",
            title="Test Track",
            artist="Test Artist",
            lines=[
                LyricLine(time_ms=500, text="Acoustic line one", duration_ms=1500)
            ],
            source="mock"
        )

        report, lyric_rep = self.analyzer.analyze_take_parallel(
            singer_wav=wav_path,
            key_shift=0,
            lyric_track=lt
        )

        self.assertGreater(report.overall_score, 0)
        self.assertTrue(len(report.take_min_note) > 0)
        self.assertTrue(len(report.take_max_note) > 0)
        self.assertEqual(lyric_rep.status, "aligned")
        self.assertGreaterEqual(lyric_rep.lines_covered, 1)

    def test_partitioned_take_storage_and_toc(self):
        wav_path = self.temp_path / "test_take.wav"
        create_synthetic_wav(wav_path, duration_s=1.5, freq_hz=440.0)

        report, lyric_rep = self.analyzer.analyze_take_parallel(
            singer_wav=wav_path,
            key_shift=0
        )

        track_info = {"title": "Don't Stop Believin'", "artist": "Journey", "video_id": "1k8craCGpgs"}
        take_dir = save_take_partitions(
            report=report,
            lyric_report=lyric_rep,
            track_info=track_info,
            take_wav=wav_path,
            dest_base_dir=self.temp_path
        )

        self.assertTrue(take_dir.exists())
        self.assertTrue((take_dir / "toc.json").is_file())
        self.assertTrue((take_dir / "acoustic_core.json").is_file())
        self.assertTrue((take_dir / "phrase_timeline.json").is_file())
        self.assertTrue((take_dir / "lyrics_alignment.json").is_file())
        self.assertTrue((take_dir / "vocal_health_diagnostics.json").is_file())

        toc = load_take_toc(take_dir)
        self.assertIsNotNone(toc)
        self.assertEqual(toc["track"]["title"], "Don't Stop Believin'")
        self.assertIn("acoustic_core", toc["partitions"])
        self.assertIn("lyrics_alignment", toc["partitions"])

        part = load_take_partition(take_dir, "acoustic_core")
        self.assertIsNotNone(part)
        self.assertEqual(part["overall_score"], report.overall_score)
        self.assertIn("timeline_series", part)
        self.assertIn("vocal_registers", part)

        phrase_part = load_take_partition(take_dir, "phrase_timeline")
        self.assertIsNotNone(phrase_part)
        self.assertIn("all_phrases", phrase_part)
        self.assertIn("total_phrases", phrase_part)

        diag_part = load_take_partition(take_dir, "vocal_health_diagnostics")
        self.assertIsNotNone(diag_part)
        self.assertIn("fatigue_risk_score", diag_part)

    def test_get_most_recent_take_dir_and_reconstruction(self):
        # Verify empty directory returns None
        empty_dir = self.temp_path / "empty_takes"
        empty_dir.mkdir()
        self.assertIsNone(get_most_recent_take_dir(empty_dir))

        wav_path = self.temp_path / "test_recon.wav"
        create_synthetic_wav(wav_path, duration_s=1.2, freq_hz=440.0)

        report, lyric_rep = self.analyzer.analyze_take_parallel(
            singer_wav=wav_path,
            key_shift=2
        )
        report.overall_score = 92
        track_info = {"title": "Hotel California", "artist": "Eagles", "duration": 390}

        saved_dir = save_take_partitions(
            report=report,
            lyric_report=lyric_rep,
            track_info=track_info,
            take_wav=wav_path,
            dest_base_dir=empty_dir
        )

        most_recent = get_most_recent_take_dir(empty_dir)
        self.assertEqual(most_recent, saved_dir)

        recon_report, recon_lyrics, recon_track = load_take_report_and_lyrics(saved_dir)
        self.assertIsNotNone(recon_report)
        self.assertEqual(recon_report.overall_score, 92)
        self.assertEqual(recon_report.current_key_shift, 2)
        self.assertEqual(recon_report.pitch_accuracy_pct, report.pitch_accuracy_pct)
        self.assertIsNotNone(recon_lyrics)
        self.assertEqual(recon_lyrics.status, lyric_rep.status)
        self.assertEqual(recon_track["title"], "Hotel California")


if __name__ == "__main__":
    unittest.main()

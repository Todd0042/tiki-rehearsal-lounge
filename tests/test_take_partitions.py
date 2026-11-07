"""
Tests for the partitioned take storage `summary.json` partition (the one-way input of the
optional AI side panel) and the AI panel activation rules.
"""
import json
import tempfile
import unittest
from dataclasses import fields, MISSING
from pathlib import Path

from audio.vocal_analyzer import TakeAnalysisReport
from config import resolve_ai_panel_active, get_ai_panel_preference, AI_PANEL_WIDTH, UI_COMPACT_SIZE, UI_AI_EXPANDED_SIZE
from storage.take_partitions import (
    save_take_partitions,
    load_take_summary,
    load_take_toc,
    load_take_report_and_lyrics,
    SUMMARY_PARTITION_FILE,
)


def make_report(**overrides) -> TakeAnalysisReport:
    """Builds a TakeAnalysisReport, filling every required field by annotation type."""
    values = {}
    for f in fields(TakeAnalysisReport):
        if f.default is not MISSING or f.default_factory is not MISSING:  # type: ignore[misc]
            continue
        ann = str(f.type)
        if "bool" in ann:
            values[f.name] = False
        elif "float" in ann:
            values[f.name] = 0.0
        elif "int" in ann:
            values[f.name] = 0
        elif "List" in ann or "list" in ann:
            values[f.name] = []
        elif "Dict" in ann or "dict" in ann:
            values[f.name] = {}
        else:
            values[f.name] = "N/A"
    values.update(overrides)
    return TakeAnalysisReport(**values)


class TestSummaryPartition(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _save(self, **kw):
        report = make_report(
            overall_score=82,
            pitch_accuracy_pct=88.4,
            phrase_highlights=[{"timestamp": "0:10", "note_span": "C4", "detail": "Great", "extra": "x"}],
            phrase_struggles=[{"timestamp": "0:30", "note_span": "G4", "detail": "Sharp", "cue": "Drop jaw"}],
            auditory_appeal_score=77,
            auditory_appeal_verdict="Harmonically Consonant",
            contour_similarity_pct=84.0,
            phrase_shape_verdict="Dynamic Contour Mirroring",
            style_genre="blues",
        )
        return save_take_partitions(
            report, None, {"title": "Test Song", "artist": "Tester"}, dest_base_dir=self.base, **kw
        )

    def test_summary_is_written_and_listed_in_toc(self):
        take_dir = self._save(vocal_profile={"vocal_type": "Tenor"}, progression={"song_take_number": 3})
        self.assertTrue((take_dir / SUMMARY_PARTITION_FILE).is_file())

        toc = load_take_toc(take_dir)
        self.assertIn("summary", toc["partitions"])
        self.assertEqual(toc["partitions"]["summary"]["file"], SUMMARY_PARTITION_FILE)

        summary = load_take_summary(take_dir)
        self.assertEqual(summary["scores"]["overall_score"], 82)
        self.assertEqual(summary["scores"]["auditory_appeal_score"], 77)
        self.assertEqual(summary["scores"]["contour_similarity_pct"], 84.0)
        self.assertEqual(summary["scores"]["phrase_shape_verdict"], "Dynamic Contour Mirroring")
        self.assertEqual(summary["scores"]["style_genre"], "blues")
        self.assertEqual(summary["track"]["title"], "Test Song")
        self.assertEqual(summary["vocal_profile"], {"vocal_type": "Tenor"})
        self.assertEqual(summary["progression"], {"song_take_number": 3})
        # Compact phrase entries only carry the whitelisted keys
        self.assertNotIn("extra", summary["phrase_highlights"][0])
        self.assertEqual(summary["phrase_struggles"][0]["cue"], "Drop jaw")

    def test_summary_optional_snapshots_default_to_none(self):
        take_dir = self._save()
        summary = load_take_summary(take_dir)
        self.assertIsNone(summary["vocal_profile"])
        self.assertIsNone(summary["progression"])

    def test_core_partitions_still_reload_without_ai(self):
        """Raw analysis must be fully reconstructable from partitions alone (AI optional)."""
        take_dir = self._save()
        report, lyric, track = load_take_report_and_lyrics(take_dir)
        self.assertIsNotNone(report)
        self.assertEqual(report.overall_score, 82)
        self.assertEqual(report.contour_similarity_pct, 84.0)
        self.assertEqual(report.phrase_shape_verdict, "Dynamic Contour Mirroring")
        self.assertEqual(report.style_genre, "blues")
        self.assertEqual(track["title"], "Test Song")

    def test_legacy_take_without_summary_returns_none(self):
        take_dir = self._save()
        (take_dir / SUMMARY_PARTITION_FILE).unlink()
        self.assertIsNone(load_take_summary(take_dir))
        report, _, _ = load_take_report_and_lyrics(take_dir)
        self.assertIsNotNone(report)

    def test_summary_json_is_valid_json(self):
        take_dir = self._save()
        json.loads((take_dir / SUMMARY_PARTITION_FILE).read_text(encoding="utf-8"))


class TestAIPanelActivation(unittest.TestCase):
    def test_geometry_constants(self):
        self.assertEqual(UI_COMPACT_SIZE, (1180, 780))
        self.assertEqual(UI_AI_EXPANDED_SIZE, (1560, 780))
        self.assertEqual(AI_PANEL_WIDTH, 380)

    def test_auto_mode_follows_model_availability(self):
        self.assertTrue(resolve_ai_panel_active(True, cfg={}))
        self.assertFalse(resolve_ai_panel_active(False, cfg={}))
        self.assertFalse(resolve_ai_panel_active(False, cfg={"ai_panel_enabled": None}))

    def test_explicit_preference_wins(self):
        self.assertFalse(resolve_ai_panel_active(True, cfg={"ai_panel_enabled": False}))
        self.assertTrue(resolve_ai_panel_active(False, cfg={"ai_panel_enabled": True}))

    def test_non_boolean_preference_is_treated_as_auto(self):
        self.assertIsNone(get_ai_panel_preference({"ai_panel_enabled": "yes"}))
        self.assertTrue(resolve_ai_panel_active(True, cfg={"ai_panel_enabled": "yes"}))


if __name__ == "__main__":
    unittest.main()

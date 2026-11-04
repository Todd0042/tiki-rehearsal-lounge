"""
Tiki Rehearsal Lounge - Partitioned Take Storage & Table of Contents (TOC)
==========================================================================
Manages structured, modular JSON persistence for every rehearsal take.
Creates a partitioned directory per take with a master Table of Contents (toc.json)
so both the UI and AI Coach Tiki can read and ground their evaluations.

Partitions:
1. toc.json                      -> Master Table of Contents & Manifest
2. acoustic_core.json            -> Objective DSP acoustic telemetry
3. phrase_timeline.json          -> Timestamped highlights and struggles
4. lyrics_alignment.json         -> Ground-truth lyrics vs singer phrasing
5. vocal_health_diagnostics.json -> Root-cause diagnosis & 30s micro-drills
"""

from __future__ import annotations
import os
import re
import json
import shutil
import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from config import TAKES_DIR
from audio.vocal_analyzer import TakeAnalysisReport
from audio.lyric_manager import LyricAlignmentReport


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name)
    cleaned = re.sub(r"\s+", "_", cleaned).strip()
    return cleaned if cleaned else "take"


def save_take_partitions(
    report: TakeAnalysisReport,
    lyric_report: Optional[LyricAlignmentReport] = None,
    track_info: Optional[Dict[str, Any]] = None,
    take_wav: Optional[Path] = None,
    dest_base_dir: Optional[Path] = None
) -> Path:
    """
    Saves a completed rehearsal take into a structured directory with partitioned JSON files
    and a master toc.json manifest.
    """
    base_dir = dest_base_dir or TAKES_DIR
    base_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.datetime.now()
    time_str = now.strftime("%Y%m%d_%H%M%S")
    track_title = track_info.get("title", "rehearsal") if track_info else "rehearsal"
    safe_title = sanitize_filename(track_title)[:24]
    take_id = f"take_{time_str}_{safe_title}"

    take_dir = base_dir / take_id
    take_dir.mkdir(parents=True, exist_ok=True)

    # 1. Copy take WAV into take directory if available
    saved_wav_name = ""
    if take_wav and Path(take_wav).is_file():
        dest_wav = take_dir / "singer_take.wav"
        try:
            shutil.copy2(take_wav, dest_wav)
            saved_wav_name = "singer_take.wav"
        except Exception:
            saved_wav_name = str(take_wav)

    # 2. Partition: acoustic_core.json
    # 2. Partition: acoustic_core.json
    acoustic_core = {
        "overall_score": report.overall_score,
        "pitch_accuracy_pct": report.pitch_accuracy_pct,
        "intonation_cents": report.intonation_cents,
        "intonation_verdict": report.intonation_verdict,
        "pitch_scoop_pct": report.pitch_scoop_pct,
        "attack_velocity_verdict": report.attack_velocity_verdict,
        "rhythm_offset_ms": report.rhythm_offset_ms,
        "rhythm_verdict": report.rhythm_verdict,
        "dynamic_contrast_db": report.dynamic_contrast_db,
        "dynamic_arc_verdict": report.dynamic_arc_verdict,
        "energy_steadiness_pct": report.energy_steadiness_pct,
        "dynamics_verdict": report.dynamics_verdict,
        "vibrato_rate_hz": report.vibrato_rate_hz,
        "vibrato_depth_cents": report.vibrato_depth_cents,
        "vibrato_present": report.vibrato_present,
        "vibrato_bloom_ms": report.vibrato_bloom_ms,
        "vibrato_style_verdict": report.vibrato_style_verdict,
        "singers_formant_ratio": report.singers_formant_ratio,
        "resonance_verdict": report.resonance_verdict,
        "breath_support_pct": report.breath_support_pct,
        "breath_support_verdict": report.breath_support_verdict,
        "onset_attack_verdict": report.onset_attack_verdict,
        "balanced_onsets_pct": report.balanced_onsets_pct,
        "sibilance_ratio": report.sibilance_ratio,
        "sibilance_verdict": report.sibilance_verdict,
        "vocal_fry_pct": report.vocal_fry_pct,
        "vocal_fry_verdict": report.vocal_fry_verdict,
        "take_min_note": report.take_min_note,
        "take_max_note": report.take_max_note,
        "take_range_semitones": report.take_range_semitones,
        "current_key_shift": report.current_key_shift,
        "recommended_key_shift": report.recommended_key_shift,
        "key_recommendation_reason": report.key_recommendation_reason,
        "coach_feedback": report.coach_feedback,
        "timeline_series": getattr(report, "timeline_series", {}),
        "vocal_registers": getattr(report, "vocal_registers", {}),
        "intonation_bias": getattr(report, "intonation_bias", {}),
        "auditory_appeal_score": getattr(report, "auditory_appeal_score", 0),
        "auditory_appeal_verdict": getattr(report, "auditory_appeal_verdict", "Harmonically Consonant"),
        "harmonic_consonance_pct": getattr(report, "harmonic_consonance_pct", 0.0)
    }
    with open(take_dir / "acoustic_core.json", "w", encoding="utf-8") as f:
        json.dump(acoustic_core, f, indent=2, ensure_ascii=False)

    # 3. Partition: phrase_timeline.json
    all_phrases = getattr(report, "all_phrases", []) or []
    phrase_timeline = {
        "phrase_highlights": report.phrase_highlights,
        "phrase_struggles": report.phrase_struggles,
        "all_phrases": all_phrases,
        "total_phrases": len(all_phrases),
        "highlights_count": len(report.phrase_highlights),
        "struggles_count": len(report.phrase_struggles)
    }
    with open(take_dir / "phrase_timeline.json", "w", encoding="utf-8") as f:
        json.dump(phrase_timeline, f, indent=2, ensure_ascii=False)

    # 4. Partition: lyrics_alignment.json
    if lyric_report:
        lyrics_data = lyric_report.to_dict()
    else:
        lyrics_data = {
            "status": "unavailable",
            "word_accuracy_pct": 0.0,
            "word_error_rate_pct": 0.0,
            "total_lines": 0,
            "lines_covered": 0,
            "timing_offset_ms": 0.0,
            "line_details": [],
            "missed_phrases": [],
            "summary_text": "No lyric alignment was processed for this take.",
            "total_target_words": 0,
            "total_sung_words": 0,
            "words_matched_count": 0,
            "missed_words": [],
            "word_timeline": [],
            "diction_clarity_pct": 0.0
        }
    with open(take_dir / "lyrics_alignment.json", "w", encoding="utf-8") as f:
        json.dump(lyrics_data, f, indent=2, ensure_ascii=False)

    # 5. Partition: vocal_health_diagnostics.json
    fatigue_score = int(round(
        (min(100.0, report.vocal_fry_pct) * 0.40) +
        (max(0.0, 75.0 - report.breath_support_pct) * 0.35) +
        (max(0.0, 100.0 - report.balanced_onsets_pct) * 0.25)
    ))
    diagnostics = {
        "fatigue_risk_score": fatigue_score,
        "fatigue_risk_verdict": "Low Vocal Cord Fatigue" if fatigue_score < 30 else ("Moderate Strain Detected" if fatigue_score < 60 else "High Vocal Fatigue / Rest Advised"),
        "laryngeal_tension_detected": bool(report.intonation_cents >= 18.0),
        "subglottic_pressure_decay": bool(report.breath_support_pct < 70.0 or report.intonation_cents <= -15.0),
        "pitch_scooping_frequent": bool(report.pitch_scoop_pct >= 35.0),
        "dynamic_compression_verdict": report.dynamics_verdict,
        "recommended_micro_drill": (
            "Clean Attack Staccato Drill" if report.pitch_scoop_pct >= 35.0 else
            ("Laryngeal Neutralizer (Puffy-Cheek 'GUG')" if report.intonation_cents >= 18.0 else
             ("SOVTE Straw/Hiss Phonation" if report.breath_support_pct < 75.0 else "Resonance Ring Vowel Drill"))
        ),
        "take_2_directive": (
            f"Transpose track to {report.recommended_key_shift:+d} semitones" if report.current_key_shift != report.recommended_key_shift else
            ("Anticipate breath on beat 4 for on-time attacks" if report.rhythm_offset_ms > 20.0 else
             ("Hold lower ribcage expansion to phrase endings" if report.breath_support_pct < 75.0 else "Maintain forward facial mask resonance"))
        )
    }
    with open(take_dir / "vocal_health_diagnostics.json", "w", encoding="utf-8") as f:
        json.dump(diagnostics, f, indent=2, ensure_ascii=False)

    # 6. Master Table of Contents (toc.json)
    lyric_status = lyrics_data.get("status", "unavailable")
    if lyric_status == "aligned":
        lyric_summary = f"{lyrics_data.get('words_matched_count', 0)}/{lyrics_data.get('total_target_words', 0)} words matched ({lyrics_data.get('word_accuracy_pct', 0)}%), sync {lyrics_data.get('timing_offset_ms', 0.0):+0.1f}ms"
    elif lyric_status == "transcribed_take_only":
        lyric_summary = f"Transcribed {lyrics_data.get('total_sung_words', 0)} words ({lyrics_data.get('diction_clarity_pct', 0)}% clarity)"
    else:
        lyric_summary = "Lyrics alignment not synced for this take"

    phrase_count_total = len(all_phrases) if all_phrases else (len(report.phrase_highlights) + len(report.phrase_struggles))
    phrase_summary = (
        f"{phrase_count_total} phrases tracked ({len(report.phrase_highlights)} highlights, {len(report.phrase_struggles)} focus moments)"
        if all_phrases else f"{len(report.phrase_highlights)} highlights, {len(report.phrase_struggles)} struggle focus moments"
    )

    toc = {
        "take_id": take_id,
        "created_at": now.isoformat(),
        "track": {
            "title": track_info.get("title", "Unknown") if track_info else "Unknown",
            "artist": track_info.get("artist", "Unknown") if track_info else "Unknown",
            "video_id": track_info.get("video_id", "") if track_info else "",
            "duration": track_info.get("duration", 0) if track_info else 0
        },
        "overall_score": report.overall_score,
        "audio_file": saved_wav_name,
        "partitions": {
            "acoustic_core": {
                "file": "acoustic_core.json",
                "status": "ready",
                "summary": f"Score {report.overall_score}/100, Pitch {report.pitch_accuracy_pct:.1f}%, Scoop {report.pitch_scoop_pct:.0f}%, Arc {report.dynamic_contrast_db:.1f} dB"
            },
            "phrase_timeline": {
                "file": "phrase_timeline.json",
                "status": "ready",
                "summary": phrase_summary
            },
            "lyrics_alignment": {
                "file": "lyrics_alignment.json",
                "status": lyric_status,
                "summary": lyric_summary
            },
            "vocal_health_diagnostics": {
                "file": "vocal_health_diagnostics.json",
                "status": "ready",
                "summary": f"Fatigue: {diagnostics['fatigue_risk_verdict']} | Drill: {diagnostics['recommended_micro_drill']}"
            }
        }
    }
    with open(take_dir / "toc.json", "w", encoding="utf-8") as f:
        json.dump(toc, f, indent=2, ensure_ascii=False)

    return take_dir


def load_take_toc(take_dir: Path) -> Optional[Dict[str, Any]]:
    """Loads and returns the toc.json manifest for a take directory."""
    toc_path = Path(take_dir) / "toc.json"
    if not toc_path.is_file():
        return None
    try:
        with open(toc_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_take_partition(take_dir: Path, partition_name: str) -> Optional[Dict[str, Any]]:
    """Loads a specific JSON partition file from a take directory."""
    filename = f"{partition_name}.json" if not partition_name.endswith(".json") else partition_name
    part_path = Path(take_dir) / filename
    if not part_path.is_file():
        return None
    try:
        with open(part_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def get_most_recent_take_dir(base_dir: Optional[Path] = None) -> Optional[Path]:
    """
    Finds the most recently recorded rehearsal take directory that contains a valid toc.json.
    """
    target_base = base_dir or TAKES_DIR
    if not target_base.is_dir():
        return None

    valid_takes: List[Tuple[float, Path]] = []
    try:
        for entry in target_base.iterdir():
            if entry.is_dir() and (entry / "toc.json").is_file() and (entry / "acoustic_core.json").is_file():
                mtime = (entry / "toc.json").stat().st_mtime
                valid_takes.append((mtime, entry))
    except Exception as e:
        print(f"[TakePartitions] Error scanning takes directory: {e}", file=sys.stderr)
        return None

    if not valid_takes:
        return None

    # Sort newest first
    valid_takes.sort(key=lambda x: x[0], reverse=True)
    return valid_takes[0][1]


def load_take_report_and_lyrics(
    take_dir: Path
) -> Tuple[Optional[TakeAnalysisReport], Optional[LyricAlignmentReport], Optional[Dict[str, Any]]]:
    """
    Reconstructs TakeAnalysisReport, LyricAlignmentReport, and track info from a take directory.
    """
    take_p = Path(take_dir)
    if not take_p.is_dir():
        return None, None, None

    toc = load_take_toc(take_p) or {}
    core = load_take_partition(take_p, "acoustic_core")
    if not core:
        return None, None, None

    phrases = load_take_partition(take_p, "phrase_timeline") or {}
    lyrics = load_take_partition(take_p, "lyrics_alignment") or {}

    report = TakeAnalysisReport(
        pitch_accuracy_pct=core.get("pitch_accuracy_pct", 0.0),
        intonation_cents=core.get("intonation_cents", 0.0),
        intonation_verdict=core.get("intonation_verdict", "N/A"),
        rhythm_offset_ms=core.get("rhythm_offset_ms", 0.0),
        rhythm_verdict=core.get("rhythm_verdict", "N/A"),
        vibrato_rate_hz=core.get("vibrato_rate_hz", 0.0),
        vibrato_depth_cents=core.get("vibrato_depth_cents", 0.0),
        vibrato_present=core.get("vibrato_present", False),
        take_min_note=core.get("take_min_note", "N/A"),
        take_max_note=core.get("take_max_note", "N/A"),
        take_range_semitones=core.get("take_range_semitones", 0.0),
        current_key_shift=core.get("current_key_shift", 0),
        recommended_key_shift=core.get("recommended_key_shift", 0),
        key_recommendation_reason=core.get("key_recommendation_reason", ""),
        resonance_verdict=core.get("resonance_verdict", "Balanced"),
        singers_formant_ratio=core.get("singers_formant_ratio", 0.0),
        breath_support_pct=core.get("breath_support_pct", 0.0),
        breath_support_verdict=core.get("breath_support_verdict", "N/A"),
        onset_attack_verdict=core.get("onset_attack_verdict", "N/A"),
        balanced_onsets_pct=core.get("balanced_onsets_pct", 0.0),
        energy_steadiness_pct=core.get("energy_steadiness_pct", 0.0),
        dynamics_verdict=core.get("dynamics_verdict", "N/A"),
        sibilance_ratio=core.get("sibilance_ratio", 0.0),
        sibilance_verdict=core.get("sibilance_verdict", "N/A"),
        vocal_fry_pct=core.get("vocal_fry_pct", 0.0),
        vocal_fry_verdict=core.get("vocal_fry_verdict", "N/A"),
        overall_score=core.get("overall_score", 0),
        coach_feedback=core.get("coach_feedback", []),
        phrase_highlights=phrases.get("phrase_highlights", []),
        phrase_struggles=phrases.get("phrase_struggles", []),
        all_phrases=phrases.get("all_phrases", []),
        pitch_scoop_pct=core.get("pitch_scoop_pct", 0.0),
        attack_velocity_verdict=core.get("attack_velocity_verdict", "Clean"),
        dynamic_contrast_db=core.get("dynamic_contrast_db", 0.0),
        dynamic_arc_verdict=core.get("dynamic_arc_verdict", "Natural"),
        vibrato_bloom_ms=core.get("vibrato_bloom_ms", 0.0),
        vibrato_style_verdict=core.get("vibrato_style_verdict", "Natural"),
        timeline_series=core.get("timeline_series", {}),
        vocal_registers=core.get("vocal_registers", {}),
        intonation_bias=core.get("intonation_bias", {}),
        auditory_appeal_score=core.get("auditory_appeal_score", 0),
        auditory_appeal_verdict=core.get("auditory_appeal_verdict", "Harmonically Consonant"),
        harmonic_consonance_pct=core.get("harmonic_consonance_pct", 0.0)
    )

    lyric_alignment = LyricAlignmentReport(
        status=lyrics.get("status", "unavailable"),
        word_accuracy_pct=lyrics.get("word_accuracy_pct", 0.0),
        word_error_rate_pct=lyrics.get("word_error_rate_pct", 0.0),
        total_lines=lyrics.get("total_lines", 0),
        lines_covered=lyrics.get("lines_covered", 0),
        timing_offset_ms=lyrics.get("timing_offset_ms", 0.0),
        line_details=lyrics.get("line_details", []),
        missed_phrases=lyrics.get("missed_phrases", []),
        summary_text=lyrics.get("summary_text", ""),
        total_target_words=lyrics.get("total_target_words", 0),
        total_sung_words=lyrics.get("total_sung_words", 0),
        words_matched_count=lyrics.get("words_matched_count", 0),
        missed_words=lyrics.get("missed_words", []),
        word_timeline=lyrics.get("word_timeline", []),
        diction_clarity_pct=lyrics.get("diction_clarity_pct", 0.0)
    )

    track_info = toc.get("track", {})
    return report, lyric_alignment, track_info

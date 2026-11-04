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
        "coach_feedback": report.coach_feedback
    }
    with open(take_dir / "acoustic_core.json", "w", encoding="utf-8") as f:
        json.dump(acoustic_core, f, indent=2, ensure_ascii=False)

    # 3. Partition: phrase_timeline.json
    phrase_timeline = {
        "phrase_highlights": report.phrase_highlights,
        "phrase_struggles": report.phrase_struggles,
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
            "total_lines": 0,
            "lines_covered": 0,
            "timing_offset_ms": 0.0,
            "line_details": [],
            "missed_phrases": [],
            "summary_text": "No lyric alignment was processed for this take."
        }
    with open(take_dir / "lyrics_alignment.json", "w", encoding="utf-8") as f:
        json.dump(lyrics_data, f, indent=2, ensure_ascii=False)

    # 5. Partition: vocal_health_diagnostics.json
    diagnostics = {
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
    lyric_summary = (
        f"{lyrics_data.get('lines_covered', 0)}/{lyrics_data.get('total_lines', 0)} lines sung ({lyrics_data.get('word_accuracy_pct', 0)}%)"
        if lyric_status == "aligned" else "Lyrics alignment not synced for this take"
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
                "summary": f"Score {report.overall_score}/100, Pitch {report.pitch_accuracy_pct}%, Scoop {report.pitch_scoop_pct:.0f}%, Arc {report.dynamic_contrast_db:.1f} dB"
            },
            "phrase_timeline": {
                "file": "phrase_timeline.json",
                "status": "ready",
                "summary": f"{len(report.phrase_highlights)} highlights, {len(report.phrase_struggles)} struggle focus moments"
            },
            "lyrics_alignment": {
                "file": "lyrics_alignment.json",
                "status": lyric_status,
                "summary": lyric_summary
            },
            "vocal_health_diagnostics": {
                "file": "vocal_health_diagnostics.json",
                "status": "ready",
                "summary": f"Drill: {diagnostics['recommended_micro_drill']} | Take #2: {diagnostics['take_2_directive']}"
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

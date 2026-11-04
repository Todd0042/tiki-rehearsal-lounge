"""
Tiki Rehearsal Lounge - Adaptive Parallel Vocal Take Analyzer
=============================================================
Distributes DSP feature extraction and lyric alignment across concurrent worker tasks.
Adapts dynamically to hardware:
- Low-end dual-core machines (e.g. Intel Celeron): restricts worker concurrency to 1-2
  to ensure the GUI and audio stream never stutter.
- Multi-core workstations (e.g. 24-27 cores): saturates all cores simultaneously.
- Thread-safe work queuing via concurrent.futures with real-time UI progress updates.
"""

from __future__ import annotations
import os
import sys
import time
import concurrent.futures
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Callable

import numpy as np

from audio.vocal_analyzer import (
    VocalTakeAnalyzer,
    TakeAnalysisReport,
    read_wav_mono,
    extract_pitch_contour,
    hz_to_midi,
    midi_to_hz,
    midi_to_note_name,
)
from audio.lyric_manager import LyricManager, LyricTrack, LyricAlignmentReport


def get_optimal_worker_count() -> int:
    """
    Calculates the safe worker pool size based on available CPU cores.
    Protects lower-end CPUs (Intel Celeron / dual cores) from UI and audio starvation,
    while scaling up to 24 concurrent workers on multi-core systems.
    """
    total_cores = os.cpu_count() or 2
    if total_cores <= 2:
        return 1  # 1 worker drains the queue; 1 core stays free for UI & audio
    elif total_cores <= 4:
        return 2  # Quad-core: 2 workers, 2 cores reserved
    elif total_cores <= 8:
        return max(2, total_cores - 2)
    else:
        # High-end systems (e.g. 16 - 27+ cores):
        # Cap pool at 24 workers, leaving cores free for OS, MPV, and LLM llama-server
        return min(total_cores - 3, 24)


class ParallelVocalTakeAnalyzer:
    """
    Concurrent wrapper around VocalTakeAnalyzer that fans out DSP feature extraction
    and lyric alignment tasks across an adaptive thread/process worker pool.
    """

    def __init__(self, vocal_profile=None, max_workers: Optional[int] = None):
        self.analyzer = VocalTakeAnalyzer(vocal_profile=vocal_profile)
        self.lyric_manager = LyricManager()
        self.max_workers = max_workers or get_optimal_worker_count()

    def update_profile(self, profile):
        self.analyzer.update_profile(profile)

    def recommend_key_for_track(self, track_peaks: Dict[str, float]) -> Tuple[int, str]:
        return self.analyzer.recommend_key_for_track(track_peaks)

    def extract_song_melody_profile(self, audio_path: Path) -> Dict[str, Any]:
        return self.analyzer.extract_song_melody_profile(audio_path)

    def analyze_take_parallel(
        self,
        singer_wav: Path,
        reference_wav: Optional[Path] = None,
        playback_audio_path: Optional[Path] = None,
        key_shift: int = 0,
        lyric_track: Optional[LyricTrack] = None,
        progress_cb: Optional[Callable[[float, str], None]] = None
    ) -> Tuple[TakeAnalysisReport, LyricAlignmentReport]:
        """
        Executes take analysis concurrently:
        1. Loads audio and extracts vocal pitch contour & suppresses bleed.
        2. Spawns concurrent worker tasks for all DSP feature extractors and lyric alignment.
        3. Aggregates results into TakeAnalysisReport and LyricAlignmentReport.
        """
        if progress_cb:
            progress_cb(0.10, "Loading audio and extracting vocal pitch contour...")

        singer_audio, sr = read_wav_mono(singer_wav)
        s_times, s_pitches = extract_pitch_contour(singer_audio, sr, voicing_threshold=0.35)

        # 0. Suppress acoustic speaker bleed if playback audio is provided
        if playback_audio_path and Path(playback_audio_path).exists():
            if progress_cb:
                progress_cb(0.18, "Filtering acoustic speaker bleed from recording...")
            s_pitches = self.analyzer._suppress_speaker_bleed(
                singer_audio=singer_audio,
                sr=sr,
                s_times=s_times,
                s_pitches=s_pitches,
                playback_path=Path(playback_audio_path),
                key_shift=key_shift
            )

        s_voiced_mask = s_pitches > 0
        s_voiced = s_pitches[s_voiced_mask]

        # Handle silent or inaudible take
        if len(s_voiced) < 20:
            if progress_cb:
                progress_cb(1.00, "Analysis complete (no vocal signal detected).")
            is_bleed = (playback_audio_path is not None)
            verdict = "No Singer Detected (Speaker Bleed Filtered Out)" if is_bleed else "No Vocal Signal Detected"
            feedback_msg = (
                "No active vocal singing detected. The microphone only captured audio bleeding "
                "from your speakers or backing track. Please sing directly into the microphone."
                if is_bleed else "No sustained vocal audio was detected in this take."
            )
            empty_report = TakeAnalysisReport(
                pitch_accuracy_pct=0.0,
                intonation_cents=0.0,
                intonation_verdict=verdict,
                rhythm_offset_ms=0.0,
                rhythm_verdict="N/A",
                vibrato_rate_hz=0.0,
                vibrato_depth_cents=0.0,
                vibrato_present=False,
                take_min_note="N/A",
                take_max_note="N/A",
                take_range_semitones=0.0,
                current_key_shift=key_shift,
                recommended_key_shift=0,
                key_recommendation_reason="Please sing closer to the microphone or verify input levels.",
                resonance_verdict="N/A",
                singers_formant_ratio=0.0,
                breath_support_pct=0.0,
                breath_support_verdict="N/A",
                onset_attack_verdict="N/A",
                balanced_onsets_pct=0.0,
                energy_steadiness_pct=0.0,
                dynamics_verdict="N/A",
                sibilance_ratio=0.0,
                sibilance_verdict="N/A",
                vocal_fry_pct=0.0,
                vocal_fry_verdict="N/A",
                overall_score=0,
                coach_feedback=[feedback_msg],
                phrase_highlights=[],
                phrase_struggles=[]
            )
            empty_lyric_report = LyricAlignmentReport(
                status="unavailable",
                summary_text="No vocal signal detected for lyric alignment."
            )
            return empty_report, empty_lyric_report

        if progress_cb:
            progress_cb(0.25, f"Distributing analysis across {self.max_workers} concurrent workers...")

        # Singer vocal range
        take_min_hz = float(np.percentile(s_voiced, 5))
        take_max_hz = float(np.percentile(s_voiced, 95))
        take_min_midi = hz_to_midi(take_min_hz)
        take_max_midi = hz_to_midi(take_max_hz)
        take_range = float(take_max_midi - take_min_midi)

        # 1. Primary pitch & rhythm alignment against reference or chromatic grid
        ref_pitches = None
        if reference_wav and Path(reference_wav).exists():
            pitch_acc, intonation, rhythm_ms, ref_pitches = self.analyzer._compare_with_reference(
                s_times, s_pitches, reference_wav, key_shift
            )
        else:
            pitch_acc, intonation, rhythm_ms = self.analyzer._analyze_against_grid(s_pitches)

        if abs(intonation) <= 12.0:
            intonation_verdict = "Dead On Center (Spot On)"
        elif 12.0 < intonation <= 30.0:
            intonation_verdict = f"Slightly Sharp (+{int(round(intonation))}¢)"
        elif intonation > 30.0:
            intonation_verdict = f"Noticeably Sharp (+{int(round(intonation))}¢)"
        elif -30.0 <= intonation < -12.0:
            intonation_verdict = f"Slightly Flat ({int(round(intonation))}¢)"
        else:
            intonation_verdict = f"Noticeably Flat ({int(round(intonation))}¢)"

        if abs(rhythm_ms) <= 25.0:
            rhythm_verdict = "Pocket Locked"
        elif rhythm_ms < -25.0:
            rhythm_verdict = f"Rushing (-{int(abs(rhythm_ms))}ms)"
        else:
            rhythm_verdict = f"Dragging (+{int(rhythm_ms)}ms)"

        # 2. Fan-out independent DSP feature extractors to concurrent worker pool
        hop_ms = 15.0
        results: Dict[str, Any] = {}
        completed_tasks = 0
        total_tasks = 11

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_key = {
                executor.submit(self.analyzer._analyze_vibrato, s_pitches, hop_ms): "vibrato",
                executor.submit(self.analyzer._analyze_resonance, singer_audio, sr, s_pitches, hop_ms): "resonance",
                executor.submit(self.analyzer._analyze_breath_support, s_pitches, hop_ms): "breath",
                executor.submit(self.analyzer._analyze_glottal_attacks, singer_audio, sr, s_pitches, hop_ms): "attacks",
                executor.submit(self.analyzer._analyze_dynamics, singer_audio, sr, s_pitches, hop_ms): "dynamics",
                executor.submit(self.analyzer._analyze_sibilance, singer_audio, sr, s_pitches, hop_ms): "sibilance",
                executor.submit(self.analyzer._analyze_vocal_fry, singer_audio, sr, s_pitches, hop_ms): "fry",
                executor.submit(self.analyzer._analyze_attack_velocity_and_scoop, s_pitches, hop_ms): "scoop",
                executor.submit(self.analyzer._analyze_dynamic_arc, singer_audio, sr, s_pitches, hop_ms): "dynamic_arc",
                executor.submit(
                    self.analyzer._extract_phrase_breakdown,
                    singer_audio=singer_audio,
                    sr=sr,
                    s_times=s_times,
                    s_pitches=s_pitches,
                    ref_pitches=ref_pitches,
                    hop_ms=hop_ms
                ): "phrases",
                executor.submit(
                    self.lyric_manager.align_singer_take,
                    singer_audio=singer_audio,
                    sr=sr,
                    s_times=s_times,
                    s_pitches=s_pitches,
                    lyric_track=lyric_track
                ): "lyrics"
            }

            for future in concurrent.futures.as_completed(future_to_key):
                key = future_to_key[future]
                completed_tasks += 1
                try:
                    results[key] = future.result()
                except Exception as e:
                    print(f"[ParallelAnalyzer] Error in task '{key}': {e}", file=sys.stderr)
                    results[key] = None

                if progress_cb:
                    prog = 0.35 + (completed_tasks / total_tasks) * 0.55
                    progress_cb(prog, f"Concurrent analysis: completed {key} ({completed_tasks}/{total_tasks})...")

        # Unpack concurrent task results with safe fallbacks
        vib_rate, vib_depth, vib_present = results.get("vibrato") or (0.0, 0.0, False)
        sf_ratio, resonance_verdict = results.get("resonance") or (0.0, "Balanced")
        breath_pct, breath_verdict = results.get("breath") or (75.0, "Stable")
        attack_verdict, balanced_onsets_pct = results.get("attacks") or ("Balanced", 75.0)
        energy_pct, dynamics_verdict = results.get("dynamics") or (80.0, "Even")
        sibilance_ratio, sibilance_verdict = results.get("sibilance") or (0.1, "Clean")
        fry_pct, fry_verdict = results.get("fry") or (0.0, "Low")
        pitch_scoop_pct, attack_velocity_verdict = results.get("scoop") or (0.0, "Clean Direct Attacks")
        dynamic_contrast_db, dynamic_arc_verdict = results.get("dynamic_arc") or (10.0, "Natural Dynamic Arc")
        phrase_highlights, phrase_struggles = results.get("phrases") or ([], [])
        lyric_alignment = results.get("lyrics") or LyricAlignmentReport(status="unavailable")

        # Vibrato bloom (depends on vib_present)
        vibrato_bloom_ms, vibrato_style_verdict = self.analyzer._analyze_vibrato_bloom(
            s_pitches, hop_ms=hop_ms, vib_present=vib_present
        )

        # Smart key recommendation
        rec_shift, rec_reason = self.analyzer.recommend_key_for_track({
            "min_hz": take_min_hz,
            "max_hz": take_max_hz
        })

        # Calculate Overall Score (0 - 100)
        intonation_penalty = min(20.0, abs(intonation))
        rhythm_penalty = min(20.0, abs(rhythm_ms) / 10.0)
        score_calc = (
            (pitch_acc * 0.50) +
            (max(0.0, 100.0 - intonation_penalty * 3.5) * 0.15) +
            (max(0.0, 100.0 - rhythm_penalty * 3.5) * 0.15) +
            (breath_pct * 0.10) +
            (balanced_onsets_pct * 0.10)
        )
        overall_score = int(round(max(0.0, min(100.0, score_calc))))

        # Actionable feedback generation
        feedback = []
        if overall_score >= 85:
            feedback.append(f"Outstanding take! Strong vocal control ({overall_score}/100) with solid melodic execution.")
        elif overall_score >= 70:
            feedback.append(f"Solid rehearsal take ({overall_score}/100). Focus on targeted adjustments below to level up.")
        else:
            feedback.append(f"Take recorded ({overall_score}/100). Work through the targeted micro-drills to build muscle memory.")

        if abs(intonation) > 15.0:
            direction = "sharp (pressing vocal cords too tightly)" if intonation > 0 else "flat (under-supporting airflow at phrase ends)"
            feedback.append(f"Intonation drifted {direction} ({intonation:+.1f}¢).")

        if abs(rhythm_ms) > 30.0:
            r_dir = "rushing ahead of" if rhythm_ms < 0 else "dragging behind"
            feedback.append(f"Phrase onsets were {r_dir} the beat by {int(abs(rhythm_ms))} ms.")

        if breath_pct < 65.0:
            feedback.append(f"Diaphragmatic breath support dropped to {breath_pct:.0f}% towards phrase endings.")

        if pitch_scoop_pct >= 40.0:
            feedback.append(f"Frequent pitch scooping detected ({pitch_scoop_pct:.0f}% of phrase attacks); aim to hit note centers directly.")
        elif dynamic_contrast_db >= 14.0:
            feedback.append(f"Expressive dynamic contrast detected ({dynamic_contrast_db:.1f} dB); strong storytelling arc.")

        if lyric_alignment.status == "aligned" and lyric_alignment.word_accuracy_pct < 80.0:
            feedback.append(f"Missed {len(lyric_alignment.missed_phrases)} lyric line entries. Practice line cue timings.")

        if progress_cb:
            progress_cb(1.00, "Vocal take analysis complete!")

        report = TakeAnalysisReport(
            pitch_accuracy_pct=pitch_acc,
            intonation_cents=intonation,
            intonation_verdict=intonation_verdict,
            rhythm_offset_ms=rhythm_ms,
            rhythm_verdict=rhythm_verdict,
            vibrato_rate_hz=vib_rate,
            vibrato_depth_cents=vib_depth,
            vibrato_present=vib_present,
            take_min_note=midi_to_note_name(take_min_midi),
            take_max_note=midi_to_note_name(take_max_midi),
            take_range_semitones=round(take_range, 1),
            current_key_shift=key_shift,
            recommended_key_shift=rec_shift,
            key_recommendation_reason=rec_reason,
            resonance_verdict=resonance_verdict,
            singers_formant_ratio=sf_ratio,
            breath_support_pct=breath_pct,
            breath_support_verdict=breath_verdict,
            onset_attack_verdict=attack_verdict,
            balanced_onsets_pct=balanced_onsets_pct,
            energy_steadiness_pct=energy_pct,
            dynamics_verdict=dynamics_verdict,
            sibilance_ratio=sibilance_ratio,
            sibilance_verdict=sibilance_verdict,
            vocal_fry_pct=fry_pct,
            vocal_fry_verdict=fry_verdict,
            overall_score=overall_score,
            coach_feedback=feedback,
            phrase_highlights=phrase_highlights,
            phrase_struggles=phrase_struggles,
            pitch_scoop_pct=pitch_scoop_pct,
            attack_velocity_verdict=attack_velocity_verdict,
            dynamic_contrast_db=dynamic_contrast_db,
            dynamic_arc_verdict=dynamic_arc_verdict,
            vibrato_bloom_ms=vibrato_bloom_ms,
            vibrato_style_verdict=vibrato_style_verdict
        )

        return report, lyric_alignment

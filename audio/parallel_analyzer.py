"""
Tiki Rehearsal Lounge - Adaptive Parallel Vocal Take Analyzer
=============================================================
Distributes DSP feature extraction and lyric alignment across concurrent OS worker processes.
Adapts dynamically to hardware:
- Low-end dual-core machines (e.g. Intel Celeron): restricts worker concurrency to 1
  to ensure the GUI and audio stream never stutter.
- Multi-core workstations (e.g. 24-27 cores): saturates all cores simultaneously using ProcessPoolExecutor.
- Robust exception handling with automatic fallback to thread/in-process execution.
- Generates deep, high-resolution phrase timelines and acoustic time-series telemetry.
"""

from __future__ import annotations
import os
import sys
import time
import concurrent.futures
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Callable, List

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


# ==============================================================================
# Top-Level Picklable Worker Functions for Multi-Core ProcessPoolExecutor
# ==============================================================================

def _worker_pitch_chunk(args: Tuple[int, np.ndarray, int, float, float, float, float]) -> Tuple[int, np.ndarray, np.ndarray]:
    """Computes normalized autocorrelation pitch contour for a slice of audio in a separate OS process."""
    chunk_idx, slice_audio, sr, frame_ms, hop_ms, thresh, time_offset = args
    times, pitches = extract_pitch_contour(
        slice_audio,
        sr,
        frame_size_ms=frame_ms,
        hop_size_ms=hop_ms,
        voicing_threshold=thresh
    )
    return chunk_idx, times + time_offset, pitches


def _worker_vibrato(s_pitches: np.ndarray, hop_ms: float = 15.0):
    analyzer = VocalTakeAnalyzer()
    return analyzer._analyze_vibrato(s_pitches, hop_ms)


def _worker_resonance(singer_audio: np.ndarray, sr: int, s_pitches: np.ndarray, hop_ms: float = 15.0):
    analyzer = VocalTakeAnalyzer()
    return analyzer._analyze_resonance(singer_audio, sr, s_pitches, hop_ms)


def _worker_breath(s_pitches: np.ndarray, hop_ms: float = 15.0):
    analyzer = VocalTakeAnalyzer()
    return analyzer._analyze_breath_support(s_pitches, hop_ms)


def _worker_attacks(singer_audio: np.ndarray, sr: int, s_pitches: np.ndarray, hop_ms: float = 15.0):
    analyzer = VocalTakeAnalyzer()
    return analyzer._analyze_glottal_attacks(singer_audio, sr, s_pitches, hop_ms)


def _worker_dynamics(singer_audio: np.ndarray, sr: int, s_pitches: np.ndarray, hop_ms: float = 15.0):
    analyzer = VocalTakeAnalyzer()
    return analyzer._analyze_dynamics(singer_audio, sr, s_pitches, hop_ms)


def _worker_sibilance(singer_audio: np.ndarray, sr: int, s_pitches: np.ndarray, hop_ms: float = 15.0):
    analyzer = VocalTakeAnalyzer()
    return analyzer._analyze_sibilance(singer_audio, sr, s_pitches, hop_ms)


def _worker_fry(singer_audio: np.ndarray, sr: int, s_pitches: np.ndarray, hop_ms: float = 15.0):
    analyzer = VocalTakeAnalyzer()
    return analyzer._analyze_vocal_fry(singer_audio, sr, s_pitches, hop_ms)


def _worker_scoop(s_pitches: np.ndarray, hop_ms: float = 15.0):
    analyzer = VocalTakeAnalyzer()
    return analyzer._analyze_attack_velocity_and_scoop(s_pitches, hop_ms)


def _worker_dynamic_arc(singer_audio: np.ndarray, sr: int, s_pitches: np.ndarray, hop_ms: float = 15.0):
    analyzer = VocalTakeAnalyzer()
    return analyzer._analyze_dynamic_arc(singer_audio, sr, s_pitches, hop_ms)


def _worker_phrase_timeline(
    singer_audio: np.ndarray,
    sr: int,
    s_times: np.ndarray,
    s_pitches: np.ndarray,
    ref_pitches: Optional[np.ndarray] = None,
    hop_ms: float = 15.0
):
    """
    Extracts high-resolution phrase breakdown:
    - Top highlights & struggle focus moments.
    - Full chronological timeline of every phrase across the entire song.
    """
    analyzer = VocalTakeAnalyzer()
    highlights, struggles = analyzer._extract_phrase_breakdown(
        singer_audio=singer_audio,
        sr=sr,
        s_times=s_times,
        s_pitches=s_pitches,
        ref_pitches=ref_pitches,
        hop_ms=hop_ms
    )
    all_phrases = _extract_all_phrases_chronological(
        singer_audio=singer_audio,
        sr=sr,
        s_times=s_times,
        s_pitches=s_pitches,
        ref_pitches=ref_pitches,
        highlights=highlights,
        struggles=struggles
    )
    return highlights, struggles, all_phrases


def _worker_lyrics(
    singer_wav_str: Optional[str],
    singer_audio: np.ndarray,
    sr: int,
    s_times: np.ndarray,
    s_pitches: np.ndarray,
    lyric_track_dict: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """Runs singer take STT and lyric alignment in a worker process."""
    lm = LyricManager()
    lt = LyricTrack.from_dict(lyric_track_dict) if lyric_track_dict else None
    wav_p = Path(singer_wav_str) if singer_wav_str else None
    rep = lm.align_singer_take(
        singer_audio=singer_audio,
        sr=sr,
        s_times=s_times,
        s_pitches=s_pitches,
        lyric_track=lt,
        singer_wav=wav_p
    )
    return rep.to_dict()


def _extract_all_phrases_chronological(
    singer_audio: np.ndarray,
    sr: int,
    s_times: np.ndarray,
    s_pitches: np.ndarray,
    ref_pitches: Optional[np.ndarray],
    highlights: List[Dict[str, Any]],
    struggles: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Segments every musical phrase across the entire track for granular coach evaluation."""
    n_frames = len(s_pitches)
    if n_frames < 30:
        return []

    phrases_raw = []
    phrase_start = None
    gap_count = 0
    last_voiced_idx = 0

    for i in range(n_frames):
        if s_pitches[i] > 0:
            if phrase_start is None:
                phrase_start = i
            last_voiced_idx = i
            gap_count = 0
            if (i - phrase_start) >= 600:  # ~9 seconds max phrase duration
                if (last_voiced_idx - phrase_start) >= 30:
                    phrases_raw.append((phrase_start, last_voiced_idx))
                phrase_start = None
        else:
            if phrase_start is not None:
                gap_count += 1
                if gap_count >= 12:  # ~180ms breath boundary
                    if (last_voiced_idx - phrase_start) >= 30:
                        phrases_raw.append((phrase_start, last_voiced_idx))
                    phrase_start = None
                    gap_count = 0

    if phrase_start is not None and (last_voiced_idx - phrase_start) >= 30:
        phrases_raw.append((phrase_start, last_voiced_idx))

    highlight_ts = {h.get("timestamp") for h in highlights}
    struggle_ts = {s.get("timestamp") for s in struggles}

    all_phrases = []
    phrase_id = 0

    for start_idx, end_idx in phrases_raw:
        sub_pitches = s_pitches[start_idx:end_idx + 1]
        voiced_mask = sub_pitches > 0
        voiced_pitches = sub_pitches[voiced_mask]
        if len(voiced_pitches) < 20:
            continue

        start_t = float(s_times[start_idx])
        end_t = float(s_times[end_idx])
        dur = end_t - start_t
        if dur < 1.0:
            continue

        phrase_id += 1
        sm, ss = int(start_t // 60), int(start_t % 60)
        em, es = int(end_t // 60), int(end_t % 60)
        if sm == em and ss == es:
            es = min(59, ss + 1)
        ts_str = f"{sm}:{ss:02d} – {em}:{es:02d}"

        midis = [hz_to_midi(p) for p in voiced_pitches]
        min_m, max_m = min(midis), max(midis)
        min_name = midi_to_note_name(round(min_m))
        max_name = midi_to_note_name(round(max_m))
        note_span = f"{min_name} – {max_name}" if min_name != max_name else min_name

        cents_errs = []
        if ref_pitches is not None and len(ref_pitches) > end_idx:
            for idx in range(start_idx, end_idx + 1):
                if s_pitches[idx] > 0 and ref_pitches[idx] > 0:
                    cents_errs.append(1200.0 * np.log2(s_pitches[idx] / ref_pitches[idx]))

        if not cents_errs:
            for m in midis:
                cents_errs.append((m - round(m)) * 100.0)

        cents_arr = np.array(cents_errs)
        acc_pct = float(np.sum(np.abs(cents_arr) <= 40.0) / len(cents_arr)) * 100.0
        med_cents = float(np.median(cents_arr))

        # Tail pitch sag
        tail_slice = voiced_pitches[-min(10, len(voiced_pitches)):]
        body_slice = voiced_pitches[int(len(voiced_pitches) * 0.2):int(len(voiced_pitches) * 0.8)]
        if len(body_slice) > 0 and len(tail_slice) > 0:
            tail_sag = float(1200.0 * np.log2(float(np.median(tail_slice)) / (float(np.median(body_slice)) + 1e-9)))
        else:
            tail_sag = 0.0

        # Local Singer's Formant power ratio
        a_start = int(start_t * sr)
        a_end = min(len(singer_audio), int(end_t * sr))
        local_sf = 0.35
        if a_end - a_start > sr * 0.3:
            chunk = singer_audio[a_start:a_end]
            n_fft = min(2048, len(chunk))
            spec = np.abs(np.fft.rfft(chunk[:n_fft] * np.hanning(n_fft))) ** 2
            freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
            sf_p = np.sum(spec[(freqs >= 2500) & (freqs <= 3500)])
            mid_p = np.sum(spec[(freqs >= 1000) & (freqs <= 2000)])
            local_sf = float(sf_p / (mid_p + 1e-9))

        status = "highlight" if ts_str in highlight_ts else ("struggle" if ts_str in struggle_ts else "solid")
        coaching_tip = (
            "Locked pitch center with strong core resonance." if status == "highlight" else
            ("Support diaphragmatic airflow through the final release." if tail_sag < -35.0 else
             ("Aim for direct attack without scooping." if abs(med_cents) > 15.0 else "Solid melodic phrase delivery."))
        )

        all_phrases.append({
            "phrase_id": phrase_id,
            "timestamp": ts_str,
            "start_s": round(start_t, 2),
            "end_s": round(end_t, 2),
            "duration_s": round(dur, 2),
            "note_span": note_span,
            "accuracy_pct": round(acc_pct, 1),
            "cents_drift": round(med_cents, 1),
            "tail_sag_cents": round(tail_sag, 1),
            "singers_formant": round(local_sf, 3),
            "status": status,
            "coaching_tip": coaching_tip
        })

    return all_phrases


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

    def _extract_pitch_parallel(
        self,
        audio: np.ndarray,
        sr: int,
        progress_cb: Optional[Callable[[float, str], None]] = None
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Extracts pitch contour across multiple OS processes by slicing audio into parallel chunks.
        Saturates all CPU cores during fundamental frequency detection.
        """
        duration_s = len(audio) / sr
        # Slicing is efficient when audio is > 6 seconds and we have >= 2 workers
        if duration_s < 6.0 or self.max_workers <= 1:
            return extract_pitch_contour(audio, sr, voicing_threshold=0.35)

        num_chunks = min(self.max_workers, 16)
        samples_per_chunk = int(len(audio) // num_chunks)
        overlap_samples = int(sr * 0.040)  # 40ms overlap for frame continuity

        chunk_tasks = []
        for i in range(num_chunks):
            start_samp = i * samples_per_chunk
            end_samp = len(audio) if i == num_chunks - 1 else min(len(audio), (i + 1) * samples_per_chunk + overlap_samples)
            slice_audio = audio[start_samp:end_samp]
            time_offset = start_samp / sr
            chunk_tasks.append((i, slice_audio, sr, 40.0, 15.0, 0.35, time_offset))

        chunk_results: List[Tuple[int, np.ndarray, np.ndarray]] = []
        try:
            with concurrent.futures.ProcessPoolExecutor(max_workers=min(self.max_workers, num_chunks)) as executor:
                for res in executor.map(_worker_pitch_chunk, chunk_tasks):
                    chunk_results.append(res)
        except Exception as e:
            # Fallback to sequential pitch contour if process pool is restricted in current environment
            print(f"[ParallelAnalyzer] ProcessPool pitch extraction fallback to sequential: {e}", file=sys.stderr)
            return extract_pitch_contour(audio, sr, voicing_threshold=0.35)

        chunk_results.sort(key=lambda r: r[0])
        all_times = [r[1] for r in chunk_results if len(r[1]) > 0]
        all_pitches = [r[2] for r in chunk_results if len(r[2]) > 0]

        if not all_times:
            return np.array([], dtype=np.float32), np.array([], dtype=np.float32)

        return np.concatenate(all_times), np.concatenate(all_pitches)

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
        1. Loads audio and extracts vocal pitch contour across parallel CPU cores.
        2. Spawns concurrent OS worker processes for all DSP feature extractors and lyric alignment.
        3. Aggregates results into high-resolution TakeAnalysisReport and LyricAlignmentReport.
        """
        if progress_cb:
            progress_cb(0.08, f"Saturating {self.max_workers} CPU cores: extracting pitch contour...")

        singer_audio, sr = read_wav_mono(singer_wav)
        s_times, s_pitches = self._extract_pitch_parallel(singer_audio, sr, progress_cb=progress_cb)

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
            progress_cb(0.25, f"Distributing DSP analysis across {self.max_workers} worker processes...")

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

        # 2. Fan-out independent DSP feature extractors to ProcessPoolExecutor
        hop_ms = 15.0
        results: Dict[str, Any] = {}
        completed_tasks = 0
        total_tasks = 11

        lt_dict = lyric_track.to_dict() if lyric_track else None
        wav_str = str(singer_wav) if singer_wav else None

        # Attempt multi-process execution first to utilize all CPU cores
        use_process_pool = True
        try:
            with concurrent.futures.ProcessPoolExecutor(max_workers=self.max_workers) as executor:
                future_to_key = {
                    executor.submit(_worker_vibrato, s_pitches, hop_ms): "vibrato",
                    executor.submit(_worker_resonance, singer_audio, sr, s_pitches, hop_ms): "resonance",
                    executor.submit(_worker_breath, s_pitches, hop_ms): "breath",
                    executor.submit(_worker_attacks, singer_audio, sr, s_pitches, hop_ms): "attacks",
                    executor.submit(_worker_dynamics, singer_audio, sr, s_pitches, hop_ms): "dynamics",
                    executor.submit(_worker_sibilance, singer_audio, sr, s_pitches, hop_ms): "sibilance",
                    executor.submit(_worker_fry, singer_audio, sr, s_pitches, hop_ms): "fry",
                    executor.submit(_worker_scoop, s_pitches, hop_ms): "scoop",
                    executor.submit(_worker_dynamic_arc, singer_audio, sr, s_pitches, hop_ms): "dynamic_arc",
                    executor.submit(_worker_phrase_timeline, singer_audio, sr, s_times, s_pitches, ref_pitches, hop_ms): "phrases",
                    executor.submit(_worker_lyrics, wav_str, singer_audio, sr, s_times, s_pitches, lt_dict): "lyrics"
                }

                for future in concurrent.futures.as_completed(future_to_key):
                    key = future_to_key[future]
                    completed_tasks += 1
                    try:
                        results[key] = future.result()
                    except Exception as e:
                        print(f"[ParallelAnalyzer] Process worker error in '{key}': {e}", file=sys.stderr)
                        results[key] = None

                    if progress_cb:
                        prog = 0.35 + (completed_tasks / total_tasks) * 0.55
                        progress_cb(prog, f"Multi-core analysis: completed {key} ({completed_tasks}/{total_tasks})...")
        except Exception as pe:
            print(f"[ParallelAnalyzer] ProcessPool fallback to ThreadPool: {pe}", file=sys.stderr)
            use_process_pool = False

        # Fallback if process pool broke
        if not use_process_pool or len(results) < 5:
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
                    executor.submit(_worker_phrase_timeline, singer_audio, sr, s_times, s_pitches, ref_pitches, hop_ms): "phrases",
                    executor.submit(
                        self.lyric_manager.align_singer_take,
                        singer_audio=singer_audio,
                        sr=sr,
                        s_times=s_times,
                        s_pitches=s_pitches,
                        lyric_track=lyric_track,
                        singer_wav=singer_wav
                    ): "lyrics"
                }
                for future in concurrent.futures.as_completed(future_to_key):
                    key = future_to_key[future]
                    try:
                        results[key] = future.result()
                    except Exception as e:
                        results[key] = None

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

        phrases_res = results.get("phrases")
        if phrases_res and len(phrases_res) == 3:
            phrase_highlights, phrase_struggles, all_phrases = phrases_res
        elif phrases_res and len(phrases_res) == 2:
            phrase_highlights, phrase_struggles = phrases_res
            all_phrases = []
        else:
            phrase_highlights, phrase_struggles, all_phrases = [], [], []

        lyrics_raw = results.get("lyrics")
        if isinstance(lyrics_raw, dict):
            lyric_alignment = LyricAlignmentReport(
                status=lyrics_raw.get("status", "unavailable"),
                word_accuracy_pct=lyrics_raw.get("word_accuracy_pct", 0.0),
                word_error_rate_pct=lyrics_raw.get("word_error_rate_pct", 0.0),
                total_lines=lyrics_raw.get("total_lines", 0),
                lines_covered=lyrics_raw.get("lines_covered", 0),
                timing_offset_ms=lyrics_raw.get("timing_offset_ms", 0.0),
                line_details=lyrics_raw.get("line_details", []),
                missed_phrases=lyrics_raw.get("missed_phrases", []),
                summary_text=lyrics_raw.get("summary_text", ""),
                total_target_words=lyrics_raw.get("total_target_words", 0),
                total_sung_words=lyrics_raw.get("total_sung_words", 0),
                words_matched_count=lyrics_raw.get("words_matched_count", 0),
                missed_words=lyrics_raw.get("missed_words", []),
                word_timeline=lyrics_raw.get("word_timeline", []),
                diction_clarity_pct=lyrics_raw.get("diction_clarity_pct", 0.0)
            )
        elif isinstance(lyrics_raw, LyricAlignmentReport):
            lyric_alignment = lyrics_raw
        else:
            lyric_alignment = LyricAlignmentReport(status="unavailable")

        # Vibrato bloom (depends on vib_present)
        vibrato_bloom_ms, vibrato_style_verdict = self.analyzer._analyze_vibrato_bloom(
            s_pitches, hop_ms=hop_ms, vib_present=vib_present
        )

        # Smart key recommendation
        rec_shift, rec_reason = self.analyzer.recommend_key_for_track({
            "min_hz": take_min_hz,
            "max_hz": take_max_hz
        })

        # Auditory Pleasingness & Artistic Musicality (Harmonic Consonance vs melody)
        auditory_score, auditory_verdict, harmonic_consonance = self.analyzer._analyze_auditory_appeal(
            singer_audio=singer_audio,
            sr=sr,
            s_pitches=s_pitches,
            ref_pitches=ref_pitches,
            hop_ms=hop_ms,
            sf_ratio=sf_ratio,
            vib_present=vib_present,
            vib_rate=vib_rate,
            vib_depth=vib_depth
        )

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

        # High-Resolution Telemetry Series & Registers
        timeline_series, vocal_registers, intonation_bias = self._compute_high_resolution_telemetry(
            singer_audio=singer_audio,
            sr=sr,
            s_times=s_times,
            s_pitches=s_pitches,
            ref_pitches=ref_pitches
        )

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
            feedback.append(f"Lyric alignment accuracy: {lyric_alignment.word_accuracy_pct:.0f}%. Practice target phrase cue entries.")

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
            all_phrases=all_phrases,
            pitch_scoop_pct=pitch_scoop_pct,
            attack_velocity_verdict=attack_velocity_verdict,
            dynamic_contrast_db=dynamic_contrast_db,
            dynamic_arc_verdict=dynamic_arc_verdict,
            vibrato_bloom_ms=vibrato_bloom_ms,
            vibrato_style_verdict=vibrato_style_verdict,
            timeline_series=timeline_series,
            vocal_registers=vocal_registers,
            intonation_bias=intonation_bias,
            auditory_appeal_score=auditory_score,
            auditory_appeal_verdict=auditory_verdict,
            harmonic_consonance_pct=harmonic_consonance
        )

        return report, lyric_alignment

    def _compute_high_resolution_telemetry(
        self,
        singer_audio: np.ndarray,
        sr: int,
        s_times: np.ndarray,
        s_pitches: np.ndarray,
        ref_pitches: Optional[np.ndarray] = None
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
        """Computes second-by-second curves and pitch register distribution."""
        duration_s = int(len(singer_audio) / sr)
        if duration_s < 1:
            return {}, {}, {}

        # 1-second interval time-series curves
        rms_db_curve = []
        pitch_acc_curve = []
        inton_curve = []
        formant_curve = []

        voiced_mask = s_pitches > 0
        voiced_pitches = s_pitches[voiced_mask]
        note_histogram: Dict[str, int] = {}
        chest_count = 0
        mix_count = 0
        head_count = 0

        flat_frames = 0
        sharp_frames = 0
        spot_on_frames = 0

        for sec in range(duration_s):
            t_start = sec
            t_end = sec + 1.0

            # Audio RMS dB
            samp_st = int(t_start * sr)
            samp_en = min(len(singer_audio), int(t_end * sr))
            chunk = singer_audio[samp_st:samp_en]
            rms = np.sqrt(np.mean(chunk ** 2)) if len(chunk) > 0 else 0.0
            rms_db = round(float(20.0 * np.log10(rms + 1e-6)), 1)
            rms_db_curve.append(rms_db)

            # Frame indices inside this 1s window
            f_mask = (s_times >= t_start) & (s_times < t_end) & (s_pitches > 0)
            sec_pitches = s_pitches[f_mask]

            if len(sec_pitches) >= 5:
                # Intonation cents & accuracy
                cents = []
                for p_val in sec_pitches:
                    m = hz_to_midi(p_val)
                    c = (m - round(m)) * 100.0
                    cents.append(c)
                    if c < -12.0:
                        flat_frames += 1
                    elif c > 12.0:
                        sharp_frames += 1
                    else:
                        spot_on_frames += 1

                c_arr = np.array(cents)
                acc = float(np.sum(np.abs(c_arr) <= 40.0) / len(c_arr)) * 100.0
                pitch_acc_curve.append(round(acc, 1))
                inton_curve.append(round(float(np.median(c_arr)), 1))

                # Local Singer's Formant
                if len(chunk) >= 2048:
                    spec = np.abs(np.fft.rfft(chunk[:2048] * np.hanning(2048))) ** 2
                    freqs = np.fft.rfftfreq(2048, 1.0 / sr)
                    sf_p = np.sum(spec[(freqs >= 2500) & (freqs <= 3500)])
                    mid_p = np.sum(spec[(freqs >= 1000) & (freqs <= 2000)])
                    formant_curve.append(round(float(sf_p / (mid_p + 1e-9)), 3))
                else:
                    formant_curve.append(0.30)
            else:
                pitch_acc_curve.append(0.0)
                inton_curve.append(0.0)
                formant_curve.append(0.0)

        # Note histogram & vocal registers
        for p in voiced_pitches:
            m = hz_to_midi(p)
            n_name = midi_to_note_name(round(m))
            note_histogram[n_name] = note_histogram.get(n_name, 0) + 1
            if m < 55.0:  # Below G3
                chest_count += 1
            elif m <= 67.0:  # G3 to G4
                mix_count += 1
            else:  # Above G4
                head_count += 1

        total_voiced = max(1, len(voiced_pitches))
        vocal_registers = {
            "chest_voice_pct": round((chest_count / total_voiced) * 100.0, 1),
            "mix_voice_pct": round((mix_count / total_voiced) * 100.0, 1),
            "head_voice_pct": round((head_count / total_voiced) * 100.0, 1),
            "note_histogram": dict(sorted(note_histogram.items(), key=lambda item: item[1], reverse=True)[:12])
        }

        total_inton_evals = max(1, flat_frames + sharp_frames + spot_on_frames)
        intonation_bias = {
            "spot_on_pct": round((spot_on_frames / total_inton_evals) * 100.0, 1),
            "flat_tendency_pct": round((flat_frames / total_inton_evals) * 100.0, 1),
            "sharp_tendency_pct": round((sharp_frames / total_inton_evals) * 100.0, 1)
        }

        timeline_series = {
            "step_seconds": 1.0,
            "rms_energy_db": rms_db_curve,
            "pitch_accuracy_curve": pitch_acc_curve,
            "intonation_cents_curve": inton_curve,
            "singer_formant_curve": formant_curve
        }

        return timeline_series, vocal_registers, intonation_bias

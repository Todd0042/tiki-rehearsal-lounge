"""
Tiki Rehearsal Lounge - Vocal Calibration & Profile Engine
==========================================================
Extracts fundamental pitch contours (F0) using normalized autocorrelation,
analyzes the two foundational vocal diagnostic exercises:
1. Siren Pitch Sweep (Ooo ➔ Aaa): Maps absolute vocal floor and ceiling.
2. Speaking Anchor (Counting 20 ➔ 0): Extracts habitual speaking frequency
   and calculates true physiological power tessitura.
Persists results to ~/.config/TikiRehearsalLounge/vocal_profile.json.
"""

from __future__ import annotations
import json
import math
import wave
import struct
import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional, List, Tuple, Dict, Any

import numpy as np
from config import VOCAL_PROFILE_PATH

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def hz_to_midi(hz: float) -> float:
    """Converts a frequency in Hz to fractional MIDI note number (A4 = 440 Hz = 69)."""
    if hz <= 0:
        return 0.0
    return 69.0 + 12.0 * math.log2(hz / 440.0)


def midi_to_hz(midi: float) -> float:
    """Converts a MIDI note number to frequency in Hz."""
    return 440.0 * (2.0 ** ((midi - 69.0) / 12.0))


def midi_to_note_name(midi: float) -> str:
    """Converts a MIDI number to a standard note string, e.g. 60 -> 'C4', 45 -> 'A2'."""
    rounded = int(round(midi))
    octave = (rounded // 12) - 1
    note_idx = rounded % 12
    return f"{NOTE_NAMES[note_idx]}{octave}"


def hz_to_note_name(hz: float) -> str:
    """Converts frequency in Hz directly to note name."""
    if hz <= 0:
        return "N/A"
    return midi_to_note_name(hz_to_midi(hz))


def cents_deviation(hz: float, target_midi: int) -> float:
    """Calculates cents deviation (-50.0 to +50.0) from the target MIDI note."""
    if hz <= 0:
        return 0.0
    actual_midi = hz_to_midi(hz)
    return (actual_midi - target_midi) * 100.0


def read_wav_mono(wav_path: Path) -> Tuple[np.ndarray, int]:
    """Reads a WAV file into a normalized 1D float32 numpy array [-1.0, 1.0]."""
    with wave.open(str(wav_path), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        raw_bytes = wf.readframes(n_frames)

    if n_frames == 0 or len(raw_bytes) == 0:
        raise ValueError("Audio recording is empty (0 frames recorded). Please verify your microphone is connected and selected as default.")

    if sampwidth == 2:
        dtype = np.int16
        scale = 32768.0
    elif sampwidth == 4:
        dtype = np.int32
        scale = 2147483648.0
    elif sampwidth == 1:
        dtype = np.uint8
        data = (np.frombuffer(raw_bytes, dtype=dtype).astype(np.float32) - 128.0) / 128.0
        if n_channels > 1:
            data = data.reshape(-1, n_channels).mean(axis=1)
        return data, framerate
    else:
        raise ValueError(f"Unsupported sample width: {sampwidth}")

    audio_int = np.frombuffer(raw_bytes, dtype=dtype)
    if n_channels > 1:
        audio_int = audio_int.reshape(-1, n_channels).mean(axis=1)

    audio_float = audio_int.astype(np.float32) / scale
    return audio_float, framerate


def extract_pitch_contour(
    audio: np.ndarray,
    sr: int,
    frame_size_ms: float = 40.0,
    hop_size_ms: float = 15.0,
    min_hz: float = 55.0,   # A1
    max_hz: float = 900.0,  # A5
    voicing_threshold: float = 0.35
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Extracts fundamental frequency (F0) contour across time using normalized autocorrelation.
    Returns:
        times: 1D array of timestamps (seconds)
        pitches: 1D array of detected frequencies (Hz, 0.0 = unvoiced)
    """
    if len(audio) < int(sr * 0.5):
        raise ValueError("Audio signal is too short (less than 0.5 seconds). Please hold vocal sweep steadily.")

    frame_len = int(sr * (frame_size_ms / 1000.0))
    hop_len = int(sr * (hop_size_ms / 1000.0))

    min_period = int(sr / max_hz)
    max_period = int(sr / min_hz)

    total_frames = max(1, (len(audio) - frame_len) // hop_len)
    times = np.zeros(total_frames, dtype=np.float32)
    pitches = np.zeros(total_frames, dtype=np.float32)

    for i in range(total_frames):
        start = i * hop_len
        times[i] = (start + frame_len // 2) / sr
        frame = audio[start : start + frame_len]

        # Calculate energy to skip quiet frames
        rms = np.sqrt(np.mean(frame ** 2))
        if rms < 0.01:
            continue

        # Windowing
        windowed = frame * np.hanning(len(frame))

        # Normalized autocorrelation
        corr = np.correlate(windowed, windowed, mode="full")
        corr = corr[len(windowed) - 1 :]  # Non-negative lags

        if corr[0] <= 1e-7:
            continue
        norm_corr = corr / corr[0]

        # Search for true local peaks within allowable pitch period range
        curr_max = min(max_period, len(norm_corr) - 2)
        if min_period >= curr_max:
            continue

        peaks = []
        for k in range(min_period + 1, curr_max):
            val = norm_corr[k]
            if val >= voicing_threshold and val > norm_corr[k - 1] and val >= norm_corr[k + 1]:
                peaks.append((k, val))

        if not peaks:
            continue

        # Choose the fundamental period: favor largest lag (lowest frequency) within 82% of max peak
        max_val = max(p[1] for p in peaks)
        chosen_k, chosen_val = peaks[0]
        for k, val in reversed(peaks):
            if val >= 0.82 * max_val:
                chosen_k, chosen_val = k, val
                break

        # Parabolic interpolation for sub-sample accuracy
        alpha = norm_corr[chosen_k - 1]
        beta = norm_corr[chosen_k]
        gamma = norm_corr[chosen_k + 1]
        denom = alpha - 2 * beta + gamma
        delta = 0.5 * (alpha - gamma) / denom if abs(denom) > 1e-7 else 0.0
        lag = chosen_k + delta

        if lag > 0:
            pitch_hz = sr / lag
            if min_hz <= pitch_hz <= max_hz:
                pitches[i] = pitch_hz

    return times, pitches


@dataclass
class VocalProfile:
    # Speaking Anchor
    anchor_hz: float = 0.0
    anchor_note: str = ""

    # Absolute Vocal Range (From Siren Sweep)
    range_min_hz: float = 0.0
    range_min_note: str = ""
    range_max_hz: float = 0.0
    range_max_note: str = ""
    range_semitones: float = 0.0

    # Power Tessitura (Comfort Zone)
    tessitura_min_hz: float = 0.0
    tessitura_min_note: str = ""
    tessitura_max_hz: float = 0.0
    tessitura_max_note: str = ""

    vocal_type: str = "Uncalibrated"
    calibrated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> VocalProfile:
        return cls(**data)


def classify_voice_type(min_midi: float, max_midi: float, anchor_midi: float) -> str:
    """Classifies voice category based on range and speaking fundamental."""
    if anchor_midi <= 0:
        if max_midi <= 55:
            return "Bass"
        elif max_midi <= 62:
            return "Baritone"
        elif max_midi <= 69:
            return "Tenor"
        elif max_midi <= 76:
            return "Mezzo-Soprano"
        else:
            return "Soprano"

    if anchor_midi < 43:  # Below G2
        return "Bass"
    elif anchor_midi < 48:  # G2 to C3
        return "Baritone"
    elif anchor_midi < 53:  # C3 to F3
        return "Tenor"
    elif anchor_midi < 58:  # F3 to A#3
        return "Contralto / Low Alto"
    elif anchor_midi < 64:  # B3 to E4
        return "Mezzo-Soprano"
    else:
        return "Soprano"


class VocalCalibrationManager:
    def __init__(self, profile_path: Path = VOCAL_PROFILE_PATH):
        self.profile_path = profile_path

    def load_profile(self) -> Optional[VocalProfile]:
        """Loads the saved profile from JSON if it exists."""
        if not self.profile_path.exists():
            return None
        try:
            with open(self.profile_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return VocalProfile.from_dict(data)
        except Exception as e:
            print(f"[VocalProfile] Failed to load profile: {e}")
            return None

    def save_profile(self, profile: VocalProfile):
        """Persists the vocal profile to JSON."""
        try:
            self.profile_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.profile_path, "w", encoding="utf-8") as f:
                json.dump(profile.to_dict(), f, indent=2)
            print(f"[VocalProfile] Successfully saved profile to {self.profile_path}")
        except Exception as e:
            print(f"[VocalProfile] Failed to save profile: {e}")

    def update_siren_sweep(self, siren_res: Dict[str, Any]) -> VocalProfile:
        """Updates in-memory profile with siren glide results and persists immediately."""
        profile = self.load_profile() or VocalProfile()
        profile.range_min_hz = siren_res["min_hz"]
        profile.range_min_note = siren_res["min_note"]
        profile.range_max_hz = siren_res["max_hz"]
        profile.range_max_note = siren_res["max_note"]
        profile.range_semitones = siren_res["range_semitones"]
        profile.calibrated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

        min_m = hz_to_midi(profile.range_min_hz)
        max_m = hz_to_midi(profile.range_max_hz)
        anch_m = hz_to_midi(profile.anchor_hz) if profile.anchor_hz > 0 else 0.0
        profile.vocal_type = classify_voice_type(min_m, max_m, anch_m)

        self.save_profile(profile)
        return profile

    def update_speaking_anchor(self, anchor_res: Dict[str, Any]) -> VocalProfile:
        """Updates in-memory profile with speaking anchor results and persists immediately."""
        profile = self.load_profile() or VocalProfile()
        profile.anchor_hz = anchor_res["anchor_hz"]
        profile.anchor_note = anchor_res["anchor_note"]
        profile.tessitura_min_hz = anchor_res["tessitura_min_hz"]
        profile.tessitura_min_note = anchor_res["tessitura_min_note"]
        profile.tessitura_max_hz = anchor_res["tessitura_max_hz"]
        profile.tessitura_max_note = anchor_res["tessitura_max_note"]
        profile.calibrated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

        min_m = hz_to_midi(profile.range_min_hz) if profile.range_min_hz > 0 else 0.0
        max_m = hz_to_midi(profile.range_max_hz) if profile.range_max_hz > 0 else 0.0
        anch_m = hz_to_midi(profile.anchor_hz)
        profile.vocal_type = classify_voice_type(min_m, max_m, anch_m)

        self.save_profile(profile)
        return profile

    def analyze_siren_sweep(self, wav_path: Path) -> Dict[str, Any]:
        """
        Exercise 1: Analyzes the Siren Glide recording (Ooooo ➔ Aaaaa).
        Returns minimum, maximum sustained pitch, and total semitone range.
        """
        audio, sr = read_wav_mono(wav_path)
        times, pitches = extract_pitch_contour(
            audio, sr, min_hz=55.0, max_hz=1000.0, voicing_threshold=0.32
        )

        voiced = pitches[pitches > 0]
        if len(voiced) < 15:
            raise ValueError("Insufficient vocal signal detected. Please sing louder or check microphone input.")

        # Apply 5-point moving median filter to eliminate transient octave jumps
        kernel_size = 5
        clean_pitches = np.convolve(voiced, np.ones(kernel_size) / kernel_size, mode="valid")

        # Use 3rd and 97th percentiles to avoid breath pops and spurious clicks
        p_min = float(np.percentile(clean_pitches, 3))
        p_max = float(np.percentile(clean_pitches, 97))

        midi_min = hz_to_midi(p_min)
        midi_max = hz_to_midi(p_max)
        semitones = max(0.0, midi_max - midi_min)

        return {
            "min_hz": round(p_min, 1),
            "min_note": midi_to_note_name(midi_min),
            "max_hz": round(p_max, 1),
            "max_note": midi_to_note_name(midi_max),
            "range_semitones": round(semitones, 1),
            "octaves": round(semitones / 12.0, 2),
        }

    def analyze_speaking_anchor(self, wav_path: Path) -> Dict[str, Any]:
        """
        Exercise 2: Analyzes natural speaking countdown (20 ➔ 0).
        Extracts median speaking fundamental (F0 speak) and derives the Power Tessitura.
        """
        audio, sr = read_wav_mono(wav_path)
        # Speaking voice fundamental search window: 60 Hz to 350 Hz
        times, pitches = extract_pitch_contour(
            audio, sr, frame_size_ms=45.0, min_hz=60.0, max_hz=350.0, voicing_threshold=0.35
        )

        voiced = pitches[pitches > 0]
        if len(voiced) < 15:
            raise ValueError("Insufficient voice detected in speech recording. Please speak louder and clearly.")

        median_hz = float(np.median(voiced))
        anchor_midi = hz_to_midi(median_hz)

        # In vocal pedagogy, the comfortable power tessitura extends:
        # Floor: ~2 semitones below speaking pitch
        # Ceiling: ~13 semitones above speaking pitch (1 octave + 1 semitone)
        tess_min_midi = anchor_midi - 2.0
        tess_max_midi = anchor_midi + 13.0

        tess_min_hz = midi_to_hz(tess_min_midi)
        tess_max_hz = midi_to_hz(tess_max_midi)
        vocal_type = classify_voice_type(0.0, 0.0, anchor_midi)

        return {
            "anchor_hz": round(median_hz, 1),
            "anchor_note": midi_to_note_name(anchor_midi),
            "tessitura_min_hz": round(tess_min_hz, 1),
            "tessitura_min_note": midi_to_note_name(tess_min_midi),
            "tessitura_max_hz": round(tess_max_hz, 1),
            "tessitura_max_note": midi_to_note_name(tess_max_midi),
            "vocal_type": vocal_type,
        }

    def combine_and_save(
        self,
        siren_res: Dict[str, Any],
        anchor_res: Dict[str, Any]
    ) -> VocalProfile:
        """Combines results from both exercises into a verified VocalProfile and saves it."""
        profile = self.update_siren_sweep(siren_res)
        profile = self.update_speaking_anchor(anchor_res)
        return profile

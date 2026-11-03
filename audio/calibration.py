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

        # Search for peak within allowable pitch period range
        if max_period >= len(norm_corr):
            max_period = len(norm_corr) - 1
        if min_period >= max_period:
            continue

        search_slice = norm_corr[min_period:max_period]
        if len(search_slice) == 0:
            continue

        peak_idx = np.argmax(search_slice) + min_period
        peak_val = norm_corr[peak_idx]

        if peak_val >= voicing_threshold:
            # Parabolic interpolation for sub-sample accuracy
            if 0 < peak_idx < len(norm_corr) - 1:
                alpha = norm_corr[peak_idx - 1]
                beta = norm_corr[peak_idx]
                gamma = norm_corr[peak_idx + 1]
                denom = alpha - 2 * beta + gamma
                if abs(denom) > 1e-7:
                    delta = 0.5 * (alpha - gamma) / denom
                    true_lag = peak_idx + delta
                else:
                    true_lag = float(peak_idx)
            else:
                true_lag = float(peak_idx)

            if true_lag > 0:
                pitches[i] = sr / true_lag

    return times, pitches


@dataclass
class VocalProfile:
    # Speaking Anchor
    anchor_hz: float
    anchor_note: str

    # Absolute Vocal Range (From Siren Sweep)
    range_min_hz: float
    range_min_note: str
    range_max_hz: float
    range_max_note: str
    range_semitones: float

    # Power Tessitura (Comfort Zone)
    tessitura_min_hz: float
    tessitura_min_note: str
    tessitura_max_hz: float
    tessitura_max_note: str

    vocal_type: str
    calibrated_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> VocalProfile:
        return cls(**data)


def classify_voice_type(min_midi: float, max_midi: float, anchor_midi: float) -> str:
    """Classifies voice category based on range and speaking fundamental."""
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

    def analyze_siren_sweep(self, wav_path: Path) -> Dict[str, Any]:
        """
        Exercise 1: Analyzes the Siren Glide recording (Ooooo ➔ Aaaaa).
        Returns minimum, maximum sustained pitch, and total semitone range.
        """
        audio, sr = read_wav_mono(wav_path)
        times, pitches = extract_pitch_contour(audio, sr, voicing_threshold=0.35)

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
        times, pitches = extract_pitch_contour(audio, sr, voicing_threshold=0.40)

        voiced = pitches[pitches > 0]
        if len(voiced) < 15:
            raise ValueError("Insufficient voice detected in speech recording.")

        median_hz = float(np.median(voiced))
        anchor_midi = hz_to_midi(median_hz)

        # In vocal pedagogy, the comfortable power tessitura extends:
        # Floor: ~2 semitones below speaking pitch
        # Ceiling: ~13 semitones above speaking pitch (1 octave + 1 semitone)
        tess_min_midi = anchor_midi - 2.0
        tess_max_midi = anchor_midi + 13.0

        tess_min_hz = midi_to_hz(tess_min_midi)
        tess_max_hz = midi_to_hz(tess_max_midi)

        return {
            "anchor_hz": round(median_hz, 1),
            "anchor_note": midi_to_note_name(anchor_midi),
            "tessitura_min_hz": round(tess_min_hz, 1),
            "tessitura_min_note": midi_to_note_name(tess_min_midi),
            "tessitura_max_hz": round(tess_max_hz, 1),
            "tessitura_max_note": midi_to_note_name(tess_max_midi),
        }

    def combine_and_save(
        self,
        siren_res: Dict[str, Any],
        anchor_res: Dict[str, Any]
    ) -> VocalProfile:
        """Combines results from both exercises into a verified VocalProfile and saves it."""
        min_midi = hz_to_midi(siren_res["min_hz"])
        max_midi = hz_to_midi(siren_res["max_hz"])
        anchor_midi = hz_to_midi(anchor_res["anchor_hz"])

        vocal_type = classify_voice_type(min_midi, max_midi, anchor_midi)

        profile = VocalProfile(
            anchor_hz=anchor_res["anchor_hz"],
            anchor_note=anchor_res["anchor_note"],
            range_min_hz=siren_res["min_hz"],
            range_min_note=siren_res["min_note"],
            range_max_hz=siren_res["max_hz"],
            range_max_note=siren_res["max_note"],
            range_semitones=siren_res["range_semitones"],
            tessitura_min_hz=anchor_res["tessitura_min_hz"],
            tessitura_min_note=anchor_res["tessitura_min_note"],
            tessitura_max_hz=anchor_res["tessitura_max_hz"],
            tessitura_max_note=anchor_res["tessitura_max_note"],
            vocal_type=vocal_type,
            calibrated_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        )

        self.save_profile(profile)
        return profile

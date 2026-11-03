"""
Tiki Rehearsal Lounge - Multi-Instrument Tone Generator & Warmup Pitch Analyzer
===============================================================================
1. Pure-Python harmonic audio synthesis of realistic instrument reference tones:
   - Acoustic Grand Piano
   - Rhodes Electric Piano
   - Cello & Warm Strings
   - Synth Flute
   - Acoustic Guitar
   - Warm Brass & Sax
2. Dynamic voice-adaptive instrument sorting.
3. Audio playback pipeline via system native audio backend (paplay, pw-play, aplay).
4. Automated Warmup Pitch Evaluator comparing sung pitch against target notes.
"""

from __future__ import annotations
import os
import re
import math
import wave
import shutil
import struct
import tempfile
import threading
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Callable

import numpy as np
from config import STUDIO_CONFIG_DIR
from audio.calibration import (
    read_wav_mono,
    extract_pitch_contour,
    hz_to_midi,
    midi_to_hz,
    midi_to_note_name,
    cents_deviation,
    NOTE_NAMES
)

TONES_DIR = STUDIO_CONFIG_DIR / "tones"
TONES_DIR.mkdir(parents=True, exist_ok=True)

# Note Name Regex
NOTE_RE = re.compile(r"\b([A-G][#b]?[1-6])\b", re.IGNORECASE)

# MIDI Base offsets
NOTE_INDEX_MAP = {
    "C": 0, "B#": 0,
    "C#": 1, "DB": 1,
    "D": 2,
    "D#": 3, "EB": 3,
    "E": 4, "FB": 4,
    "F": 5, "E#": 5,
    "F#": 6, "GB": 6,
    "G": 7,
    "G#": 8, "AB": 8,
    "A": 9,
    "A#": 10, "BB": 10,
    "B": 11, "CB": 11
}

INSTRUMENT_DEFS = {
    "piano": {
        "name": "Acoustic Grand Piano",
        "icon": "🎹",
        "harmonics": [1.0, 0.65, 0.40, 0.22, 0.12, 0.06, 0.03, 0.015],
        "attack_s": 0.012,
        "decay_rate": 2.2,
        "vibrato_hz": 0.0,
        "vibrato_depth": 0.0
    },
    "rhodes": {
        "name": "Rhodes Electric Piano",
        "icon": "⚡",
        "harmonics": [1.0, 0.38, 0.14, 0.05, 0.015],
        "attack_s": 0.020,
        "decay_rate": 1.6,
        "vibrato_hz": 4.8,
        "vibrato_depth": 0.0025
    },
    "strings": {
        "name": "Cello & Warm Strings",
        "icon": "🎻",
        "harmonics": [1.0, 0.85, 0.65, 0.45, 0.30, 0.20, 0.12, 0.08],
        "attack_s": 0.080,
        "decay_rate": 0.5,
        "vibrato_hz": 5.2,
        "vibrato_depth": 0.005
    },
    "flute": {
        "name": "Synth Flute",
        "icon": "🪈",
        "harmonics": [1.0, 0.08, 0.16, 0.03, 0.01],
        "attack_s": 0.045,
        "decay_rate": 0.4,
        "vibrato_hz": 5.5,
        "vibrato_depth": 0.006
    },
    "guitar": {
        "name": "Acoustic Guitar",
        "icon": "🎸",
        "harmonics": [1.0, 0.72, 0.48, 0.28, 0.16, 0.09, 0.04],
        "attack_s": 0.015,
        "decay_rate": 2.5,
        "vibrato_hz": 0.0,
        "vibrato_depth": 0.0
    },
    "brass": {
        "name": "Warm Brass & Sax",
        "icon": "🎷",
        "harmonics": [0.75, 1.0, 0.90, 0.65, 0.40, 0.25, 0.15, 0.08],
        "attack_s": 0.040,
        "decay_rate": 0.7,
        "vibrato_hz": 4.5,
        "vibrato_depth": 0.004
    }
}


def note_name_to_midi(name: str) -> Optional[int]:
    """Converts a note string like 'C4', 'F#3', 'Eb2' to MIDI integer (60 = C4)."""
    match = re.match(r"^([A-Ga-g][#b]?)([0-9])$", name.strip())
    if not match:
        return None
    note_part = match.group(1).upper()
    octave = int(match.group(2))
    idx = NOTE_INDEX_MAP.get(note_part)
    if idx is None:
        return None
    return (octave + 1) * 12 + idx


def note_name_to_hz(name: str) -> Optional[float]:
    """Converts a note string to frequency in Hz."""
    m = note_name_to_midi(name)
    if m is None:
        return None
    return midi_to_hz(m)


def parse_target_notes(text: str) -> List[str]:
    """Extracts all valid musical notes in sequence from text."""
    matches = NOTE_RE.findall(text)
    # Normalize notes (uppercase note name with octave)
    result = []
    for m in matches:
        m_norm = m.capitalize()
        if note_name_to_midi(m_norm) is not None:
            result.append(m_norm)
    return result


def get_sorted_instruments(vocal_type: Optional[str] = None) -> List[Dict[str, str]]:
    """
    Returns instruments sorted by pedagogical suitability for the singer's vocal type.
    """
    v_lower = (vocal_type or "").lower()

    if any(k in v_lower for k in ["bass", "baritone", "contralto"]):
        order = [
            ("strings", " (Suggested for Low Range)"),
            ("piano", " (Universal Standard)"),
            ("rhodes", ""),
            ("guitar", ""),
            ("brass", ""),
            ("flute", "")
        ]
    elif any(k in v_lower for k in ["soprano", "high tenor"]):
        order = [
            ("flute", " (Suggested for High Range)"),
            ("piano", " (Universal Standard)"),
            ("rhodes", ""),
            ("strings", ""),
            ("guitar", ""),
            ("brass", "")
        ]
    else:  # Tenor, Mezzo-Soprano, Contemporary
        order = [
            ("piano", " (Suggested for Mid Voice)"),
            ("rhodes", " (Smooth Harmonics)"),
            ("flute", ""),
            ("strings", ""),
            ("guitar", ""),
            ("brass", "")
        ]

    items = []
    for key, badge in order:
        defn = INSTRUMENT_DEFS[key]
        display = f"{defn['icon']} {defn['name']}{badge}"
        items.append({
            "key": key,
            "display": display,
            "name": defn["name"]
        })
    return items


class ToneSynthesizer:
    """Synthesizes and caches realistic reference instrument tones."""

    def __init__(self):
        self._active_play_proc: Optional[subprocess.Popen] = None
        self._pattern_thread: Optional[threading.Thread] = None
        self._pattern_stop_event = threading.Event()
        self._find_audio_player()

    def _find_audio_player(self):
        """Discovers the best low-latency CLI audio player on Linux."""
        for player in ["paplay", "pw-play", "aplay"]:
            path = shutil.which(player)
            if path:
                self._player_bin = path
                self._player_type = player
                return
        self._player_bin = shutil.which("mpv") or "mpv"
        self._player_type = "mpv"

    def synthesize_wave(
        self,
        freq_hz: float,
        duration_s: float = 1.8,
        instrument: str = "piano",
        sample_rate: int = 44100
    ) -> np.ndarray:
        """Synthesizes high-fidelity instrument audio using additive harmonic modeling."""
        defn = INSTRUMENT_DEFS.get(instrument, INSTRUMENT_DEFS["piano"])
        total_samples = int(sample_rate * duration_s)
        t = np.linspace(0, duration_s, total_samples, endpoint=False)

        # Modulate frequency for subtle vibrato
        vib_hz = defn["vibrato_hz"]
        vib_depth = defn["vibrato_depth"]
        if vib_hz > 0 and vib_depth > 0:
            freq_mod = freq_hz * (1.0 + vib_depth * np.sin(2 * np.pi * vib_hz * t))
            phase = 2 * np.pi * np.cumsum(freq_mod) / sample_rate
        else:
            phase = 2 * np.pi * freq_hz * t

        # Additive harmonics
        signal = np.zeros(total_samples, dtype=np.float32)
        harmonics = defn["harmonics"]
        for idx, weight in enumerate(harmonics, start=1):
            h_phase = phase * idx
            signal += weight * np.sin(h_phase)

        # Extra transient attack character
        if instrument == "piano":
            # Add subtle percussive hammer strike thump (low-frequency impulse)
            hammer = np.sin(2 * np.pi * 120.0 * t[:int(sample_rate * 0.035)]) * np.exp(-t[:int(sample_rate * 0.035)] * 90)
            signal[:len(hammer)] += hammer * 0.25
        elif instrument == "rhodes":
            # Add metallic tine ring at 4.2x fundamental
            tine_len = int(sample_rate * 0.12)
            tine = np.sin(phase[:tine_len] * 4.2) * np.exp(-t[:tine_len] * 40.0)
            signal[:tine_len] += tine * 0.35

        # ADSR Amplitude Envelope
        attack_s = defn["attack_s"]
        attack_samples = max(1, int(sample_rate * attack_s))
        decay_rate = defn["decay_rate"]

        envelope = np.exp(-decay_rate * t)
        if attack_samples > 1:
            attack_curve = np.linspace(0, 1.0, attack_samples)
            envelope[:attack_samples] *= attack_curve

        # Smooth release at tail to eliminate any click
        tail_samples = int(sample_rate * 0.05)
        if tail_samples > 0:
            tail_fade = np.linspace(1.0, 0.0, tail_samples)
            envelope[-tail_samples:] *= tail_fade

        signal *= envelope

        # Normalize and convert to 16-bit PCM
        peak = np.max(np.abs(signal))
        if peak > 1e-6:
            signal = (signal / peak) * 0.85
        return (signal * 32767).astype(np.int16)

    def get_or_render_tone_file(self, note_name: str, instrument: str = "piano") -> Path:
        """Renders and caches a single tone WAV file."""
        clean_note = note_name.upper().strip()
        filename = f"tone_{instrument}_{clean_note}.wav"
        target_path = TONES_DIR / filename
        if target_path.exists() and target_path.stat().st_size > 1000:
            return target_path

        freq_hz = note_name_to_hz(clean_note)
        if not freq_hz:
            freq_hz = 261.63  # Middle C fallback

        audio_pcm = self.synthesize_wave(freq_hz, duration_s=1.8, instrument=instrument)
        with wave.open(str(target_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(44100)
            wf.writeframes(audio_pcm.tobytes())

        return target_path

    def play_note(self, note_name: str, instrument: str = "piano"):
        """Plays a single note asynchronously with instant response."""
        self.stop_playback()
        wav_path = self.get_or_render_tone_file(note_name, instrument)

        cmd = [self._player_bin, str(wav_path)]
        if self._player_type == "aplay":
            cmd = [self._player_bin, "-q", str(wav_path)]
        elif self._player_type == "mpv":
            cmd = [self._player_bin, "--no-video", "--really-quiet", str(wav_path)]

        try:
            self._active_play_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception as e:
            print(f"[ToneSynthesizer] Audio play error: {e}")

    def play_pattern(
        self,
        notes: List[str],
        instrument: str = "piano",
        bpm: float = 95.0,
        on_step_cb: Optional[Callable[[int, str], None]] = None,
        on_finished_cb: Optional[Callable[[], None]] = None
    ):
        """Plays a sequence of notes at steady tempo asynchronously."""
        self.stop_playback()
        self._pattern_stop_event.clear()

        def _worker():
            step_interval = 60.0 / bpm
            for idx, note in enumerate(notes):
                if self._pattern_stop_event.is_set():
                    break
                if on_step_cb:
                    on_step_cb(idx, note)
                self.play_note(note, instrument)
                # Sleep interval
                t0 = threading.Event()
                t0.wait(step_interval)

            if on_finished_cb and not self._pattern_stop_event.is_set():
                on_finished_cb()

        self._pattern_thread = threading.Thread(target=_worker, daemon=True)
        self._pattern_thread.start()

    def stop_playback(self):
        """Immediately silences any active tone or pattern."""
        self._pattern_stop_event.set()
        if self._active_play_proc and self._active_play_proc.poll() is None:
            try:
                self._active_play_proc.terminate()
            except Exception:
                pass
            self._active_play_proc = None


# Global instance
tone_synth = ToneSynthesizer()


# ---------------------------------------------------------------------------
# WARMUP PITCH EVALUATOR
# ---------------------------------------------------------------------------
def evaluate_warmup_take(
    take_wav: Path,
    target_notes: List[str]
) -> Dict[str, Any]:
    """
    Evaluates recorded vocal warmup take against target notes:
    - Pitch accuracy per target note
    - Duration sustained on target
    - Cents offset (sharp / flat)
    - Highest & lowest notes reached
    - Vocal steadiness & coach diagnosis
    """
    if not take_wav or not take_wav.exists():
        return {
            "success": False,
            "error": "Warmup take audio file not found."
        }

    try:
        audio, sr = read_wav_mono(take_wav)
    except Exception as e:
        return {"success": False, "error": f"Failed to read take audio: {e}"}

    if len(audio) < sr * 0.5:
        return {"success": False, "error": "Take was too brief to evaluate (under 0.5s)."}

    times, pitches = extract_pitch_contour(audio, sr)
    voiced = pitches[pitches > 0]

    if len(voiced) < 8:
        return {
            "success": False,
            "error": "No clear voiced singing detected. Make sure your microphone is close and volume is steady."
        }

    # Range limits reached
    min_hz = float(np.min(voiced))
    max_hz = float(np.max(voiced))
    min_note = midi_to_note_name(hz_to_midi(min_hz))
    max_note = midi_to_note_name(hz_to_midi(max_hz))

    # Evaluate target notes
    hop_sec = 0.015  # 15ms default in extract_pitch_contour
    evaluations = []
    notes_hit_count = 0

    for note in target_notes:
        target_hz = note_name_to_hz(note)
        target_midi = note_name_to_midi(note)
        if not target_hz or target_midi is None:
            continue

        # Look for voiced frames within ±50 cents of target MIDI
        cents_diffs = []
        consec_count = 0
        max_consec = 0

        for p in voiced:
            c = (hz_to_midi(p) - target_midi) * 100.0
            if abs(c) <= 50.0:
                cents_diffs.append(c)
                consec_count += 1
                if consec_count > max_consec:
                    max_consec = consec_count
            else:
                consec_count = 0

        sustained_sec = len(cents_diffs) * hop_sec
        is_hit = sustained_sec >= 0.20  # At least 200ms sustained within 50 cents

        if cents_diffs:
            median_cents = float(np.median(cents_diffs))
        else:
            # Find closest pitch sung to target
            all_diffs = [(hz_to_midi(p) - target_midi) * 100.0 for p in voiced]
            closest = min(all_diffs, key=abs)
            median_cents = float(closest)

        if is_hit:
            notes_hit_count += 1

        evaluations.append({
            "note": note,
            "target_hz": target_hz,
            "hit": is_hit,
            "cents_err": median_cents,
            "duration_held": sustained_sec
        })

    # Overall Steadiness Score
    diffs = np.abs(np.diff(voiced))
    steadiness_pct = float(np.clip(100.0 - (np.mean(diffs) * 4.0), 40.0, 98.0))

    # Targeted Coach Tiki Diagnosis
    coach_diagnosis = _generate_warmup_diagnosis(evaluations, min_note, max_note, steadiness_pct)

    return {
        "success": True,
        "evaluations": evaluations,
        "notes_hit_count": notes_hit_count,
        "total_targets": len(evaluations),
        "hit_ratio": notes_hit_count / max(1, len(evaluations)),
        "min_note": min_note,
        "max_note": max_note,
        "steadiness_pct": steadiness_pct,
        "coach_diagnosis": coach_diagnosis
    }


def _generate_warmup_diagnosis(
    evals: List[Dict[str, Any]],
    min_note: str,
    max_note: str,
    steadiness_pct: float
) -> str:
    """Generates an insightful, pedagogical assessment of the warmup performance."""
    if not evals:
        return f"Voiced span: {min_note} to {max_note}. Pitch steadiness: {steadiness_pct:.0f}%."

    all_hit = all(e["hit"] for e in evals)
    hit_notes = [e["note"] for e in evals if e["hit"]]
    missed_notes = [e["note"] for e in evals if not e["hit"]]

    flats = [e for e in evals if e["cents_err"] < -18.0]
    sharps = [e for e in evals if e["cents_err"] > +18.0]

    lines = []
    if all_hit:
        lines.append(f"🌟 Excellent pitch placement! You nailed all target notes cleanly ({', '.join(hit_notes)}) across a span of {min_note} to {max_note}.")
        if steadiness_pct >= 85:
            lines.append("Vocal cords exhibited solid subglottic breath lock with zero line-end droop.")
        else:
            lines.append("Pitch steadiness had slight waver—keep your lower ribcage expanded through the duration.")
    else:
        if hit_notes:
            lines.append(f"Solid grounding on {', '.join(hit_notes)}.")
        if missed_notes:
            lines.append(f"⚠️ Target note{'s' if len(missed_notes)>1 else ''} {', '.join(missed_notes)} were missed or only touched momentarily.")

        if flats:
            flat_names = ", ".join(f"{f['note']} ({f['cents_err']:.0f}¢)" for f in flats)
            lines.append(f"Tendency to pull flat on {flat_names}. Tip: Think 'down' as your melody goes higher, and lift your soft palate to open space.")
        elif sharps:
            sharp_names = ", ".join(f"{s['note']} (+{s['cents_err']:.0f}¢)" for s in sharps)
            lines.append(f"Slightly sharp on {sharp_names}. Tip: Reduce breath pressure and allow cords to vibrate freely without pushing.")
        else:
            lines.append(f"Span reached: {min_note} to {max_note}. Try humming the reference pattern first before singing with open vowels.")

    return " ".join(lines)

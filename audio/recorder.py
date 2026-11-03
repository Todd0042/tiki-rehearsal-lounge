"""
Tiki Rehearsal Lounge - Low-Latency Audio Recorder
===================================================
Captures microphone audio to WAV with zero external C-dependencies.
Supports PipeWire (`pw-record`), PulseAudio (`parec` / `ffmpeg`), or `arecord`,
with live RMS and Peak callbacks to feed real-time neon VU meters.
"""

from __future__ import annotations
import os
import sys
import time
import shutil
import wave
import struct
import math
import subprocess
import threading
from pathlib import Path
from typing import Optional, Callable


class AudioRecorder:
    def __init__(
        self,
        sample_rate: int = 44100,
        channels: int = 1,
        on_level: Optional[Callable[[float, float], None]] = None  # (rms, peak) normalized 0.0 - 1.0
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.on_level = on_level

        self._process: Optional[subprocess.Popen] = None
        self._record_thread: Optional[threading.Thread] = None
        self._is_recording = False
        self._output_path: Optional[Path] = None
        self._start_time: float = 0.0

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def elapsed_seconds(self) -> float:
        if not self._is_recording:
            return 0.0
        return max(0.0, time.time() - self._start_time)

    def start_recording(self, output_path: Path) -> bool:
        """Starts background recording to the target WAV file."""
        if self._is_recording:
            return False

        self._output_path = Path(output_path)
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        self._is_recording = True
        self._start_time = time.time()

        # Determine best available system recorder
        cmd = self._build_record_command()
        if not cmd:
            print("[AudioRecorder] Error: No suitable audio capture tool found (pw-record, ffmpeg, or arecord).", file=sys.stderr)
            self._is_recording = False
            return False

        try:
            # Start process streaming raw 16-bit mono PCM to stdout
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=2048
            )
        except Exception as e:
            print(f"[AudioRecorder] Failed to spawn capture process: {e}", file=sys.stderr)
            self._is_recording = False
            return False

        # Spawn reader thread to capture stream, calculate live VU, and write WAV
        self._record_thread = threading.Thread(
            target=self._capture_worker,
            daemon=True,
            name="AudioRecorderWorker"
        )
        self._record_thread.start()
        return True

    def stop_recording(self) -> Optional[Path]:
        """Stops the recording process and finalizes the WAV file."""
        if not self._is_recording:
            return self._output_path

        self._is_recording = False

        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=1.5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

        if self._record_thread and self._record_thread.is_alive():
            self._record_thread.join(timeout=2.0)

        # Notify level reset
        if self.on_level:
            try:
                self.on_level(0.0, 0.0)
            except Exception:
                pass

        if self._output_path and self._output_path.exists() and self._output_path.stat().st_size > 44:
            return self._output_path
        return None

    def _build_record_command(self) -> Optional[list[str]]:
        """Selects the cleanest capture command on the system."""
        # 1. Native PipeWire pw-record (Raw 16-bit PCM to stdout)
        if shutil.which("pw-record"):
            return [
                "pw-record",
                "--rate", str(self.sample_rate),
                "--channels", str(self.channels),
                "--format", "s16",
                "-"
            ]

        # 2. FFmpeg from pulse source
        if shutil.which("ffmpeg"):
            return [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "quiet",
                "-f", "pulse",
                "-i", "default",
                "-ac", str(self.channels),
                "-ar", str(self.sample_rate),
                "-f", "s16le",
                "-"
            ]

        # 3. ALSA arecord
        if shutil.which("arecord"):
            return [
                "arecord",
                "-q",
                "-r", str(self.sample_rate),
                "-c", str(self.channels),
                "-f", "S16_LE",
                "-t", "raw"
            ]

        return None

    def _capture_worker(self):
        """Streams audio data, computes live levels, and writes to WAV file."""
        chunk_samples = 1024
        bytes_per_sample = 2  # 16-bit
        chunk_bytes = chunk_samples * bytes_per_sample * self.channels

        try:
            with wave.open(str(self._output_path), "wb") as wav_file:
                wav_file.setnchannels(self.channels)
                wav_file.setsampwidth(bytes_per_sample)
                wav_file.setframerate(self.sample_rate)

                while self._is_recording and self._process and self._process.poll() is None:
                    raw_data = self._process.stdout.read(chunk_bytes)
                    if not raw_data:
                        break

                    wav_file.writeframes(raw_data)

                    # Compute live RMS & Peak for VU meter
                    if self.on_level:
                        rms, peak = self._calculate_levels(raw_data)
                        try:
                            self.on_level(rms, peak)
                        except Exception:
                            pass
        except Exception as e:
            print(f"[AudioRecorder] Capture worker exception: {e}", file=sys.stderr)
        finally:
            if self.on_level:
                try:
                    self.on_level(0.0, 0.0)
                except Exception:
                    pass

    @staticmethod
    def _calculate_levels(raw_bytes: bytes) -> tuple[float, float]:
        """Calculates normalized RMS and Peak from 16-bit PCM bytes."""
        count = len(raw_bytes) // 2
        if count == 0:
            return 0.0, 0.0

        shorts = struct.unpack(f"<{count}h", raw_bytes[:count * 2])
        sum_sq = 0.0
        peak = 0

        for s in shorts:
            val = abs(s)
            if val > peak:
                peak = val
            sum_sq += s * s

        rms = math.sqrt(sum_sq / count)
        norm_rms = min(1.0, rms / 32767.0)
        norm_peak = min(1.0, peak / 32767.0)
        return norm_rms, norm_peak

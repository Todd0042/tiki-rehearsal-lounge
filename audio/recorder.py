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
        on_level: Optional[Callable[[float, float], None]] = None,  # (rms, peak) normalized 0.0 - 1.0
        on_pitch: Optional[Callable[[float], None]] = None          # live fundamental frequency in Hz (0.0 = unvoiced)
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.on_level = on_level
        self.on_pitch = on_pitch

        self._process: Optional[subprocess.Popen] = None
        self._record_thread: Optional[threading.Thread] = None
        self._sd_stream = None
        self._wav_file: Optional[wave.Wave_write] = None
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

        # 1. Primary: Try native sounddevice (PortAudio/WASAPI/DirectSound/ALSA/Pulse)
        try:
            import sounddevice as sd
            self._wav_file = wave.open(str(self._output_path), "wb")
            self._wav_file.setnchannels(self.channels)
            self._wav_file.setsampwidth(2)
            self._wav_file.setframerate(self.sample_rate)

            def _sd_callback(indata, frames, time_info, status):
                if not self._is_recording:
                    return
                raw_bytes = indata.tobytes()
                if self._wav_file:
                    try:
                        self._wav_file.writeframes(raw_bytes)
                    except Exception:
                        pass

                rms, peak = 0.0, 0.0
                if self.on_level:
                    rms, peak = self._calculate_levels(raw_bytes)
                    try:
                        self.on_level(rms, peak)
                    except Exception:
                        pass

                if self.on_pitch:
                    self._process_live_pitch(raw_bytes, rms)

            self._sd_stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                callback=_sd_callback,
                blocksize=1024
            )
            self._sd_stream.start()
            return True
        except Exception as e:
            # Sounddevice failed or not present; fall back to subprocess capture
            if self._wav_file:
                try:
                    self._wav_file.close()
                except Exception:
                    pass
                self._wav_file = None
            self._sd_stream = None

        # 2. Secondary: Subprocess-based capture (pw-record, ffmpeg, arecord)
        cmd = self._build_record_command()
        if not cmd:
            print("[AudioRecorder] Error: No suitable audio capture tool found (sounddevice, pw-record, ffmpeg, or arecord).", file=sys.stderr)
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

        # Stop sounddevice stream if active
        if self._sd_stream:
            try:
                self._sd_stream.stop()
                self._sd_stream.close()
            except Exception:
                pass
            self._sd_stream = None

        if self._wav_file:
            try:
                self._wav_file.close()
            except Exception:
                pass
            self._wav_file = None

        # Terminate subprocess if active
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
        if self.on_pitch:
            try:
                self.on_pitch(0.0)
            except Exception:
                pass

        if self._output_path and self._output_path.exists() and self._output_path.stat().st_size > 44:
            return self._output_path
        return None

    def _build_record_command(self) -> Optional[list[str]]:
        """Selects the cleanest capture command on the system across platforms."""
        from config import get_bundled_binary_path
        ffmpeg_bin = get_bundled_binary_path("ffmpeg") or shutil.which("ffmpeg")

        # 1. Windows: FFmpeg DirectShow default audio capture
        if sys.platform == "win32" and ffmpeg_bin:
            return [
                str(ffmpeg_bin),
                "-hide_banner",
                "-loglevel", "quiet",
                "-f", "dshow",
                "-i", "audio=default",
                "-ac", str(self.channels),
                "-ar", str(self.sample_rate),
                "-f", "s16le",
                "-"
            ]

        # 2. Linux: Native PipeWire pw-record (Raw 16-bit PCM to stdout)
        if shutil.which("pw-record"):
            return [
                "pw-record",
                "--rate", str(self.sample_rate),
                "--channels", str(self.channels),
                "--format", "s16",
                "-"
            ]

        # 3. Linux: FFmpeg from pulse source
        if ffmpeg_bin:
            return [
                str(ffmpeg_bin),
                "-hide_banner",
                "-loglevel", "quiet",
                "-f", "pulse",
                "-i", "default",
                "-ac", str(self.channels),
                "-ar", str(self.sample_rate),
                "-f", "s16le",
                "-"
            ]

        # 4. Linux: ALSA arecord
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
                    rms, peak = 0.0, 0.0
                    if self.on_level:
                        rms, peak = self._calculate_levels(raw_data)
                        try:
                            self.on_level(rms, peak)
                        except Exception:
                            pass

                    # Compute live pitch if listener is registered
                    if self.on_pitch:
                        self._process_live_pitch(raw_data, rms)
        except Exception as e:
            print(f"[AudioRecorder] Capture worker exception: {e}", file=sys.stderr)
        finally:
            if self.on_level:
                try:
                    self.on_level(0.0, 0.0)
                except Exception:
                    pass
            if self.on_pitch:
                try:
                    self.on_pitch(0.0)
                except Exception:
                    pass

    def _process_live_pitch(self, raw_data: bytes, rms: float):
        """Extracts instant fundamental frequency (F0) from 16-bit PCM chunk via autocorrelation."""
        if not self.on_pitch:
            return
        if rms < 0.02:
            try:
                self.on_pitch(0.0)
            except Exception:
                pass
            return

        try:
            import numpy as np
            shorts = np.frombuffer(raw_data, dtype=np.int16).astype(np.float32) / 32768.0
            corr = np.correlate(shorts, shorts, mode="full")
            corr = corr[len(shorts) - 1 :]
            if corr[0] > 1e-5:
                norm_corr = corr / corr[0]
                min_lag = int(self.sample_rate / 650.0)  # max 650 Hz
                max_lag = int(self.sample_rate / 65.0)   # min 65 Hz
                max_lag = min(max_lag, len(norm_corr) - 2)
                if min_lag < max_lag:
                    peak_idx = int(np.argmax(norm_corr[min_lag:max_lag]) + min_lag)
                    if norm_corr[peak_idx] >= 0.35:
                        hz = float(self.sample_rate / peak_idx)
                        self.on_pitch(hz)
                    else:
                        self.on_pitch(0.0)
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

"""
Tiki Rehearsal Lounge - Synchronized Rehearsal Media Player
===========================================================
High-fidelity media player with real-time pitch shifting:
- Plays video (.mp4, .webm, .mkv), audio (.mp3), and CD+G (.cdg + .mp3)
- Real-time formant-preserved pitch shifting [-3, +3] semitones via librubberband
- Controls playback via mpv IPC JSON socket
- Emits playback completion events to automatically trigger take analysis
"""

from __future__ import annotations
import os
import sys
import json
import time
import socket
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional, Callable


class RehearsalPlayer:
    def __init__(self, on_finished: Optional[Callable[[], None]] = None):
        self.on_finished = on_finished
        self._process: Optional[subprocess.Popen] = None
        self._ipc_socket_path = Path(f"/tmp/tiki_rehearsal_mpv_{os.getpid()}.sock")
        self._ipc_pipe_name = rf"\\.\pipe\tiki_rehearsal_mpv_{os.getpid()}"
        self._monitor_thread: Optional[threading.Thread] = None
        self._is_playing = False
        self._current_file: Optional[Path] = None
        self._current_key_shift: int = 0
        self._mpv_path = self._find_player_bin()

    def _find_player_bin(self) -> str:
        from config import get_bundled_binary_path
        cand = get_bundled_binary_path("mpv")
        if cand:
            return str(cand)
        cand = shutil.which("mpv")
        if cand:
            return str(cand)
        cand = get_bundled_binary_path("ffplay")
        if cand:
            return str(cand)
        return shutil.which("ffplay") or "mpv"

    @property
    def is_playing(self) -> bool:
        return self._is_playing and (self._process is not None) and (self._process.poll() is None)

    @property
    def current_file(self) -> Optional[Path]:
        return self._current_file

    @property
    def current_key_shift(self) -> int:
        return self._current_key_shift

    def play(
        self,
        file_path: Path,
        key_shift: int = 0,
        fullscreen: bool = False
    ) -> bool:
        """
        Launches media playback with real-time pitch shift applied.
        """
        self.stop()

        target = Path(file_path).resolve()
        if not target.exists():
            print(f"[RehearsalPlayer] File not found: {target}", file=sys.stderr)
            return False

        self._current_file = target
        self._current_key_shift = int(max(-3, min(3, key_shift)))

        # Clean old socket if exists (Linux)
        if sys.platform != "win32" and self._ipc_socket_path.exists():
            try:
                self._ipc_socket_path.unlink()
            except Exception:
                pass

        # Compute pitch-scale factor for librubberband
        # 1 semitone = 2 ** (1 / 12) ≈ 1.059463
        pitch_scale = 2.0 ** (self._current_key_shift / 12.0)
        af_arg = f"rubberband=pitch-scale={pitch_scale:.6f}:pitch=consistency:formant=preserved:engine=finer"

        is_ffplay = "ffplay" in Path(self._mpv_path).name.lower()
        if is_ffplay:
            cmd = [
                self._mpv_path,
                str(target),
                "-af", af_arg,
                "-window_title", "🌴 Tiki Rehearsal Stage",
                "-autoexit"
            ]
            if fullscreen:
                cmd.append("-fs")
        else:
            ipc_arg = f"--input-ipc-server={self._ipc_pipe_name}" if sys.platform == "win32" else f"--input-ipc-server={self._ipc_socket_path}"
            cmd = [
                self._mpv_path,
                str(target),
                ipc_arg,
                f"--af={af_arg}",
                "--title=🌴 Tiki Rehearsal Stage",
                "--keep-open=no",
                "--force-window=yes",
                "--geometry=70%:70%",
                "--autofit=960x540",
            ]
            if fullscreen:
                cmd.append("--fs")

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            self._is_playing = True
        except Exception as e:
            print(f"[RehearsalPlayer] Failed to start mpv: {e}", file=sys.stderr)
            self._is_playing = False
            return False

        # Launch monitor thread to detect playback end
        self._monitor_thread = threading.Thread(
            target=self._playback_watcher,
            daemon=True,
            name="RehearsalPlayerWatcher"
        )
        self._monitor_thread.start()
        return True

    def stop(self):
        """Stops playback immediately."""
        self._is_playing = False
        if self._process:
            try:
                self._send_ipc_command(["quit"])
                self._process.terminate()
                self._process.wait(timeout=1.0)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            finally:
                self._process = None

        if self._ipc_socket_path.exists():
            try:
                self._ipc_socket_path.unlink()
            except Exception:
                pass

    def pause_toggle(self):
        """Toggles play / pause."""
        self._send_ipc_command(["cycle", "pause"])

    def set_key_shift(self, key_shift: int):
        """Updates pitch transposition in real time."""
        clamped = int(max(-3, min(3, key_shift)))
        if clamped == self._current_key_shift:
            return
        self._current_key_shift = clamped
        pitch_scale = 2.0 ** (self._current_key_shift / 12.0)
        af_arg = f"rubberband=pitch-scale={pitch_scale:.6f}:pitch=consistency:formant=preserved:engine=finer"
        self._send_ipc_command(["set_property", "af", af_arg])

    def get_time_pos(self) -> float:
        """Returns current playback position in seconds."""
        res = self._send_ipc_command(["get_property", "time-pos"])
        if res and isinstance(res.get("data"), (int, float)):
            return float(res["data"])
        return 0.0

    def _playback_watcher(self):
        """Monitors mpv process; fires on_finished callback upon completion."""
        if not self._process:
            return
        self._process.wait()
        was_active = self._is_playing
        self._is_playing = False
        if was_active and self.on_finished:
            try:
                self.on_finished()
            except Exception as e:
                print(f"[RehearsalPlayer] Error in on_finished callback: {e}", file=sys.stderr)

    def _send_ipc_command(self, command: list) -> Optional[dict]:
        """Sends a JSON command to mpv IPC socket (Linux) or Named Pipe (Windows)."""
        payload = json.dumps({"command": command}) + "\n"

        if sys.platform == "win32":
            try:
                with open(self._ipc_pipe_name, "r+b", buffering=0) as pipe:
                    pipe.write(payload.encode("utf-8"))
                    line = pipe.readline()
                    if line:
                        return json.loads(line.decode("utf-8").strip())
            except Exception:
                pass
            return None

        if not self._ipc_socket_path.exists():
            return None
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(0.5)
                client.connect(str(self._ipc_socket_path))
                client.sendall(payload.encode("utf-8"))
                resp_data = client.recv(4096)
                if resp_data:
                    return json.loads(resp_data.decode("utf-8").split("\n")[0])
        except Exception:
            pass
        return None

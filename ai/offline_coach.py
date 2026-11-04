"""
Tiki Rehearsal Lounge - Embedded 100% Offline AI Vocal Coach Engine
===================================================================
Operates 100% locally with zero cloud dependencies, zero external accounts,
zero API keys, and zero rate-limit quotas.

Supported Quantized GGUF Models:
1. Qwen2.5-3B-Instruct (Q4_K_M) - ~1.95 GB (Deep vocal pedagogy & nuanced feedback)
2. Qwen2.5-1.5B-Instruct (Q4_K_M) - ~986 MB (Sub-1 GB, ultra-fast generation on CPU)
"""

from __future__ import annotations
import os
import sys
import time
import json
import shutil
import socket
import atexit
import subprocess
import threading
import urllib.request
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable

from config import (
    COLOR_CYAN, COLOR_MAGENTA, COLOR_GOLD, COLOR_LIME,
    STUDIO_CONFIG_DIR, get_bundled_binary_path,
    load_studio_config, save_studio_config
)
from audio.tone_generator import transpose_note, clamp_note_to_range, parse_target_notes

MODELS_DIR = STUDIO_CONFIG_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

AVAILABLE_MODELS: Dict[str, Dict[str, Any]] = {
    "7b": {
        "key": "7b",
        "name": "Qwen2.5-7B-Instruct (Q4_K_M)",
        "tagline": "Master-Class Studio Director (6GB+ GPU / 8-Core CPU)",
        "filename": "qwen2.5-7b-instruct-q4_k_m.gguf",
        "size_str": "~4.68 GB",
        "size_bytes": 4683074240,
        "speed": "~60–90 tok/s on GPU • ~15–25 tok/s on CPU",
        "url": "https://huggingface.co/bartowski/Qwen2.5-7B-Instruct-GGUF/resolve/main/Qwen2.5-7B-Instruct-Q4_K_M.gguf"
    },
    "3b": {
        "key": "3b",
        "name": "Qwen2.5-3B-Instruct (Q4_K_M)",
        "tagline": "Deep Vocal Pedagogy & Musical Nuance",
        "filename": "qwen2.5-3b-instruct-q4_k_m.gguf",
        "size_str": "~1.95 GB",
        "size_bytes": 2085000000,
        "speed": "~80–120 tok/s on GPU • ~30–45 tok/s on CPU",
        "url": "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf"
    },
    "1.5b": {
        "key": "1.5b",
        "name": "Qwen2.5-1.5B-Instruct (Q4_K_M)",
        "tagline": "Ultra-Fast & Compact (Sub-1 GB)",
        "filename": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "size_str": "~986 MB",
        "size_bytes": 986000000,
        "speed": "~150+ tok/s on GPU • ~60–80 tok/s on CPU",
        "url": "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf"
    }
}

DEBRIEF_SYSTEM_PROMPT = (
    "You are Maestro Tiki, an encouraging, world-class vocal coach and master rehearsal director "
    "inside a retro neon Polynesian studio lounge. You analyze performance takes with sharp "
    "acoustic and musical precision (pitch accuracy %, intonation drift cents, rhythm onset latency ms, "
    "diaphragmatic breath support %, singer's formant resonance ring, glottal attack dynamics, vibrato dynamics, "
    "and vocal fry). Ground your evaluation strictly in the objective audio measurements provided. "
    "Provide warm, supportive, actionable feedback with specific physical vocal cues (soft palate, mask placement, "
    "ribcage expansion, tongue posture, key shift recommendations). "
    "Keep take debriefings focused, chronological, and pedagogical. Never sound robotic."
)

CHAT_SYSTEM_PROMPT = (
    "You are Maestro Tiki, an encouraging, candid, and expert vocal coach having a 1-on-1 dialogue "
    "with a singer in your neon studio control room right after their rehearsal take. "
    "You have direct access to their exact objective performance metrics (pitch accuracy %, intonation cents drift, "
    "rhythm pocket timing in milliseconds, breath support, vibrato, dynamics, and timestamped highlights/struggles) "
    "as well as the take debrief analysis you just provided. "
    "Answer the singer's questions directly, conversationally, and specifically using their real numbers and timestamps. "
    "When asked about timing, cite their rhythm pocket latency (in ms) and whether they were dragging or rushing. "
    "When asked about pitch or range, cite specific notes, cents drift, and whether a key shift is warranted. "
    "Speak naturally like a passionate mentor in 1-2 focused, conversational paragraphs. "
    "Do NOT use rigid boilerplate headings or unsolicited bulleted lists unless explicitly asked. "
    "Never be vague, never repeat yourself, and never contradict the take telemetry."
)

SYSTEM_PROMPT = DEBRIEF_SYSTEM_PROMPT


class OfflineVocalCoach:
    def __init__(self):
        self.models_dir = MODELS_DIR
        self._llm = None
        self._loaded_model_key: Optional[str] = None
        self._loaded_acceleration: Optional[str] = None
        self._load_lock = threading.Lock()
        self._server_proc: Optional[subprocess.Popen] = None
        self._server_port: Optional[int] = None
        self._server_url: Optional[str] = None
        atexit.register(self.shutdown_engine)

    def get_acceleration_mode(self) -> str:
        """Returns 'gpu' or 'cpu' from studio config (defaults to 'gpu')."""
        cfg = load_studio_config()
        mode = cfg.get("ai_acceleration", "gpu").lower()
        return "gpu" if mode in ("gpu", "cuda", "vulkan", "rocm", "metal") else "cpu"

    def set_acceleration_mode(self, mode: str) -> bool:
        """Sets acceleration mode ('gpu' or 'cpu'), saves to config, and reloads engine if changed."""
        mode = "gpu" if str(mode).lower() in ("gpu", "cuda", "vulkan", "rocm", "metal") else "cpu"
        cfg = load_studio_config()
        if cfg.get("ai_acceleration") == mode and self._loaded_acceleration == mode and self.is_engine_ready():
            return True
        cfg["ai_acceleration"] = mode
        save_studio_config(cfg)
        # If engine is currently loaded or running, restart it with new acceleration setting
        if self._server_proc is not None or self._llm is not None:
            self.shutdown_engine()
            return self.load_engine()
        return True

    def set_preferred_model(self, model_key: str) -> bool:
        """Sets preferred model key in studio config and reloads engine if needed."""
        if model_key not in AVAILABLE_MODELS:
            return False
        cfg = load_studio_config()
        cfg["ai_model_key"] = model_key
        save_studio_config(cfg)
        if self._loaded_model_key != model_key and self.is_engine_ready():
            self.shutdown_engine()
            return self.load_engine()
        return True

    def get_installed_model(self) -> Optional[Dict[str, Any]]:
        """Returns metadata of currently installed model, if any."""
        cfg = load_studio_config()
        preferred_key = cfg.get("ai_model_key")
        if preferred_key and preferred_key in AVAILABLE_MODELS:
            meta = AVAILABLE_MODELS[preferred_key]
            p = self.models_dir / meta["filename"]
            if p.exists() and p.stat().st_size > (meta["size_bytes"] * 0.85):
                return {**meta, "path": p}

        for key, meta in AVAILABLE_MODELS.items():
            p = self.models_dir / meta["filename"]
            if p.exists() and p.stat().st_size > (meta["size_bytes"] * 0.85):
                return {**meta, "path": p}
        return None

    def is_installed(self) -> bool:
        return self.get_installed_model() is not None

    def shutdown_engine(self):
        """Terminates running llama-server background process and resets state."""
        with self._load_lock:
            if self._server_proc is not None:
                try:
                    self._server_proc.terminate()
                    self._server_proc.wait(timeout=2)
                except Exception:
                    try:
                        self._server_proc.kill()
                    except Exception:
                        pass
                self._server_proc = None
                self._server_port = None
                self._server_url = None
                self._loaded_model_key = None
                self._loaded_acceleration = None
            self._llm = None

    def uninstall_model(self, model_key: Optional[str] = None) -> bool:
        """
        Safely unloads LLM instance and removes downloaded model file(s) from disk.
        If model_key is None, removes all installed models.
        """
        self.shutdown_engine()
        with self._load_lock:
            removed = False
            targets = [AVAILABLE_MODELS[model_key]] if model_key and model_key in AVAILABLE_MODELS else AVAILABLE_MODELS.values()
            for meta in targets:
                p = self.models_dir / meta["filename"]
                tmp_p = self.models_dir / f"{meta['filename']}.tmp"
                if p.exists():
                    try:
                        p.unlink()
                        removed = True
                    except Exception as e:
                        print(f"[OfflineVocalCoach] Failed to delete {p}: {e}", file=sys.stderr)
                if tmp_p.exists():
                    try:
                        tmp_p.unlink()
                    except Exception:
                        pass
            return removed

    def download_model(
        self,
        model_key: str,
        progress_cb: Optional[Callable[[float, str], None]] = None,
        cancel_event: Optional[threading.Event] = None
    ) -> bool:
        """
        Downloads the requested GGUF model chunk-by-chunk with progress reporting.
        """
        meta = AVAILABLE_MODELS.get(model_key)
        if not meta:
            return False

        target_file = self.models_dir / meta["filename"]
        tmp_file = self.models_dir / f"{meta['filename']}.tmp"

        url = meta["url"]
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) TikiRehearsalLounge/1.0"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp, open(tmp_file, "wb") as out:
                total_bytes = int(resp.headers.get("Content-Length", meta["size_bytes"]))
                downloaded = 0
                chunk_size = 1024 * 1024  # 1 MB

                while True:
                    if cancel_event and cancel_event.is_set():
                        if tmp_file.exists():
                            tmp_file.unlink()
                        return False

                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break

                    out.write(chunk)
                    downloaded += len(chunk)
                    pct = min(1.0, downloaded / max(1, total_bytes))
                    mb_down = downloaded / (1024 * 1024)
                    mb_total = total_bytes / (1024 * 1024)

                    if progress_cb:
                        progress_cb(pct, f"{mb_down:.1f} MB / {mb_total:.1f} MB ({int(pct * 100)}%)")

            # Finalize file rename
            if tmp_file.exists():
                tmp_file.rename(target_file)

            # Record preferred model in studio config
            cfg = load_studio_config()
            cfg["ai_model_key"] = model_key
            save_studio_config(cfg)

            if progress_cb:
                progress_cb(1.0, f"Installed {meta['name']} successfully!")

            # Warm up engine in background
            threading.Thread(target=self.load_engine, daemon=True).start()
            return True

        except Exception as e:
            if tmp_file.exists():
                tmp_file.unlink()
            if progress_cb:
                progress_cb(0.0, f"Download failed: {e}")
            return False

    def _find_llama_server_binary(self) -> Optional[Path]:
        """Locates bundled or system llama-server binary."""
        cand = get_bundled_binary_path("llama-server")
        if cand and cand.is_file():
            return cand
        cand = shutil.which("llama-server")
        if cand:
            return Path(cand)
        app_root = Path(__file__).resolve().parent.parent
        for sub in ["bin", "usr/bin", ""]:
            target_name = "llama-server.exe" if sys.platform == "win32" else "llama-server"
            p = app_root / sub / target_name if sub else app_root / target_name
            if p.is_file():
                return p
        return None

    def _check_server_healthy(self, url: str) -> bool:
        """Polls llama-server /health endpoint."""
        try:
            req = urllib.request.Request(f"{url}/health", headers={"User-Agent": "TikiCoach/1.0"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data.get("status") in ("ok", "loading model", "no slot available")
        except Exception:
            pass
        return False

    def is_engine_ready(self) -> bool:
        """Returns True if the engine is loaded and responsive."""
        if self._server_url:
            return self._check_server_healthy(self._server_url)
        return self._llm is not None

    def load_engine(self) -> bool:
        """Loads llama-server standalone engine (or llama-cpp-python fallback) with installed model."""
        with self._load_lock:
            installed = self.get_installed_model()
            if not installed:
                return False

            accel = self.get_acceleration_mode()
            ngl = "99" if accel == "gpu" else "0"

            # Check if server already running and healthy for this model and acceleration mode
            if (self._server_proc is not None and self._server_url and
                self._loaded_model_key == installed["key"] and
                self._loaded_acceleration == accel):
                if self._check_server_healthy(self._server_url):
                    return True
                self.shutdown_engine()

            # Check if in-process llama_cpp already loaded
            if (self._llm is not None and
                self._loaded_model_key == installed["key"] and
                self._loaded_acceleration == accel):
                return True

            # If model key or acceleration changed while running, shut down first
            if self._server_proc is not None or self._llm is not None:
                self.shutdown_engine()

            # 1. Try launching standalone llama-server
            server_bin = self._find_llama_server_binary()
            if server_bin:
                try:
                    # Find free local port
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.bind(("127.0.0.1", 0))
                        port = s.getsockname()[1]

                    n_threads = max(2, os.cpu_count() - 1 if os.cpu_count() else 4)
                    cmd = [
                        str(server_bin),
                        "-m", str(installed["path"]),
                        "--port", str(port),
                        "--host", "127.0.0.1",
                        "--threads", str(n_threads),
                        "-ngl", ngl,
                        "--ctx-size", "2048",
                        "--log-disable"
                    ]
                    flags = 0
                    if sys.platform == "win32":
                        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=flags
                    )
                    url = f"http://127.0.0.1:{port}"

                    # Poll for server readiness (up to 15 seconds)
                    ready = False
                    for _ in range(75):
                        if proc.poll() is not None:
                            break
                        if self._check_server_healthy(url):
                            ready = True
                            break
                        time.sleep(0.2)

                    if ready:
                        self._server_proc = proc
                        self._server_port = port
                        self._server_url = url
                        self._loaded_model_key = installed["key"]
                        self._loaded_acceleration = accel
                        print(f"[OfflineVocalCoach] Standalone llama-server active on {url} (acceleration: {accel}, -ngl {ngl})", file=sys.stderr)
                        return True
                    else:
                        print(f"[OfflineVocalCoach] llama-server startup timed out (exit code: {proc.poll()})", file=sys.stderr)
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                except Exception as e:
                    print(f"[OfflineVocalCoach] Error launching llama-server: {e}", file=sys.stderr)

            # 2. In-process fallback via llama_cpp if installed
            try:
                from llama_cpp import Llama
                n_threads = max(2, os.cpu_count() - 1 if os.cpu_count() else 4)
                n_gpu_layers = 99 if accel == "gpu" else 0
                self._llm = Llama(
                    model_path=str(installed["path"]),
                    n_ctx=2048,
                    n_threads=n_threads,
                    n_gpu_layers=n_gpu_layers,
                    verbose=False
                )
                self._loaded_model_key = installed["key"]
                self._loaded_acceleration = accel
                return True
            except ImportError:
                pass
            except Exception as e:
                print(f"[OfflineVocalCoach] In-process llama_cpp error: {e}", file=sys.stderr)

            return False

    def _query_completion(
        self,
        prompt: str,
        max_tokens: int = 500,
        temperature: float = 0.7,
        stop: Optional[List[str]] = None
    ) -> Optional[str]:
        """Queries active backend (llama-server or llama_cpp in-process)."""
        stop_tokens = stop or ["<|im_end|>", "<|endoftext|>"]

        # 1. llama-server loopback endpoint
        if self._server_url:
            try:
                url = f"{self._server_url}/completion"
                req = urllib.request.Request(
                    url,
                    headers={"Content-Type": "application/json", "User-Agent": "TikiCoach/1.0"}
                )
                payload = json.dumps({
                    "prompt": prompt,
                    "n_predict": max_tokens,
                    "temperature": temperature,
                    "top_p": 0.9,
                    "stop": stop_tokens
                }).encode("utf-8")
                with urllib.request.urlopen(req, data=payload, timeout=60) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read().decode("utf-8"))
                        content = data.get("content", "")
                        return content.strip() if content else None
            except Exception as e:
                print(f"[OfflineVocalCoach] llama-server query error: {e}", file=sys.stderr)

        # 2. In-process llama_cpp fallback
        if self._llm is not None:
            try:
                output = self._llm(
                    prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    top_p=0.9,
                    stop=stop_tokens
                )
                text = output["choices"][0]["text"].strip()
                return text if text else None
            except Exception as e:
                print(f"[OfflineVocalCoach] In-process inference error: {e}", file=sys.stderr)

        return None

    def generate_take_debrief(
        self,
        report_data: Dict[str, Any],
        track_info: Optional[Dict[str, Any]] = None,
        profile_data: Optional[Dict[str, Any]] = None,
        progression_data: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Synthesizes raw DSP metrics into an encouraging, pedagogical debrief
        incorporating phrase-level moments and vocal progression trajectory.
        """
        if not self.load_engine():
            return self._generate_rule_based_debrief(report_data, track_info, profile_data, progression_data, lyric_alignment=lyric_alignment)

        # Build prompt
        prompt = self._build_debrief_prompt(
            report_data, track_info, profile_data, progression_data,
            lyric_alignment=lyric_alignment, toc=toc
        )
        result = self._query_completion(prompt, max_tokens=650, temperature=0.7)
        if result:
            return result
        return self._generate_rule_based_debrief(report_data, track_info, profile_data, progression_data, lyric_alignment=lyric_alignment)

    def chat_with_coach(
        self,
        user_message: str,
        conversation_history: List[Dict[str, str]],
        report_data: Optional[Dict[str, Any]] = None,
        track_info: Optional[Dict[str, Any]] = None,
        debrief_text: Optional[str] = None,
        lyric_alignment: Optional[Dict[str, Any]] = None,
        toc: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Interactive conversational reply regarding the take, lyrics, and vocal mechanics.
        """
        if not self.load_engine():
            if not self.is_installed():
                return (
                    "✦ Maestro Tiki: No offline AI model is installed yet. Open Settings (⚙️) -> "
                    "'Offline AI Vocal Coach' to download the model for 100% private, on-device coaching!"
                )
            return (
                "✦ Maestro Tiki: Offline AI engine is initializing. In the meantime, remember: "
                "focus on steady ribcage breath support and keeping vowel resonance centered in your facial mask!"
            )

        prompt = self._build_chat_prompt(
            user_message,
            conversation_history,
            report_data,
            track_info,
            debrief_text=debrief_text,
            lyric_alignment=lyric_alignment,
            toc=toc
        )
        result = self._query_completion(
            prompt,
            max_tokens=450,
            temperature=0.7,
            stop=["<|im_end|>", "<|endoftext|>", "Singer:", "User:"]
        )
        if result:
            return result
        return "Coach note: Keep practicing with relaxed throat and diaphragmatic breath support!"

    def _build_debrief_prompt(
        self,
        report: Dict[str, Any],
        track_info: Optional[Dict[str, Any]],
        profile: Optional[Dict[str, Any]],
        progression_data: Optional[Dict[str, Any]] = None,
        lyric_alignment: Optional[Dict[str, Any]] = None,
        toc: Optional[Dict[str, Any]] = None
    ) -> str:
        track_title = track_info.get("title", "Rehearsal Song") if track_info else "Rehearsal Song"
        track_artist = track_info.get("artist", "") if track_info else ""
        shift = report.get("current_key_shift", 0)
        rec_shift = report.get("recommended_key_shift", 0)

        highlights = report.get("phrase_highlights", [])
        struggles = report.get("phrase_struggles", [])

        phrase_section = ""
        if highlights:
            phrase_section += "\nTop Song Highlights (Timestamped):\n"
            for h in highlights:
                phrase_section += f"- [{h['timestamp']}] ({h['note_span']}): {h['detail']}\n"

        if struggles:
            phrase_section += "\nTop Focus / Struggle Moments (Timestamped):\n"
            for s in struggles:
                phrase_section += f"- [{s['timestamp']}] ({s['note_span']}): {s['detail']} | Mechanical Cue: {s.get('cue', '')}\n"

        prog_section = ""
        if progression_data:
            take_num = progression_data.get("song_take_number", 1)
            delta_score = progression_data.get("delta_score", 0)
            delta_pitch = progression_data.get("delta_pitch_pct", 0.0)
            recent = progression_data.get("recent_take_summaries", [])
            has_history = progression_data.get("has_prior_history", False)

            prog_section += f"\nRehearsal Progression Context (Take #{take_num} on this track):\n"
            if has_history:
                prog_section += f"- Score change from immediately prior take: {delta_score:+d} points\n"
                prog_section += f"- Pitch accuracy change: {delta_pitch:+0.1f}%\n"
                if recent:
                    prog_section += f"- Recent history on this track: {'; '.join(recent)}\n"
            else:
                prog_section += "- This is your baseline take (Attempt #1) for this track.\n"
            prog_section += f"- Total practice takes in studio history: {progression_data.get('studio_total_takes', 1)}\n"

        vib_str = (
            f"{report.get('vibrato_rate_hz', 0.0)} Hz (±{report.get('vibrato_depth_cents', 0)}¢)"
            if report.get("vibrato_present")
            else "Straight-tone / Minimal vibrato"
        )
        feedback_list = report.get("coach_feedback", [])
        feedback_section = ""
        if feedback_list:
            feedback_section = "\nAcoustic Telemetry Observations:\n" + "\n".join([f"- {f}" for f in feedback_list[:4]]) + "\n"

        prompt = (
            f"<|im_start|>system\n{DEBRIEF_SYSTEM_PROMPT}<|im_end|>\n"
            f"<|im_start|>user\n"
            f"Here are the objective audio analysis results for my vocal take of '{track_title}' {f'by {track_artist}' if track_artist else ''}:\n"
            f"- Overall Performance Score: {report.get('overall_score', 0)}/100\n"
            f"- Pitch Accuracy: {report.get('pitch_accuracy_pct', 0)}% within ±50 cents\n"
            f"- Intonation Drift: {report.get('intonation_cents', 0):+0.1f}¢ ({report.get('intonation_verdict', '')})\n"
            f"- Rhythm Pocket: {report.get('rhythm_offset_ms', 0):+0.1f} ms onset latency ({report.get('rhythm_verdict', 'Locked On')})\n"
            f"- Resonance Ring: {report.get('resonance_verdict', '')} (ratio {report.get('singers_formant_ratio', 0)})\n"
            f"- Breath Support: {report.get('breath_support_pct', 0)}% ({report.get('breath_support_verdict', '')})\n"
            f"- Glottal Attack Dynamics: {report.get('onset_attack_verdict', '')}\n"
            f"- Vibrato: {vib_str} (Bloom Style: {report.get('vibrato_style_verdict', 'Natural')})\n"
            f"- Dynamic Steadiness: {report.get('energy_steadiness_pct', 0)}% ({report.get('dynamics_verdict', '')})\n"
            f"- Sibilance & Fry: Sibilance {report.get('sibilance_verdict', '')}, Fry {report.get('vocal_fry_pct', 0)}% ({report.get('vocal_fry_verdict', '')})\n"
            f"- Range Covered: {report.get('take_min_note', 'N/A')} to {report.get('take_max_note', 'N/A')}\n"
            f"- Attack Velocity & Scooping: {report.get('pitch_scoop_pct', 0)}% scooped attacks ({report.get('attack_velocity_verdict', 'Clean')})\n"
            f"- Dynamic Story Arc: {report.get('dynamic_contrast_db', 0)} dB contrast ({report.get('dynamic_arc_verdict', 'Natural')})\n"
            f"- Key Shift: {shift:+d} semitones (Recommended: {rec_shift:+d} semitones)\n"
            f"{feedback_section}"
            f"{phrase_section}\n"
            f"{prog_section}\n"
            f"{f'[PARTITION: LYRICS_ALIGNMENT]' + chr(10) + '- Status: ' + lyric_alignment.get('status', 'unavailable') + chr(10) + '- Summary: ' + lyric_alignment.get('summary_text', '') + chr(10) if lyric_alignment and lyric_alignment.get('status') == 'aligned' else ''}"
            f"CRITICAL INSTRUCTION: The singer ALREADY sees the raw timestamped highlight list and struggle list in the UI cards directly above your text. "
            f"DO NOT duplicate or re-list them. Deliver your unique, expert vocal masterclass synthesis with:\n"
            f"1. 🔬 **Acoustic Diagnosis (The Root-Cause 'Why')**: Explain the underlying vocal fold physiology, airflow, attack scooping, and vocal tract resonance behind my take metrics.\n"
            f"2. 🛠️ **Targeted Micro-Drill (30-Second Practice Fix)**: Prescribe ONE concrete, isolated physical vocal exercise (e.g. SOVTE straw phonation, puffy-cheek 'GUG', or staccato onset drill) to practice before my next take.\n"
            f"3. 🎯 **Priority Directive for Take #2**: ONE single, clear mental focus directive for my very next rehearsal take.\n"
            f"4. 📈 **Vocal Progression Trajectory**: A 2-sentence summary of my trajectory across takes (or benchmark if Take #1).\n"
            f"Speak directly to me like a master studio director. Do NOT repeat the cards above.<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )
        return prompt

    def _build_chat_prompt(
        self,
        user_msg: str,
        history: List[Dict[str, str]],
        report: Optional[Dict[str, Any]],
        track_info: Optional[Dict[str, Any]],
        debrief_text: Optional[str] = None,
        lyric_alignment: Optional[Dict[str, Any]] = None,
        toc: Optional[Dict[str, Any]] = None
    ) -> str:
        prompt = f"<|im_start|>system\n{CHAT_SYSTEM_PROMPT}\n"
        track_title = track_info.get("title", "") if track_info else ""
        track_artist = track_info.get("artist", "") if track_info else ""

        if report:
            highlights = report.get("phrase_highlights", [])
            struggles = report.get("phrase_struggles", [])
            h_lines = [f"• [{h['timestamp']}] ({h['note_span']}): {h['detail']}" for h in highlights]
            s_lines = [f"• [{s['timestamp']}] ({s['note_span']}): {s['detail']} | Mechanical Cue: {s.get('cue', '')}" for s in struggles]
            h_str = "\n".join(h_lines) if h_lines else "None flagged."
            s_str = "\n".join(s_lines) if s_lines else "None flagged."

            feedback_list = report.get("coach_feedback", [])
            feedback_str = "\n".join([f"- {f}" for f in feedback_list[:4]]) if feedback_list else "None."

            rhythm_ms = report.get("rhythm_offset_ms", 0.0)
            rhythm_verdict = report.get("rhythm_verdict", "Locked On")
            vib_str = (
                f"{report.get('vibrato_rate_hz', 0.0)} Hz (±{report.get('vibrato_depth_cents', 0)}¢)"
                if report.get("vibrato_present")
                else "Straight-tone / Minimal vibrato"
            )

            lyric_status = lyric_alignment.get("status", "unavailable") if lyric_alignment else "unavailable"
            lyric_summary = lyric_alignment.get("summary_text", "No synchronized lyrics were tracked for this rehearsal take.") if lyric_alignment else "No synchronized lyrics were tracked for this rehearsal take."
            lyric_details = lyric_alignment.get("line_details", []) if lyric_alignment else []
            lyric_sample_str = ""
            if lyric_details:
                lyric_sample_str = "\n".join([
                    f"• [{l.get('timestamp', '0:00')}] \"{l.get('line', '')}\" -> {'Sung (' + str(l.get('sync_offset_ms', 0)) + 'ms ' + str(l.get('sync_verdict', '')) + ')' if l.get('sung') else 'Missed'}"
                    for l in lyric_details[:6]
                ])

            prompt += (
                f"\n=== SINGER'S LATEST PERFORMANCE TELEMETRY ===\n"
                f"Track: '{track_title}' {f'by {track_artist}' if track_artist else ''}\n"
                f"[PARTITION: ACOUSTIC_CORE]\n"
                f"- Overall Performance Score: {report.get('overall_score', 0)}/100\n"
                f"- Pitch Accuracy: {report.get('pitch_accuracy_pct', 0)}% (within ±50¢)\n"
                f"- Intonation Drift: {report.get('intonation_cents', 0):+0.1f}¢ ({report.get('intonation_verdict', 'Center')})\n"
                f"- Attack Velocity & Scooping: {report.get('pitch_scoop_pct', 0)}% scooped attacks ({report.get('attack_velocity_verdict', 'Clean')})\n"
                f"- Rhythm / Timing Pocket: {rhythm_ms:+0.1f} ms onset latency ({rhythm_verdict})\n"
                f"- Resonance Ring (Singer's Formant): {report.get('resonance_verdict', 'Balanced')} (ratio: {report.get('singers_formant_ratio', 0)})\n"
                f"- Diaphragmatic Breath Support: {report.get('breath_support_pct', 0)}% ({report.get('breath_support_verdict', 'Stable')})\n"
                f"- Phonation Onset Attacks: {report.get('onset_attack_verdict', 'Balanced')}\n"
                f"- Vibrato: {vib_str} (Bloom Style: {report.get('vibrato_style_verdict', 'Natural')})\n"
                f"- Dynamic Story Arc: {report.get('dynamic_contrast_db', 0)} dB contrast ({report.get('dynamic_arc_verdict', 'Natural')})\n"
                f"- Dynamics Steadiness: {report.get('energy_steadiness_pct', 0)}% ({report.get('dynamics_verdict', 'Balanced')})\n"
                f"- Vocal Range Covered: {report.get('take_min_note', 'N/A')} to {report.get('take_max_note', 'N/A')} ({report.get('take_range_semitones', 0)} semitones)\n"
                f"- Key Shift: {report.get('current_key_shift', 0):+d} semitones (Recommended: {report.get('recommended_key_shift', 0):+d})\n"
                f"\nKey Take Observations:\n{feedback_str}\n"
                f"\n[PARTITION: PHRASE_TIMELINE]\n"
                f"Highlights:\n{h_str}\n"
                f"Focus Moments:\n{s_str}\n"
                f"\n[PARTITION: LYRICS_ALIGNMENT]\n"
                f"Status: {lyric_status}\n"
                f"Summary: {lyric_summary}\n"
                f"{f'Tracked Lines Sample:' + chr(10) + lyric_sample_str + chr(10) if lyric_sample_str else ''}"
                f"\nCRITICAL GROUNDING DIRECTIVE:\n"
                f"1. When the user asks about lyrics, words, or song phrasing, ground your answer strictly in [PARTITION: LYRICS_ALIGNMENT]. Quote the exact target and sung words.\n"
                f"2. If [PARTITION: LYRICS_ALIGNMENT] status is 'unavailable' or 'reference_only', explicitly tell the user that lyric transcription was not active for this rehearsal take, and NEVER guess lyrics from pitch or tone!\n"
            )

        if debrief_text:
            prompt += f"\nDebrief You Already Delivered for this Take:\n\"\"\"{debrief_text.strip()}\"\"\"\n"

        prompt += "<|im_end|>\n"

        # Prior history turns (exclude the trailing turn if it matches user_msg to avoid duplicate tokens)
        prior_history = history[:-1] if (history and history[-1].get("role") == "user" and history[-1].get("content") == user_msg) else history
        for turn in prior_history[-4:]:
            role = turn.get("role", "user")
            content = turn.get("content", "")
            prompt += f"<|im_start|>{role}\n{content}<|im_end|>\n"

        prompt += f"<|im_start|>user\n{user_msg}<|im_end|>\n<|im_start|>assistant\n"
        return prompt

    @staticmethod
    def _generate_rule_based_debrief(
        report: Dict[str, Any],
        track_info: Optional[Dict[str, Any]],
        profile: Optional[Dict[str, Any]],
        progression_data: Optional[Dict[str, Any]] = None,
        lyric_alignment: Optional[Dict[str, Any]] = None
    ) -> str:
        """High-grade pedagogical debrief synthesis providing unique non-duplicative analysis, diagnosis, and micro-drills."""
        score = report.get("overall_score", 0)
        pitch = report.get("pitch_accuracy_pct", 0)
        into = report.get("intonation_cents", 0.0)
        breath = report.get("breath_support_pct", 0)
        res = report.get("resonance_verdict", "Balanced")
        shift = report.get("current_key_shift", 0)
        rec_shift = report.get("recommended_key_shift", 0)
        struggles = report.get("phrase_struggles", [])

        lines = []
        lines.append(
            f"Solid take! Scoring **{score}/100**, your vocal showed a **{res}** tone character with "
            f"**{report.get('attack_velocity_verdict', 'clean direct attacks')}** and a **{report.get('dynamic_arc_verdict', 'natural dynamic arc')}**.\n"
            "*(Review the timestamped highlight and focus cards above for your exact note breakdowns.)*"
        )

        if lyric_alignment and lyric_alignment.get("status") == "aligned":
            cov = lyric_alignment.get("word_accuracy_pct", 0)
            lines.append(f"📖 **Lyric Phrasing**: {lyric_alignment.get('lines_covered', 0)}/{lyric_alignment.get('total_lines', 0)} lines on-cue ({cov:.0f}% coverage).")

        # 1. Acoustic Diagnosis (The Root-Cause 'Why')
        lines.append("\n🔬 **Acoustic Diagnosis (Underlying Physiology)**:")
        if into <= -15.0 or breath < 70.0:
            lines.append(
                f"• Airflow pressure sagged across phrase endings (breath support: {breath}%), pulling intonation flat ({into:+.1f}¢). "
                "The vocal folds lost subglottic air support before phonation completed."
            )
        elif into >= 18.0:
            lines.append(
                f"• Intonation pushed sharp ({into:+.1f}¢), indicating elevated laryngeal positioning and excess muscular cord compression on higher vowels."
            )
        else:
            lines.append(
                f"• Intonation stayed well-centered ({into:+.1f}¢) with balanced vocal fold adduction and steady diaphragmatic support."
            )

        if report.get("pitch_scoop_pct", 0) >= 30.0:
            lines.append(
                f"• Pitch attack velocity showed {report.get('pitch_scoop_pct'):.0f}% scooping—you are sliding up from underneath into phrase starts rather than establishing pitch center at onset."
            )

        rhythm_offset = report.get("rhythm_offset_ms", 0.0)
        if abs(rhythm_offset) >= 20.0:
            rhythm_dir = "lagging behind" if rhythm_offset > 0 else "anticipating ahead of"
            lines.append(
                f"• Rhythm pocket was {rhythm_offset:+.1f} ms off the beat transients ({rhythm_dir} tempo), which creates a feeling of rhythmic hesitation."
            )

        # 2. Targeted Micro-Drill (30-Second Practice Fix)
        lines.append("\n🛠️ **Targeted Micro-Drill (30-Second Practice Fix)**:")
        if report.get("pitch_scoop_pct", 0) >= 30.0:
            lines.append(
                "• **Clean Attack Staccato Drill**: Sing the first 3 words of your struggle phrase as detached, spoken-level 'STAC-CA-TO' syllables on a neutral 'BUH' or 'DUH'. Lock the pitch instant at onset before reconnecting into legato."
            )
        elif into >= 18.0 or (struggles and any("sharp" in s.get("detail", "") for s in struggles)):
            lines.append(
                "• **Laryngeal Neutralizer (Puffy-Cheek 'GUG')**: Isolate your highest passage and sing the melodic line on 'GUG-GUG-GUG' with puffy cheeks. The hard 'G' drop disengages outer neck constrictors and lowers the thyroid cartilage."
            )
        elif breath < 75.0 or (struggles and any("sag" in s.get("detail", "") for s in struggles)):
            lines.append(
                "• **SOVTE Straw/Hiss Phonation**: Take your struggle passage and blow a steady unvoiced 'Sssss' or phonate through a straw for the exact phrase duration. Feel your floating ribs remain expanded until the final second."
            )
        else:
            lines.append(
                "• **Resonance Ring Vowel Drill**: Sing the melodic hook on an open 'Mee-Ah' vowel glide, keeping the vibration buzz focused in your cheekbones and nasal bridge."
            )

        # 3. Priority Focus Directive for Take #2
        lines.append("\n🎯 **Priority Directive for Take #2**:")
        if shift != rec_shift:
            lines.append(f"• Transpose the track to **{rec_shift:+d} semitones** ({report.get('key_recommendation_reason', '')}).")
        elif report.get("rhythm_offset_ms", 0.0) > 20.0:
            lines.append("• Anticipate your breath on beat 4 so you attack line-entries right on beat 1 instead of dragging.")
        elif breath < 70.0:
            lines.append("• Prioritize holding lower ribcage expansion all the way through phrase endings.")
        else:
            lines.append("• Maintain forward facial mask resonance and trust your breath support through transitions.")

        # 4. Vocal Progression & Growth Trajectory
        if progression_data:
            take_num = progression_data.get("song_take_number", 1)
            delta_s = progression_data.get("delta_score", 0)
            delta_p = progression_data.get("delta_pitch_pct", 0.0)
            has_hist = progression_data.get("has_prior_history", False)

            lines.append("\n📈 **Vocal Progression & Growth Trajectory**:")
            if has_hist:
                s_sign = f"+{delta_s}" if delta_s > 0 else f"{delta_s}"
                p_sign = f"+{delta_p:.1f}" if delta_p > 0 else f"{delta_p:.1f}"
                if delta_s > 0:
                    lines.append(f"• **Take #{take_num}**: You gained **{s_sign} points** compared to your previous attempt! Pitch accuracy shifted {p_sign}%. Your muscular memory is locking in.")
                elif delta_s == 0:
                    lines.append(f"• **Take #{take_num}**: Maintained a steady performance matching your previous score. Focus on diaphragmatic breath release to push into the next tier.")
                else:
                    lines.append(f"• **Take #{take_num}**: Score shifted {s_sign} points. Take a quick sip of water and reset your ribcage posture before the next run.")
            else:
                lines.append(f"• **Baseline Set (Take #1)**: This run sets your benchmark for this track at **{score}/100** ({pitch:.0f}% pitch accuracy). Subsequent takes will track your precision gains!")

        if shift != rec_shift:
            lines.append(
                f"\n💡 **Key Strategy**: Transposing from {shift:+d} to **{rec_shift:+d}** semitones will bring "
                f"the track's highest melody peak into your power sweet spot without straining."
            )

        return "\n".join(lines)

    def generate_warmup_routine(
        self,
        profile_data: Optional[Dict[str, Any]] = None,
        track_info: Optional[Dict[str, Any]] = None,
        key_shift: int = 0,
        climax_note: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Generates an adaptive, multi-step vocal warmup routine tailored to:
        - Singer's calibrated tessitura (vocal type, power sweet spot)
        - Target song melody peaks and recommended key shift
        """
        ceiling = (profile_data.get("tessitura_max_note") or "G4") if profile_data else "G4"
        if not self.load_engine():
            return self._generate_rule_based_warmup(profile_data, track_info, key_shift, climax_note)

        prompt = self._build_warmup_prompt(profile_data, track_info, key_shift, climax_note)
        text = self._query_completion(prompt, max_tokens=650, temperature=0.6)
        if text:
            parsed = self._parse_warmup_json(text, max_ceiling=ceiling)
            if parsed and len(parsed) >= 2:
                return parsed
        return self._generate_rule_based_warmup(profile_data, track_info, key_shift, climax_note)

    def _build_warmup_prompt(
        self,
        profile: Optional[Dict[str, Any]],
        track_info: Optional[Dict[str, Any]],
        key_shift: int,
        climax_note: Optional[str] = None
    ) -> str:
        song_title = track_info.get("title", "Selected Rehearsal Song") if track_info else "Selected Rehearsal Song"
        artist = track_info.get("artist", "") if track_info else ""
        vocal_type = profile.get("vocal_type", "Contemporary Vocalist") if profile else "Contemporary Vocalist"
        sweet_min = profile.get("tessitura_min_note", "C3") if profile else "C3"
        sweet_max = profile.get("tessitura_max_note", "G4") if profile else "G4"
        anchor = profile.get("anchor_note", "F3") if profile else "F3"
        eff_climax = climax_note or (transpose_note(sweet_max, key_shift) if key_shift != 0 else sweet_max)

        prompt = (
            f"<|im_start|>system\n"
            f"You are Maestro Tiki, an expert Polynesian studio vocal coach. Design a personalized 4-step progressive vocal warmup routine.\n"
            f"Return ONLY a valid JSON list of 4 exercise objects with keys:\n"
            f'["title", "category", "target_notes", "duration_s", "purpose", "mechanics", "song_context"]\n'
            f"No markdown formatting, no commentary, only the raw JSON array.<|im_end|>\n"
            f"<|im_start|>user\n"
            f"Singer Profile: {vocal_type}, Speaking Anchor: {anchor}, Comfort Sweet Spot: {sweet_min} to {sweet_max}.\n"
            f"Upcoming Song: '{song_title}' {f'by {artist}' if artist else ''} at key shift {key_shift:+d} semitones. Target Climax Note: {eff_climax}.\n"
            f"IMPORTANT: Exercise 3 target_notes must glide smoothly to the song climax ({eff_climax}) and NEVER exceed {sweet_max}.\n"
            f"Create 4 progressive warmups that unlock diaphragmatic breath, Singer's Formant ring (2.8–3.4 kHz), and extend upper range safely up to the song climax.<|im_end|>\n"
            f"<|im_start|>assistant\n["
        )
        return prompt

    def _parse_warmup_json(self, text: str, max_ceiling: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
        try:
            cleaned = text.strip()
            if not cleaned.startswith("["):
                cleaned = "[" + cleaned
            if not cleaned.endswith("]"):
                last_brace = cleaned.rfind("}")
                if last_brace != -1:
                    cleaned = cleaned[:last_brace + 1] + "]"
                else:
                    return None
            data = json.loads(cleaned)
            if isinstance(data, list) and len(data) >= 2:
                for idx, item in enumerate(data, 1):
                    item.setdefault("step_num", idx)
                    # Clamp exercise duration to a realistic pedagogical range (25s - 60s)
                    try:
                        raw_dur = int(item.get("duration_s", 35))
                        item["duration_s"] = max(25, min(60, raw_dur if raw_dur >= 15 else 35))
                    except (ValueError, TypeError):
                        item["duration_s"] = 35

                    tn = item.get("target_notes")
                    if isinstance(tn, list):
                        item["target_notes"] = " ➔ ".join(str(n) for n in tn)
                    elif not tn:
                        item["target_notes"] = "Comfortable Tessitura"

                    # If max_ceiling specified, clamp target notes to safe singer ceiling
                    if max_ceiling and isinstance(item.get("target_notes"), str):
                        parsed = parse_target_notes(item["target_notes"])
                        if parsed:
                            clamped_notes = [clamp_note_to_range(n, max_note=max_ceiling) for n in parsed]
                            item["target_notes"] = " ➔ ".join(clamped_notes)

                    if not item.get("song_context"):
                        item["song_context"] = "Targeted cord and breath conditioning for your upcoming rehearsal."
                return data
        except Exception:
            pass
        return None

    @staticmethod
    def _generate_rule_based_warmup(
        profile: Optional[Dict[str, Any]],
        track_info: Optional[Dict[str, Any]],
        key_shift: int,
        climax_note: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Pedagogical fallback generator incorporating singer range, track, and key shift."""
        song_title = track_info.get("title", "Your Rehearsal Song") if track_info else "Your Rehearsal Song"
        vocal_type = profile.get("vocal_type", "Contemporary Vocalist") if profile else "Calibrated Vocalist"
        sweet_min = profile.get("tessitura_min_note", "C3") if profile else "C3"
        sweet_max = profile.get("tessitura_max_note", "G4") if profile else "G4"
        anchor = profile.get("anchor_note", "E3") if profile else "E3"

        # Determine target climax note factoring in key shift and physiological ceiling
        target_climax = climax_note
        if not target_climax:
            if key_shift != 0:
                target_climax = transpose_note(sweet_max, key_shift)
            else:
                target_climax = sweet_max

        # Strict safety clamp
        target_climax = clamp_note_to_range(target_climax, min_note=sweet_min, max_note=sweet_max)

        shift_tag = f" (Key {key_shift:+d})" if key_shift != 0 else ""

        return [
            {
                "step_num": 1,
                "title": "Diaphragmatic Breath & Lip Trill Glide",
                "category": "Breath & Core Phonation",
                "target_notes": f"{sweet_min} ➔ {anchor} ➔ {sweet_min}",
                "duration_s": 35,
                "purpose": "Awakens vocal cords without friction while establishing subglottic air pressure.",
                "mechanics": "Expand your lower ribcage like an umbrella. Blow relaxed, steady air through loose fluttering lips on a gentle 5-tone descending glide. Zero throat squeeze.",
                "song_context": f"Calibrates breath support to sustain clean line ends in '{song_title}'."
            },
            {
                "step_num": 2,
                "title": "Singer's Formant Ring & Forward Mask ('Nay / Mum')",
                "category": "Acoustic Resonance (2.8–3.4 kHz)",
                "target_notes": f"{anchor} ➔ {sweet_max}",
                "duration_s": 40,
                "purpose": "Focuses acoustic projection into the facial mask and hard palate to pierce through backing stems.",
                "mechanics": "Sing a brassy, twangy 'Nay-nay-nay' with a lifted soft palate and relaxed jaw. Feel the vibrations buzzing right behind your upper front teeth.",
                "song_context": f"Activates the piercing acoustic cut needed for the melody in '{song_title}'."
            },
            {
                "step_num": 3,
                "title": "Upper Tessitura Climax Sirens ('Ooo ➔ Aaa')",
                "category": "Range Extension & High Belts",
                "target_notes": f"{anchor} ➔ {target_climax}{shift_tag}",
                "duration_s": 45,
                "purpose": "Smooths vocal register transitions and prepares cords for the track's highest belted climax.",
                "mechanics": "Start in rich chest voice on 'Ooo' and siren upward smoothly toward your power ceiling, opening the vowel to 'Aaa' as you ascend. Anchor support from your lower abdomen.",
                "song_context": f"Prepares your cords to hit the highest melody notes of '{song_title}'{shift_tag}."
            },
            {
                "step_num": 4,
                "title": "Vowel Cascade & Pitch Centering ('Wah-Oh-Ah')",
                "category": "Intonation & Diction",
                "target_notes": f"{anchor} (Sustained Center)",
                "duration_s": 30,
                "purpose": "Locks in intonation stability and releases tongue/jaw tension across vowel shifts.",
                "mechanics": "Hold your comfortable anchor note while smoothly morphing vowels 'Wah... Oh... Ah'. Keep pitch centered within ±10 cents without allowing line-end droop.",
                "song_context": "Stabilizes pitch precision for the verse-to-chorus dynamic transitions."
            }
        ]

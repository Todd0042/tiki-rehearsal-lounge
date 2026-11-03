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

from config import COLOR_CYAN, COLOR_MAGENTA, COLOR_GOLD, COLOR_LIME, STUDIO_CONFIG_DIR, get_bundled_binary_path
from audio.tone_generator import transpose_note, clamp_note_to_range, parse_target_notes

MODELS_DIR = STUDIO_CONFIG_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)

AVAILABLE_MODELS: Dict[str, Dict[str, Any]] = {
    "3b": {
        "key": "3b",
        "name": "Qwen2.5-3B-Instruct (Q4_K_M)",
        "tagline": "Deep Vocal Pedagogy & Musical Nuance",
        "filename": "qwen2.5-3b-instruct-q4_k_m.gguf",
        "size_str": "~1.95 GB",
        "size_bytes": 2085000000,
        "speed": "~30–45 tok/s on CPU",
        "url": "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf"
    },
    "1.5b": {
        "key": "1.5b",
        "name": "Qwen2.5-1.5B-Instruct (Q4_K_M)",
        "tagline": "Ultra-Fast & Compact (Sub-1 GB)",
        "filename": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "size_str": "~986 MB",
        "size_bytes": 986000000,
        "speed": "~60–80 tok/s on CPU",
        "url": "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf"
    }
}

SYSTEM_PROMPT = (
    "You are Maestro Tiki, an encouraging, world-class vocal coach and master rehearsal director "
    "inside a retro neon Polynesian studio lounge. You analyze performance takes with sharp "
    "acoustic and musical precision (pitch accuracy, intonation drift, diaphragmatic breath support, "
    "singer's formant resonance ring, glottal attack onset dynamics, energy steadiness, sibilance, and vocal fry). "
    "Provide warm, supportive, actionable feedback with specific physical vocal cues (soft palate, mask placement, "
    "ribcage expansion, tongue posture, key shift recommendations). "
    "Keep take debriefings focused to 2 concise paragraphs with 2 bullet action items. Never sound robotic."
)


class OfflineVocalCoach:
    def __init__(self):
        self.models_dir = MODELS_DIR
        self._llm = None
        self._loaded_model_key: Optional[str] = None
        self._load_lock = threading.Lock()
        self._server_proc: Optional[subprocess.Popen] = None
        self._server_port: Optional[int] = None
        self._server_url: Optional[str] = None
        atexit.register(self.shutdown_engine)

    def get_installed_model(self) -> Optional[Dict[str, Any]]:
        """Returns metadata of currently installed model, if any."""
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

            # Check if server already running and healthy for this model
            if self._server_proc is not None and self._server_url and self._loaded_model_key == installed["key"]:
                if self._check_server_healthy(self._server_url):
                    return True
                self.shutdown_engine()

            # Check if in-process llama_cpp already loaded
            if self._llm is not None and self._loaded_model_key == installed["key"]:
                return True

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
                        print(f"[OfflineVocalCoach] Standalone llama-server active on {url}", file=sys.stderr)
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
                self._llm = Llama(
                    model_path=str(installed["path"]),
                    n_ctx=2048,
                    n_threads=n_threads,
                    verbose=False
                )
                self._loaded_model_key = installed["key"]
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
            return self._generate_rule_based_debrief(report_data, track_info, profile_data, progression_data)

        # Build prompt
        prompt = self._build_debrief_prompt(report_data, track_info, profile_data, progression_data)
        result = self._query_completion(prompt, max_tokens=650, temperature=0.7)
        if result:
            return result
        return self._generate_rule_based_debrief(report_data, track_info, profile_data, progression_data)

    def chat_with_coach(
        self,
        user_message: str,
        conversation_history: List[Dict[str, str]],
        report_data: Optional[Dict[str, Any]] = None,
        track_info: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Interactive conversational reply regarding the take and vocal mechanics.
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

        prompt = self._build_chat_prompt(user_message, conversation_history, report_data, track_info)
        result = self._query_completion(
            prompt,
            max_tokens=280,
            temperature=0.7,
            stop=["<|im_end|>", "<|endoftext|>", "Singer:"]
        )
        if result:
            return result
        return "Coach note: Keep practicing with relaxed throat and diaphragmatic breath support!"

    def _build_debrief_prompt(
        self,
        report: Dict[str, Any],
        track_info: Optional[Dict[str, Any]],
        profile: Optional[Dict[str, Any]],
        progression_data: Optional[Dict[str, Any]] = None
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

        prompt = (
            f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n"
            f"<|im_start|>user\n"
            f"Here are the objective audio analysis results for my vocal take of '{track_title}' {f'by {track_artist}' if track_artist else ''}:\n"
            f"- Overall Performance Score: {report.get('overall_score', 0)}/100\n"
            f"- Pitch Accuracy: {report.get('pitch_accuracy_pct', 0)}% within ±50 cents\n"
            f"- Intonation Drift: {report.get('intonation_cents', 0):+0.1f}¢ ({report.get('intonation_verdict', '')})\n"
            f"- Resonance Ring: {report.get('resonance_verdict', '')} (ratio {report.get('singers_formant_ratio', 0)})\n"
            f"- Breath Support: {report.get('breath_support_pct', 0)}% ({report.get('breath_support_verdict', '')})\n"
            f"- Glottal Attack: {report.get('onset_attack_verdict', '')}\n"
            f"- Key Shift: {shift:+d} semitones (Recommended: {rec_shift:+d} semitones)\n"
            f"{phrase_section}\n"
            f"{prog_section}\n"
            f"Give me an encouraging, human-grade, in-depth studio debrief structured with:\n"
            f"1. 🌟 **What Clicked ([MM:SS])**: Detail the specific timestamped passages where I excelled, the notes, and why the tone worked.\n"
            f"2. ⚠️ **Where It Slipped ([MM:SS])**: Detail the specific timestamped passages where pitch wandered or breath sagged, explaining the acoustic cause.\n"
            f"3. 🛠️ **Targeted Physical Adjustments**: Exactly 2 actionable mechanical cues (ribcage expansion, soft palate lift, mask placement) to fix the struggle moments on my next take.\n"
            f"4. 📈 **Vocal Progression & Growth Trajectory**: A 2-sentence summary of my trajectory across takes (celebrating improvements or highlighting what to stabilize next). If Attempt #1, establish my starting benchmark.\n"
            f"Speak directly to me like an expert mentor in the studio lounge. Do NOT just recite global percentages.<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )
        return prompt

    def _build_chat_prompt(
        self,
        user_msg: str,
        history: List[Dict[str, str]],
        report: Optional[Dict[str, Any]],
        track_info: Optional[Dict[str, Any]]
    ) -> str:
        prompt = f"<|im_start|>system\n{SYSTEM_PROMPT}\n"
        if report:
            highlights = report.get("phrase_highlights", [])
            struggles = report.get("phrase_struggles", [])
            h_str = "; ".join([f"[{h['timestamp']}] {h['note_span']}" for h in highlights]) if highlights else "None"
            s_str = "; ".join([f"[{s['timestamp']}] {s['note_span']}" for s in struggles]) if struggles else "None"

            prompt += (
                f"Context for Current Take: Score {report.get('overall_score')}/100, "
                f"Pitch {report.get('pitch_accuracy_pct')}%, Intonation {report.get('intonation_cents'):+0.1f}¢, "
                f"Resonance {report.get('resonance_verdict')}, Breath {report.get('breath_support_verdict')}, "
                f"Attacks {report.get('onset_attack_verdict')}, Key Shift: {report.get('current_key_shift', 0):+d}.\n"
                f"Song Highlights: {h_str}\n"
                f"Struggle Focus Moments: {s_str}\n"
            )
        prompt += "<|im_end|>\n"

        # Add recent history (last 4 turns)
        for turn in history[-4:]:
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
        progression_data: Optional[Dict[str, Any]] = None
    ) -> str:
        """High-grade pedagogical debrief synthesis incorporating timestamped song moments and progression."""
        score = report.get("overall_score", 0)
        pitch = report.get("pitch_accuracy_pct", 0)
        into = report.get("intonation_cents", 0.0)
        breath = report.get("breath_support_pct", 0)
        res = report.get("resonance_verdict", "Balanced")
        shift = report.get("current_key_shift", 0)
        rec_shift = report.get("recommended_key_shift", 0)
        highlights = report.get("phrase_highlights", [])
        struggles = report.get("phrase_struggles", [])

        lines = []

        lines.append(
            f"Solid take! Scoring **{score}/100**, your vocal showed a **{res}** tone character with strong dynamic commitment. "
            "Here is the chronological breakdown of where you locked into the pocket and where to make physical adjustments:"
        )

        # 1. What Clicked
        lines.append("\n🌟 **What Clicked**:")
        if highlights:
            for h in highlights:
                lines.append(f"• **[{h['timestamp']}] ({h['note_span']})**: {h['detail']}")
        else:
            lines.append("• Vocal onsets showed clean dynamic energy with healthy phonation.")

        # 2. Where It Slipped
        lines.append("\n⚠️ **Where It Slipped**:")
        if struggles:
            for s in struggles:
                lines.append(f"• **[{s['timestamp']}] ({s['note_span']})**: {s['detail']}")
        else:
            lines.append("• Minor intonation variances detected during rapid melodic transitions.")

        # 3. Targeted Physical Adjustments
        lines.append("\n🛠️ **Targeted Physical Adjustments**:")
        if struggles and any(s.get("cue") for s in struggles):
            cues = [s["cue"] for s in struggles if s.get("cue")]
            for idx, c in enumerate(cues[:2], 1):
                lines.append(f"{idx}. {c}")
        else:
            lines.append("1. Keep your lower ribcage expanded through the vowel tail to maintain air pressure without throat tension.")
            lines.append("2. Lift your soft palate (think of starting a relaxed yawn) before ascending into higher phrases.")

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

"""
Tiki Rehearsal Lounge - Standalone Offline Speech-to-Text (STT) Engine
========================================================================
Provides zero-cloud, ultra-fast offline speech recognition using bundled whisper-cli.
Transcribes backing track audio and singer rehearsal takes with millisecond timestamps,
token confidence probabilities, and phrase segmentations.
"""

from __future__ import annotations
import os
import sys
import json
import shutil
import platform
import subprocess
import urllib.request
from pathlib import Path
from typing import Dict, Any, List, Optional

from config import STUDIO_CONFIG_DIR, PROJECT_ROOT

WHISPER_MODELS_DIR = STUDIO_CONFIG_DIR / "models"
DEFAULT_MODEL_NAME = "ggml-base.en.bin"
DEFAULT_MODEL_PATH = WHISPER_MODELS_DIR / DEFAULT_MODEL_NAME
MODEL_DOWNLOAD_URL = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{DEFAULT_MODEL_NAME}"


def find_whisper_binary() -> Optional[Path]:
    """
    Locates the bundled whisper-cli binary or system fallback.
    Checks:
    1. Project bundled bin/whisper/whisper-cli (Linux) or bin/whisper-win/Release/whisper-cli.exe (Windows)
    2. ~/.config/TikiRehearsalLounge/bin/whisper-cli
    3. System PATH (which whisper-cli / which whisper)
    """
    is_win = platform.system() == "Windows"
    binary_name = "whisper-cli.exe" if is_win else "whisper-cli"

    candidates = []
    if is_win:
        candidates.append(PROJECT_ROOT / "bin" / "whisper-win" / "Release" / binary_name)
        candidates.append(PROJECT_ROOT / "bin" / "whisper-win" / binary_name)
    else:
        candidates.append(PROJECT_ROOT / "bin" / "whisper" / binary_name)
        candidates.append(PROJECT_ROOT / "bin" / binary_name)

    candidates.append(STUDIO_CONFIG_DIR / "bin" / binary_name)

    for cand in candidates:
        if cand.is_file() and (is_win or os.access(str(cand), os.X_OK)):
            return cand

    # Check system PATH
    sys_path = shutil.which(binary_name) or shutil.which("whisper-cli") or shutil.which("whisper")
    if sys_path:
        return Path(sys_path)

    return None


def ensure_whisper_model(model_path: Optional[Path] = None) -> Optional[Path]:
    """
    Verifies that the GGML Whisper model exists.
    If missing, downloads the lightweight base.en model into models dir.
    """
    target = model_path or DEFAULT_MODEL_PATH
    if target.is_file() and target.stat().st_size > 10_000_000:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_suffix(".tmp")
    try:
        print(f"[STTEngine] Downloading default Whisper model to {target}...", file=sys.stderr)
        req = urllib.request.Request(
            MODEL_DOWNLOAD_URL,
            headers={"User-Agent": "TikiRehearsalLounge/1.0"}
        )
        with urllib.request.urlopen(req, timeout=60.0) as resp, open(tmp_path, "wb") as f:
            shutil.copyfileobj(resp, f)
        tmp_path.rename(target)
        print(f"[STTEngine] Model successfully downloaded ({target.stat().st_size // (1024*1024)} MB).", file=sys.stderr)
        return target
    except Exception as e:
        print(f"[STTEngine] Error downloading Whisper model: {e}", file=sys.stderr)
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        return None


def is_stt_available() -> bool:
    """Checks whether the offline STT engine is ready for inference."""
    binary = find_whisper_binary()
    if not binary:
        return False
    if DEFAULT_MODEL_PATH.is_file() and DEFAULT_MODEL_PATH.stat().st_size > 10_000_000:
        return True
    return False


def transcribe_audio_offline(
    audio_path: Path,
    model_path: Optional[Path] = None,
    language: str = "en",
    threads: Optional[int] = None,
    timeout_sec: float = 90.0
) -> Dict[str, Any]:
    """
    Executes offline STT transcription on an audio file using whisper-cli.
    Returns:
    {
        "full_text": str,
        "segments": [{"start_ms": int, "end_ms": int, "text": str}],
        "words": [{"word": str, "start_ms": int, "end_ms": int, "confidence": float}],
        "error": Optional[str]
    }
    """
    audio_p = Path(audio_path)
    if not audio_p.is_file():
        return {"full_text": "", "segments": [], "words": [], "error": f"Audio file not found: {audio_path}"}

    binary = find_whisper_binary()
    if not binary:
        return {"full_text": "", "segments": [], "words": [], "error": "whisper-cli binary not found"}

    model = ensure_whisper_model(model_path)
    if not model or not model.is_file():
        return {"full_text": "", "segments": [], "words": [], "error": "Whisper model not available"}

    num_threads = threads or min(os.cpu_count() or 4, 8)
    out_stem = audio_p.parent / f"_transcribe_{audio_p.stem}"
    out_json = audio_p.parent / f"_transcribe_{audio_p.stem}.json"

    # Clean up prior temp file
    if out_json.exists():
        out_json.unlink(missing_ok=True)

    # Setup environment: include whisper binary directory in LD_LIBRARY_PATH (Linux)
    env = os.environ.copy()
    bin_dir = str(binary.parent)
    if platform.system() != "Windows":
        cur_ld = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = f"{bin_dir}:{cur_ld}" if cur_ld else bin_dir

    cmd = [
        str(binary),
        "-m", str(model),
        "-f", str(audio_p),
        "-l", language,
        "-t", str(num_threads),
        "-ojf",                 # Output full JSON with token timings
        "-of", str(out_stem),
        "-np"                   # No terminal print spam
    ]

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=timeout_sec,
            text=True
        )
    except subprocess.TimeoutExpired:
        if out_json.exists():
            out_json.unlink(missing_ok=True)
        return {"full_text": "", "segments": [], "words": [], "error": f"Transcription timed out after {timeout_sec}s"}
    except Exception as e:
        return {"full_text": "", "segments": [], "words": [], "error": str(e)}

    if not out_json.is_file():
        # Try fallback to standard -oj
        cmd[cmd.index("-ojf")] = "-oj"
        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=timeout_sec)
        except Exception:
            pass

    if not out_json.is_file():
        return {"full_text": "", "segments": [], "words": [], "error": f"whisper-cli failed: {proc.stderr[:200]}"}

    # Parse whisper JSON output
    try:
        with open(out_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        out_json.unlink(missing_ok=True)
        return {"full_text": "", "segments": [], "words": [], "error": f"Failed to parse JSON: {e}"}
    finally:
        out_json.unlink(missing_ok=True)

    segments: List[Dict[str, Any]] = []
    words: List[Dict[str, Any]] = []
    full_text_parts = []

    for seg in data.get("transcription", []):
        offsets = seg.get("offsets", {})
        start_ms = int(offsets.get("from", 0))
        end_ms = int(offsets.get("to", 0))
        text = seg.get("text", "").strip()

        # Clean music symbols ♪
        clean_text = text.replace("♪", "").replace("♫", "").strip()
        if clean_text:
            full_text_parts.append(clean_text)

        tokens = seg.get("tokens", [])
        seg_words = []
        for tok in tokens:
            t_text = tok.get("text", "").strip()
            # Ignore special tokens like [_BEG_], [_TT_...], [music], etc.
            if (t_text.startswith("[") and t_text.endswith("]")) or (t_text.startswith("<") and t_text.endswith(">")):
                continue
            if not t_text or t_text in ["♪", "♫"]:
                continue

            tok_offsets = tok.get("offsets", {})
            tok_start = int(tok_offsets.get("from", start_ms))
            tok_end = int(tok_offsets.get("to", end_ms))
            conf = float(tok.get("p", 0.85))

            word_entry = {
                "word": t_text,
                "start_ms": tok_start,
                "end_ms": tok_end,
                "confidence": round(conf, 3)
            }
            words.append(word_entry)
            seg_words.append(word_entry)

        segments.append({
            "start_ms": start_ms,
            "end_ms": end_ms,
            "text": clean_text,
            "words": seg_words
        })

    return {
        "full_text": " ".join(full_text_parts),
        "segments": segments,
        "words": words,
        "error": None
    }

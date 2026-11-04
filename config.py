"""
Tiki Rehearsal Lounge - Configuration & System Bridge
=====================================================
Locates and reads active configuration and database:
- Can operate 100% standalone with dedicated paths
- Can operate integrated with Tiki Karaoke
- Manages local studio preferences and singer vocal profiles.
"""

from __future__ import annotations
import os
import sys
import json
import shutil
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional

# Studio Version
STUDIO_VERSION = "1.0.6"

# User Data Directories
HOME_DIR = Path.home()
if sys.platform == "win32":
    CONFIG_HOME = Path(os.getenv("LOCALAPPDATA") or os.getenv("APPDATA") or (HOME_DIR / "AppData" / "Local"))
else:
    CONFIG_HOME = Path(os.getenv("XDG_CONFIG_HOME", str(HOME_DIR / ".config")))

STUDIO_CONFIG_DIR = CONFIG_HOME / "TikiRehearsalLounge"
STUDIO_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
STUDIO_CONFIG_PATH = STUDIO_CONFIG_DIR / "config.json"

VOCAL_PROFILE_PATH = STUDIO_CONFIG_DIR / "vocal_profile.json"
TEMP_TAKES_DIR = Path(tempfile.gettempdir()) / "tiki_rehearsal_takes"
TEMP_TAKES_DIR.mkdir(parents=True, exist_ok=True)


def get_bundled_binary_path(binary_name: str) -> Optional[Path]:
    """
    Finds a bundled binary (e.g. ffmpeg, ffprobe, mpv) in PyInstaller bundle,
    local bin/ directory, or system PATH with platform extension awareness.
    """
    ext = ".exe" if sys.platform == "win32" else ""
    target_name = f"{binary_name}{ext}" if not binary_name.lower().endswith(ext) else binary_name

    # 1. Check PyInstaller _MEIPASS
    if hasattr(sys, "_MEIPASS"):
        for sub in ["bin", "_internal/bin", ""]:
            cand = Path(sys._MEIPASS) / sub / target_name if sub else Path(sys._MEIPASS) / target_name
            if cand.is_file():
                return cand

    # 2. Check directory of running executable when frozen (one-dir or installed)
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        for sub in ["bin", "_internal/bin", ""]:
            cand = exe_dir / sub / target_name if sub else exe_dir / target_name
            if cand.is_file():
                return cand

    # 3. Check local application bin/
    app_root = Path(__file__).resolve().parent
    for sub in ["bin", "usr/bin", ""]:
        cand = app_root / sub / target_name if sub else app_root / target_name
        if cand.is_file():
            return cand

    # 3. Check system PATH
    sys_path = shutil.which(target_name) or shutil.which(binary_name)
    if sys_path:
        return Path(sys_path)

    return None


def find_tiki_karaoke_config_path() -> Optional[Path]:
    """Finds Tiki Karaoke's config.json from AppImage/System, local, or home paths."""
    appdata = os.getenv("APPDATA")
    localappdata = os.getenv("LOCALAPPDATA")
    raw_candidates = [
        CONFIG_HOME / "TikiKaraoke" / "config.json",
        HOME_DIR / ".config" / "TikiKaraoke" / "config.json",
        Path(appdata) / "TikiKaraoke" / "config.json" if appdata else None,
        Path(localappdata) / "TikiKaraoke" / "config.json" if localappdata else None,
        HOME_DIR / "Music" / "Karaoke" / "Tiki" / "db" / "config.json",
        HOME_DIR / "Music" / "Karaoke" / "Tiki" / "config.json",
        HOME_DIR / "Documents" / "tiki-karaoke" / "data" / "config.json",
        Path("/home/todd/Documents/tiki-karaoke/data/config.json"),
        Path("../tiki-karaoke/config.json"),
    ]
    for p in raw_candidates:
        if p and p.is_file():
            return p
    return None


def load_tiki_karaoke_config() -> Dict[str, Any]:
    """Loads Tiki Karaoke's JSON configuration, falling back to sensible defaults."""
    cfg_path = find_tiki_karaoke_config_path()
    if cfg_path:
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data
        except Exception as e:
            print(f"[Config] Error loading {cfg_path}: {e}", file=sys.stderr)

    default_base = HOME_DIR / "Music" / "Karaoke" / "Tiki"
    return {
        "bar_name": "The Tiki Hut Karaoke Lounge",
        "data_dir": str(default_base / "db"),
        "library_dir": str(default_base),
        "recordings_dir": str(default_base / "performance_recordings"),
        "target_lufs": -16.0,
        "video_quality_profile": "pro",
    }


def save_studio_config(cfg: Dict[str, Any]) -> bool:
    """Saves configuration to STUDIO_CONFIG_PATH."""
    try:
        STUDIO_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(STUDIO_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        return True
    except Exception as e:
        print(f"[Config] Error saving {STUDIO_CONFIG_PATH}: {e}", file=sys.stderr)
        return False


def load_studio_config() -> Dict[str, Any]:
    """
    Loads active config from STUDIO_CONFIG_PATH.
    If missing, auto-detects Tiki Karaoke or creates standalone defaults.
    """
    if STUDIO_CONFIG_PATH.exists():
        try:
            with open(STUDIO_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Config] Error reading {STUDIO_CONFIG_PATH}: {e}", file=sys.stderr)

    tiki_cfg_path = find_tiki_karaoke_config_path()
    if tiki_cfg_path:
        tiki = load_tiki_karaoke_config()
        lib_dir = str(tiki.get("library_dir") or (HOME_DIR / "storage" / "Karaoke"))
        data_dir = str(tiki.get("data_dir") or (HOME_DIR / "Documents" / "tiki-karaoke" / "data"))
        rec_dir = str(tiki.get("recordings_dir") or (Path(lib_dir) / "performance_recordings"))
        cfg = {
            "storage_mode": "integrated",
            "library_dir": lib_dir,
            "data_dir": data_dir,
            "recordings_dir": rec_dir,
            "target_lufs": float(tiki.get("target_lufs", -23.0)),
            "video_quality_profile": str(tiki.get("video_quality_profile", "pro")),
        }
    else:
        base_dir = HOME_DIR / "Music" / "TikiRehearsalLounge"
        cfg = {
            "storage_mode": "standalone",
            "library_dir": str(base_dir / "library"),
            "data_dir": str(STUDIO_CONFIG_DIR / "data"),
            "recordings_dir": str(base_dir / "recordings"),
            "target_lufs": -16.0,
            "video_quality_profile": "pro",
        }

    save_studio_config(cfg)
    return cfg


# Active Studio Configuration
STUDIO_CONFIG = load_studio_config()
TIKI_CONFIG = STUDIO_CONFIG  # Backwards compatibility alias

STORAGE_MODE = str(STUDIO_CONFIG.get("storage_mode", "integrated"))
TIKI_DATA_DIR = Path(STUDIO_CONFIG.get("data_dir", str(STUDIO_CONFIG_DIR / "data")))
TIKI_LIBRARY_DIR = Path(STUDIO_CONFIG.get("library_dir", str(HOME_DIR / "Music" / "TikiRehearsalLounge" / "library")))
TIKI_RECORDINGS_DIR = Path(STUDIO_CONFIG.get("recordings_dir", str(TIKI_LIBRARY_DIR / "performance_recordings")))
TIKI_TARGET_LUFS = float(STUDIO_CONFIG.get("target_lufs", -16.0))
TIKI_VIDEO_QUALITY_PROFILE = str(STUDIO_CONFIG.get("video_quality_profile", "pro"))

TIKI_DB_PATH = TIKI_DATA_DIR / "karaoke.db"

TRACKS_DIR = TIKI_LIBRARY_DIR / "tracks"
SING_ALONG_DIR = TIKI_LIBRARY_DIR / "sing_along"
FILLER_DIR = TIKI_LIBRARY_DIR / "filler"

# Create directories if they do not exist
try:
    TIKI_DATA_DIR.mkdir(parents=True, exist_ok=True)
    TRACKS_DIR.mkdir(parents=True, exist_ok=True)
    SING_ALONG_DIR.mkdir(parents=True, exist_ok=True)
    TIKI_RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

# UI Aesthetic Constants (Matching Control Hub & Tiki Theme)
COLOR_BG_MAIN = "#0d0814"        # Deep Midnight Violet
COLOR_BG_CARD = "#1a1025"        # Card Surface
COLOR_BG_CARD_HOVER = "#231534"  # Active Surface
COLOR_BORDER = "#3a2055"         # Subtle Border
COLOR_CYAN = "#00f0ff"           # Electric Cyan
COLOR_MAGENTA = "#ff2a85"        # Hot Magenta
COLOR_LIME = "#39ff14"           # Neon Lime
COLOR_GOLD = "#ffd700"           # Golden Sunset
COLOR_TEXT_MAIN = "#ffffff"      # High-contrast White
COLOR_TEXT_MUTED = "#a098b5"     # Muted Violet Grey
COLOR_DANGER = "#ff3344"         # Error / Stop Red

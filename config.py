"""
Tiki Rehearsal Lounge - Configuration & System Bridge
=====================================================
Locates and reads Tiki Karaoke's active configuration and database,
and manages local studio preferences and singer vocal profiles.
"""

from __future__ import annotations
import os
import sys
import json
from pathlib import Path
from typing import Dict, Any, Optional

# Studio Version
STUDIO_VERSION = "1.0.0"

# User Data Directories
HOME_DIR = Path.home()
CONFIG_HOME = Path(os.getenv("XDG_CONFIG_HOME", str(HOME_DIR / ".config")))
STUDIO_CONFIG_DIR = CONFIG_HOME / "TikiRehearsalLounge"
STUDIO_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

VOCAL_PROFILE_PATH = STUDIO_CONFIG_DIR / "vocal_profile.json"
TEMP_TAKES_DIR = Path("/tmp/tiki_rehearsal_takes")
TEMP_TAKES_DIR.mkdir(parents=True, exist_ok=True)


def find_tiki_karaoke_config_path() -> Optional[Path]:
    """Finds Tiki Karaoke's config.json from AppImage/System, local, or home paths."""
    candidates = [
        CONFIG_HOME / "TikiKaraoke" / "config.json",
        HOME_DIR / ".config" / "TikiKaraoke" / "config.json",
        HOME_DIR / "Music" / "Karaoke" / "Tiki" / "db" / "config.json",
        HOME_DIR / "Music" / "Karaoke" / "Tiki" / "config.json",
        Path("/home/todd/Documents/tiki-karaoke/data/config.json"),
        Path("../tiki-karaoke/config.json"),
    ]
    for p in candidates:
        if p.is_file():
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

    # Defaults matching Tiki Karaoke standard layout
    default_base = HOME_DIR / "Music" / "Karaoke" / "Tiki"
    return {
        "bar_name": "The Tiki Hut Karaoke Lounge",
        "data_dir": str(default_base / "db"),
        "library_dir": str(default_base),
        "recordings_dir": str(default_base / "performance_recordings"),
        "target_lufs": -16.0,
        "video_quality_profile": "pro",
    }


# Active Tiki Karaoke Paths
TIKI_CONFIG = load_tiki_karaoke_config()

TIKI_DATA_DIR = Path(TIKI_CONFIG.get("data_dir", str(HOME_DIR / "Music" / "Karaoke" / "Tiki" / "db")))
TIKI_LIBRARY_DIR = Path(TIKI_CONFIG.get("library_dir", str(HOME_DIR / "Music" / "Karaoke" / "Tiki")))
TIKI_RECORDINGS_DIR = Path(TIKI_CONFIG.get("recordings_dir", str(TIKI_LIBRARY_DIR / "performance_recordings")))
TIKI_TARGET_LUFS = float(TIKI_CONFIG.get("target_lufs", -16.0))
TIKI_VIDEO_QUALITY_PROFILE = str(TIKI_CONFIG.get("video_quality_profile", "pro"))

TIKI_DB_PATH = TIKI_DATA_DIR / "karaoke.db"

# Library Subdirectories
TRACKS_DIR = TIKI_LIBRARY_DIR / "tracks"
SING_ALONG_DIR = TIKI_LIBRARY_DIR / "sing_along"
FILLER_DIR = TIKI_LIBRARY_DIR / "filler"

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

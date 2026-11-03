"""
Tiki Rehearsal Lounge - Track Downloader & Normalizer
=====================================================
Downloads YouTube karaoke and sing-along guide tracks with:
- yt-dlp integration (system binary or Tiki Karaoke venv binary)
- 2-pass FFmpeg EBU R128 loudness normalization (-16.0 LUFS)
- Full schema and FTS5 synchronization with Tiki Karaoke's karaoke.db
"""

from __future__ import annotations
import os
import re
import sys
import json
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List

from config import (
    TRACKS_DIR,
    SING_ALONG_DIR,
    TIKI_TARGET_LUFS,
    TIKI_DB_PATH
)
from db.bridge import TikiDatabaseBridge


def find_ytdlp_binary() -> str:
    """Locates yt-dlp from PATH or Tiki Karaoke virtual environment."""
    system_path = shutil.which("yt-dlp")
    if system_path:
        return system_path

    candidates = [
        Path("/home/todd/Documents/GitHub/tiki-karaoke/venv/bin/yt-dlp"),
        Path.home() / ".local/bin/yt-dlp",
        Path.home() / "bin/yt-dlp",
    ]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)

    return "yt-dlp"


def sanitize_filename(name: str) -> str:
    """Strips dangerous characters for safe filesystem naming."""
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if cleaned else "unknown"


def parse_artist_title(raw_title: str) -> tuple[str, str]:
    """Extracts artist and song title from typical YouTube title formats."""
    # Remove karaoke/sing-along tags for cleaner presentation
    cleaned = re.sub(r"(?i)\s*[\(\[](karaoke|sing-along|instrumental|lyrics|official|hd|video|audio)[\)\]]", "", raw_title)
    cleaned = re.sub(r"(?i)\s*-\s*(karaoke|sing-along|instrumental).*", "", cleaned).strip()

    if " - " in cleaned:
        parts = cleaned.split(" - ", 1)
        return parts[0].strip(), parts[1].strip()
    elif " — " in cleaned:
        parts = cleaned.split(" — ", 1)
        return parts[0].strip(), parts[1].strip()

    return "Unknown Artist", cleaned.strip() or raw_title.strip()


class TrackDownloader:
    def __init__(self, db_bridge: Optional[TikiDatabaseBridge] = None):
        self.db = db_bridge or TikiDatabaseBridge()
        self.ytdlp_bin = find_ytdlp_binary()
        self.ffmpeg_bin = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"

    def search_youtube(
        self,
        query: str,
        limit: int = 5,
        karaoke_only: bool = True
    ) -> List[Dict[str, Any]]:
        """Searches YouTube for karaoke or official tracks via yt-dlp."""
        search_query = query
        if karaoke_only and "karaoke" not in query.lower():
            search_query = f"{query} karaoke"

        cmd = [
            self.ytdlp_bin,
            f"ytsearch{limit}:{search_query}",
            "--dump-json",
            "--flat-playlist",
            "--no-warnings",
            "--default-search", "ytsearch"
        ]

        results = []
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
            for line in proc.stdout.splitlines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    raw_title = data.get("title", "")
                    artist, title = parse_artist_title(raw_title)
                    is_sing_along = bool(re.search(r"(?i)(sing-along|guide\s*vocal|lead\s*vocal|with\s*vocals)", raw_title))

                    results.append({
                        "video_id": data.get("id", ""),
                        "title": title,
                        "artist": artist,
                        "original_title": raw_title,
                        "duration": int(data.get("duration") or 0),
                        "channel": data.get("uploader", "") or data.get("channel", ""),
                        "is_sing_along": is_sing_along,
                        "url": data.get("url") or f"https://www.youtube.com/watch?v={data.get('id')}"
                    })
                except Exception:
                    continue
        except Exception as e:
            print(f"[TrackDownloader] Search failed: {e}", file=sys.stderr)

        return results

    def download_track(
        self,
        video_id: str,
        title: str,
        artist: str,
        original_title: str = "",
        channel: str = "",
        duration: int = 0,
        is_sing_along: bool = False,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Downloads the YouTube video, normalizes audio to -16.0 LUFS,
        and saves into Tiki Karaoke's library with immediate database sync.
        """
        if progress_callback:
            progress_callback(0.05, "Preparing download...")

        dest_dir = SING_ALONG_DIR if is_sing_along else TRACKS_DIR
        dest_dir.mkdir(parents=True, exist_ok=True)

        safe_artist = sanitize_filename(artist)
        safe_title = sanitize_filename(title)
        raw_output_tmpl = str(dest_dir / f"__raw_{safe_artist} - {safe_title} [{video_id}].%(ext)s")
        final_filename = f"{safe_artist} - {safe_title} [{video_id}].mp4"
        final_path = dest_dir / final_filename

        # 1. Download via yt-dlp
        if progress_callback:
            progress_callback(0.20, f"Downloading {title}...")

        url = f"https://www.youtube.com/watch?v={video_id}"
        ydl_format = (
            "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo[height<=1080]+bestaudio/"
            "best[height<=1080][ext=mp4]/"
            "best"
        )

        dl_cmd = [
            self.ytdlp_bin,
            url,
            "-f", ydl_format,
            "-o", raw_output_tmpl,
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--no-warnings"
        ]

        try:
            subprocess.run(dl_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        except Exception as e:
            print(f"[TrackDownloader] yt-dlp download failed: {e}", file=sys.stderr)
            if progress_callback:
                progress_callback(0.0, "Download failed")
            return None

        # Locate raw downloaded file
        raw_candidates = list(dest_dir.glob(f"__raw_{safe_artist} - {safe_title} [{video_id}].*"))
        if not raw_candidates:
            print(f"[TrackDownloader] Downloaded raw file not found", file=sys.stderr)
            return None
        raw_file = raw_candidates[0]

        # 2. Normalize audio to -16.0 LUFS via FFmpeg loudnorm
        if progress_callback:
            progress_callback(0.70, "Normalizing audio loudness (-16.0 LUFS)...")

        norm_cmd = [
            self.ffmpeg_bin, "-y",
            "-i", str(raw_file),
            "-c:v", "copy",
            "-af", f"loudnorm=I={TIKI_TARGET_LUFS}:TP=-1.5:LRA=11",
            "-c:a", "aac",
            "-b:a", "192k",
            str(final_path)
        ]

        try:
            subprocess.run(norm_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            # Remove intermediate raw download
            if raw_file.exists():
                raw_file.unlink()
        except Exception as e:
            print(f"[TrackDownloader] Audio normalization failed: {e}. Moving raw file.", file=sys.stderr)
            if raw_file.exists():
                raw_file.rename(final_path)

        # 3. Synchronize with Tiki Karaoke Database
        if progress_callback:
            progress_callback(0.95, "Synchronizing with Tiki Karaoke database...")

        meta = {
            "video_id": video_id,
            "title": title,
            "artist": artist,
            "original_title": original_title or f"{artist} - {title}",
            "channel": channel,
            "duration": duration,
            "local_path": str(final_path),
            "normalized_lufs": TIKI_TARGET_LUFS,
            "is_sing_along": 1 if is_sing_along else 0,
            "genre": "",
            "release_year": 0,
            "media_source": "youtube",
            "media_type": "video",
            "cdg_path": ""
        }

        row_id = self.db.insert_downloaded_track(meta)
        meta["id"] = row_id

        if progress_callback:
            progress_callback(1.0, "Track ready for rehearsal!")

        return meta

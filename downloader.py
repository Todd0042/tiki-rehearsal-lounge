"""
Tiki Rehearsal Lounge - Track Downloader & Normalizer
=====================================================
Downloads YouTube karaoke and sing-along guide tracks with:
- 100% parity with Tiki Karaoke AppImage backend
- Dynamic video quality resolution (Pro 1080p, Balanced 720p, Compact 480p)
- Artist subdirectory hierarchy (tracks/<Artist>/<Title> [<id>].mp4)
- Direct yt-dlp API integration with live percentage hooks and <=600s filter
- FFmpeg EBU R128 loudness normalization matching Tiki Karaoke's config target LUFS
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

# Ensure yt_dlp is importable from Tiki Karaoke's virtualenv if not in global env
try:
    import yt_dlp
except ImportError:
    for candidate in [
        Path("/home/todd/Documents/GitHub/tiki-karaoke/venv/lib/python3.14/site-packages"),
        Path.home() / ".local/lib/python3.14/site-packages",
    ]:
        if candidate.exists() and str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))
    try:
        import yt_dlp
    except ImportError:
        yt_dlp = None

from config import (
    TRACKS_DIR,
    SING_ALONG_DIR,
    TIKI_TARGET_LUFS,
    TIKI_VIDEO_QUALITY_PROFILE,
    TIKI_DB_PATH
)
from db.bridge import TikiDatabaseBridge


def find_ytdlp_binary() -> str:
    """Locates yt-dlp binary from PATH or Tiki Karaoke virtual environment."""
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
    """Strips dangerous characters for safe filesystem naming matching Tiki Karaoke."""
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


def get_ydl_video_format(quality_profile: Optional[str] = None) -> str:
    """
    Returns the yt-dlp format selector matching Tiki Karaoke AppImage profiles:
    - pro (default): 1080p Full HD AVC1 + m4a
    - balanced_720p: 720p HD
    - compact_480p: 480p SD
    """
    fmt_480p = "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=480]+bestaudio/best[height<=480][ext=mp4]/best[height<=480]/worst[ext=mp4]"
    fmt_720p = "bestvideo[height<=720][vcodec^=avc1]+bestaudio[ext=m4a]/bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720][ext=mp4]/best[height<=720]/best[ext=mp4]"
    fmt_1080p = "bestvideo[height<=1080][vcodec^=avc1]+bestaudio[ext=m4a]/bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"

    profile = (quality_profile or TIKI_VIDEO_QUALITY_PROFILE or "pro").lower()
    if profile in ("compact_480p", "480p", "inept", "stream_only", "sd"):
        return fmt_480p
    if profile in ("balanced_720p", "720p", "hd"):
        return fmt_720p
    # Pro / 1080p default
    return fmt_1080p


def normalize_audio_loudness(input_path: Path, output_path: Path, target_lufs: float = TIKI_TARGET_LUFS) -> bool:
    """
    Performs EBU R128 loudness normalization via FFmpeg loudnorm filter,
    copying video stream losslessly (-c:v copy) and re-encoding audio to 192k AAC.
    """
    ffmpeg_bin = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
    cmd = [
        ffmpeg_bin, "-y",
        "-i", str(input_path),
        "-c:v", "copy",
        "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
        "-c:a", "aac",
        "-b:a", "192k",
        str(output_path)
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return res.returncode == 0
    except Exception as e:
        print(f"[TrackDownloader] Loudnorm error: {e}", file=sys.stderr)
        return False


class TrackDownloader:
    def __init__(self, db_bridge: Optional[TikiDatabaseBridge] = None):
        self.db = db_bridge or TikiDatabaseBridge()
        self.ytdlp_bin = find_ytdlp_binary()
        self.ffmpeg_bin = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"

    def search_youtube(
        self,
        query: str,
        limit: int = 10,
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
        quality_profile: Optional[str] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Downloads a YouTube track with exact Tiki Karaoke parity:
        - Artist subfolder structure: tracks/<Artist>/<Title> [<video_id>].mp4
        - Config-defined video quality profile (Pro 1080p, Balanced 720p, Compact 480p)
        - Duration <= 600s safeguarding
        - Real-time percentage progress callback
        - EBU R128 volume normalization (-23 or -16 LUFS from config)
        - SQLite database registration with FTS5 triggers
        """
        if progress_callback:
            progress_callback(0.02, "Preparing download...")

        # 1. Structure paths matching Tiki Karaoke
        base_dir = SING_ALONG_DIR if is_sing_along else TRACKS_DIR
        safe_artist = sanitize_filename(artist)
        safe_title = sanitize_filename(title)
        artist_dir = base_dir / safe_artist
        artist_dir.mkdir(parents=True, exist_ok=True)

        temp_path = artist_dir / f"temp_{video_id}.mp4"
        final_path = artist_dir / f"{safe_title} [{video_id}].mp4"

        # Check if already downloaded on disk
        if final_path.exists() and final_path.stat().st_size > 10000:
            if progress_callback:
                progress_callback(1.0, "Ready (Already Downloaded)")
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
            return meta

        fmt_selector = get_ydl_video_format(quality_profile)
        url = f"https://www.youtube.com/watch?v={video_id}"

        # 2. Download via yt_dlp Python API or CLI fallback
        last_reported_pct = [-1.0]

        def ydl_progress_hook(d):
            if not progress_callback:
                return
            status = d.get("status")
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes") or 0
                if total > 0:
                    pct = round((downloaded / total) * 100.0, 1)
                    if pct - last_reported_pct[0] >= 3.0 or pct >= 99.0:
                        last_reported_pct[0] = pct
                        # Scale download 0..70%
                        progress_callback(0.05 + (pct / 100.0) * 0.65, f"Downloading video ({pct}%)...")
            elif status == "finished":
                last_reported_pct[0] = 100.0
                progress_callback(0.72, "Download finished. Normalizing audio...")

        download_success = False

        if yt_dlp is not None:
            ydl_opts = {
                "format": fmt_selector,
                "outtmpl": str(temp_path),
                "quiet": True,
                "no_warnings": True,
                "merge_output_format": "mp4",
                "match_filter": yt_dlp.utils.match_filter_func("duration <= 600"),
                "socket_timeout": 15,
                "retries": 3,
                "fragment_retries": 3,
                "skip_unavailable_fragments": True,
                "progress_hooks": [ydl_progress_hook]
            }
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                download_success = temp_path.exists() and temp_path.stat().st_size > 5000
            except Exception as e:
                print(f"[TrackDownloader] yt_dlp Python API error: {e}. Trying CLI fallback...", file=sys.stderr)

        if not download_success:
            # Fallback to CLI yt-dlp binary
            if progress_callback:
                progress_callback(0.15, "Downloading via CLI engine...")
            cli_cmd = [
                self.ytdlp_bin,
                url,
                "-f", fmt_selector,
                "-o", str(temp_path),
                "--merge-output-format", "mp4",
                "--no-playlist",
                "--no-warnings",
                "--match-filter", "duration <= 600",
                "--retries", "3"
            ]
            try:
                subprocess.run(cli_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                download_success = temp_path.exists() and temp_path.stat().st_size > 5000
            except Exception as e:
                print(f"[TrackDownloader] CLI yt-dlp failed: {e}", file=sys.stderr)

        if not download_success or not temp_path.exists():
            if progress_callback:
                progress_callback(0.0, "Download failed")
            return None

        # 3. EBU R128 Audio Normalization (-23 or -16 LUFS from Tiki Karaoke config)
        if progress_callback:
            progress_callback(0.75, f"Normalizing audio loudness ({TIKI_TARGET_LUFS} LUFS)...")

        norm_ok = normalize_audio_loudness(temp_path, final_path, target_lufs=TIKI_TARGET_LUFS)
        if norm_ok and final_path.exists():
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            local_file = str(final_path)
        else:
            # Fallback: rename temp to final if loudnorm fails
            temp_path.rename(final_path)
            local_file = str(final_path)

        # 4. SQLite Registration & FTS5 Sync
        if progress_callback:
            progress_callback(0.95, "Synchronizing with Tiki Karaoke database...")

        meta = {
            "video_id": video_id,
            "title": title,
            "artist": artist,
            "original_title": original_title or f"{artist} - {title}",
            "channel": channel,
            "duration": duration,
            "local_path": local_file,
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
            progress_callback(1.0, "Ready (Offline)")

        return meta

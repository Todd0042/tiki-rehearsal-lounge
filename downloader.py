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
import time
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


def relocate_and_rename_track_file(
    curr_local_path: str,
    new_title: str,
    new_artist: str,
    new_is_sing_along: bool,
    video_id: str = ""
) -> Optional[Path]:
    """
    Renames and relocates a downloaded track file on disk to match Tiki Karaoke's folder hierarchy:
    - tracks/<Artist>/<Title> [<video_id>].mp4
    - sing_along/<Artist>/<Title> [<video_id>].mp4
    """
    if not curr_local_path:
        return None
    old_path = Path(curr_local_path)
    if not old_path.exists():
        return None

    base_dir = SING_ALONG_DIR if new_is_sing_along else TRACKS_DIR
    safe_artist = sanitize_filename(new_artist)
    safe_title = sanitize_filename(new_title)
    dest_dir = base_dir / safe_artist
    dest_dir.mkdir(parents=True, exist_ok=True)

    suffix = old_path.suffix or ".mp4"
    if video_id:
        new_filename = f"{safe_title} [{video_id}]{suffix}"
    else:
        new_filename = f"{safe_title}{suffix}"

    dest_path = dest_dir / new_filename

    # If the file path is already the destination, return as is
    if dest_path.resolve() == old_path.resolve():
        return dest_path

    # If destination file already exists and is different, prevent accidental clobbering
    if dest_path.exists() and dest_path != old_path:
        new_filename = f"{safe_title}_{int(time.time())} [{video_id}]{suffix}" if video_id else f"{safe_title}_{int(time.time())}{suffix}"
        dest_path = dest_dir / new_filename

    shutil.move(str(old_path), str(dest_path))

    # Also move accompanying CDG file if it exists (for CD+G tracks)
    old_cdg = old_path.with_suffix(".cdg")
    if old_cdg.exists():
        dest_cdg = dest_path.with_suffix(".cdg")
        try:
            shutil.move(str(old_cdg), str(dest_cdg))
        except Exception:
            pass

    # Clean up old artist directory if empty
    try:
        old_parent = old_path.parent
        if old_parent != base_dir and old_parent.is_dir() and not any(old_parent.iterdir()):
            old_parent.rmdir()
    except Exception:
        pass

    return dest_path


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


def clean_search_query(query: str) -> str:
    """Strips noisy tag words and brackets so ytsearch targets clean titles/artists."""
    q = query
    # Remove brackets and parentheses content like (karaoke), [sing-along], etc.
    q = re.sub(r"(?i)[\(\[](?:karaoke|sing-along|sing\s*along|lyrics?|instrumental|official|hd|video|audio|version)[\)\]]", "", q)
    # Remove trailing words like 'karaoke', 'sing along', 'lyrics'
    q = re.sub(r"(?i)\b(karaoke|sing-along|sing\s*along|lyrics?|instrumental)\b", "", q)
    # Clean whitespace and stray dashes
    q = re.sub(r"\s+", " ", q).strip(" -")
    return q or query.strip()


def is_karaoke_track(title: str, uploader: str = "") -> bool:
    """Validates whether a track is a legitimate karaoke/instrumental backing track."""
    t_lower = title.lower()
    u_lower = uploader.lower()

    negative_patterns = [
        r"\bofficial\s+music\s+video\b", r"\bofficial\s+video\b", r"\bmusic\s+video\b",
        r"\bofficial\s+audio\b", r"\blive\s+at\b", r"\blive\s+in\b", r"\bconcert\b",
        r"\breaction\b", r"\breacts\b", r"\bvocal\s+coach\b", r"\bhow\s+to\s+sing\b",
        r"\btutorial\b", r"\bguitar\s+tab\b", r"\bbass\s+tab\b", r"\bcover\s+by\b",
        r"\bfull\s+album\b", r"\bparody\b"
    ]
    if any(re.search(pat, t_lower) for pat in negative_patterns):
        return False

    karaoke_keywords = [
        "karaoke", "instrumental", "backing track", "backingtrack", "backing-track",
        "minus one", "minus 1", "minusone", "off vocal", "no vocal", "vocal off",
        "sing king", "singking", "karafun", "sunfly", "acoustic backing", "piano backing"
    ]
    has_kw = any(kw in t_lower for kw in karaoke_keywords)
    is_k_channel = any(kw in u_lower for kw in ["karaoke", "karafun", "sing king", "sunfly"])
    return has_kw or is_k_channel


def is_sing_along_track(title: str, uploader: str = "") -> bool:
    """Validates whether a track is a sing-along / lyric video with lead vocals."""
    t_lower = title.lower()
    u_lower = uploader.lower()

    pure_karaoke_patterns = [
        r"\bkaraoke\b", r"\binstrumental\b", r"\bbacking\s+track\b",
        r"\boff\s+vocal\b", r"\bminus\s+one\b", r"\bno\s+vocal\b", r"\bvocal\s+off\b"
    ]
    if any(re.search(pat, t_lower) for pat in pure_karaoke_patterns):
        return False

    if any(kw in u_lower for kw in ["karafun", "sing king", "singking", "sunfly", "zoom karaoke"]):
        return False

    junk_patterns = [
        r"\breaction\b", r"\breacts\b", r"\bvocal\s+coach\b",
        r"\btutorial\b", r"\bfull\s+album\b", r"\bparody\b", r"\b10\s*hours?\b"
    ]
    if any(re.search(pat, t_lower) for pat in junk_patterns):
        return False

    lyric_indicators = [
        "lyrics", "lyric video", "with lyrics", "lyrics on screen",
        "sing along", "sing-along", "singalong"
    ]
    return any(ind in t_lower for ind in lyric_indicators)


class TrackDownloader:
    def __init__(self, db_bridge: Optional[TikiDatabaseBridge] = None):
        self.db = db_bridge or TikiDatabaseBridge()
        self.ytdlp_bin = find_ytdlp_binary()
        self.ffmpeg_bin = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"

    def search_youtube(
        self,
        query: str,
        limit: int = 10,
        page: int = 1,
        filter_mode: str = "all",
        seen_video_ids: Optional[Set[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Searches YouTube with deep pagination, query cleaning, and Tiki Karaoke parity:
        - filter_mode: 'all', 'singalong', or 'karaoke'
        - page: 1-indexed pagination (deep candidate scanning)
        - seen_video_ids: excludes previously returned songs across pages
        """
        clean_q = clean_search_query(query)
        if not clean_q:
            clean_q = query.strip()
        if not clean_q:
            return []

        seen = set(seen_video_ids) if seen_video_ids else set()
        target_count = max(page * 25, 25)

        # Build search queries based on filter mode
        search_queries: List[Tuple[str, bool]] = []
        if filter_mode == "singalong":
            search_queries.append((f"ytsearch{target_count}:{clean_q} lyrics", True))
        elif filter_mode == "karaoke":
            search_queries.append((f"ytsearch{target_count}:{clean_q} karaoke", False))
        else:
            # All Tracks: balanced blend of karaoke stems and sing-along lyric videos
            search_queries.append((f"ytsearch{target_count}:{clean_q} karaoke", False))
            search_queries.append((f"ytsearch{target_count}:{clean_q} lyrics", True))

        raw_candidates: List[Dict[str, Any]] = []

        # 1. Primary: Python yt_dlp flat extraction (fast, reliable, handles warnings)
        if yt_dlp is not None:
            ydl_opts = {
                "quiet": True,
                "skip_download": True,
                "extract_flat": True,
                "no_warnings": True,
                "socket_timeout": 8,
                "retries": 2,
                "nocheckcertificate": True,
            }
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    for sq, default_sa in search_queries:
                        info = ydl.extract_info(sq, download=False)
                        entries = list(info.get("entries") or []) if isinstance(info, dict) else []
                        for e in entries:
                            if e and isinstance(e, dict):
                                e_copy = dict(e)
                                e_copy["_hint_sa"] = default_sa
                                raw_candidates.append(e_copy)
            except Exception as e:
                print(f"[TrackDownloader] yt_dlp API search warning: {e}", file=sys.stderr)

        # 2. Fallback: CLI yt-dlp binary (check=False so warnings never blank out output)
        if not raw_candidates:
            for sq, default_sa in search_queries:
                cmd = [
                    self.ytdlp_bin,
                    sq,
                    "--dump-json",
                    "--flat-playlist",
                    "--no-warnings",
                    "--default-search", "ytsearch"
                ]
                try:
                    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
                    for line in proc.stdout.splitlines():
                        line = line.strip()
                        if line:
                            try:
                                d = json.loads(line)
                                d["_hint_sa"] = default_sa
                                raw_candidates.append(d)
                            except Exception:
                                pass
                except Exception as e:
                    print(f"[TrackDownloader] CLI search warning: {e}", file=sys.stderr)

        # 3. Filter, classify, and deduplicate candidates
        results: List[Dict[str, Any]] = []
        for e in raw_candidates:
            vid = e.get("id") or ""
            if not vid or vid in seen:
                continue

            duration = int(e.get("duration") or 0)
            if duration > 660 or (duration > 0 and duration < 25):
                continue

            raw_title = e.get("title", "")
            channel = e.get("uploader", "") or e.get("channel", "")

            # Determine whether it's sing-along or karaoke
            if is_sing_along_track(raw_title, channel):
                is_sa = True
            elif is_karaoke_track(raw_title, channel):
                is_sa = False
            else:
                is_sa = bool(e.get("_hint_sa", False))

            if filter_mode == "singalong" and not is_sa:
                continue
            if filter_mode == "karaoke" and is_sa:
                continue

            artist, title = parse_artist_title(raw_title)
            seen.add(vid)

            results.append({
                "video_id": vid,
                "title": title,
                "artist": artist,
                "original_title": raw_title,
                "duration": duration,
                "channel": channel,
                "is_sing_along": is_sa,
                "url": f"https://www.youtube.com/watch?v={vid}"
            })

            if len(results) >= limit:
                break

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

"""
Tiki Rehearsal Lounge - Lyric Manager & Alignment Engine
=========================================================
Provides multi-source lyric retrieval, persistent SQLite caching,
non-blocking background track transcription, and singer lyric alignment.

Sources:
1. SQLite Database Cache (lyrics_cache) - instant retrieval after first resolution
2. Local companion files (.lrc, .vtt, .srt, .cdg)
3. Embedded MP4/MKV video subtitle streams (via ffmpeg)
4. Public Zero-Key LRCLIB API (synchronized line timestamps)
5. Background Audio STT Transcription Fallback
"""

from __future__ import annotations
import os
import re
import sys
import json
import sqlite3
import urllib.parse
import urllib.request
import subprocess
import threading
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Tuple, Callable

from config import LYRICS_CACHE_DB_PATH, get_bundled_binary_path


@dataclass
class LyricLine:
    time_ms: int
    text: str
    duration_ms: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LyricTrack:
    track_key: str
    title: str
    artist: str
    lines: List[LyricLine] = field(default_factory=list)
    plain_lyrics: str = ""
    source: str = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "track_key": self.track_key,
            "title": self.title,
            "artist": self.artist,
            "lines": [l.to_dict() for l in self.lines],
            "plain_lyrics": self.plain_lyrics,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> LyricTrack:
        lines = [
            LyricLine(
                time_ms=l.get("time_ms", 0),
                text=l.get("text", ""),
                duration_ms=l.get("duration_ms")
            )
            for l in data.get("lines", [])
        ]
        return cls(
            track_key=data.get("track_key", ""),
            title=data.get("title", ""),
            artist=data.get("artist", ""),
            lines=lines,
            plain_lyrics=data.get("plain_lyrics", ""),
            source=data.get("source", "unknown")
        )


@dataclass
class LyricAlignmentReport:
    status: str = "unavailable"           # "aligned", "transcribed_take_only", "reference_only", "unavailable"
    word_accuracy_pct: float = 0.0
    word_error_rate_pct: float = 0.0
    total_lines: int = 0
    lines_covered: int = 0
    timing_offset_ms: float = 0.0          # Average sync latency across lines (ms)
    line_details: List[Dict[str, Any]] = field(default_factory=list)
    missed_phrases: List[str] = field(default_factory=list)
    summary_text: str = "No synchronized lyrics available for this track."
    total_target_words: int = 0
    total_sung_words: int = 0
    words_matched_count: int = 0
    missed_words: List[Dict[str, Any]] = field(default_factory=list)
    word_timeline: List[Dict[str, Any]] = field(default_factory=list)
    diction_clarity_pct: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class LyricManager:
    """Manages multi-tier lyric resolution and singer performance alignment."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or LYRICS_CACHE_DB_PATH
        self._memory_cache: Dict[str, LyricTrack] = {}
        self._active_jobs: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        """Ensures SQLite lyrics_cache table exists with WAL mode."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with sqlite3.connect(str(self.db_path), timeout=10.0) as conn:
                conn.execute("PRAGMA journal_mode = WAL;")
                conn.execute("PRAGMA synchronous = NORMAL;")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS lyrics_cache (
                        track_key TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        artist TEXT NOT NULL,
                        synced_lyrics_json TEXT DEFAULT '[]',
                        plain_lyrics TEXT DEFAULT '',
                        source TEXT DEFAULT 'unknown',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_lyrics_artist_title ON lyrics_cache(artist, title);")
                conn.commit()
        except Exception as e:
            print(f"[LyricManager] Database initialization error: {e}", file=sys.stderr)

    @staticmethod
    def get_track_key(track: Dict[str, Any]) -> str:
        """Generates a canonical lookup key from video_id or artist/title."""
        vid = track.get("video_id")
        if vid and str(vid).strip():
            return str(vid).strip()
        artist = re.sub(r"\W+", "_", str(track.get("artist", "")).strip().lower())
        title = re.sub(r"\W+", "_", str(track.get("title", "")).strip().lower())
        return f"{artist}__{title}"

    def get_cached_lyrics(self, track_key: str) -> Optional[LyricTrack]:
        """Checks in-memory cache and SQLite cache for stored lyrics."""
        with self._lock:
            if track_key in self._memory_cache:
                return self._memory_cache[track_key]

        try:
            with sqlite3.connect(str(self.db_path), timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT track_key, title, artist, synced_lyrics_json, plain_lyrics, source FROM lyrics_cache WHERE track_key = ?;",
                    (track_key,)
                ).fetchone()
                if row:
                    try:
                        raw_lines = json.loads(row["synced_lyrics_json"])
                    except Exception:
                        raw_lines = []
                    lines = [LyricLine(time_ms=l.get("time_ms", 0), text=l.get("text", ""), duration_ms=l.get("duration_ms")) for l in raw_lines]
                    track = LyricTrack(
                        track_key=row["track_key"],
                        title=row["title"],
                        artist=row["artist"],
                        lines=lines,
                        plain_lyrics=row["plain_lyrics"],
                        source=row["source"]
                    )
                    with self._lock:
                        self._memory_cache[track_key] = track
                    return track
        except Exception as e:
            print(f"[LyricManager] Cache read error for '{track_key}': {e}", file=sys.stderr)

        return None

    def save_lyrics_to_cache(self, lyric_track: LyricTrack):
        """Persists resolved lyrics into SQLite and memory."""
        with self._lock:
            self._memory_cache[lyric_track.track_key] = lyric_track

        try:
            lines_json = json.dumps([l.to_dict() for l in lyric_track.lines], ensure_ascii=False)
            with sqlite3.connect(str(self.db_path), timeout=10.0) as conn:
                conn.execute("""
                    INSERT INTO lyrics_cache (track_key, title, artist, synced_lyrics_json, plain_lyrics, source)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(track_key) DO UPDATE SET
                        title = excluded.title,
                        artist = excluded.artist,
                        synced_lyrics_json = excluded.synced_lyrics_json,
                        plain_lyrics = excluded.plain_lyrics,
                        source = excluded.source;
                """, (
                    lyric_track.track_key,
                    lyric_track.title,
                    lyric_track.artist,
                    lines_json,
                    lyric_track.plain_lyrics,
                    lyric_track.source
                ))
                conn.commit()
        except Exception as e:
            print(f"[LyricManager] Cache write error for '{lyric_track.track_key}': {e}", file=sys.stderr)

    def resolve_track_lyrics(self, track: Dict[str, Any]) -> Optional[LyricTrack]:
        """
        Multi-tier lyric resolver:
        Tier 0: SQLite Cache
        Tier 1: Companion files (.lrc, .vtt, .srt)
        Tier 2: Embedded video subtitles via ffmpeg
        Tier 3: LRCLIB public synced lyrics API
        """
        track_key = self.get_track_key(track)
        title = track.get("title", "").strip()
        artist = track.get("artist", "").strip()
        local_path = track.get("local_path") or track.get("file_path")

        # Tier 0: SQLite Cache
        cached = self.get_cached_lyrics(track_key)
        if cached and (cached.lines or cached.plain_lyrics):
            return cached

        # Tier 1: Local companion files
        if local_path:
            p = Path(local_path)
            cand_files = [
                p.with_suffix(".lrc"),
                p.with_suffix(".vtt"),
                p.with_suffix(".srt"),
                p.parent / f"{p.stem}.lrc",
                p.parent / f"{p.stem}.vtt"
            ]
            for cf in cand_files:
                if cf.is_file():
                    parsed = self._parse_subtitle_file(cf)
                    if parsed:
                        lt = LyricTrack(
                            track_key=track_key,
                            title=title,
                            artist=artist,
                            lines=parsed,
                            plain_lyrics="\n".join([l.text for l in parsed]),
                            source="local_file"
                        )
                        self.save_lyrics_to_cache(lt)
                        return lt

        # Tier 2: Embedded subtitles in video via ffmpeg
        if local_path and Path(local_path).is_file():
            embedded = self._extract_embedded_subtitles(Path(local_path))
            if embedded:
                lt = LyricTrack(
                    track_key=track_key,
                    title=title,
                    artist=artist,
                    lines=embedded,
                    plain_lyrics="\n".join([l.text for l in embedded]),
                    source="embedded_subtitles"
                )
                self.save_lyrics_to_cache(lt)
                return lt

        # Tier 3: LRCLIB public API (zero API key required)
        if title and artist:
            lrclib_track = self._fetch_from_lrclib(title, artist, int(track.get("duration", 0)))
            if lrclib_track:
                lrclib_track.track_key = track_key
                self.save_lyrics_to_cache(lrclib_track)
                return lrclib_track

        # Tier 4: Standalone Offline STT on Backing Track Audio
        if local_path and Path(local_path).is_file():
            try:
                from audio.stt_engine import transcribe_audio_offline
                stt_res = transcribe_audio_offline(Path(local_path), timeout_sec=60.0)
                if stt_res.get("segments"):
                    lines = [
                        LyricLine(
                            time_ms=seg["start_ms"],
                            text=seg["text"],
                            duration_ms=max(500, seg["end_ms"] - seg["start_ms"])
                        )
                        for seg in stt_res["segments"] if seg.get("text")
                    ]
                    if lines:
                        lt = LyricTrack(
                            track_key=track_key,
                            title=title,
                            artist=artist,
                            lines=lines,
                            plain_lyrics="\n".join([l.text for l in lines]),
                            source="offline_stt_transcription"
                        )
                        self.save_lyrics_to_cache(lt)
                        return lt
            except Exception as e:
                print(f"[LyricManager] Offline track STT error: {e}", file=sys.stderr)

        return None

    def resolve_lyrics_in_background(
        self,
        track: Dict[str, Any],
        callback: Optional[Callable[[Optional[LyricTrack]], None]] = None
    ):
        """
        Dispatches background thread to resolve lyrics immediately when track is loaded,
        ensuring zero latency when rehearsal starts or finishes.
        """
        track_key = self.get_track_key(track)
        with self._lock:
            if track_key in self._active_jobs and self._active_jobs[track_key].is_alive():
                return

        def _worker():
            try:
                res = self.resolve_track_lyrics(track)
                if callback:
                    callback(res)
            except Exception as e:
                print(f"[LyricManager] Background lyric resolution error: {e}", file=sys.stderr)
            finally:
                with self._lock:
                    self._active_jobs.pop(track_key, None)

        t = threading.Thread(target=_worker, name=f"LyricResolver-{track_key}", daemon=True)
        with self._lock:
            self._active_jobs[track_key] = t
        t.start()

    def _parse_subtitle_file(self, file_path: Path) -> List[LyricLine]:
        """Parses LRC or WebVTT / SRT file into structured LyricLine instances."""
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            if file_path.suffix.lower() == ".lrc" or "[" in content[:100]:
                return self.parse_lrc_string(content)
            elif file_path.suffix.lower() in [".vtt", ".srt"]:
                return self._parse_vtt_or_srt(content)
        except Exception as e:
            print(f"[LyricManager] Error reading subtitle file {file_path}: {e}", file=sys.stderr)
        return []

    @staticmethod
    def parse_lrc_string(lrc_text: str) -> List[LyricLine]:
        """Parses standard LRC timestamped text '[mm:ss.xx] lyric text'."""
        lines = []
        lrc_pattern = re.compile(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{2,3}))?\](.*)")
        for line in lrc_text.splitlines():
            line = line.strip()
            if not line:
                continue
            m = lrc_pattern.match(line)
            if m:
                mins = int(m.group(1))
                secs = int(m.group(2))
                frac = m.group(3) or "0"
                ms_val = int(frac) * (10 if len(frac) == 2 else 1)
                time_ms = (mins * 60 + secs) * 1000 + ms_val
                text = m.group(4).strip()
                if text:
                    lines.append(LyricLine(time_ms=time_ms, text=text))
        lines.sort(key=lambda l: l.time_ms)
        return lines

    @staticmethod
    def _parse_vtt_or_srt(text: str) -> List[LyricLine]:
        """Parses WebVTT or SubRip SRT subtitle format into LyricLine objects."""
        lines = []
        time_pattern = re.compile(r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[.,](\d{3})")
        curr_time_ms = None
        curr_dur_ms = None

        for block in text.split("\n\n"):
            block_lines = [l.strip() for l in block.splitlines() if l.strip()]
            for bl in block_lines:
                m = time_pattern.search(bl)
                if m:
                    h, m_val, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
                    curr_time_ms = ((h * 60 + m_val) * 60 + s) * 1000 + ms
                    eh, em, es, ems = int(m.group(5)), int(m.group(6)), int(m.group(7)), int(m.group(8))
                    end_ms = ((eh * 60 + em) * 60 + es) * 1000 + ems
                    curr_dur_ms = max(0, end_ms - curr_time_ms)
                elif curr_time_ms is not None and not bl.isdigit() and not bl.startswith("WEBVTT"):
                    clean_text = re.sub(r"<[^>]+>", "", bl).strip()
                    if clean_text:
                        lines.append(LyricLine(time_ms=curr_time_ms, text=clean_text, duration_ms=curr_dur_ms))
                    curr_time_ms = None
                    curr_dur_ms = None
        lines.sort(key=lambda l: l.time_ms)
        return lines

    def _extract_embedded_subtitles(self, video_path: Path) -> List[LyricLine]:
        """Extracts embedded subtitle stream from MP4 or MKV using bundled ffmpeg."""
        ffmpeg_bin = get_bundled_binary_path("ffmpeg")
        if not ffmpeg_bin:
            return []

        try:
            cmd = [
                str(ffmpeg_bin),
                "-v", "quiet",
                "-i", str(video_path),
                "-map", "0:s:0",
                "-f", "webvtt",
                "pipe:1"
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5)
            if proc.returncode == 0 and proc.stdout:
                vtt_text = proc.stdout.decode("utf-8", errors="ignore")
                return self._parse_vtt_or_srt(vtt_text)
        except Exception:
            pass
        return []

    def _fetch_from_lrclib(self, title: str, artist: str, duration_sec: int = 0) -> Optional[LyricTrack]:
        """Queries the public LRCLIB API (no token or auth required)."""
        clean_title = re.sub(r"(?i)\s*[\(\[](karaoke|sing-along|instrumental|lyrics|official|hd|video|audio)[\)\]]", "", title).strip()
        params = {
            "track_name": clean_title,
            "artist_name": artist.strip()
        }
        if duration_sec > 0:
            params["duration"] = str(duration_sec)

        url = f"https://lrclib.net/api/get?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "TikiRehearsalLounge/1.0 (https://github.com/tiki-karaoke)"}
        )

        try:
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    synced = data.get("syncedLyrics")
                    plain = data.get("plainLyrics") or ""
                    lines = self.parse_lrc_string(synced) if synced else []
                    if lines or plain:
                        return LyricTrack(
                            track_key=f"{artist}__{title}",
                            title=title,
                            artist=artist,
                            lines=lines,
                            plain_lyrics=plain,
                            source="lrclib_synced" if lines else "lrclib_plain"
                        )
        except Exception:
            pass
        return None

    def align_singer_take(
        self,
        singer_audio: Any,
        sr: int,
        s_times: Any,
        s_pitches: Any,
        lyric_track: Optional[LyricTrack] = None,
        singer_wav: Optional[Path] = None
    ) -> LyricAlignmentReport:
        """
        Aligns singer's vocal take to ground-truth lyrics with full word-level STT telemetry:
        1. Transcribes singer take offline using bundled whisper-cli.
        2. Evaluates Word Error Rate (WER), missed lyrics, and word onset timing.
        3. If no ground-truth lyrics are available, returns transcribed singer words.
        """
        # Step 1: Run offline STT transcription on the singer's WAV take
        stt_res: Dict[str, Any] = {"full_text": "", "segments": [], "words": [], "error": None}
        if singer_wav and Path(singer_wav).is_file():
            try:
                from audio.stt_engine import transcribe_audio_offline
                stt_res = transcribe_audio_offline(Path(singer_wav), timeout_sec=45.0)
            except Exception as e:
                print(f"[LyricManager] Offline singer take STT error: {e}", file=sys.stderr)

        stt_words = stt_res.get("words", [])
        stt_segments = stt_res.get("segments", [])
        avg_confidence = (
            float(sum(w.get("confidence", 0.8) for w in stt_words) / len(stt_words))
            if stt_words else 0.0
        )

        # Step 2: Handle cases where target reference lyrics are unavailable
        if not lyric_track or not lyric_track.lines:
            if stt_words:
                line_details = [
                    {
                        "timestamp": f"{int(seg['start_ms'] // 60000)}:{int((seg['start_ms'] % 60000) // 1000):02d}",
                        "line": seg["text"],
                        "sung": True,
                        "sync_offset_ms": 0.0,
                        "sync_verdict": "Transcribed Vocal"
                    }
                    for seg in stt_segments if seg.get("text")
                ]
                return LyricAlignmentReport(
                    status="transcribed_take_only",
                    word_accuracy_pct=round(avg_confidence * 100.0, 1),
                    word_error_rate_pct=0.0,
                    total_lines=len(line_details),
                    lines_covered=len(line_details),
                    timing_offset_ms=0.0,
                    line_details=line_details,
                    missed_phrases=[],
                    summary_text=f"Singer take transcribed offline: {len(stt_words)} words captured with {avg_confidence*100.0:.0f}% diction clarity. Acoustic pitch was measured against chromatic grid.",
                    total_target_words=0,
                    total_sung_words=len(stt_words),
                    words_matched_count=len(stt_words),
                    missed_words=[],
                    word_timeline=stt_words,
                    diction_clarity_pct=round(avg_confidence * 100.0, 1)
                )

            plain_summary = "Target reference lyrics not found. Acoustic intonation was measured against chromatic grid."
            if lyric_track and lyric_track.plain_lyrics:
                plain_summary = f"Plain lyrics available (unsynced):\n{lyric_track.plain_lyrics[:300]}..."
            return LyricAlignmentReport(
                status="unavailable" if not lyric_track else "reference_only",
                word_accuracy_pct=0.0,
                word_error_rate_pct=0.0,
                total_lines=len(lyric_track.lines) if lyric_track else 0,
                lines_covered=0,
                timing_offset_ms=0.0,
                line_details=[],
                missed_phrases=[],
                summary_text=plain_summary,
                total_target_words=0,
                total_sung_words=0,
                words_matched_count=0,
                missed_words=[],
                word_timeline=[],
                diction_clarity_pct=0.0
            )

        # Step 3: Align singer words & voiced segments against target lyrics
        # Collect target words with expected time and line index
        target_words_list: List[Dict[str, Any]] = []
        for line_idx, line in enumerate(lyric_track.lines):
            line_text = line.text.strip()
            tokens = re.findall(r"\b[\w']+\b", line_text)
            dur_ms = line.duration_ms or 2500
            token_interval = dur_ms / max(1, len(tokens))
            for t_idx, tok in enumerate(tokens):
                expected_time_ms = line.time_ms + int(t_idx * token_interval)
                target_words_list.append({
                    "clean_word": tok.lower(),
                    "raw_word": tok,
                    "line_idx": line_idx,
                    "line_text": line_text,
                    "expected_time_ms": expected_time_ms
                })

        total_target_words = len(target_words_list)
        total_sung_words = len(stt_words)

        # Match sung STT words to target words
        matched_sung_indices = set()
        matched_target_indices = set()
        word_timeline: List[Dict[str, Any]] = []
        word_timing_deltas: List[float] = []

        for s_idx, sw in enumerate(stt_words):
            raw_w = sw.get("word", "").strip()
            clean_s = re.sub(r"[^\w']", "", raw_w).lower()
            if not clean_s:
                continue

            s_start = sw.get("start_ms", 0)
            best_t_idx = None
            best_time_diff = float("inf")

            for t_idx, tw in enumerate(target_words_list):
                if t_idx in matched_target_indices:
                    continue
                if tw["clean_word"] == clean_s:
                    diff = abs(s_start - tw["expected_time_ms"])
                    if diff < 3000 and diff < best_time_diff:  # Within 3.0s window
                        best_time_diff = diff
                        best_t_idx = t_idx

            if best_t_idx is not None:
                matched_target_indices.add(best_t_idx)
                matched_sung_indices.add(s_idx)
                tw = target_words_list[best_t_idx]
                delta_ms = float(s_start - tw["expected_time_ms"])
                word_timing_deltas.append(delta_ms)
                word_timeline.append({
                    "word": raw_w,
                    "target_word": tw["raw_word"],
                    "matched": True,
                    "start_ms": s_start,
                    "end_ms": sw.get("end_ms", s_start + 300),
                    "sync_offset_ms": round(delta_ms, 1),
                    "confidence": sw.get("confidence", 0.9)
                })
            else:
                word_timeline.append({
                    "word": raw_w,
                    "target_word": None,
                    "matched": False,
                    "start_ms": s_start,
                    "end_ms": sw.get("end_ms", s_start + 300),
                    "sync_offset_ms": 0.0,
                    "confidence": sw.get("confidence", 0.8)
                })

        words_matched_count = len(matched_target_indices)
        missed_words = [
            {
                "word": tw["raw_word"],
                "expected_time_ms": tw["expected_time_ms"],
                "line": tw["line_text"]
            }
            for t_idx, tw in enumerate(target_words_list)
            if t_idx not in matched_target_indices
        ]

        # Calculate Word Error Rate (WER)
        substitutions_or_insertions = max(0, total_sung_words - words_matched_count)
        deletions = len(missed_words)
        wer_num = deletions + substitutions_or_insertions
        wer_pct = round((wer_num / max(1, total_target_words)) * 100.0, 1)
        word_acc_pct = round(max(0.0, 100.0 - wer_pct), 1)

        # Detect singer voiced phrase boundaries (start_time, end_time) for line coverage
        voiced_segments: List[Tuple[float, float]] = []
        in_voiced = False
        start_t = 0.0

        for t, p in zip(s_times, s_pitches):
            if p > 0 and not in_voiced:
                in_voiced = True
                start_t = t
            elif p <= 0 and in_voiced:
                in_voiced = False
                if (t - start_t) >= 0.25:  # Phrase >= 250ms
                    voiced_segments.append((start_t, t))
        if in_voiced and len(s_times) > 0 and (s_times[-1] - start_t) >= 0.25:
            voiced_segments.append((start_t, s_times[-1]))

        line_details = []
        covered_count = 0
        line_timing_offsets = []
        missed_phrases = []

        for line_idx, line in enumerate(lyric_track.lines):
            target_t = line.time_ms / 1000.0
            dur = (line.duration_ms / 1000.0) if line.duration_ms else 2.5
            target_end = target_t + dur

            # Check if any sung words match this line
            line_matched_words = [tw for tw in target_words_list if tw["line_idx"] == line_idx and tw["clean_word"] in {re.sub(r"[^\w']", "", w.get("word", "")).lower() for w in stt_words}]
            line_words_total = [tw for tw in target_words_list if tw["line_idx"] == line_idx]

            matched_seg = None
            min_dist = float("inf")
            for s_start, s_end in voiced_segments:
                entry_delta = s_start - target_t
                if abs(entry_delta) <= 0.85 or (s_start <= target_end and s_end >= target_t):
                    dist = abs(entry_delta)
                    if dist < min_dist:
                        min_dist = dist
                        matched_seg = (s_start, s_end, entry_delta)

            is_covered = (matched_seg is not None) or (len(line_matched_words) >= max(1, len(line_words_total) // 2))
            if is_covered:
                covered_count += 1
                delta_ms = round(matched_seg[2] * 1000.0, 1) if matched_seg else 0.0
                line_timing_offsets.append(delta_ms)
                status_str = "Pocket Locked" if abs(delta_ms) <= 80 else ("Rushing" if delta_ms < 0 else "Dragging")
                line_details.append({
                    "timestamp": f"{int(target_t // 60)}:{int(target_t % 60):02d}",
                    "line": line.text,
                    "sung": True,
                    "sync_offset_ms": delta_ms,
                    "sync_verdict": status_str,
                    "words_matched": f"{len(line_matched_words)}/{len(line_words_total)}"
                })
            else:
                line_details.append({
                    "timestamp": f"{int(target_t // 60)}:{int(target_t % 60):02d}",
                    "line": line.text,
                    "sung": False,
                    "sync_offset_ms": 0.0,
                    "sync_verdict": "Missed Phrase",
                    "words_matched": f"0/{len(line_words_total)}"
                })
                missed_phrases.append(line.text)

        total_lines = len(lyric_track.lines)
        coverage_pct = round((covered_count / max(1, total_lines)) * 100.0, 1)
        mean_offset = (
            round(float(sum(word_timing_deltas) / len(word_timing_deltas)), 1)
            if word_timing_deltas else
            (round(float(sum(line_timing_offsets) / len(line_timing_offsets)), 1) if line_timing_offsets else 0.0)
        )

        # Summary narrative for UI and AI Coach
        missed_sample = ", ".join(m["word"] for m in missed_words[:5])
        missed_msg = f"Missed words: {missed_sample}." if missed_words else "Flawless lyric recall."
        summary = (
            f"Lyric Alignment: {words_matched_count}/{total_target_words} target words matched ({word_acc_pct}% accuracy, WER {wer_pct}%). "
            f"Line phrasing coverage: {covered_count}/{total_lines} lines ({coverage_pct}%). "
            f"Onset sync average: {mean_offset:+0.1f} ms. "
            f"{missed_msg}"
        )

        return LyricAlignmentReport(
            status="aligned",
            word_accuracy_pct=word_acc_pct if total_sung_words > 0 else coverage_pct,
            word_error_rate_pct=wer_pct,
            total_lines=total_lines,
            lines_covered=covered_count,
            timing_offset_ms=mean_offset,
            line_details=line_details,
            missed_phrases=missed_phrases[:10],
            summary_text=summary,
            total_target_words=total_target_words,
            total_sung_words=total_sung_words,
            words_matched_count=words_matched_count,
            missed_words=missed_words[:30],
            word_timeline=word_timeline,
            diction_clarity_pct=round(avg_confidence * 100.0, 1)
        )

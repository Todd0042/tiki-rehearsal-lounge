"""
Tiki Rehearsal Lounge - Progression Tracking & Rehearsal History Database
=========================================================================
Stores singer rehearsal takes in a dedicated, isolated SQLite database:
%LOCALAPPDATA%/TikiRehearsalLounge/data/rehearsal_history.db (Windows)
~/.config/TikiRehearsalLounge/data/rehearsal_history.db (Linux)

Features:
- Completely decoupled from Tiki Karaoke's karaoke.db
- Automatic rolling pruning: Keeps max 15 takes per song, max 100 takes total
- Computes bounded, token-efficient progression summaries for the LLM
- Thread-safe SQLite access with WAL mode
"""

from __future__ import annotations
import sqlite3
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional

from config import STUDIO_CONFIG_DIR

DEFAULT_PROGRESSION_DB_PATH = STUDIO_CONFIG_DIR / "data" / "rehearsal_history.db"

# Pruning limits
MAX_TAKES_PER_SONG = 15
MAX_TOTAL_TAKES = 100


def ensure_progression_schema(conn: sqlite3.Connection):
    """Initializes the rehearsal_takes table and query indices."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rehearsal_takes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER DEFAULT 0,
            track_title TEXT NOT NULL,
            track_artist TEXT DEFAULT '',
            key_shift INTEGER DEFAULT 0,
            overall_score INTEGER NOT NULL,
            pitch_accuracy_pct REAL NOT NULL,
            intonation_cents REAL NOT NULL,
            rhythm_offset_ms REAL NOT NULL,
            breath_support_pct REAL DEFAULT 0.0,
            singers_formant_ratio REAL DEFAULT 0.0,
            balanced_onsets_pct REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rehearsal_title ON rehearsal_takes(track_title, created_at);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rehearsal_created ON rehearsal_takes(created_at);")
    conn.commit()


class ProgressionDatabase:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DEFAULT_PROGRESSION_DB_PATH
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_connection() as conn:
            ensure_progression_schema(conn)

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    def record_take(
        self,
        track_title: str,
        overall_score: int,
        pitch_accuracy_pct: float,
        intonation_cents: float,
        rhythm_offset_ms: float = 0.0,
        breath_support_pct: float = 0.0,
        singers_formant_ratio: float = 0.0,
        balanced_onsets_pct: float = 0.0,
        track_id: int = 0,
        track_artist: str = "",
        key_shift: int = 0
    ) -> int:
        """
        Inserts a new take and automatically prunes old records to keep the database compact.
        """
        clean_title = (track_title or "Rehearsal Take").strip()
        clean_artist = (track_artist or "").strip()

        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO rehearsal_takes (
                    track_id, track_title, track_artist, key_shift,
                    overall_score, pitch_accuracy_pct, intonation_cents,
                    rhythm_offset_ms, breath_support_pct, singers_formant_ratio,
                    balanced_onsets_pct
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    track_id,
                    clean_title,
                    clean_artist,
                    key_shift,
                    int(overall_score),
                    float(pitch_accuracy_pct),
                    float(intonation_cents),
                    float(rhythm_offset_ms),
                    float(breath_support_pct),
                    float(singers_formant_ratio),
                    float(balanced_onsets_pct),
                )
            )
            inserted_id = cursor.lastrowid

            # 1. Prune per-song takes exceeding MAX_TAKES_PER_SONG
            conn.execute(
                """
                DELETE FROM rehearsal_takes
                WHERE id IN (
                    SELECT id FROM rehearsal_takes
                    WHERE track_title = ?
                    ORDER BY id DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (clean_title, MAX_TAKES_PER_SONG)
            )

            # 2. Prune overall studio takes exceeding MAX_TOTAL_TAKES
            conn.execute(
                """
                DELETE FROM rehearsal_takes
                WHERE id IN (
                    SELECT id FROM rehearsal_takes
                    ORDER BY id DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (MAX_TOTAL_TAKES,)
            )

            conn.commit()
            return inserted_id

    def get_song_history(self, track_title: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Returns the most recent takes for a specific track, ordered oldest to newest."""
        clean_title = (track_title or "").strip()
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM rehearsal_takes
                    WHERE track_title = ?
                    ORDER BY id DESC
                    LIMIT ?
                ) ORDER BY id ASC
                """,
                (clean_title, limit)
            )
            return [dict(r) for r in cursor.fetchall()]

    def get_song_take_count(self, track_title: str) -> int:
        """Returns the total number of takes recorded for this track."""
        clean_title = (track_title or "").strip()
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM rehearsal_takes WHERE track_title = ?",
                (clean_title,)
            )
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def get_lifetime_stats(self) -> Dict[str, Any]:
        """Returns overall studio lifetime statistics."""
        with self._lock, self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT
                    COUNT(*) as total_takes,
                    COUNT(DISTINCT track_title) as unique_songs,
                    COALESCE(AVG(overall_score), 0.0) as avg_score,
                    COALESCE(MAX(overall_score), 0) as max_score,
                    COALESCE(AVG(pitch_accuracy_pct), 0.0) as avg_pitch_acc
                FROM rehearsal_takes
                """
            )
            row = cursor.fetchone()
            if not row or row["total_takes"] == 0:
                return {
                    "total_takes": 0,
                    "unique_songs": 0,
                    "avg_score": 0.0,
                    "max_score": 0,
                    "avg_pitch_acc": 0.0,
                }
            return {
                "total_takes": int(row["total_takes"]),
                "unique_songs": int(row["unique_songs"]),
                "avg_score": round(float(row["avg_score"]), 1),
                "max_score": int(row["max_score"]),
                "avg_pitch_acc": round(float(row["avg_pitch_acc"]), 1),
            }

    def build_llm_progression_context(
        self,
        track_title: str,
        current_score: int,
        current_pitch_acc: float,
        current_breath: float
    ) -> Dict[str, Any]:
        """
        Produces a concise, token-budgeted dictionary summarizing progress for the LLM prompt.
        Avoids prompt bloat by sending only key deltas and the last 3-4 attempts.
        """
        history = self.get_song_history(track_title, limit=4)
        lifetime = self.get_lifetime_stats()
        song_take_count = self.get_song_take_count(track_title)

        # Calculate deltas against immediately preceding take on this song (if any)
        prev_take = history[-1] if history else None
        delta_score = (current_score - prev_take["overall_score"]) if prev_take else 0
        delta_pitch = round(current_pitch_acc - prev_take["pitch_accuracy_pct"], 1) if prev_take else 0.0

        # Construct short readable history lines for prompt
        recent_summaries = []
        for idx, t in enumerate(history, start=1):
            recent_summaries.append(
                f"Take #{idx}: Score {t['overall_score']} (Pitch: {t['pitch_accuracy_pct']:.0f}%, Breath: {t['breath_support_pct']:.0f}%)"
            )

        return {
            "track_title": track_title,
            "song_take_number": song_take_count + 1,
            "prior_takes_on_song": song_take_count,
            "delta_score": delta_score,
            "delta_pitch_pct": delta_pitch,
            "recent_take_summaries": recent_summaries,
            "studio_total_takes": lifetime["total_takes"] + 1,
            "studio_unique_songs": lifetime["unique_songs"],
            "has_prior_history": len(history) > 0
        }

    def clear_history(self, track_title: Optional[str] = None):
        """Clears history for a specific track or resets the entire progression database."""
        with self._lock, self._get_connection() as conn:
            if track_title:
                conn.execute("DELETE FROM rehearsal_takes WHERE track_title = ?", (track_title.strip(),))
            else:
                conn.execute("DELETE FROM rehearsal_takes")
            conn.commit()
            conn.execute("VACUUM;")

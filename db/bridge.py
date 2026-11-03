"""
Tiki Rehearsal Lounge - Database Bridge
=======================================
Provides safe, concurrent, WAL-mode SQLite access to Tiki Karaoke's karaoke.db.
Supports fast full-text searching, local track lookup, sing-along matching,
and schema-compatible track insertion with automated FTS5 trigger updates.
"""

from __future__ import annotations
import sqlite3
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from config import TIKI_DB_PATH, TIKI_TARGET_LUFS


def ensure_database_schema(conn: sqlite3.Connection):
    """
    Ensures that the required SQLite schema exists for Tiki Rehearsal Lounge,
    matching 100% of Tiki Karaoke's schema, FTS5 virtual tables, indices, and triggers.
    Allows running 100% standalone without an existing Tiki Karaoke installation.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            artist TEXT NOT NULL,
            original_title TEXT NOT NULL,
            channel TEXT DEFAULT '',
            duration INTEGER DEFAULT 0,
            is_downloaded INTEGER DEFAULT 0,
            local_path TEXT DEFAULT '',
            normalized_lufs REAL DEFAULT -16.0,
            download_status TEXT DEFAULT 'idle',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            play_count INTEGER DEFAULT 0,
            last_played TIMESTAMP,
            is_sing_along INTEGER DEFAULT 0,
            genre TEXT DEFAULT '',
            release_year INTEGER DEFAULT 0,
            media_source TEXT DEFAULT 'youtube',
            media_type TEXT DEFAULT 'video',
            cdg_path TEXT DEFAULT '',
            is_starter_pack INTEGER DEFAULT 0,
            starter_pack_name TEXT DEFAULT ''
        );
    """)

    # Create FTS5 virtual table if it doesn't exist
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS tracks_fts USING fts5(
            title,
            artist,
            original_title,
            channel,
            content='tracks',
            content_rowid='id'
        );
    """)

    # FTS5 Triggers
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS tracks_ai AFTER INSERT ON tracks BEGIN
            INSERT INTO tracks_fts(rowid, title, artist, original_title, channel)
            VALUES (new.id, new.title, new.artist, new.original_title, new.channel);
        END;
    """)

    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS tracks_ad AFTER DELETE ON tracks BEGIN
            INSERT INTO tracks_fts(tracks_fts, rowid, title, artist, original_title, channel)
            VALUES('delete', old.id, old.title, old.artist, old.original_title, old.channel);
        END;
    """)

    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS tracks_au AFTER UPDATE ON tracks BEGIN
            INSERT INTO tracks_fts(tracks_fts, rowid, title, artist, original_title, channel)
            VALUES('delete', old.id, old.title, old.artist, old.original_title, old.channel);
            INSERT INTO tracks_fts(rowid, title, artist, original_title, channel)
            VALUES (new.id, new.title, new.artist, new.original_title, new.channel);
        END;
    """)

    # Fast query indices
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_library ON tracks(is_downloaded, play_count, last_played);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tracks_sing_along ON tracks(is_sing_along, is_downloaded);")
    conn.commit()


class TikiDatabaseBridge:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or TIKI_DB_PATH

    def _get_connection(self) -> sqlite3.Connection:
        """Opens a robust SQLite connection with WAL support, auto-creating schema if running standalone."""
        if not self.db_path.exists():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.db_path), timeout=10.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("PRAGMA busy_timeout = 5000;")
            ensure_database_schema(conn)
            return conn

        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")

        # Quick check if tracks table exists
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tracks';")
        if not cur.fetchone():
            ensure_database_schema(conn)

        return conn

    def is_connected(self) -> bool:
        """Checks if the database exists and can execute a ping query."""
        try:
            with self._get_connection() as conn:
                conn.execute("SELECT 1;").fetchone()
                return True
        except Exception:
            return False

    def get_stats(self) -> Dict[str, int]:
        """Returns high-level statistics about the local library."""
        try:
            with self._get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM tracks;")
                total = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM tracks WHERE is_sing_along = 1;")
                sing_alongs = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM tracks WHERE is_downloaded = 1 OR local_path != '';")
                local_ready = cur.fetchone()[0]

                return {
                    "total_tracks": total,
                    "sing_along_tracks": sing_alongs,
                    "local_ready_tracks": local_ready,
                }
        except Exception:
            return {"total_tracks": 0, "sing_along_tracks": 0, "local_ready_tracks": 0}

    def get_library_stats(self) -> Dict[str, int]:
        """Alias for get_stats()."""
        return self.get_stats()

    def search_tracks(
        self,
        query: str,
        limit: int = 50,
        sing_along_only: bool = False,
        downloaded_only: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Searches tracks using FTS5 (if available) with fallback to LIKE.
        Prefers local/downloaded tracks first, followed by title/artist match.
        """
        q = (query or "").strip()
        params_base = {
            "sa": 1 if sing_along_only else 0,
            "dl": 1 if downloaded_only else 0,
            "lim": limit
        }

        if not q:
            # Return most recently added or played tracks
            sql = """
                SELECT id, video_id, title, artist, duration, is_downloaded, local_path,
                       is_sing_along, media_type, cdg_path, genre, release_year
                FROM tracks
                WHERE (:sa = 0 OR is_sing_along = 1)
                  AND (:dl = 0 OR is_downloaded = 1)
                ORDER BY is_downloaded DESC, id DESC
                LIMIT :lim;
            """
            with self._get_connection() as conn:
                rows = conn.execute(sql, params_base).fetchall()
                return [dict(r) for r in rows]

        # 1. Try FTS5 Match
        clean_fts = re.sub(r'[^\w\s]', ' ', q).strip()
        if clean_fts:
            fts_query = " ".join([f'"{tok}"*' for tok in clean_fts.split()])
            try:
                with self._get_connection() as conn:
                    sql_fts = """
                        SELECT t.id, t.video_id, t.title, t.artist, t.duration, t.is_downloaded,
                               t.local_path, t.is_sing_along, t.media_type, t.cdg_path, t.genre, t.release_year
                        FROM tracks_fts f
                        JOIN tracks t ON t.id = f.rowid
                        WHERE tracks_fts MATCH :match_q
                          AND (:sa = 0 OR t.is_sing_along = 1)
                          AND (:dl = 0 OR t.is_downloaded = 1)
                        ORDER BY t.is_downloaded DESC, rank
                        LIMIT :lim;
                    """
                    p = dict(params_base)
                    p["match_q"] = fts_query
                    rows = conn.execute(sql_fts, p).fetchall()
                    if rows:
                        return [dict(r) for r in rows]
            except Exception:
                pass  # Fall back to LIKE search

        # 2. LIKE Search Fallback
        like_term = f"%{q}%"
        sql_like = """
            SELECT id, video_id, title, artist, duration, is_downloaded, local_path,
                   is_sing_along, media_type, cdg_path, genre, release_year
            FROM tracks
            WHERE (title LIKE :term OR artist LIKE :term OR original_title LIKE :term)
              AND (:sa = 0 OR is_sing_along = 1)
              AND (:dl = 0 OR is_downloaded = 1)
            ORDER BY is_downloaded DESC, id DESC
            LIMIT :lim;
        """
        with self._get_connection() as conn:
            p = dict(params_base)
            p["term"] = like_term
            rows = conn.execute(sql_like, p).fetchall()
            return [dict(r) for r in rows]

    def get_track_by_id(self, track_id: int) -> Optional[Dict[str, Any]]:
        """Retrieves a track record by its integer ID."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM tracks WHERE id = ?;", (track_id,)).fetchone()
            return dict(row) if row else None

    def get_track_by_video_id(self, video_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a track record by its YouTube video_id."""
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM tracks WHERE video_id = ?;", (video_id,)).fetchone()
            return dict(row) if row else None

    def update_track_metadata(self, track_id: int, updates: Dict[str, Any]) -> bool:
        """
        Updates metadata (title, artist, original_title, is_sing_along, local_path, genre, release_year)
        for a track in Tiki Karaoke's database. Automatically synchronizes with tracks_fts via SQLite triggers.
        """
        allowed = {"title", "artist", "original_title", "is_sing_along", "local_path", "genre", "release_year"}
        fields = {k: v for k, v in updates.items() if k in allowed}
        if not fields:
            return False

        set_clause = ", ".join([f"{k} = :{k}" for k in fields.keys()])
        sql = f"UPDATE tracks SET {set_clause} WHERE id = :id;"
        params = dict(fields)
        params["id"] = track_id

        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
            return cur.rowcount > 0

    def find_sing_along_equivalent(self, title: str, artist: str) -> Optional[Dict[str, Any]]:
        """
        Finds a local sing-along track that matches the given title and artist.
        Used to provide a studio-grade lead vocal reference when practicing on an instrumental track.
        """
        with self._get_connection() as conn:
            # 1. Exact match on title & artist with is_sing_along = 1
            row = conn.execute(
                """
                SELECT * FROM tracks
                WHERE is_sing_along = 1
                  AND LOWER(TRIM(title)) = LOWER(TRIM(?))
                  AND LOWER(TRIM(artist)) = LOWER(TRIM(?))
                LIMIT 1;
                """,
                (title, artist)
            ).fetchone()
            if row:
                return dict(row)

            # 2. Fuzzy title match if artist is identical
            row = conn.execute(
                """
                SELECT * FROM tracks
                WHERE is_sing_along = 1
                  AND LOWER(TRIM(artist)) = LOWER(TRIM(?))
                  AND LOWER(title) LIKE LOWER(?)
                LIMIT 1;
                """,
                (artist, f"%{title.strip()}%")
            ).fetchone()
            return dict(row) if row else None

    def insert_downloaded_track(self, meta: Dict[str, Any]) -> int:
        """
        Inserts or updates a newly downloaded track into Tiki Karaoke's database.
        Maintains full column parity with Tiki Karaoke database schema and FTS5 triggers.
        """
        sql = """
            INSERT INTO tracks (
                video_id, title, artist, original_title, channel, duration,
                is_downloaded, local_path, normalized_lufs, download_status,
                is_sing_along, genre, release_year, media_source, media_type, cdg_path
            ) VALUES (
                :video_id, :title, :artist, :original_title, :channel, :duration,
                1, :local_path, :normalized_lufs, 'ready',
                :is_sing_along, :genre, :release_year, :media_source, :media_type, :cdg_path
            )
            ON CONFLICT(video_id) DO UPDATE SET
                title = excluded.title,
                artist = excluded.artist,
                duration = excluded.duration,
                is_downloaded = 1,
                local_path = excluded.local_path,
                normalized_lufs = excluded.normalized_lufs,
                download_status = 'ready',
                is_sing_along = excluded.is_sing_along,
                genre = excluded.genre,
                release_year = excluded.release_year,
                media_type = excluded.media_type,
                cdg_path = excluded.cdg_path;
        """
        params = {
            "video_id": meta["video_id"],
            "title": meta.get("title", "Unknown Title"),
            "artist": meta.get("artist", "Unknown Artist"),
            "original_title": meta.get("original_title", meta.get("title", "")),
            "channel": meta.get("channel", ""),
            "duration": int(meta.get("duration", 0)),
            "local_path": str(meta.get("local_path", "")),
            "normalized_lufs": float(meta.get("normalized_lufs", TIKI_TARGET_LUFS)),
            "is_sing_along": 1 if meta.get("is_sing_along") else 0,
            "genre": meta.get("genre", ""),
            "release_year": int(meta.get("release_year", 0)),
            "media_source": meta.get("media_source", "youtube"),
            "media_type": meta.get("media_type", "video"),
            "cdg_path": str(meta.get("cdg_path", "")),
        }
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            conn.commit()
            # In SQLite, UPSERT (ON CONFLICT DO UPDATE) with FTS triggers does not update lastrowid.
            # Explicitly query the primary key by video_id to ensure the authentic ID is returned.
            row = conn.execute("SELECT id FROM tracks WHERE video_id = ?;", (params["video_id"],)).fetchone()
            if row:
                return row["id"] if isinstance(row, sqlite3.Row) else row[0]
            return cur.lastrowid or 0

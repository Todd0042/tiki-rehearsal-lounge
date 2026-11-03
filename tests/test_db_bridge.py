"""
Tests for Tiki Rehearsal Lounge Database Bridge (Standalone Schema Auto-Creation & FTS5).
"""
import unittest
import tempfile
import sqlite3
from pathlib import Path

from db.bridge import TikiDatabaseBridge, ensure_database_schema


class TestDatabaseBridge(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_karaoke.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_schema_auto_creation_on_fresh_path(self):
        self.assertFalse(self.db_path.exists())
        bridge = TikiDatabaseBridge(db_path=self.db_path)
        self.assertTrue(bridge.is_connected())
        self.assertTrue(self.db_path.exists())

        # Inspect tables
        with bridge._get_connection() as conn:
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            self.assertIn("tracks", tables)
            self.assertIn("tracks_fts", tables)

    def test_search_and_insert_track(self):
        bridge = TikiDatabaseBridge(db_path=self.db_path)
        with bridge._get_connection() as conn:
            conn.execute("""
                INSERT INTO tracks (video_id, title, artist, original_title, channel, duration, is_sing_along, local_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, ("vid_caroline", "Sweet Caroline", "Neil Diamond", "Sweet Caroline", "KaraokeChannel", 210, 0, "tracks/Neil Diamond/Sweet Caroline.mp4"))
            conn.commit()

        # Search via FTS
        results = bridge.search_tracks("Caroline")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "Sweet Caroline")
        self.assertEqual(results[0]["artist"], "Neil Diamond")

    def test_update_track_metadata_and_fts_sync(self):
        bridge = TikiDatabaseBridge(db_path=self.db_path)
        with bridge._get_connection() as conn:
            cur = conn.execute("""
                INSERT INTO tracks (video_id, title, artist, original_title, channel, duration, is_sing_along, local_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, ("vid_prayer", "Livin on a Prayer", "Bon Jovi", "Livin on a Prayer", "RockTube", 245, 0, "tracks/Bon Jovi/Livin on a Prayer.mp4"))
            track_id = cur.lastrowid
            conn.commit()

        # Update metadata
        ok = bridge.update_track_metadata(track_id, {
            "artist": "Jon Bon Jovi",
            "title": "Livin' on a Prayer (Acoustic)",
            "is_sing_along": 1
        })
        self.assertTrue(ok)

        # Verify updated values in tracks table
        track = bridge.get_track_by_id(track_id)
        self.assertIsNotNone(track)
        self.assertEqual(track["artist"], "Jon Bon Jovi")
        self.assertEqual(track["title"], "Livin' on a Prayer (Acoustic)")
        self.assertEqual(track["is_sing_along"], 1)

        # Verify FTS sync
        search_res = bridge.search_tracks("Acoustic")
        self.assertEqual(len(search_res), 1)
        self.assertEqual(search_res[0]["id"], track_id)


if __name__ == "__main__":
    unittest.main()

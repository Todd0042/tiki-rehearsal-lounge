"""
Tiki Rehearsal Lounge - Studio Cockpit Window
=============================================
Native desktop studio hub matching Tiki Karaoke's Control Hub aesthetic:
- Color palette: Deep Midnight Violet (#0d0814), Electric Cyan (#00f0ff),
  Hot Magenta (#ff2a85), Neon Lime (#39ff14), Gold (#ffd700).
- Modular 4-Deck Layout:
  1. Vocal Calibration Deck (Siren Glide & Speaking Anchor exercises)
  2. Song Search & Downloader Deck (FTS5 SQLite bridge to karaoke.db)
  3. Rehearsal Transport Deck (MPV real-time pitch shifting + mic capture)
  4. Post-Take Report Card (Pitch %, Intonation, Rhythm, Vibrato, Smart Key)
"""

from __future__ import annotations
import os
import sys
import time
import threading
from pathlib import Path
from typing import Optional, Dict, Any, List

try:
    import tkinter as tk
    from tkinter import ttk, messagebox
    HAS_TKINTER = True
except Exception:
    HAS_TKINTER = False
    tk = None
    ttk = None

from config import (
    COLOR_BG_MAIN,
    COLOR_BG_CARD,
    COLOR_BG_CARD_HOVER,
    COLOR_BORDER,
    COLOR_CYAN,
    COLOR_MAGENTA,
    COLOR_LIME,
    COLOR_GOLD,
    COLOR_TEXT_MAIN,
    COLOR_TEXT_MUTED,
    COLOR_DANGER,
    STUDIO_VERSION,
    TEMP_TAKES_DIR,
    TIKI_RECORDINGS_DIR
)
from db.bridge import TikiDatabaseBridge
from audio.recorder import AudioRecorder
from audio.calibration import VocalCalibrationManager, VocalProfile
from audio.player import RehearsalPlayer
from audio.vocal_analyzer import VocalTakeAnalyzer, TakeAnalysisReport
from downloader import TrackDownloader


class StudioCockpitWindow:
    def __init__(self):
        if not HAS_TKINTER:
            raise RuntimeError("Tkinter is not available. Please install 'tk' package via your package manager.")

        self.root = tk.Tk()
        self.root.title(f"🌺 Tiki Rehearsal Lounge — Studio Cockpit v{STUDIO_VERSION}")
        self.root.geometry("1020x820")
        self.root.minsize(880, 680)
        self.root.configure(bg=COLOR_BG_MAIN)

        # Core Engines
        self.db = TikiDatabaseBridge()
        self.calibrator = VocalCalibrationManager()
        self.vocal_profile: Optional[VocalProfile] = self.calibrator.load_profile()
        self.analyzer = VocalTakeAnalyzer(vocal_profile=self.vocal_profile)
        self.recorder = AudioRecorder(on_level=self._on_mic_level)
        self.player = RehearsalPlayer(on_finished=self._on_playback_ended)
        self.downloader = TrackDownloader(db_bridge=self.db)

        # Session State
        self.selected_track: Optional[Dict[str, Any]] = None
        self.current_key_shift: int = 0
        self.current_take_wav: Optional[Path] = None
        self.is_rehearsing: bool = False
        self.is_calibrating: bool = False
        self._calib_stop_requested: bool = False

        # Build UI
        self._setup_styles()
        self._build_header()
        self._build_main_notebook()
        self._build_footer()

        # Handle clean close
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def run(self):
        """Starts UI main event loop."""
        self.root.mainloop()

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        # Custom Notebook Tabs
        style.configure(
            "TNotebook",
            background=COLOR_BG_MAIN,
            borderwidth=0
        )
        style.configure(
            "TNotebook.Tab",
            background=COLOR_BG_CARD,
            foreground=COLOR_TEXT_MUTED,
            font=("Helvetica", 11, "bold"),
            padding=[16, 8],
            borderwidth=0
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", COLOR_BORDER), ("active", COLOR_BG_CARD_HOVER)],
            foreground=[("selected", COLOR_CYAN), ("active", COLOR_TEXT_MAIN)]
        )

    def _build_header(self):
        header = tk.Frame(self.root, bg=COLOR_BG_MAIN)
        header.pack(fill="x", padx=24, pady=(16, 10))

        # Title Row
        title_row = tk.Frame(header, bg=COLOR_BG_MAIN)
        title_row.pack(fill="x")

        title_lbl = tk.Label(
            title_row,
            text="🌺 Tiki Rehearsal Lounge",
            font=("Helvetica", 18, "bold"),
            fg=COLOR_CYAN,
            bg=COLOR_BG_MAIN
        )
        title_lbl.pack(side="left")

        ver_lbl = tk.Label(
            title_row,
            text=f"v{STUDIO_VERSION}",
            font=("Helvetica", 9, "bold"),
            fg=COLOR_MAGENTA,
            bg=COLOR_BG_MAIN
        )
        ver_lbl.pack(side="left", padx=(8, 0), pady=(6, 0))

        # Database Status Badge
        self.db_status_lbl = tk.Label(
            title_row,
            text="● Database Synced",
            font=("Helvetica", 10, "bold"),
            fg=COLOR_LIME,
            bg=COLOR_BG_MAIN
        )
        self.db_status_lbl.pack(side="right", pady=(4, 0))

        # Subtitle
        sub_lbl = tk.Label(
            header,
            text="Professional Vocal Rehearsal, Formant Pitch Tracking & Adaptive Key Matching",
            font=("Helvetica", 10),
            fg=COLOR_TEXT_MUTED,
            bg=COLOR_BG_MAIN
        )
        sub_lbl.pack(anchor="w", pady=(2, 0))

    def _build_main_notebook(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=24, pady=8)

        # Tab 1: Rehearsal Studio (Primary)
        self.tab_studio = tk.Frame(self.notebook, bg=COLOR_BG_MAIN)
        self.notebook.add(self.tab_studio, text="🎤 Rehearsal Cockpit")
        self._build_studio_tab(self.tab_studio)

        # Tab 2: Song Library & Search
        self.tab_library = tk.Frame(self.notebook, bg=COLOR_BG_MAIN)
        self.notebook.add(self.tab_library, text="🎵 Song Library & Downloader")
        self._build_library_tab(self.tab_library)

        # Tab 3: Vocal Calibration
        self.tab_calib = tk.Frame(self.notebook, bg=COLOR_BG_MAIN)
        self.notebook.add(self.tab_calib, text="🎙️ Vocal Calibration")
        self._build_calibration_tab(self.tab_calib)

    # -------------------------------------------------------------
    # TAB 1: REHEARSAL STUDIO
    # -------------------------------------------------------------
    def _build_studio_tab(self, parent: tk.Frame):
        # Left Panel: Track Card & Key Shift
        left_panel = tk.Frame(parent, bg=COLOR_BG_MAIN, width=380)
        left_panel.pack(side="left", fill="both", padx=(0, 12), pady=8)

        # Selected Track Card
        track_card = tk.LabelFrame(
            left_panel,
            text=" CURRENT REHEARSAL TRACK ",
            bg=COLOR_BG_CARD,
            fg=COLOR_CYAN,
            font=("Helvetica", 10, "bold"),
            bd=1,
            relief="solid",
            padx=14,
            pady=12
        )
        track_card.pack(fill="x", pady=(0, 12))

        self.lbl_track_title = tk.Label(
            track_card,
            text="No track selected",
            font=("Helvetica", 13, "bold"),
            fg=COLOR_TEXT_MAIN,
            bg=COLOR_BG_CARD,
            wraplength=340,
            justify="left"
        )
        self.lbl_track_title.pack(anchor="w")

        self.lbl_track_artist = tk.Label(
            track_card,
            text="Browse or search library to begin",
            font=("Helvetica", 10),
            fg=COLOR_GOLD,
            bg=COLOR_BG_CARD
        )
        self.lbl_track_artist.pack(anchor="w", pady=(2, 8))

        self.lbl_track_meta = tk.Label(
            track_card,
            text="Duration: --:--  •  Format: --",
            font=("Helvetica", 9),
            fg=COLOR_TEXT_MUTED,
            bg=COLOR_BG_CARD
        )
        self.lbl_track_meta.pack(anchor="w")

        # Smart Key Deck
        key_card = tk.LabelFrame(
            left_panel,
            text=" SMART KEY TRANSPOSITION ",
            bg=COLOR_BG_CARD,
            fg=COLOR_MAGENTA,
            font=("Helvetica", 10, "bold"),
            bd=1,
            relief="solid",
            padx=14,
            pady=12
        )
        key_card.pack(fill="x", pady=(0, 12))

        key_ctrl_row = tk.Frame(key_card, bg=COLOR_BG_CARD)
        key_ctrl_row.pack(fill="x", pady=4)

        btn_key_down = tk.Button(
            key_ctrl_row,
            text="◀ -1",
            bg=COLOR_BG_CARD_HOVER,
            fg=COLOR_TEXT_MAIN,
            font=("Helvetica", 10, "bold"),
            bd=0,
            padx=10,
            pady=6,
            command=self._on_key_down
        )
        btn_key_down.pack(side="left")

        self.lbl_key_val = tk.Label(
            key_ctrl_row,
            text="Original Key (0)",
            font=("Helvetica", 12, "bold"),
            fg=COLOR_GOLD,
            bg=COLOR_BG_CARD,
            width=16
        )
        self.lbl_key_val.pack(side="left", padx=8)

        btn_key_up = tk.Button(
            key_ctrl_row,
            text="+1 ▶",
            bg=COLOR_BG_CARD_HOVER,
            fg=COLOR_TEXT_MAIN,
            font=("Helvetica", 10, "bold"),
            bd=0,
            padx=10,
            pady=6,
            command=self._on_key_up
        )
        btn_key_up.pack(side="left")

        self.btn_auto_key = tk.Button(
            key_card,
            text="✨ Auto-Match to My Voice",
            bg="#2a1640",
            fg=COLOR_CYAN,
            font=("Helvetica", 9, "bold"),
            bd=1,
            pady=6,
            command=self._on_auto_match_key
        )
        self.btn_auto_key.pack(fill="x", pady=(10, 4))

        self.lbl_key_hint = tk.Label(
            key_card,
            text="Transposition limited to ±3 semitones for pristine audio fidelity.",
            font=("Helvetica", 8),
            fg=COLOR_TEXT_MUTED,
            bg=COLOR_BG_CARD,
            wraplength=340,
            justify="left"
        )
        self.lbl_key_hint.pack(anchor="w")

        # Transport & Record Controls
        transport_card = tk.LabelFrame(
            left_panel,
            text=" REHEARSAL TRANSPORT ",
            bg=COLOR_BG_CARD,
            fg=COLOR_LIME,
            font=("Helvetica", 10, "bold"),
            bd=1,
            relief="solid",
            padx=14,
            pady=12
        )
        transport_card.pack(fill="x")

        self.btn_record_take = tk.Button(
            transport_card,
            text="🎤 START REHEARSAL TAKE",
            bg="#ff2a85",
            fg="#ffffff",
            font=("Helvetica", 12, "bold"),
            bd=0,
            pady=10,
            command=self._on_toggle_rehearsal
        )
        self.btn_record_take.pack(fill="x", pady=(0, 8))

        # Live VU Meter
        vu_row = tk.Frame(transport_card, bg=COLOR_BG_CARD)
        vu_row.pack(fill="x", pady=4)

        tk.Label(
            vu_row,
            text="MIC VU:",
            font=("Helvetica", 8, "bold"),
            fg=COLOR_TEXT_MUTED,
            bg=COLOR_BG_CARD
        ).pack(side="left")

        self.vu_canvas = tk.Canvas(
            vu_row,
            bg="#0d0814",
            height=14,
            bd=0,
            highlightthickness=0
        )
        self.vu_canvas.pack(side="left", fill="x", expand=True, padx=(8, 0))

        # Right Panel: Post-Take Performance Report Card
        right_panel = tk.Frame(parent, bg=COLOR_BG_MAIN)
        right_panel.pack(side="right", fill="both", expand=True, pady=8)

        self.report_frame = tk.LabelFrame(
            right_panel,
            text=" PERFORMANCE REPORT CARD ",
            bg=COLOR_BG_CARD,
            fg=COLOR_CYAN,
            font=("Helvetica", 11, "bold"),
            bd=1,
            relief="solid",
            padx=18,
            pady=16
        )
        self.report_frame.pack(fill="both", expand=True)

        self._build_report_card_view(self.report_frame)

    def _build_report_card_view(self, parent: tk.LabelFrame):
        # Score Banner
        self.score_banner = tk.Frame(parent, bg=COLOR_BG_CARD)
        self.score_banner.pack(fill="x", pady=(0, 16))

        self.lbl_score_num = tk.Label(
            self.score_banner,
            text="--",
            font=("Helvetica", 36, "bold"),
            fg=COLOR_CYAN,
            bg=COLOR_BG_CARD
        )
        self.lbl_score_num.pack(side="left")

        score_txt_frame = tk.Frame(self.score_banner, bg=COLOR_BG_CARD)
        score_txt_frame.pack(side="left", padx=14)

        self.lbl_score_title = tk.Label(
            score_txt_frame,
            text="Take Score",
            font=("Helvetica", 14, "bold"),
            fg=COLOR_TEXT_MAIN,
            bg=COLOR_BG_CARD
        )
        self.lbl_score_title.pack(anchor="w")

        self.lbl_score_sub = tk.Label(
            score_txt_frame,
            text="Sing a take to generate performance metrics",
            font=("Helvetica", 9),
            fg=COLOR_TEXT_MUTED,
            bg=COLOR_BG_CARD
        )
        self.lbl_score_sub.pack(anchor="w")

        # Metric Tiles Grid
        grid_frame = tk.Frame(parent, bg=COLOR_BG_CARD)
        grid_frame.pack(fill="x", pady=(0, 16))

        # Tile 1: Pitch Accuracy
        self.tile_pitch = self._create_metric_tile(grid_frame, "Pitch Accuracy", "-- %", "Quarter-tone tolerance")
        self.tile_pitch.grid(row=0, column=0, padx=6, pady=6, sticky="nsew")

        # Tile 2: Intonation Center
        self.tile_into = self._create_metric_tile(grid_frame, "Intonation Center", "-- ¢", "Sharp / Flat drift")
        self.tile_into.grid(row=0, column=1, padx=6, pady=6, sticky="nsew")

        # Tile 3: Rhythm Pocket
        self.tile_rhythm = self._create_metric_tile(grid_frame, "Rhythm Pocket", "-- ms", "Onset timing")
        self.tile_rhythm.grid(row=1, column=0, padx=6, pady=6, sticky="nsew")

        # Tile 4: Vibrato
        self.tile_vib = self._create_metric_tile(grid_frame, "Vibrato Dynamics", "-- Hz", "Modulation rate")
        self.tile_vib.grid(row=1, column=1, padx=6, pady=6, sticky="nsew")

        grid_frame.columnconfigure(0, weight=1)
        grid_frame.columnconfigure(1, weight=1)

        # Coach Notes
        notes_frame = tk.LabelFrame(
            parent,
            text=" COACH FEEDBACK ",
            bg="#140b20",
            fg=COLOR_GOLD,
            font=("Helvetica", 9, "bold"),
            bd=1,
            padx=12,
            pady=10
        )
        notes_frame.pack(fill="both", expand=True, pady=(0, 12))

        self.lbl_coach_notes = tk.Label(
            notes_frame,
            text="Complete a rehearsal take to view detailed coaching feedback.",
            font=("Helvetica", 10),
            fg=COLOR_TEXT_MAIN,
            bg="#140b20",
            wraplength=480,
            justify="left"
        )
        self.lbl_coach_notes.pack(anchor="w")

    def _create_metric_tile(self, parent: tk.Frame, title: str, val: str, sub: str) -> tk.Frame:
        tile = tk.Frame(parent, bg="#201330", bd=1, relief="solid", padx=12, pady=10)
        t_lbl = tk.Label(tile, text=title, font=("Helvetica", 8, "bold"), fg=COLOR_CYAN, bg="#201330")
        t_lbl.pack(anchor="w")

        v_lbl = tk.Label(tile, text=val, font=("Helvetica", 16, "bold"), fg=COLOR_TEXT_MAIN, bg="#201330")
        v_lbl.pack(anchor="w", pady=(2, 0))
        tile._val_lbl = v_lbl

        s_lbl = tk.Label(tile, text=sub, font=("Helvetica", 8), fg=COLOR_TEXT_MUTED, bg="#201330")
        s_lbl.pack(anchor="w")
        tile._sub_lbl = s_lbl
        return tile

    # -------------------------------------------------------------
    # TAB 2: LIBRARY & DOWNLOADER
    # -------------------------------------------------------------
    def _build_library_tab(self, parent: tk.Frame):
        # Search & Filter Bar
        search_bar = tk.Frame(parent, bg=COLOR_BG_CARD, padx=12, pady=10)
        search_bar.pack(fill="x", padx=12, pady=(12, 6))

        tk.Label(
            search_bar,
            text="SEARCH:",
            font=("Helvetica", 10, "bold"),
            fg=COLOR_CYAN,
            bg=COLOR_BG_CARD
        ).pack(side="left")

        self.ent_search = tk.Entry(
            search_bar,
            font=("Helvetica", 11),
            bg="#0d0814",
            fg=COLOR_TEXT_MAIN,
            insertbackground=COLOR_CYAN,
            bd=1,
            relief="solid"
        )
        self.ent_search.pack(side="left", fill="x", expand=True, padx=10)
        self.ent_search.bind("<Return>", lambda e: self._on_search_library())

        btn_search = tk.Button(
            search_bar,
            text="🔍 Search Library",
            bg=COLOR_BG_CARD_HOVER,
            fg=COLOR_TEXT_MAIN,
            font=("Helvetica", 9, "bold"),
            bd=0,
            padx=12,
            pady=4,
            command=self._on_search_library
        )
        btn_search.pack(side="left", padx=(0, 8))

        btn_yt = tk.Button(
            search_bar,
            text="📥 YouTube Fetch",
            bg="#2a1545",
            fg=COLOR_MAGENTA,
            font=("Helvetica", 9, "bold"),
            bd=1,
            padx=12,
            pady=4,
            command=self._on_search_youtube
        )
        btn_yt.pack(side="left")

        # Results Table
        table_frame = tk.Frame(parent, bg=COLOR_BG_MAIN)
        table_frame.pack(fill="both", expand=True, padx=12, pady=6)

        columns = ("id", "title", "artist", "duration", "type", "downloaded")
        self.tree_tracks = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")

        self.tree_tracks.heading("id", text="ID")
        self.tree_tracks.heading("title", text="Title")
        self.tree_tracks.heading("artist", text="Artist")
        self.tree_tracks.heading("duration", text="Duration")
        self.tree_tracks.heading("type", text="Type")
        self.tree_tracks.heading("downloaded", text="Ready")

        self.tree_tracks.column("id", width=50, anchor="center")
        self.tree_tracks.column("title", width=340)
        self.tree_tracks.column("artist", width=220)
        self.tree_tracks.column("duration", width=80, anchor="center")
        self.tree_tracks.column("type", width=120, anchor="center")
        self.tree_tracks.column("downloaded", width=70, anchor="center")

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree_tracks.yview)
        self.tree_tracks.configure(yscrollcommand=scrollbar.set)

        self.tree_tracks.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.tree_tracks.bind("<Double-1>", lambda e: self._on_select_track())

        # Select Button Bar
        btn_bar = tk.Frame(parent, bg=COLOR_BG_MAIN)
        btn_bar.pack(fill="x", padx=12, pady=(4, 12))

        btn_select = tk.Button(
            btn_bar,
            text="Load Selected Song into Cockpit ➔",
            bg=COLOR_CYAN,
            fg="#000000",
            font=("Helvetica", 10, "bold"),
            bd=0,
            padx=16,
            pady=8,
            command=self._on_select_track
        )
        btn_select.pack(side="right")

    # -------------------------------------------------------------
    # TAB 3: VOCAL CALIBRATION
    # -------------------------------------------------------------
    def _build_calibration_tab(self, parent: tk.Frame):
        container = tk.Frame(parent, bg=COLOR_BG_MAIN, padx=20, pady=16)
        container.pack(fill="both", expand=True)

        # Profile Summary Card
        prof_card = tk.LabelFrame(
            container,
            text=" SINGER VOCAL PROFILE ",
            bg=COLOR_BG_CARD,
            fg=COLOR_GOLD,
            font=("Helvetica", 11, "bold"),
            bd=1,
            relief="solid",
            padx=16,
            pady=14
        )
        prof_card.pack(fill="x", pady=(0, 16))

        self.lbl_prof_voice_type = tk.Label(
            prof_card,
            text="Voice Classification: Not Calibrated",
            font=("Helvetica", 13, "bold"),
            fg=COLOR_TEXT_MAIN,
            bg=COLOR_BG_CARD
        )
        self.lbl_prof_voice_type.pack(anchor="w")

        self.lbl_prof_details = tk.Label(
            prof_card,
            text="Speaking Anchor: --  •  Vocal Floor: --  •  Vocal Ceiling: --",
            font=("Helvetica", 10),
            fg=COLOR_CYAN,
            bg=COLOR_BG_CARD
        )
        self.lbl_prof_details.pack(anchor="w", pady=(4, 2))

        self.lbl_prof_tessitura = tk.Label(
            prof_card,
            text="Power Tessitura Sweet Spot: -- to --",
            font=("Helvetica", 10, "bold"),
            fg=COLOR_LIME,
            bg=COLOR_BG_CARD
        )
        self.lbl_prof_tessitura.pack(anchor="w")

        self._refresh_profile_display()

        # Calibration Exercises Row
        ex_row = tk.Frame(container, bg=COLOR_BG_MAIN)
        ex_row.pack(fill="x", pady=8)

        # Step 1: Siren Pitch Sweep
        card_ex1 = tk.LabelFrame(
            ex_row,
            text=" STEP 1: SIREN GLIDE ('Ooo ➔ Aaa') — 15 SECONDS ",
            bg=COLOR_BG_CARD,
            fg=COLOR_CYAN,
            font=("Helvetica", 10, "bold"),
            bd=1,
            padx=14,
            pady=14
        )
        card_ex1.pack(side="left", fill="both", expand=True, padx=(0, 8))

        desc_ex1 = (
            "🎯 How to perform:\n"
            "1. Take a full, relaxed breath from your diaphragm.\n"
            "2. Start at your lowest comfortable, chest-resonant pitch on a round 'Oooo'.\n"
            "3. Slowly and smoothly glide upward like a siren to your highest head/falsetto ceiling, opening to an 'Aaaa' at the top.\n"
            "4. Hold the peak note briefly (1–2s), then smoothly glide all the way back down to your lowest note.\n\n"
            "💡 Tip: Moderate volume only—do not shout or strain. We are mapping your natural boundaries."
        )
        tk.Label(
            card_ex1,
            text=desc_ex1,
            font=("Helvetica", 9),
            fg=COLOR_TEXT_MUTED,
            bg=COLOR_BG_CARD,
            wraplength=440,
            justify="left"
        ).pack(anchor="w", pady=(0, 12))

        self.btn_run_ex1 = tk.Button(
            card_ex1,
            text="🎙️ Record Siren Sweep (15s)",
            bg="#2a1640",
            fg=COLOR_CYAN,
            font=("Helvetica", 10, "bold"),
            bd=1,
            pady=8,
            command=self._on_record_siren_sweep
        )
        self.btn_run_ex1.pack(fill="x")

        # Step 2: Speaking Anchor
        card_ex2 = tk.LabelFrame(
            ex_row,
            text=" STEP 2: SPEAKING ANCHOR ('20 ➔ 0') — 25 SECONDS ",
            bg=COLOR_BG_CARD,
            fg=COLOR_MAGENTA,
            font=("Helvetica", 10, "bold"),
            bd=1,
            padx=14,
            pady=14
        )
        card_ex2.pack(side="right", fill="both", expand=True, padx=(8, 0))

        desc_ex2 = (
            "🎯 How to perform:\n"
            "1. Count backwards aloud from 20 down to 0 at an unhurried, natural pace (~1s per number: 'Twenty... Nineteen... Eighteen...').\n"
            "2. Speak naturally, exactly as if talking casually to a friend across a table.\n\n"
            "⚠️ Important: Do NOT sing or monotone chant. We need your authentic everyday conversational speaking pitch to calculate your Power Tessitura Sweet Spot."
        )
        tk.Label(
            card_ex2,
            text=desc_ex2,
            font=("Helvetica", 9),
            fg=COLOR_TEXT_MUTED,
            bg=COLOR_BG_CARD,
            wraplength=440,
            justify="left"
        ).pack(anchor="w", pady=(0, 12))

        self.btn_run_ex2 = tk.Button(
            card_ex2,
            text="🗣️ Record Speaking Anchor (25s)",
            bg="#351428",
            fg=COLOR_MAGENTA,
            font=("Helvetica", 10, "bold"),
            bd=1,
            pady=8,
            command=self._on_record_speaking_anchor
        )
        self.btn_run_ex2.pack(fill="x")

    def _build_footer(self):
        footer = tk.Frame(self.root, bg=COLOR_BG_MAIN)
        footer.pack(fill="x", padx=24, pady=(4, 12))

        self.lbl_status = tk.Label(
            footer,
            text="Ready. Select a song or run calibration.",
            font=("Helvetica", 9),
            fg=COLOR_TEXT_MUTED,
            bg=COLOR_BG_MAIN
        )
        self.lbl_status.pack(side="left")

    # -------------------------------------------------------------
    # EVENT HANDLERS & ACTIONS
    # -------------------------------------------------------------
    def _on_mic_level(self, rms: float, peak: float):
        """Updates neon VU meter canvas on main thread."""
        def _update():
            if not hasattr(self, "vu_canvas"):
                return
            width = self.vu_canvas.winfo_width()
            height = self.vu_canvas.winfo_height()
            self.vu_canvas.delete("all")
            bar_w = int(width * min(1.0, peak * 2.5))
            color = COLOR_LIME if peak < 0.75 else (COLOR_GOLD if peak < 0.90 else COLOR_DANGER)
            self.vu_canvas.create_rectangle(0, 0, bar_w, height, fill=color, outline="")

        if self.root:
            self.root.after_idle(_update)

    def _on_key_down(self):
        if self.current_key_shift > -3:
            self.current_key_shift -= 1
            self._update_key_display()

    def _on_key_up(self):
        if self.current_key_shift < 3:
            self.current_key_shift += 1
            self._update_key_display()

    def _update_key_display(self):
        shift = self.current_key_shift
        sign = f"+{shift}" if shift > 0 else (str(shift) if shift < 0 else "0 (Original)")
        self.lbl_key_val.config(text=f"Key Shift: {sign}")
        if self.player.is_playing:
            self.player.set_key_shift(shift)

    def _on_auto_match_key(self):
        if not self.selected_track:
            messagebox.showinfo("Select Track", "Please select a rehearsal track first.")
            return
        if not self.vocal_profile:
            messagebox.showinfo("Calibrate First", "Please complete vocal calibration in the 'Vocal Calibration' tab to enable smart key matching.")
            return

        # Recommend key based on track
        rec_shift, reason = self.analyzer.recommend_key_for_track({
            "max_hz": 330.0  # fallback or extracted
        })
        self.current_key_shift = rec_shift
        self._update_key_display()
        messagebox.showinfo("Smart Key Recommendation", f"Recommended Key: {rec_shift:+d}\n\n{reason}")

    def _on_toggle_rehearsal(self):
        if self.is_rehearsing:
            self._stop_rehearsal()
        else:
            self._start_rehearsal()

    def _start_rehearsal(self):
        if not self.selected_track:
            messagebox.showinfo("Select Track", "Please select a song from the library first.")
            return

        local_path = self.selected_track.get("local_path")
        if not local_path or not Path(local_path).exists():
            messagebox.showwarning("Track Not Found", "The local media file for this track was not found. Please download it first.")
            return

        self.is_rehearsing = True
        self.btn_record_take.config(text="⏹️ STOP REHEARSAL TAKE", bg=COLOR_DANGER)
        self.lbl_status.config(text="Rehearsing take... Playback and mic recording active.")

        # Prepare take file
        timestamp = int(time.time())
        take_path = TEMP_TAKES_DIR / f"take_{timestamp}.wav"
        self.current_take_wav = take_path

        # Start low-latency mic capture
        self.recorder.start_recording(take_path)

        # Start MPV playback
        self.player.play(Path(local_path), key_shift=self.current_key_shift)

    def _stop_rehearsal(self):
        self.is_rehearsing = False
        self.btn_record_take.config(text="🎤 START REHEARSAL TAKE", bg=COLOR_MAGENTA)
        self.lbl_status.config(text="Finalizing take and calculating vocal report card...")

        # Stop player & recorder
        self.player.stop()
        final_take = self.recorder.stop_recording()

        # Run analysis on background thread
        threading.Thread(
            target=self._run_take_analysis,
            args=(final_take,),
            daemon=True
        ).start()

    def _on_playback_ended(self):
        """Called automatically when MPV finishes playing track."""
        if self.is_rehearsing:
            self.root.after_idle(self._stop_rehearsal)

    def _run_take_analysis(self, take_wav: Optional[Path]):
        if not take_wav or not take_wav.exists():
            return

        # Reference sing-along vocal if available
        ref_path = None
        if self.selected_track:
            sa_meta = self.db.find_sing_along_equivalent(
                self.selected_track.get("title", ""),
                self.selected_track.get("artist", "")
            )
            if sa_meta and sa_meta.get("local_path"):
                ref_path = Path(sa_meta["local_path"])

        report = self.analyzer.analyze_take(
            singer_wav=take_wav,
            reference_wav=ref_path,
            key_shift=self.current_key_shift
        )

        self.root.after_idle(lambda: self._display_report_card(report))

    def _display_report_card(self, report: TakeAnalysisReport):
        self.lbl_score_num.config(text=str(report.overall_score))
        self.tile_pitch._val_lbl.config(text=f"{report.pitch_accuracy_pct}%")
        self.tile_into._val_lbl.config(text=f"{report.intonation_cents:+0.1f}¢")
        self.tile_rhythm._val_lbl.config(text=f"{report.rhythm_offset_ms:+0.1f}ms")
        self.tile_vib._val_lbl.config(text=f"{report.vibrato_rate_hz:0.1f} Hz" if report.vibrato_present else "Straight")

        feedback_text = "\n\n".join(report.coach_feedback)
        if report.key_recommendation_reason:
            feedback_text += f"\n\n💡 Smart Key: {report.key_recommendation_reason}"
        self.lbl_coach_notes.config(text=feedback_text)
        self.lbl_status.config(text="Performance analysis complete. Great work!")

    def _on_search_library(self):
        query = self.ent_search.get().strip()
        tracks = self.db.search_tracks(query, limit=50)

        # Clear existing
        for item in self.tree_tracks.get_children():
            self.tree_tracks.delete(item)

        for t in tracks:
            dur_min = t.get("duration", 0) // 60
            dur_sec = t.get("duration", 0) % 60
            m_type = "Sing-Along 🎙️" if t.get("is_sing_along") else "Karaoke 🎵"
            ready = "✅" if t.get("is_downloaded") else "☁️"

            self.tree_tracks.insert(
                "",
                "end",
                values=(
                    t.get("id"),
                    t.get("title"),
                    t.get("artist"),
                    f"{dur_min}:{dur_sec:02d}",
                    m_type,
                    ready
                )
            )

    def _on_select_track(self):
        selected = self.tree_tracks.selection()
        if not selected:
            return
        item_vals = self.tree_tracks.item(selected[0], "values")
        track_id = int(item_vals[0])

        track = self.db.get_track_by_id(track_id)
        if not track:
            return

        self.selected_track = track
        self.lbl_track_title.config(text=track.get("title", "Unknown Title"))
        self.lbl_track_artist.config(text=track.get("artist", "Unknown Artist"))
        dur_min = track.get("duration", 0) // 60
        dur_sec = track.get("duration", 0) % 60
        self.lbl_track_meta.config(
            text=f"Duration: {dur_min}:{dur_sec:02d}  •  Source: {track.get('media_source', 'local').upper()}"
        )
        self.notebook.select(self.tab_studio)
        self.lbl_status.config(text=f"Loaded '{track.get('title')}' into rehearsal cockpit.")

    def _on_search_youtube(self):
        query = self.ent_search.get().strip()
        if not query:
            messagebox.showinfo("Search Query", "Please enter a song title or artist to search YouTube.")
            return

        self.lbl_status.config(text="Searching YouTube via yt-dlp...")
        threading.Thread(
            target=self._run_youtube_search_worker,
            args=(query,),
            daemon=True
        ).start()

    def _run_youtube_search_worker(self, query: str):
        results = self.downloader.search_youtube(query, limit=10)

        def _update():
            for item in self.tree_tracks.get_children():
                self.tree_tracks.delete(item)
            for r in results:
                dur_min = r.get("duration", 0) // 60
                dur_sec = r.get("duration", 0) % 60
                m_type = "Sing-Along" if r.get("is_sing_along") else "Karaoke"
                self.tree_tracks.insert(
                    "",
                    "end",
                    values=(
                        "YT",
                        r.get("title"),
                        r.get("artist"),
                        f"{dur_min}:{dur_sec:02d}",
                        m_type,
                        "☁️ (Click to fetch)"
                    )
                )
            self.lbl_status.config(text=f"Found {len(results)} YouTube tracks. Double-click to download.")

        self.root.after_idle(_update)

    def _on_record_siren_sweep(self):
        if self.is_calibrating:
            # User clicked early stop
            self._calib_stop_requested = True
            return

        self.is_calibrating = True
        self._calib_stop_requested = False
        take_path = TEMP_TAKES_DIR / "calib_siren.wav"
        self.recorder.start_recording(take_path)

        total_secs = 15

        def _worker():
            for remaining in range(total_secs, 0, -1):
                if self._calib_stop_requested:
                    break
                self.root.after_idle(
                    lambda r=remaining: self.btn_run_ex1.config(
                        text=f"Singing Siren... ({r}s) [Click to Finish Early]",
                        bg=COLOR_GOLD,
                        fg="#000000"
                    )
                )
                self.root.after_idle(
                    lambda r=remaining: self.lbl_status.config(
                        text=f"Recording Siren Glide (floor ➔ ceiling ➔ floor)... {r}s remaining"
                    )
                )
                time.sleep(1.0)

            self.recorder.stop_recording()
            self.is_calibrating = False
            self.root.after_idle(lambda: self.lbl_status.config(text="Analyzing siren sweep pitch contour..."))

            try:
                res = self.calibrator.analyze_siren_sweep(take_path)
                msg = (
                    f"Siren Sweep Analyzed:\n\n"
                    f"• Vocal Floor: {res['min_note']} ({res['min_hz']:.1f} Hz)\n"
                    f"• Vocal Ceiling: {res['max_note']} ({res['max_hz']:.1f} Hz)\n"
                    f"• Total Span: {res['range_semitones']:.1f} semitones"
                )
                self.root.after_idle(lambda: messagebox.showinfo("Exercise 1 Complete", msg))
                self.vocal_profile = self.calibrator.load_profile()
                self._refresh_profile_display()
            except Exception as e:
                self.root.after_idle(lambda: messagebox.showerror("Calibration Notice", str(e)))
            finally:
                self.root.after_idle(
                    lambda: self.btn_run_ex1.config(
                        text="🎙️ Record Siren Sweep (15s)",
                        bg="#2a1640",
                        fg=COLOR_CYAN
                    )
                )

        threading.Thread(target=_worker, daemon=True).start()

    def _on_record_speaking_anchor(self):
        if self.is_calibrating:
            # User clicked early stop
            self._calib_stop_requested = True
            return

        self.is_calibrating = True
        self._calib_stop_requested = False
        take_path = TEMP_TAKES_DIR / "calib_speaking.wav"
        self.recorder.start_recording(take_path)

        total_secs = 25

        def _worker():
            for remaining in range(total_secs, 0, -1):
                if self._calib_stop_requested:
                    break
                self.root.after_idle(
                    lambda r=remaining: self.btn_run_ex2.config(
                        text=f"Counting 20➔0... ({r}s) [Click to Finish Early]",
                        bg=COLOR_GOLD,
                        fg="#000000"
                    )
                )
                self.root.after_idle(
                    lambda r=remaining: self.lbl_status.config(
                        text=f"Recording Speaking Pitch Anchor (count 20 to 0)... {r}s remaining"
                    )
                )
                time.sleep(1.0)

            self.recorder.stop_recording()
            self.is_calibrating = False
            self.root.after_idle(lambda: self.lbl_status.config(text="Analyzing speaking pitch anchor..."))

            try:
                res = self.calibrator.analyze_speaking_anchor(take_path)
                msg = (
                    f"Speaking Anchor Established:\n\n"
                    f"• Median Speaking Pitch: {res['anchor_note']} ({res['anchor_hz']:.1f} Hz)\n"
                    f"• Power Tessitura: {res['tessitura_min_note']} to {res['tessitura_max_note']}\n"
                    f"• Voice Type: {res['vocal_type']}"
                )
                self.root.after_idle(lambda: messagebox.showinfo("Exercise 2 Complete", msg))
                self.vocal_profile = self.calibrator.load_profile()
                self._refresh_profile_display()
            except Exception as e:
                self.root.after_idle(lambda: messagebox.showerror("Anchor Notice", str(e)))
            finally:
                self.root.after_idle(
                    lambda: self.btn_run_ex2.config(
                        text="🗣️ Record Speaking Anchor (25s)",
                        bg="#351428",
                        fg=COLOR_MAGENTA
                    )
                )

        threading.Thread(target=_worker, daemon=True).start()

    def _refresh_profile_display(self):
        if not self.vocal_profile:
            return
        p = self.vocal_profile
        self.lbl_prof_voice_type.config(text=f"Voice Classification: {p.vocal_type}")
        self.lbl_prof_details.config(
            text=f"Speaking Anchor: {p.anchor_note} ({p.anchor_hz:.1f} Hz)  •  Range: {p.range_min_note} to {p.range_max_note} ({p.range_semitones:.1f} st)"
        )
        self.lbl_prof_tessitura.config(
            text=f"Power Tessitura Sweet Spot: {p.tessitura_min_note} to {p.tessitura_max_note}"
        )

    def _on_close(self):
        self.player.stop()
        self.recorder.stop_recording()
        self.root.destroy()

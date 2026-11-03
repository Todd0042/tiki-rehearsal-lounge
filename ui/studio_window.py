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
from audio.calibration import (
    VocalCalibrationManager,
    VocalProfile,
    hz_to_midi,
    midi_to_note_name
)
from audio.player import RehearsalPlayer
from audio.vocal_analyzer import VocalTakeAnalyzer, TakeAnalysisReport
from downloader import TrackDownloader
from ai.offline_coach import OfflineVocalCoach, AVAILABLE_MODELS


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
        self.recorder = AudioRecorder(
            on_level=self._on_mic_level,
            on_pitch=self._on_mic_pitch
        )
        self.player = RehearsalPlayer(on_finished=self._on_playback_ended)
        self.downloader = TrackDownloader(db_bridge=self.db)

        # Offline AI Vocal Coach Engine
        self.offline_coach = OfflineVocalCoach()
        self.chat_history: List[Dict[str, str]] = []
        self._last_take_report: Optional[TakeAnalysisReport] = None
        self._is_ai_generating: bool = False
        self._selected_model_key: str = "3b"
        self._is_downloading_model: bool = False
        self._download_cancel_event: Optional[threading.Event] = None
        self._drawer_expanded: bool = False

        # Session State
        self.selected_track: Optional[Dict[str, Any]] = None
        self.current_key_shift: int = 0
        self.current_take_wav: Optional[Path] = None
        self.is_rehearsing: bool = False
        self.is_calibrating: bool = False
        self._calib_stop_requested: bool = False
        self._pitch_history: List[float] = []
        self._last_pitch_hz: float = 0.0

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

        # Multi-Stage Analysis Progress Bar (Shown during DSP analysis)
        self.analysis_prog_frame = tk.Frame(transport_card, bg="#1a0c28", bd=1, relief="solid", padx=10, pady=8)
        self.lbl_analysis_title = tk.Label(
            self.analysis_prog_frame,
            text="🔬 ANALYZING VOCAL PERFORMANCE...",
            font=("Helvetica", 8, "bold"),
            fg=COLOR_CYAN,
            bg="#1a0c28"
        )
        self.lbl_analysis_title.pack(anchor="w")

        self.analysis_prog_bar = ttk.Progressbar(
            self.analysis_prog_frame,
            orient="horizontal",
            mode="determinate"
        )
        self.analysis_prog_bar.pack(fill="x", pady=(4, 4))

        self.lbl_analysis_step = tk.Label(
            self.analysis_prog_frame,
            text="Initializing acoustic DSP engine...",
            font=("Helvetica", 8),
            fg=COLOR_GOLD,
            bg="#1a0c28",
            wraplength=320,
            justify="left"
        )
        self.lbl_analysis_step.pack(anchor="w")

        # Live VU Meter
        self.vu_row = tk.Frame(transport_card, bg=COLOR_BG_CARD)
        self.vu_row.pack(fill="x", pady=4)

        tk.Label(
            self.vu_row,
            text="MIC VU:",
            font=("Helvetica", 8, "bold"),
            fg=COLOR_TEXT_MUTED,
            bg=COLOR_BG_CARD
        ).pack(side="left")

        self.vu_canvas = tk.Canvas(
            self.vu_row,
            bg="#0d0814",
            height=14,
            bd=0,
            highlightthickness=0
        )
        self.vu_canvas.pack(side="left", fill="x", expand=True, padx=(8, 0))

        # Live Pitch Trace Canvas
        trace_header = tk.Frame(transport_card, bg=COLOR_BG_CARD)
        trace_header.pack(fill="x", pady=(10, 2))

        tk.Label(
            trace_header,
            text="LIVE PITCH TRACE:",
            font=("Helvetica", 8, "bold"),
            fg=COLOR_CYAN,
            bg=COLOR_BG_CARD
        ).pack(side="left")

        self.lbl_live_pitch = tk.Label(
            trace_header,
            text="Standby",
            font=("Helvetica", 8, "bold"),
            fg=COLOR_TEXT_MUTED,
            bg=COLOR_BG_CARD
        )
        self.lbl_live_pitch.pack(side="right")

        self.pitch_canvas = tk.Canvas(
            transport_card,
            bg="#0d0814",
            height=120,
            bd=1,
            relief="solid",
            highlightthickness=0
        )
        self.pitch_canvas.pack(fill="x", pady=(2, 0))
        self.pitch_canvas.bind("<Configure>", lambda e: self._draw_pitch_trace())

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
        # Responsive Scrollable Canvas Container
        self.report_canvas = tk.Canvas(parent, bg=COLOR_BG_CARD, bd=0, highlightthickness=0)
        self.report_scrollbar = ttk.Scrollbar(parent, orient="vertical", command=self.report_canvas.yview)
        scroll_content = tk.Frame(self.report_canvas, bg=COLOR_BG_CARD)

        scroll_content.bind(
            "<Configure>",
            lambda e: self.report_canvas.configure(scrollregion=self.report_canvas.bbox("all"))
        )
        canvas_win = self.report_canvas.create_window((0, 0), window=scroll_content, anchor="nw")

        def _on_canvas_configure(e):
            self.report_canvas.itemconfig(canvas_win, width=e.width)

        self.report_canvas.bind("<Configure>", _on_canvas_configure)
        self.report_canvas.configure(yscrollcommand=self.report_scrollbar.set)

        def _on_mousewheel(event):
            if not hasattr(self, "report_canvas") or not self.report_canvas.winfo_exists():
                return
            if event.num == 4 or (hasattr(event, "delta") and event.delta > 0):
                self.report_canvas.yview_scroll(-2, "units")
            elif event.num == 5 or (hasattr(event, "delta") and event.delta < 0):
                self.report_canvas.yview_scroll(2, "units")

        self.report_canvas.bind("<Enter>", lambda e: self.report_canvas.bind_all("<Button-4>", _on_mousewheel) or self.report_canvas.bind_all("<Button-5>", _on_mousewheel) or self.report_canvas.bind_all("<MouseWheel>", _on_mousewheel))
        self.report_canvas.bind("<Leave>", lambda e: self.report_canvas.unbind_all("<Button-4>") or self.report_canvas.unbind_all("<Button-5>") or self.report_canvas.unbind_all("<MouseWheel>"))

        self.report_canvas.pack(side="left", fill="both", expand=True)
        self.report_scrollbar.pack(side="right", fill="y")

        # Score Banner
        self.score_banner = tk.Frame(scroll_content, bg=COLOR_BG_CARD)
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
        grid_frame = tk.Frame(scroll_content, bg=COLOR_BG_CARD)
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

        # Tile 5: Resonance Ring (Singer's Formant 2.8 - 3.4 kHz)
        self.tile_res = self._create_metric_tile(grid_frame, "Resonance Ring", "--", "Singer's formant ratio")
        self.tile_res.grid(row=2, column=0, padx=6, pady=6, sticky="nsew")

        # Tile 6: Breath Support Stability
        self.tile_breath = self._create_metric_tile(grid_frame, "Breath Support", "-- %", "Phrase-end stability")
        self.tile_breath.grid(row=2, column=1, padx=6, pady=6, sticky="nsew")

        grid_frame.columnconfigure(0, weight=1)
        grid_frame.columnconfigure(1, weight=1)

        # Expandable Acoustic Timbre & Dynamics Breakdown Drawer
        self.drawer_container = tk.Frame(scroll_content, bg=COLOR_BG_CARD)
        self.drawer_container.pack(fill="x", pady=(0, 12))

        self.btn_toggle_drawer = tk.Button(
            self.drawer_container,
            text="▶ Acoustic Timbre & Dynamics Breakdown (Click to Expand)",
            font=("Helvetica", 8, "bold"),
            bg="#1d1228",
            fg=COLOR_CYAN,
            bd=1,
            relief="solid",
            pady=4,
            command=self._on_toggle_drawer
        )
        self.btn_toggle_drawer.pack(fill="x")

        self.drawer_subcards = tk.Frame(self.drawer_container, bg="#130a1c", bd=1, relief="solid", padx=10, pady=8)

        row_dyn = tk.Frame(self.drawer_subcards, bg="#130a1c")
        row_dyn.pack(fill="x", pady=2)
        tk.Label(row_dyn, text="⚡ Energy Steadiness & Compression:", font=("Helvetica", 8, "bold"), fg=COLOR_GOLD, bg="#130a1c").pack(side="left")
        self.lbl_drawer_dyn = tk.Label(row_dyn, text="-- % (Standby)", font=("Helvetica", 8), fg=COLOR_TEXT_MAIN, bg="#130a1c")
        self.lbl_drawer_dyn.pack(side="left", padx=8)

        row_sib = tk.Frame(self.drawer_subcards, bg="#130a1c")
        row_sib.pack(fill="x", pady=2)
        tk.Label(row_sib, text="🗣️ Consonant Crispness & Sibilance (5-8kHz):", font=("Helvetica", 8, "bold"), fg=COLOR_CYAN, bg="#130a1c").pack(side="left")
        self.lbl_drawer_sib = tk.Label(row_sib, text="-- (Standby)", font=("Helvetica", 8), fg=COLOR_TEXT_MAIN, bg="#130a1c")
        self.lbl_drawer_sib.pack(side="left", padx=8)

        row_fry = tk.Frame(self.drawer_subcards, bg="#130a1c")
        row_fry.pack(fill="x", pady=2)
        tk.Label(row_fry, text="🌊 Vocal Fry & Phonation Creak:", font=("Helvetica", 8, "bold"), fg=COLOR_MAGENTA, bg="#130a1c").pack(side="left")
        self.lbl_drawer_fry = tk.Label(row_fry, text="-- % (Standby)", font=("Helvetica", 8), fg=COLOR_TEXT_MAIN, bg="#130a1c")
        self.lbl_drawer_fry.pack(side="left", padx=8)

        # Coach Notes
        self.notes_frame = tk.LabelFrame(
            scroll_content,
            text=" COACH FEEDBACK ",
            bg="#140b20",
            fg=COLOR_GOLD,
            font=("Helvetica", 9, "bold"),
            bd=1,
            padx=14,
            pady=12
        )
        self.notes_frame.pack(fill="x", pady=(0, 12))

        self.coach_feedback_container = tk.Frame(self.notes_frame, bg="#140b20")
        self.coach_feedback_container.pack(fill="x")

        self.lbl_coach_empty = tk.Label(
            self.coach_feedback_container,
            text="Complete a rehearsal take to view detailed coaching feedback.",
            font=("Helvetica", 10),
            fg=COLOR_TEXT_MUTED,
            bg="#140b20",
            anchor="w",
            justify="left"
        )
        self.lbl_coach_empty.pack(fill="x", padx=4, pady=6)

        # ---------------------------------------------------------
        # AI COACH CONTAINER (Installer, Debrief, and Conversation)
        # ---------------------------------------------------------
        self.ai_container = tk.Frame(scroll_content, bg=COLOR_BG_CARD)
        self.ai_container.pack(fill="x", pady=(0, 16))

        # A. Optional Installer Card (Shown if model not installed)
        self.ai_install_card = tk.LabelFrame(
            self.ai_container,
            text=" 🤖 OFFLINE AI VOCAL COACH (OPTIONAL MODULE) ",
            bg="#1d0f2b",
            fg=COLOR_CYAN,
            font=("Helvetica", 9, "bold"),
            bd=1,
            padx=12,
            pady=10
        )

        tk.Label(
            self.ai_install_card,
            text="Enable in-depth multi-paragraph vocal diagnostics and interactive conversation.\n"
                 "100% offline • Zero accounts • Zero API keys • Completely private.",
            font=("Helvetica", 9),
            fg=COLOR_TEXT_MAIN,
            bg="#1d0f2b",
            justify="left"
        ).pack(anchor="w", pady=(0, 8))

        self.model_choice_var = tk.StringVar(value="3b")
        opt_frame = tk.Frame(self.ai_install_card, bg="#1d0f2b")
        opt_frame.pack(fill="x", pady=(0, 8))

        rb1 = tk.Radiobutton(
            opt_frame,
            text="Qwen 2.5 3B-Instruct (~1.95 GB) — Deep Pedagogy (Recommended)",
            variable=self.model_choice_var,
            value="3b",
            fg=COLOR_GOLD,
            bg="#1d0f2b",
            selectcolor="#0d0814",
            activebackground="#1d0f2b",
            activeforeground=COLOR_GOLD,
            font=("Helvetica", 9, "bold")
        )
        rb1.pack(anchor="w")

        rb2 = tk.Radiobutton(
            opt_frame,
            text="Qwen 2.5 1.5B-Instruct (~986 MB) — Ultra-Fast & Compact (Sub-1 GB)",
            variable=self.model_choice_var,
            value="1.5b",
            fg=COLOR_CYAN,
            bg="#1d0f2b",
            selectcolor="#0d0814",
            activebackground="#1d0f2b",
            activeforeground=COLOR_CYAN,
            font=("Helvetica", 9)
        )
        rb2.pack(anchor="w")

        btn_row = tk.Frame(self.ai_install_card, bg="#1d0f2b")
        btn_row.pack(fill="x")

        self.btn_install_ai = tk.Button(
            btn_row,
            text="📥 Download & Install Offline AI Coach",
            bg="#3a1859",
            fg=COLOR_CYAN,
            font=("Helvetica", 9, "bold"),
            bd=1,
            padx=12,
            pady=6,
            command=self._on_download_ai_model
        )
        self.btn_install_ai.pack(side="left")

        self.lbl_ai_install_status = tk.Label(
            btn_row,
            text="Ready to install",
            font=("Helvetica", 8),
            fg=COLOR_TEXT_MUTED,
            bg="#1d0f2b"
        )
        self.lbl_ai_install_status.pack(side="left", padx=10)

        self.prog_ai = ttk.Progressbar(self.ai_install_card, orient="horizontal", mode="determinate")

        # B. Automatic AI Debriefing Card (Shown if model installed)
        self.ai_debrief_card = tk.LabelFrame(
            self.ai_container,
            text=" 🤖 AI VOCAL COACH IN-DEPTH DEBRIEF ",
            bg="#1c0e2c",
            fg=COLOR_GOLD,
            font=("Helvetica", 9, "bold"),
            bd=1,
            padx=12,
            pady=10
        )

        self.lbl_ai_debrief_text = tk.Label(
            self.ai_debrief_card,
            text="Take debriefing will appear here once take analysis completes.",
            font=("Helvetica", 9),
            fg=COLOR_TEXT_MAIN,
            bg="#1c0e2c",
            anchor="w",
            justify="left",
            wraplength=420
        )
        self.lbl_ai_debrief_text.pack(fill="x")

        # C. Interactive Performance Conversation Card
        self.conversation_card = tk.LabelFrame(
            self.ai_container,
            text=" 💬 CONVERSATION WITH COACH ",
            bg="#150a22",
            fg=COLOR_CYAN,
            font=("Helvetica", 9, "bold"),
            bd=1,
            padx=12,
            pady=10
        )

        # Quick Action Pills
        pills_row = tk.Frame(self.conversation_card, bg="#150a22")
        pills_row.pack(fill="x", pady=(0, 6))

        btn_p1 = tk.Button(
            pills_row,
            text="🎯 Fix Pitch Droop",
            font=("Helvetica", 7, "bold"),
            bg="#2a143f",
            fg=COLOR_TEXT_MAIN,
            bd=0,
            padx=6,
            pady=3,
            command=lambda: self._send_coach_prompt("How can I fix pitch droop at the ends of my phrases?")
        )
        btn_p1.pack(side="left", padx=(0, 4))

        btn_p2 = tk.Button(
            pills_row,
            text="🎹 Key Shift Advice",
            font=("Helvetica", 7, "bold"),
            bg="#2a143f",
            fg=COLOR_GOLD,
            bd=0,
            padx=6,
            pady=3,
            command=lambda: self._send_coach_prompt("Would changing the key shift make this song fit my voice better?")
        )
        btn_p2.pack(side="left", padx=4)

        btn_p3 = tk.Button(
            pills_row,
            text="✨ Add Resonance Ring",
            font=("Helvetica", 7, "bold"),
            bg="#2a143f",
            fg=COLOR_CYAN,
            bd=0,
            padx=6,
            pady=3,
            command=lambda: self._send_coach_prompt("How can I get more singer's formant ring without throat strain?")
        )
        btn_p3.pack(side="left", padx=4)

        # Chat Transcript Frame
        self.chat_transcript_container = tk.Frame(self.conversation_card, bg="#0d0615", bd=1, relief="solid", padx=8, pady=6)
        self.chat_transcript_container.pack(fill="both", expand=True, pady=(2, 8))

        self.lbl_chat_intro = tk.Label(
            self.chat_transcript_container,
            text="Ask Maestro Tiki anything about your take, breath technique, or key adjustments:",
            font=("Helvetica", 8, "italic"),
            fg=COLOR_TEXT_MUTED,
            bg="#0d0615"
        )
        self.lbl_chat_intro.pack(anchor="w")

        # Chat Input Row
        chat_input_row = tk.Frame(self.conversation_card, bg="#150a22")
        chat_input_row.pack(fill="x")

        self.ent_chat_input = tk.Entry(
            chat_input_row,
            font=("Helvetica", 9),
            bg="#0d0814",
            fg=COLOR_TEXT_MAIN,
            insertbackground=COLOR_CYAN,
            bd=1,
            relief="solid"
        )
        self.ent_chat_input.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.ent_chat_input.bind("<Return>", lambda e: self._on_submit_chat())

        self.btn_send_chat = tk.Button(
            chat_input_row,
            text="Send ➔",
            font=("Helvetica", 8, "bold"),
            bg=COLOR_CYAN,
            fg="#000000",
            bd=0,
            padx=10,
            pady=4,
            command=self._on_submit_chat
        )
        self.btn_send_chat.pack(side="right")

        self._refresh_ai_coach_view()

        def _on_resize_notes(event):
            wrap = max(260, event.width - 60)
            if hasattr(self, "lbl_coach_empty") and self.lbl_coach_empty.winfo_exists():
                self.lbl_coach_empty.config(wraplength=wrap)
            if hasattr(self, "lbl_ai_debrief_text") and self.lbl_ai_debrief_text.winfo_exists():
                self.lbl_ai_debrief_text.config(wraplength=wrap)
            for widget in self.coach_feedback_container.winfo_children():
                for sub in widget.winfo_children():
                    if isinstance(sub, tk.Label) and sub.cget("text") != "✦":
                        sub.config(wraplength=wrap)
            if hasattr(self, "chat_transcript_container"):
                for w in self.chat_transcript_container.winfo_children():
                    if isinstance(w, tk.Label):
                        w.config(wraplength=wrap)
        self.notes_frame.bind("<Configure>", _on_resize_notes)

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

    def _on_mic_pitch(self, hz: float):
        """Updates live scrolling pitch trace on main thread."""
        self._pitch_history.append(hz)
        if len(self._pitch_history) > 60:
            self._pitch_history.pop(0)
        self._last_pitch_hz = hz

        if self.root:
            self.root.after_idle(self._draw_pitch_trace)

    def _draw_pitch_trace(self):
        if not hasattr(self, "pitch_canvas") or not self.pitch_canvas.winfo_exists():
            return

        w = self.pitch_canvas.winfo_width()
        h = self.pitch_canvas.winfo_height()
        if w <= 10 or h <= 10:
            return

        self.pitch_canvas.delete("all")

        # Range boundaries (MIDI 36 = C2 ~65Hz, MIDI 72 = C5 ~523Hz)
        min_midi = 36.0
        max_midi = 72.0
        midi_span = max_midi - min_midi

        # Note reference lines to draw
        guidelines = [
            (36, "C2"), (43, "G2"), (48, "C3"), (55, "G3"), (60, "C4"), (67, "G4"), (72, "C5")
        ]

        for m_val, m_name in guidelines:
            y = h - int(((m_val - min_midi) / midi_span) * (h - 20)) - 10
            if 0 <= y <= h:
                self.pitch_canvas.create_line(30, y, w, y, fill="#1e132c", dash=(2, 4))
                self.pitch_canvas.create_text(
                    16, y, text=m_name, font=("Helvetica", 7), fill="#6c5688", anchor="center"
                )

        if not self.is_rehearsing or not self._pitch_history:
            self.pitch_canvas.create_text(
                w / 2, h / 2,
                text="Live Pitch Monitor (Active during rehearsal take)",
                font=("Helvetica", 9),
                fill="#4a3760"
            )
            return

        # Plot pitch history points
        pts = self._pitch_history
        n = len(pts)
        if n < 1:
            return

        coords = []
        step_x = (w - 38) / max(1, 60 - 1)
        start_x = 34

        for idx, hz in enumerate(pts):
            x = start_x + (idx * step_x)
            if hz > 0:
                m = hz_to_midi(hz)
                clamped_m = max(min_midi, min(max_midi, m))
                y = h - int(((clamped_m - min_midi) / midi_span) * (h - 20)) - 10
                coords.append((x, y, hz, m))
            else:
                coords.append((x, None, 0.0, 0.0))

        # Draw lines between consecutive voiced points
        for i in range(len(coords) - 1):
            x1, y1, hz1, _ = coords[i]
            x2, y2, hz2, _ = coords[i + 1]
            if y1 is not None and y2 is not None:
                self.pitch_canvas.create_line(x1, y1, x2, y2, fill=COLOR_CYAN, width=2)

        # Draw current leading point
        last_x, last_y, last_hz, last_m = coords[-1]
        if last_y is not None and last_hz > 0:
            target_midi = round(last_m)
            cents_err = (last_m - target_midi) * 100.0
            note_name = midi_to_note_name(last_m)

            if abs(cents_err) <= 28.0:
                # Locked in tune! Neon Lime glow
                self.pitch_canvas.create_oval(last_x - 6, last_y - 6, last_x + 6, last_y + 6, fill="", outline=COLOR_LIME, width=2)
                self.pitch_canvas.create_oval(last_x - 3, last_y - 3, last_x + 3, last_y + 3, fill=COLOR_LIME, outline="")
                self.lbl_live_pitch.config(
                    text=f"✦ {note_name} ({last_hz:.1f} Hz) LOCKED IN ✦",
                    fg=COLOR_LIME
                )
            else:
                self.pitch_canvas.create_oval(last_x - 4, last_y - 4, last_x + 4, last_y + 4, fill=COLOR_CYAN, outline="")
                self.lbl_live_pitch.config(
                    text=f"{note_name} ({last_hz:.1f} Hz) {cents_err:+0.0f}¢",
                    fg=COLOR_CYAN
                )
        else:
            self.lbl_live_pitch.config(text="Unvoiced", fg=COLOR_TEXT_MUTED)

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
        self._pitch_history.clear()
        self._draw_pitch_trace()

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
        self.lbl_live_pitch.config(text="Take Completed", fg=COLOR_GOLD)
        self._draw_pitch_trace()

        # Stop player & recorder
        self.player.stop()
        final_take = self.recorder.stop_recording()

        # Show multi-stage analysis progress bar
        self.analysis_prog_bar["value"] = 5
        self.lbl_analysis_step.config(text="Stopping capture and loading audio...")
        self.analysis_prog_frame.pack(fill="x", pady=(0, 8), before=self.vu_row)

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
            self.root.after_idle(lambda: self.analysis_prog_frame.pack_forget())
            return

        def _prog_cb(pct: float, step_msg: str):
            def _ui():
                if hasattr(self, "analysis_prog_bar") and self.analysis_prog_bar.winfo_exists():
                    self.analysis_prog_bar["value"] = int(pct * 100)
                    self.lbl_analysis_step.config(text=f"[{int(pct * 100)}%] {step_msg}")
                self.lbl_status.config(text=f"Analyzing take ({int(pct * 100)}%): {step_msg}")
            self.root.after_idle(_ui)

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
            key_shift=self.current_key_shift,
            progress_cb=_prog_cb
        )

        self.root.after_idle(lambda: self._display_report_card(report))

    def _display_report_card(self, report: TakeAnalysisReport):
        # Hide analysis progress bar
        if hasattr(self, "analysis_prog_frame") and self.analysis_prog_frame.winfo_exists():
            self.analysis_prog_frame.pack_forget()

        self.lbl_score_num.config(text=str(report.overall_score))
        self.tile_pitch._val_lbl.config(text=f"{report.pitch_accuracy_pct}%")
        self.tile_into._val_lbl.config(text=f"{report.intonation_cents:+0.1f}¢")
        self.tile_rhythm._val_lbl.config(text=f"{report.rhythm_offset_ms:+0.1f}ms")
        self.tile_vib._val_lbl.config(text=f"{report.vibrato_rate_hz:0.1f} Hz" if report.vibrato_present else "Straight")
        self.tile_res._val_lbl.config(text=f"{report.singers_formant_ratio:.2f}")
        self.tile_res._sub_lbl.config(text=report.resonance_verdict)
        self.tile_breath._val_lbl.config(text=f"{report.breath_support_pct:.0f}%")
        self.tile_breath._sub_lbl.config(text=report.breath_support_verdict)

        # Update Expandable Acoustic Drawer
        self.lbl_drawer_dyn.config(text=f"{report.energy_steadiness_pct:.0f}% ({report.dynamics_verdict})")
        self.lbl_drawer_sib.config(text=f"{report.sibilance_verdict} (ratio: {report.sibilance_ratio:.2f})")
        self.lbl_drawer_fry.config(text=f"{report.vocal_fry_pct:.0f}% ({report.vocal_fry_verdict})")

        # Save last report for conversational context
        self._last_take_report = report

        # Clear previous feedback items
        for child in self.coach_feedback_container.winfo_children():
            child.destroy()

        avail_wrap = max(260, self.notes_frame.winfo_width() - 60)

        # 1. Add feedback rows
        for note in report.coach_feedback:
            row = tk.Frame(self.coach_feedback_container, bg="#140b20")
            row.pack(fill="x", pady=3, anchor="w")

            bullet_lbl = tk.Label(
                row,
                text="✦",
                font=("Helvetica", 9, "bold"),
                fg=COLOR_CYAN,
                bg="#140b20"
            )
            bullet_lbl.pack(side="left", anchor="nw", padx=(4, 6), pady=(1, 0))

            msg_lbl = tk.Label(
                row,
                text=note,
                font=("Helvetica", 10),
                fg=COLOR_TEXT_MAIN,
                bg="#140b20",
                anchor="w",
                justify="left",
                wraplength=avail_wrap
            )
            msg_lbl.pack(side="left", fill="x", expand=True)

        # 2. Add Timestamped Phrase Highlights Card
        if report.phrase_highlights:
            hl_box = tk.Frame(self.coach_feedback_container, bg="#0e2320", bd=1, relief="solid", padx=10, pady=8)
            hl_box.pack(fill="x", pady=(8, 4), anchor="w")

            tk.Label(
                hl_box,
                text="🌟 Song Peak Highlights (Timestamped):",
                font=("Helvetica", 9, "bold"),
                fg=COLOR_LIME,
                bg="#0e2320"
            ).pack(anchor="w")

            for h in report.phrase_highlights:
                h_row = tk.Frame(hl_box, bg="#0e2320")
                h_row.pack(fill="x", pady=(4, 2), anchor="w")

                tk.Label(
                    h_row,
                    text=f"• [{h['timestamp']}] {h['note_span']}:",
                    font=("Helvetica", 8, "bold"),
                    fg=COLOR_CYAN,
                    bg="#0e2320"
                ).pack(anchor="w")

                tk.Label(
                    h_row,
                    text=h['detail'],
                    font=("Helvetica", 8),
                    fg=COLOR_TEXT_MAIN,
                    bg="#0e2320",
                    wraplength=avail_wrap - 30,
                    justify="left"
                ).pack(anchor="w", padx=(10, 0))

        # 3. Add Timestamped Focus / Struggle Card
        if report.phrase_struggles:
            st_box = tk.Frame(self.coach_feedback_container, bg="#271120", bd=1, relief="solid", padx=10, pady=8)
            st_box.pack(fill="x", pady=(4, 4), anchor="w")

            tk.Label(
                st_box,
                text="⚠️ Focus & Struggle Moments (Timestamped):",
                font=("Helvetica", 9, "bold"),
                fg="#ff70a6",
                bg="#271120"
            ).pack(anchor="w")

            for s in report.phrase_struggles:
                s_row = tk.Frame(st_box, bg="#271120")
                s_row.pack(fill="x", pady=(4, 2), anchor="w")

                tk.Label(
                    s_row,
                    text=f"• [{s['timestamp']}] {s['note_span']}:",
                    font=("Helvetica", 8, "bold"),
                    fg=COLOR_GOLD,
                    bg="#271120"
                ).pack(anchor="w")

                s_body = s['detail']
                if s.get("cue"):
                    s_body += f"\n👉 Cue: {s['cue']}"

                tk.Label(
                    s_row,
                    text=s_body,
                    font=("Helvetica", 8),
                    fg=COLOR_TEXT_MAIN,
                    bg="#271120",
                    wraplength=avail_wrap - 30,
                    justify="left"
                ).pack(anchor="w", padx=(10, 0))

        # 4. Add Smart Key recommendation card if present
        if report.key_recommendation_reason:
            smart_box = tk.Frame(self.coach_feedback_container, bg="#201133", bd=1, relief="solid", padx=10, pady=8)
            smart_box.pack(fill="x", pady=(8, 4), anchor="w")

            title_row = tk.Frame(smart_box, bg="#201133")
            title_row.pack(fill="x")

            tk.Label(
                title_row,
                text="🎹 Smart Key Recommendation:",
                font=("Helvetica", 9, "bold"),
                fg=COLOR_GOLD,
                bg="#201133"
            ).pack(side="left")

            body_lbl = tk.Label(
                smart_box,
                text=report.key_recommendation_reason,
                font=("Helvetica", 9),
                fg=COLOR_TEXT_MAIN,
                bg="#201133",
                anchor="w",
                justify="left",
                wraplength=avail_wrap - 24
            )
            body_lbl.pack(fill="x", pady=(4, 0))

        # 5. Automatically trigger AI Take Debriefing
        self._trigger_ai_take_debrief(report)

        self.root.update_idletasks()
        if hasattr(self, "report_canvas"):
            self.report_canvas.configure(scrollregion=self.report_canvas.bbox("all"))

        self.lbl_status.config(text="Performance analysis complete. Great work!")

    def _on_toggle_drawer(self):
        if not self._drawer_expanded:
            self.drawer_subcards.pack(fill="x", pady=(4, 0))
            self.btn_toggle_drawer.config(text="▼ Acoustic Timbre & Dynamics Breakdown (Click to Collapse)")
            self._drawer_expanded = True
        else:
            self.drawer_subcards.pack_forget()
            self.btn_toggle_drawer.config(text="▶ Acoustic Timbre & Dynamics Breakdown (Click to Expand)")
            self._drawer_expanded = False

        self.root.update_idletasks()
        if hasattr(self, "report_canvas"):
            self.report_canvas.configure(scrollregion=self.report_canvas.bbox("all"))

    def _refresh_ai_coach_view(self):
        installed = self.offline_coach.get_installed_model()
        if installed:
            self.ai_install_card.pack_forget()
            self.ai_debrief_card.pack(fill="x", pady=(8, 0))
            self.conversation_card.pack(fill="x", pady=(8, 0))
        else:
            self.ai_debrief_card.pack_forget()
            self.conversation_card.pack_forget()
            self.ai_install_card.pack(fill="x", pady=(8, 0))

        self.root.update_idletasks()
        if hasattr(self, "report_canvas"):
            self.report_canvas.configure(scrollregion=self.report_canvas.bbox("all"))

    def _on_download_ai_model(self):
        if self._is_downloading_model:
            return

        choice = self.model_choice_var.get()
        meta = AVAILABLE_MODELS.get(choice, AVAILABLE_MODELS["3b"])
        self._is_downloading_model = True
        self.btn_install_ai.config(state="disabled", text="⏳ Downloading Model...")
        self.prog_ai.pack(fill="x", pady=(8, 4))
        self.prog_ai["value"] = 0
        self.lbl_ai_install_status.config(text=f"Connecting to Hugging Face ({meta['size_str']})...")

        def _progress(pct, msg):
            def _ui():
                self.prog_ai["value"] = int(pct * 100)
                self.lbl_ai_install_status.config(text=msg)
            self.root.after_idle(_ui)

        def _worker():
            success = self.offline_coach.download_model(choice, progress_cb=_progress)
            def _done():
                self._is_downloading_model = False
                self.btn_install_ai.config(state="normal", text="📥 Download & Install Offline AI Coach")
                if success:
                    messagebox.showinfo("Installation Complete", f"Offline AI Coach ({meta['name']}) installed successfully!\n\nAll AI coaching is now 100% offline.")
                    self._refresh_ai_coach_view()
                    if self._last_take_report:
                        self._trigger_ai_take_debrief(self._last_take_report)
                else:
                    messagebox.showwarning("Download Notice", "Could not complete model download. Please check your internet connection and try again.")
            self.root.after_idle(_done)

        threading.Thread(target=_worker, daemon=True).start()

    def _trigger_ai_take_debrief(self, report: TakeAnalysisReport):
        self._refresh_ai_coach_view()
        if not self.offline_coach.is_installed():
            return

        self.lbl_ai_debrief_text.config(text="✦ Maestro Tiki is analyzing your take metrics offline...", fg=COLOR_CYAN)

        def _worker():
            profile_dict = self.vocal_profile.to_dict() if self.vocal_profile else None
            debrief = self.offline_coach.generate_take_debrief(
                report.to_dict(),
                self.selected_track,
                profile_dict
            )
            def _update():
                self.lbl_ai_debrief_text.config(text=debrief, fg=COLOR_TEXT_MAIN)
            self.root.after_idle(_update)

        threading.Thread(target=_worker, daemon=True).start()

    def _send_coach_prompt(self, msg: str):
        self.ent_chat_input.delete(0, tk.END)
        self.ent_chat_input.insert(0, msg)
        self._on_submit_chat()

    def _on_submit_chat(self):
        msg = self.ent_chat_input.get().strip()
        if not msg or self._is_ai_generating:
            return

        self.ent_chat_input.delete(0, tk.END)
        self._is_ai_generating = True
        self.btn_send_chat.config(state="disabled")

        if hasattr(self, "lbl_chat_intro") and self.lbl_chat_intro.winfo_exists():
            self.lbl_chat_intro.destroy()

        user_row = tk.Frame(self.chat_transcript_container, bg="#0d0615")
        user_row.pack(fill="x", pady=2, anchor="w")
        tk.Label(user_row, text="👤 You:", font=("Helvetica", 8, "bold"), fg=COLOR_CYAN, bg="#0d0615").pack(side="left", anchor="nw")
        tk.Label(user_row, text=msg, font=("Helvetica", 8), fg=COLOR_TEXT_MAIN, bg="#0d0615", wraplength=380, justify="left").pack(side="left", padx=4)

        coach_row = tk.Frame(self.chat_transcript_container, bg="#0d0615")
        coach_row.pack(fill="x", pady=2, anchor="w")
        tk.Label(coach_row, text="🌺 Coach Tiki:", font=("Helvetica", 8, "bold"), fg=COLOR_GOLD, bg="#0d0615").pack(side="left", anchor="nw")
        coach_lbl = tk.Label(coach_row, text="Thinking...", font=("Helvetica", 8, "italic"), fg=COLOR_TEXT_MUTED, bg="#0d0615", wraplength=380, justify="left")
        coach_lbl.pack(side="left", padx=4)

        self.chat_history.append({"role": "user", "content": msg})

        def _worker():
            rep_dict = self._last_take_report.to_dict() if self._last_take_report else None
            reply = self.offline_coach.chat_with_coach(
                msg,
                self.chat_history,
                rep_dict,
                self.selected_track
            )
            self.chat_history.append({"role": "assistant", "content": reply})

            def _update():
                coach_lbl.config(text=reply, font=("Helvetica", 8), fg=COLOR_TEXT_MAIN)
                self._is_ai_generating = False
                self.btn_send_chat.config(state="normal")
            self.root.after_idle(_update)

        threading.Thread(target=_worker, daemon=True).start()

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
                # Immediately update profile and persist to JSON
                self.vocal_profile = self.calibrator.update_siren_sweep(res)
                self.analyzer.update_profile(self.vocal_profile)
                self.root.after_idle(self._refresh_profile_display)

                msg = (
                    f"Siren Sweep Analyzed & Saved:\n\n"
                    f"• Vocal Floor: {res['min_note']} ({res['min_hz']:.1f} Hz)\n"
                    f"• Vocal Ceiling: {res['max_note']} ({res['max_hz']:.1f} Hz)\n"
                    f"• Total Span: {res['range_semitones']:.1f} semitones ({res['octaves']} octaves)\n\n"
                    f"Profile updated and saved to vocal_profile.json!"
                )
                self.root.after_idle(lambda: messagebox.showinfo("Exercise 1 Complete", msg))
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
                # Immediately update profile and persist to JSON
                self.vocal_profile = self.calibrator.update_speaking_anchor(res)
                self.analyzer.update_profile(self.vocal_profile)
                self.root.after_idle(self._refresh_profile_display)

                msg = (
                    f"Speaking Anchor Established & Saved:\n\n"
                    f"• Speaking Pitch Anchor: {res['anchor_note']} ({res['anchor_hz']:.1f} Hz)\n"
                    f"• Power Tessitura Sweet Spot: {res['tessitura_min_note']} to {res['tessitura_max_note']}\n"
                    f"• Voice Category: {self.vocal_profile.vocal_type}\n\n"
                    f"Profile updated and saved to vocal_profile.json!"
                )
                self.root.after_idle(lambda: messagebox.showinfo("Exercise 2 Complete", msg))
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
        p = self.vocal_profile or self.calibrator.load_profile()
        if not p or (p.anchor_hz <= 0 and p.range_max_hz <= 0):
            self.lbl_prof_voice_type.config(text="Voice Classification: Not Calibrated")
            self.lbl_prof_details.config(text="Speaking Anchor: --  •  Vocal Floor: --  •  Vocal Ceiling: --")
            self.lbl_prof_tessitura.config(text="Power Tessitura Sweet Spot: -- to --")
            return

        self.vocal_profile = p
        self.lbl_prof_voice_type.config(text=f"Voice Classification: {p.vocal_type}")

        anch_str = f"{p.anchor_note} ({p.anchor_hz:.1f} Hz)" if p.anchor_hz > 0 else "Pending Step 2"
        if p.range_max_hz > 0:
            range_str = f"{p.range_min_note} to {p.range_max_note} ({p.range_semitones:.1f} st)"
        else:
            range_str = "Pending Step 1"

        self.lbl_prof_details.config(
            text=f"Speaking Anchor: {anch_str}  •  Range: {range_str}"
        )

        if p.tessitura_max_hz > 0:
            tess_str = f"{p.tessitura_min_note} to {p.tessitura_max_note}"
        else:
            tess_str = "-- to -- (Pending Step 2)"
        self.lbl_prof_tessitura.config(text=f"Power Tessitura Sweet Spot: {tess_str}")

    def _on_close(self):
        self.player.stop()
        self.recorder.stop_recording()
        self.root.destroy()

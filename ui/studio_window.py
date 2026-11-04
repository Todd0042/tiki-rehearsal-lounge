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
import queue
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
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
    TIKI_RECORDINGS_DIR,
    TIKI_VIDEO_QUALITY_PROFILE,
    TIKI_TARGET_LUFS,
    save_studio_config,
    load_studio_config,
    STUDIO_CONFIG,
    STORAGE_MODE,
    find_tiki_karaoke_config_path
)
from db.bridge import TikiDatabaseBridge
from db.progression_db import ProgressionDatabase
from audio.recorder import AudioRecorder
from audio.calibration import (
    VocalCalibrationManager,
    VocalProfile,
    hz_to_midi,
    midi_to_note_name
)
from audio.player import RehearsalPlayer
from audio.vocal_analyzer import VocalTakeAnalyzer, TakeAnalysisReport
from audio.parallel_analyzer import ParallelVocalTakeAnalyzer
from audio.lyric_manager import LyricManager, LyricTrack, LyricAlignmentReport
from storage.take_partitions import (
    save_take_partitions,
    load_take_toc,
    get_most_recent_take_dir,
    load_take_report_and_lyrics
)
from audio.tone_generator import (
    tone_synth,
    parse_target_notes,
    get_sorted_instruments,
    evaluate_warmup_take,
    INSTRUMENT_DEFS,
    transpose_note,
    clamp_note_to_range,
    note_name_to_midi
)
from downloader import TrackDownloader, relocate_and_rename_track_file
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

        # Set application icon
        try:
            icon_png = Path(__file__).resolve().parent.parent / "static" / "tiki-icon.png"
            if icon_png.exists():
                self._app_icon = tk.PhotoImage(file=str(icon_png))
                self.root.iconphoto(True, self._app_icon)
        except Exception:
            pass

        # Core Engines
        self.db = TikiDatabaseBridge()
        self.progression_db = ProgressionDatabase()
        self.calibrator = VocalCalibrationManager()
        self.vocal_profile: Optional[VocalProfile] = self.calibrator.load_profile()
        self.analyzer = VocalTakeAnalyzer(vocal_profile=self.vocal_profile)
        self.parallel_analyzer = ParallelVocalTakeAnalyzer(vocal_profile=self.vocal_profile)
        self.lyric_manager = LyricManager()
        self._current_lyric_track: Optional[LyricTrack] = None
        self._last_lyric_alignment: Optional[LyricAlignmentReport] = None
        self._last_take_dir: Optional[Path] = None
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

        # Library Filter & Search State
        self._library_filter: str = "ready"
        self._filter_buttons: Dict[str, tk.Button] = {}
        self._yt_current_page: int = 1
        self._yt_seen_ids: Set[str] = set()
        self._yt_current_query: str = ""

        # AI Vocal Warmup Studio & Reference Tone State
        self._warmup_routine: List[Dict[str, Any]] = []
        self._warmup_step_idx: int = 0
        self._warmup_timer_seconds: int = 35
        self._warmup_timer_running: bool = False
        self._warmup_timer_job: Optional[str] = None
        self._is_generating_warmup: bool = False
        self._selected_instrument: str = "piano"
        self._instrument_menu_map: Dict[str, str] = {}
        self._is_recording_warmup: bool = False
        self._active_target_notes: List[str] = []
        self._is_playing_pattern: bool = False
        self._warmup_chip_widgets: List[tk.Widget] = []

        # Session State
        self.selected_track: Optional[Dict[str, Any]] = None
        self._song_peaks: Optional[Dict[str, Any]] = None
        self._recommended_key_shift: int = 0
        self.current_key_shift: int = 0
        self._yt_search_results: Dict[str, Dict[str, Any]] = {}
        self._downloading_tracks: set[str] = set()
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

        # Thread-safe UI dispatch queue (Python 3.14 safe)
        self._ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self.root.after(20, self._poll_ui_queue)

        # Handle clean close
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Automatically reload and display the most recent rehearsal take on startup
        self.root.after(80, self._load_most_recent_take_on_startup)

    def dispatch_to_main(self, fn: Callable[[], None]):
        """Thread-safe dispatcher for executing UI updates on the Tkinter main thread."""
        self._ui_queue.put(fn)

    def _poll_ui_queue(self):
        try:
            while not self._ui_queue.empty():
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception as e:
                    print(f"[UI Queue] Callback error: {e}", file=sys.stderr)
        finally:
            if hasattr(self, "root") and self.root and self.root.winfo_exists():
                self.root.after(20, self._poll_ui_queue)

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

        # Option B: Vibrant Synthwave Neon Scrollbar (Hot Magenta thumb on dark violet trough)
        style.configure(
            "Vertical.TScrollbar",
            gripcount=0,
            background="#ff2a85",          # Hot magenta thumb
            darkcolor="#ff2a85",
            lightcolor="#ff5c9f",
            troughcolor="#12081c",        # Dark tropical violet trough
            bordercolor="#12081c",
            arrowcolor="#00f5d4",         # Cyan arrows
            arrowsize=11
        )
        style.map(
            "Vertical.TScrollbar",
            background=[("active", "#ff5c9f"), ("pressed", "#d61c6b"), ("disabled", "#33162b")],
            arrowcolor=[("active", "#ffffff")]
        )

        style.configure(
            "Horizontal.TScrollbar",
            gripcount=0,
            background="#ff2a85",
            darkcolor="#ff2a85",
            lightcolor="#ff5c9f",
            troughcolor="#12081c",
            bordercolor="#12081c",
            arrowcolor="#00f5d4",
            arrowsize=11
        )
        style.map(
            "Horizontal.TScrollbar",
            background=[("active", "#ff5c9f"), ("pressed", "#d61c6b")]
        )

        # Themed Song Library Treeview Table
        style.configure(
            "Treeview",
            background="#150a22",
            foreground="#ffffff",
            fieldbackground="#150a22",
            rowheight=28,
            font=("Helvetica", 9),
            borderwidth=0
        )
        style.map(
            "Treeview",
            background=[("selected", "#00f5d4")],
            foreground=[("selected", "#000000")]
        )
        style.configure(
            "Treeview.Heading",
            background="#25123a",
            foreground="#00f5d4",
            font=("Helvetica", 9, "bold"),
            relief="flat",
            padding=[8, 6]
        )
        style.map(
            "Treeview.Heading",
            background=[("active", "#381c56")],
            foreground=[("active", "#ffffff")]
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

        # Settings Button
        self.btn_settings = tk.Button(
            title_row,
            text="⚙️ Settings",
            font=("Helvetica", 9, "bold"),
            bg="#231238",
            fg=COLOR_CYAN,
            activebackground="#3b1b59",
            activeforeground="#ffffff",
            bd=1,
            relief="solid",
            padx=10,
            pady=3,
            cursor="hand2",
            command=self._open_settings_dialog
        )
        self.btn_settings.pack(side="right", padx=(8, 0), pady=(2, 0))

        # Persistent AI Coach Manager & Uninstaller Button
        self.btn_header_ai = tk.Button(
            title_row,
            text="🤖 AI Coach: Manage / Uninstall",
            font=("Helvetica", 9, "bold"),
            bg="#301542",
            fg=COLOR_GOLD,
            activebackground="#4c1e68",
            activeforeground="#ffffff",
            bd=1,
            relief="solid",
            padx=10,
            pady=3,
            cursor="hand2",
            command=self._open_manage_models_dialog
        )
        self.btn_header_ai.pack(side="right", padx=(8, 0), pady=(2, 0))

        # Database Status Badge
        mode_text = "● Tiki Synced" if getattr(self, "_storage_mode", STORAGE_MODE) == "integrated" else "● Standalone"
        self.db_status_lbl = tk.Label(
            title_row,
            text=mode_text,
            font=("Helvetica", 9, "bold"),
            fg=COLOR_LIME if hasattr(self, "db") and self.db.is_connected() else COLOR_GOLD,
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

        # Tab 3: Vocal Calibration & Warmup
        self.tab_calib = tk.Frame(self.notebook, bg=COLOR_BG_MAIN)
        self.notebook.add(self.tab_calib, text="🎙️ Vocal Calibration & Warmup")
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
        self.btn_record_take.pack(fill="x", pady=(0, 4))

        tk.Label(
            transport_card,
            text="🎧 Tip: Headphones prevent speaker audio bleed for studio-accurate scoring.",
            font=("Helvetica", 7, "italic"),
            fg=COLOR_TEXT_MUTED,
            bg=COLOR_BG_CARD,
            wraplength=320,
            justify="center"
        ).pack(fill="x", pady=(0, 6))

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

        # Auditory Pleasingness / Artistic Appeal Banner (Top-right across from Take Score)
        auditory_banner_frame = tk.Frame(self.score_banner, bg=COLOR_BG_CARD)
        auditory_banner_frame.pack(side="right")

        self.lbl_auditory_num = tk.Label(
            auditory_banner_frame,
            text="--",
            font=("Helvetica", 36, "bold"),
            fg=COLOR_GOLD,
            bg=COLOR_BG_CARD
        )
        self.lbl_auditory_num.pack(side="right")

        auditory_txt_frame = tk.Frame(auditory_banner_frame, bg=COLOR_BG_CARD)
        auditory_txt_frame.pack(side="right", padx=14)

        self.lbl_auditory_title = tk.Label(
            auditory_txt_frame,
            text="Auditory Appeal",
            font=("Helvetica", 14, "bold"),
            fg=COLOR_TEXT_MAIN,
            bg=COLOR_BG_CARD
        )
        self.lbl_auditory_title.pack(anchor="e")

        self.lbl_auditory_sub = tk.Label(
            auditory_txt_frame,
            text="Harmonic musicality & listener appeal",
            font=("Helvetica", 9),
            fg=COLOR_TEXT_MUTED,
            bg=COLOR_BG_CARD
        )
        self.lbl_auditory_sub.pack(anchor="e")

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

        row_scoop = tk.Frame(self.drawer_subcards, bg="#130a1c")
        row_scoop.pack(fill="x", pady=2)
        tk.Label(row_scoop, text="🎯 Pitch Attack Velocity & Scooping:", font=("Helvetica", 8, "bold"), fg=COLOR_CYAN, bg="#130a1c").pack(side="left")
        self.lbl_drawer_scoop = tk.Label(row_scoop, text="-- (Standby)", font=("Helvetica", 8), fg=COLOR_TEXT_MAIN, bg="#130a1c")
        self.lbl_drawer_scoop.pack(side="left", padx=8)

        row_arc = tk.Frame(self.drawer_subcards, bg="#130a1c")
        row_arc.pack(fill="x", pady=2)
        tk.Label(row_arc, text="🎭 Dynamic Story Arc & Contrast:", font=("Helvetica", 8, "bold"), fg=COLOR_GOLD, bg="#130a1c").pack(side="left")
        self.lbl_drawer_arc = tk.Label(row_arc, text="-- (Standby)", font=("Helvetica", 8), fg=COLOR_TEXT_MAIN, bg="#130a1c")
        self.lbl_drawer_arc.pack(side="left", padx=8)

        row_bloom = tk.Frame(self.drawer_subcards, bg="#130a1c")
        row_bloom.pack(fill="x", pady=2)
        tk.Label(row_bloom, text="🌸 Vibrato Bloom Style:", font=("Helvetica", 8, "bold"), fg=COLOR_MAGENTA, bg="#130a1c").pack(side="left")
        self.lbl_drawer_bloom = tk.Label(row_bloom, text="-- (Standby)", font=("Helvetica", 8), fg=COLOR_TEXT_MAIN, bg="#130a1c")
        self.lbl_drawer_bloom.pack(side="left", padx=8)

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

        self.model_choice_var = tk.StringVar(value="7b")
        opt_frame = tk.Frame(self.ai_install_card, bg="#1d0f2b")
        opt_frame.pack(fill="x", pady=(0, 8))

        rb0 = tk.Radiobutton(
            opt_frame,
            text="Qwen 2.5 7B-Instruct (~4.68 GB) — Master-Class Studio Director (6GB+ GPU / 8-Core CPU)",
            variable=self.model_choice_var,
            value="7b",
            fg=COLOR_LIME,
            bg="#1d0f2b",
            selectcolor="#0d0814",
            activebackground="#1d0f2b",
            activeforeground=COLOR_LIME,
            font=("Helvetica", 9, "bold")
        )
        rb0.pack(anchor="w")

        rb1 = tk.Radiobutton(
            opt_frame,
            text="Qwen 2.5 3B-Instruct (~1.95 GB) — Deep Pedagogy (Recommended for mid-range GPUs/CPUs)",
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
            text="Qwen 2.5 1.5B-Instruct (~986 MB) — Ultra-Fast & Compact (Sub-1 GB, fast on light CPUs)",
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

        accel_frame = tk.Frame(self.ai_install_card, bg="#1d0f2b")
        accel_frame.pack(fill="x", pady=(0, 10))

        tk.Label(
            accel_frame,
            text="Inference Acceleration:",
            font=("Helvetica", 9, "bold"),
            fg=COLOR_GOLD,
            bg="#1d0f2b"
        ).pack(side="left", padx=(0, 10))

        self.accel_choice_var = tk.StringVar(value=self.offline_coach.get_acceleration_mode())

        rb_gpu = tk.Radiobutton(
            accel_frame,
            text="⚡ GPU (VRAM Offload - Recommended for Dedicated GPUs)",
            variable=self.accel_choice_var,
            value="gpu",
            fg=COLOR_CYAN,
            bg="#1d0f2b",
            selectcolor="#0d0814",
            activebackground="#1d0f2b",
            activeforeground=COLOR_CYAN,
            font=("Helvetica", 8, "bold"),
            command=lambda: self.offline_coach.set_acceleration_mode(self.accel_choice_var.get())
        )
        rb_gpu.pack(side="left", padx=(0, 12))

        rb_cpu = tk.Radiobutton(
            accel_frame,
            text="🖥️ CPU Only (Universal Compatibility)",
            variable=self.accel_choice_var,
            value="cpu",
            fg=COLOR_TEXT_MAIN,
            bg="#1d0f2b",
            selectcolor="#0d0814",
            activebackground="#1d0f2b",
            activeforeground=COLOR_TEXT_MAIN,
            font=("Helvetica", 8),
            command=lambda: self.offline_coach.set_acceleration_mode(self.accel_choice_var.get())
        )
        rb_cpu.pack(side="left")

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

        debrief_header = tk.Frame(self.ai_debrief_card, bg="#1c0e2c")
        debrief_header.pack(fill="x", pady=(0, 6))

        self.lbl_ai_model_badge = tk.Label(
            debrief_header,
            text="⚡ Model: Offline CPU",
            font=("Helvetica", 8, "italic"),
            fg=COLOR_CYAN,
            bg="#1c0e2c"
        )
        self.lbl_ai_model_badge.pack(side="left")

        btn_manage_ai = tk.Button(
            debrief_header,
            text="🗑️ Uninstall / Manage AI Model",
            font=("Helvetica", 8, "bold"),
            bg="#3b1548",
            fg=COLOR_GOLD,
            activebackground="#5a2070",
            activeforeground="#ffffff",
            bd=1,
            relief="solid",
            padx=10,
            pady=3,
            command=self._open_manage_models_dialog
        )
        btn_manage_ai.pack(side="right")

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
            text="⏱️ Timing & Rhythm",
            font=("Helvetica", 7, "bold"),
            bg="#2a143f",
            fg=COLOR_CYAN,
            bd=0,
            padx=6,
            pady=3,
            command=lambda: self._send_coach_prompt("How was my line onset timing and rhythm? Did I start any sentences late or drag?")
        )
        btn_p2.pack(side="left", padx=4)

        btn_p3 = tk.Button(
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
        btn_p3.pack(side="left", padx=4)

        btn_p4 = tk.Button(
            pills_row,
            text="✨ Resonance Ring",
            font=("Helvetica", 7, "bold"),
            bg="#2a143f",
            fg=COLOR_LIME,
            bd=0,
            padx=6,
            pady=3,
            command=lambda: self._send_coach_prompt("How can I get more singer's formant ring without throat strain?")
        )
        btn_p4.pack(side="left", padx=4)

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
        # Search Bar
        search_bar = tk.Frame(parent, bg=COLOR_BG_CARD, padx=12, pady=10)
        search_bar.pack(fill="x", padx=12, pady=(12, 4))

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
        self.ent_search.bind("<Return>", lambda e: self._on_search_unified())

        btn_search = tk.Button(
            search_bar,
            text="🔍 Search Library",
            bg=COLOR_BG_CARD_HOVER,
            fg=COLOR_TEXT_MAIN,
            font=("Helvetica", 9, "bold"),
            bd=0,
            padx=12,
            pady=4,
            command=self._on_click_search_library
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

        # Quick Filter Chips Bar with Video Quality Selector
        filter_bar = tk.Frame(parent, bg=COLOR_BG_MAIN, padx=12, pady=4)
        filter_bar.pack(fill="x", pady=(2, 6))

        tk.Label(
            filter_bar,
            text="FILTER BY:",
            font=("Helvetica", 8, "bold"),
            fg=COLOR_TEXT_MUTED,
            bg=COLOR_BG_MAIN
        ).pack(side="left", padx=(0, 6))

        filters = [
            ("all", "🌴 All Tracks"),
            ("ready", "✅ Offline Ready"),
            ("singalong", "🎙️ Sing-Along Guides"),
            ("karaoke", "🎵 Karaoke Stems"),
        ]
        self._filter_buttons = {}
        for f_key, f_label in filters:
            btn = tk.Button(
                filter_bar,
                text=f_label,
                font=("Helvetica", 8, "bold"),
                bg="#2a1240" if self._library_filter == f_key else "#150a22",
                fg=COLOR_CYAN if self._library_filter == f_key else COLOR_TEXT_MUTED,
                activebackground="#3b1b59",
                activeforeground=COLOR_CYAN,
                bd=1,
                relief="solid",
                padx=10,
                pady=3,
                command=lambda k=f_key: self._set_library_filter(k)
            )
            btn.pack(side="left", padx=3)
            self._filter_buttons[f_key] = btn

        # Video Quality Profile Selector (Defaulted from Tiki Karaoke config)
        q_frame = tk.Frame(filter_bar, bg=COLOR_BG_MAIN)
        q_frame.pack(side="right")

        tk.Label(
            q_frame,
            text="QUALITY:",
            font=("Helvetica", 8, "bold"),
            fg=COLOR_GOLD,
            bg=COLOR_BG_MAIN
        ).pack(side="left", padx=(0, 4))

        self.quality_var = tk.StringVar()
        cfg_q = TIKI_VIDEO_QUALITY_PROFILE.lower()
        if "720" in cfg_q:
            default_q = "Balanced 720p HD"
        elif "480" in cfg_q or "sd" in cfg_q or "compact" in cfg_q:
            default_q = "Compact 480p SD"
        else:
            default_q = "Pro 1080p Full HD (Tiki Config)"

        self.quality_var.set(default_q)
        self.cbo_quality = ttk.Combobox(
            q_frame,
            textvariable=self.quality_var,
            values=[
                "Pro 1080p Full HD (Tiki Config)",
                "Balanced 720p HD",
                "Compact 480p SD"
            ],
            state="readonly",
            width=26
        )
        self.cbo_quality.pack(side="left")

        # Results Table
        table_frame = tk.Frame(parent, bg=COLOR_BG_MAIN)
        table_frame.pack(fill="both", expand=True, padx=12, pady=4)

        columns = ("id", "title", "artist", "duration", "type", "downloaded")
        self.tree_tracks = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")

        self.tree_tracks.heading("id", text="ID")
        self.tree_tracks.heading("title", text="Title")
        self.tree_tracks.heading("artist", text="Artist")
        self.tree_tracks.heading("duration", text="Duration")
        self.tree_tracks.heading("type", text="Type")
        self.tree_tracks.heading("downloaded", text="Readiness Status")

        self.tree_tracks.column("id", width=50, anchor="center")
        self.tree_tracks.column("title", width=340)
        self.tree_tracks.column("artist", width=220)
        self.tree_tracks.column("duration", width=80, anchor="center")
        self.tree_tracks.column("type", width=140, anchor="center")
        self.tree_tracks.column("downloaded", width=140, anchor="center")

        # Striped rows styling
        self.tree_tracks.tag_configure("row_odd", background="#150a22", foreground="#ffffff")
        self.tree_tracks.tag_configure("row_even", background="#1c0d2e", foreground="#ffffff")

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree_tracks.yview, style="Vertical.TScrollbar")
        self.tree_tracks.configure(yscrollcommand=scrollbar.set)

        self.tree_tracks.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.tree_tracks.bind("<Double-1>", lambda e: self._on_select_track())

        # Context Menu (Right Click)
        self.track_menu = tk.Menu(self.root, tearoff=0, bg="#1a1025", fg="#ffffff", activebackground="#3a1859", activeforeground=COLOR_CYAN)
        self.track_menu.add_command(label="➔ Load into Rehearsal Cockpit", command=self._on_select_track)
        self.track_menu.add_command(label="✏️ Edit Track Details...", command=self._on_edit_selected_track_metadata)
        self.track_menu.add_command(label="⬇️ Download to Library", command=self._on_download_selected_track)

        def _on_track_right_click(event):
            row_id = self.tree_tracks.identify_row(event.y)
            if row_id:
                self.tree_tracks.selection_set(row_id)
                self.track_menu.tk_popup(event.x_root, event.y_root)

        self.tree_tracks.bind("<Button-3>", _on_track_right_click)

        # Select & Download Button Bar
        btn_bar = tk.Frame(parent, bg=COLOR_BG_MAIN)
        btn_bar.pack(fill="x", padx=12, pady=(4, 12))

        self.btn_find_more = tk.Button(
            btn_bar,
            text="➕ Find More Results (YouTube)",
            bg="#1c0f2b",
            fg=COLOR_GOLD,
            activebackground="#33144d",
            activeforeground=COLOR_GOLD,
            font=("Helvetica", 9, "bold"),
            bd=1,
            relief="solid",
            padx=14,
            pady=8,
            command=self._on_find_more_youtube
        )
        self.btn_find_more.pack(side="left")
        self.btn_find_more.pack_forget()  # Hidden until search produces YouTube items

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

        self.btn_edit_metadata = tk.Button(
            btn_bar,
            text="✏️ Edit Track Details",
            bg="#25123d",
            fg=COLOR_GOLD,
            activebackground="#45186b",
            activeforeground="#ffffff",
            font=("Helvetica", 10, "bold"),
            bd=1,
            relief="solid",
            padx=14,
            pady=8,
            command=self._on_edit_selected_track_metadata
        )
        self.btn_edit_metadata.pack(side="right", padx=(0, 8))

        self.btn_download_track = tk.Button(
            btn_bar,
            text="⬇️ Download to Library",
            bg="#2a1240",
            fg=COLOR_MAGENTA,
            activebackground="#45186b",
            activeforeground="#ffffff",
            font=("Helvetica", 10, "bold"),
            bd=1,
            relief="solid",
            padx=14,
            pady=8,
            command=self._on_download_selected_track
        )
        self.btn_download_track.pack(side="right", padx=(0, 8))

        # Initial populate
        self.root.after_idle(self._on_search_library)

    # -------------------------------------------------------------
    # TAB 3: VOCAL CALIBRATION & WARMUP
    # -------------------------------------------------------------
    def _build_calibration_tab(self, parent: tk.Frame):
        self.calib_canvas = tk.Canvas(parent, bg=COLOR_BG_MAIN, bd=0, highlightthickness=0)
        self.calib_scrollbar = ttk.Scrollbar(parent, orient="vertical", command=self.calib_canvas.yview, style="Vertical.TScrollbar")
        container = tk.Frame(self.calib_canvas, bg=COLOR_BG_MAIN, padx=20, pady=16)

        canvas_win = self.calib_canvas.create_window((0, 0), window=container, anchor="nw")

        def _on_calib_canvas_configure(e):
            self.calib_canvas.itemconfig(canvas_win, width=e.width)

        def _on_calib_content_configure(event):
            self.calib_canvas.configure(scrollregion=self.calib_canvas.bbox("all"))
            wrap = max(380, event.width - 80)
            if hasattr(self, "lbl_warmup_song_context") and self.lbl_warmup_song_context.winfo_exists():
                self.lbl_warmup_song_context.config(wraplength=wrap)
            if hasattr(self, "lbl_warmup_purpose") and self.lbl_warmup_purpose.winfo_exists():
                self.lbl_warmup_purpose.config(wraplength=wrap)
            if hasattr(self, "lbl_warmup_mechanics") and self.lbl_warmup_mechanics.winfo_exists():
                self.lbl_warmup_mechanics.config(wraplength=wrap)
            if hasattr(self, "lbl_eval_diagnosis") and self.lbl_eval_diagnosis.winfo_exists():
                self.lbl_eval_diagnosis.config(wraplength=wrap)

        container.bind("<Configure>", _on_calib_content_configure)
        self.calib_canvas.bind("<Configure>", _on_calib_canvas_configure)
        self.calib_canvas.configure(yscrollcommand=self.calib_scrollbar.set)

        def _on_calib_mousewheel(event):
            if not hasattr(self, "calib_canvas") or not self.calib_canvas.winfo_exists():
                return
            if event.num == 4 or (hasattr(event, "delta") and event.delta > 0):
                self.calib_canvas.yview_scroll(-2, "units")
            elif event.num == 5 or (hasattr(event, "delta") and event.delta < 0):
                self.calib_canvas.yview_scroll(2, "units")

        self.calib_canvas.bind("<Enter>", lambda e: self.calib_canvas.bind_all("<Button-4>", _on_calib_mousewheel) or self.calib_canvas.bind_all("<Button-5>", _on_calib_mousewheel) or self.calib_canvas.bind_all("<MouseWheel>", _on_calib_mousewheel))
        self.calib_canvas.bind("<Leave>", lambda e: self.calib_canvas.unbind_all("<Button-4>") or self.calib_canvas.unbind_all("<Button-5>") or self.calib_canvas.unbind_all("<MouseWheel>"))

        self.calib_canvas.pack(side="left", fill="both", expand=True)
        self.calib_scrollbar.pack(side="right", fill="y")

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

        # -------------------------------------------------------------
        # AI VOCAL WARMUP STUDIO (MAESTRO TIKI)
        # -------------------------------------------------------------
        warmup_card = tk.LabelFrame(
            container,
            text=" 🌺 AI VOCAL WARMUP STUDIO (MAESTRO TIKI) ",
            bg=COLOR_BG_CARD,
            fg=COLOR_MAGENTA,
            font=("Helvetica", 11, "bold"),
            bd=1,
            relief="solid",
            padx=16,
            pady=16
        )
        warmup_card.pack(fill="x", pady=(16, 20))

        warmup_top_row = tk.Frame(warmup_card, bg=COLOR_BG_CARD)
        warmup_top_row.pack(fill="x", pady=(0, 8))

        tk.Label(
            warmup_top_row,
            text="Physiological warmups tailored to your vocal cords, power sweet spot, and selected song climax notes. Powered 100% offline.",
            font=("Helvetica", 9),
            fg=COLOR_TEXT_MUTED,
            bg=COLOR_BG_CARD
        ).pack(side="left", anchor="w")

        btn_warmup_ai_mgr = tk.Button(
            warmup_top_row,
            text="⚙️ Manage / Uninstall AI Model",
            font=("Helvetica", 8, "bold"),
            bg="#2e153f",
            fg=COLOR_GOLD,
            activebackground="#4a1e68",
            activeforeground="#ffffff",
            bd=1,
            relief="solid",
            padx=10,
            pady=3,
            command=self._open_manage_models_dialog
        )
        btn_warmup_ai_mgr.pack(side="right")

        # Dynamic Context Badges Bar
        ctx_bar = tk.Frame(warmup_card, bg="#13091e", bd=1, relief="solid", padx=10, pady=8)
        ctx_bar.pack(fill="x", pady=(0, 12))

        self.lbl_warmup_voice_badge = tk.Label(
            ctx_bar,
            text="👤 Voice: Calibrated Profile",
            font=("Helvetica", 8, "bold"),
            fg=COLOR_CYAN,
            bg="#13091e"
        )
        self.lbl_warmup_voice_badge.pack(side="left", padx=(0, 12))

        self.lbl_warmup_song_badge = tk.Label(
            ctx_bar,
            text="🎵 Track: No Track Loaded",
            font=("Helvetica", 8, "bold"),
            fg=COLOR_GOLD,
            bg="#13091e"
        )
        self.lbl_warmup_song_badge.pack(side="left", padx=12)

        self.lbl_warmup_key_badge = tk.Label(
            ctx_bar,
            text="🎹 Key Shift: 0 st",
            font=("Helvetica", 8, "bold"),
            fg=COLOR_LIME,
            bg="#13091e"
        )
        self.lbl_warmup_key_badge.pack(side="left", padx=12)

        # Routines & Presets Bar
        routine_bar = tk.Frame(warmup_card, bg=COLOR_BG_CARD)
        routine_bar.pack(fill="x", pady=(0, 12))

        tk.Label(
            routine_bar,
            text="ROUTINES:",
            font=("Helvetica", 8, "bold"),
            fg=COLOR_TEXT_MUTED,
            bg=COLOR_BG_CARD
        ).pack(side="left", padx=(0, 6))

        presets = [
            ("3min", "⚡ 3-Min Wakeup"),
            ("climax", "🚀 Climax Stretch"),
            ("formant", "💎 Formant Ring"),
            ("tension", "🌊 Tension Release")
        ]
        for p_key, p_label in presets:
            tk.Button(
                routine_bar,
                text=p_label,
                font=("Helvetica", 8, "bold"),
                bg="#231038",
                fg=COLOR_TEXT_MAIN,
                activebackground="#3b1b59",
                activeforeground=COLOR_CYAN,
                bd=1,
                relief="solid",
                padx=8,
                pady=4,
                command=lambda k=p_key: self._load_warmup_preset(k)
            ).pack(side="left", padx=3)

        self.btn_gen_warmup = tk.Button(
            routine_bar,
            text="✨ Generate Song-Adaptive Routine (Offline AI)",
            font=("Helvetica", 8, "bold"),
            bg="#ff2a85",
            fg="#ffffff",
            activebackground="#ff5c9f",
            activeforeground="#ffffff",
            bd=0,
            padx=12,
            pady=5,
            command=self._on_generate_ai_warmup
        )
        self.btn_gen_warmup.pack(side="right")

        self.lbl_warmup_status = tk.Label(
            warmup_card,
            text="",
            font=("Helvetica", 8, "italic"),
            fg=COLOR_CYAN,
            bg=COLOR_BG_CARD
        )
        self.lbl_warmup_status.pack(anchor="w", pady=(0, 6))

        # Interactive Exercise Player Card
        player_card = tk.Frame(warmup_card, bg="#10071a", bd=1, relief="solid", padx=16, pady=14)
        player_card.pack(fill="x")

        p_header = tk.Frame(player_card, bg="#10071a")
        p_header.pack(fill="x", pady=(0, 4))

        self.lbl_warmup_step_num = tk.Label(
            p_header,
            text="EXERCISE 1 OF 4",
            font=("Helvetica", 8, "bold"),
            fg=COLOR_CYAN,
            bg="#10071a"
        )
        self.lbl_warmup_step_num.pack(side="left")

        self.lbl_warmup_category = tk.Label(
            p_header,
            text="BREATH & CORE PHONATION",
            font=("Helvetica", 8, "bold"),
            fg=COLOR_MAGENTA,
            bg="#10071a"
        )
        self.lbl_warmup_category.pack(side="right")

        self.lbl_warmup_title = tk.Label(
            player_card,
            text="Diaphragmatic Breath & Lip Trill Glide",
            font=("Helvetica", 14, "bold"),
            fg="#ffffff",
            bg="#10071a"
        )
        self.lbl_warmup_title.pack(anchor="w", pady=(2, 6))

        # Interactive Target Notes & Pattern Player Bar
        self.warmup_notes_frame = tk.Frame(player_card, bg="#10071a")
        self.warmup_notes_frame.pack(fill="x", pady=(2, 6))

        tk.Label(
            self.warmup_notes_frame,
            text="🎶 Target Notes:",
            font=("Helvetica", 9, "bold"),
            fg=COLOR_GOLD,
            bg="#10071a"
        ).pack(side="left", padx=(0, 6))

        # Dynamic Chips Container
        self.warmup_chips_container = tk.Frame(self.warmup_notes_frame, bg="#10071a")
        self.warmup_chips_container.pack(side="left")

        # Sequence Play Button
        self.btn_play_pattern = tk.Button(
            self.warmup_notes_frame,
            text="▶ Play Sequence",
            font=("Helvetica", 8, "bold"),
            bg="#2a143f",
            fg=COLOR_CYAN,
            activebackground="#3f1c5e",
            activeforeground="#ffffff",
            bd=1,
            relief="solid",
            padx=8,
            pady=2,
            command=self._on_play_warmup_pattern
        )
        self.btn_play_pattern.pack(side="left", padx=(10, 8))

        # Instrument Selector Dropdown
        tk.Label(
            self.warmup_notes_frame,
            text="Timbre:",
            font=("Helvetica", 8, "bold"),
            fg=COLOR_TEXT_MUTED,
            bg="#10071a"
        ).pack(side="left", padx=(4, 4))

        self.instrument_var = tk.StringVar(value="🎹 Acoustic Grand Piano")
        self.cbo_instrument = ttk.Combobox(
            self.warmup_notes_frame,
            textvariable=self.instrument_var,
            state="readonly",
            font=("Helvetica", 8),
            width=28
        )
        self.cbo_instrument.pack(side="left", padx=2)
        self.cbo_instrument.bind("<<ComboboxSelected>>", self._on_instrument_selected)

        self.lbl_warmup_song_context = tk.Label(
            player_card,
            text="🎵 Song Connection: Calibrates breath support to sustain clean line ends.",
            font=("Helvetica", 9, "italic"),
            fg="#b0a2cc",
            bg="#10071a",
            wraplength=640,
            justify="left"
        )
        self.lbl_warmup_song_context.pack(anchor="w", pady=(0, 6))

        self.lbl_warmup_purpose = tk.Label(
            player_card,
            text="🎯 Purpose: Awakens vocal cords without friction while establishing subglottic air pressure.",
            font=("Helvetica", 9),
            fg=COLOR_TEXT_MAIN,
            bg="#10071a",
            wraplength=640,
            justify="left"
        )
        self.lbl_warmup_purpose.pack(anchor="w", pady=(0, 4))

        self.lbl_warmup_mechanics = tk.Label(
            player_card,
            text="🛠️ Mechanics: Expand your lower ribcage like an umbrella. Blow relaxed, steady air through loose fluttering lips on a gentle 5-tone descending glide. Zero throat squeeze.",
            font=("Helvetica", 9),
            fg="#e0d6f5",
            bg="#10071a",
            wraplength=640,
            justify="left"
        )
        self.lbl_warmup_mechanics.pack(anchor="w", pady=(0, 10))

        # Controls & Countdown Timer Row
        timer_row = tk.Frame(player_card, bg="#10071a")
        timer_row.pack(fill="x", pady=(6, 2))

        # Big neon countdown timer
        self.lbl_warmup_timer = tk.Label(
            timer_row,
            text="00:35",
            font=("Helvetica", 22, "bold"),
            fg=COLOR_CYAN,
            bg="#10071a"
        )
        self.lbl_warmup_timer.pack(side="left", padx=(0, 14))

        self.btn_warmup_timer_toggle = tk.Button(
            timer_row,
            text="▶ Start Timer",
            font=("Helvetica", 9, "bold"),
            bg=COLOR_CYAN,
            fg="#000000",
            activebackground="#5cf8e2",
            bd=0,
            padx=12,
            pady=5,
            command=self._toggle_warmup_timer
        )
        self.btn_warmup_timer_toggle.pack(side="left", padx=3)

        # Record & Check Warmup Take Button
        self.btn_warmup_record = tk.Button(
            timer_row,
            text="🎙️ Record & Check Take",
            font=("Helvetica", 9, "bold"),
            bg="#ff2a85",
            fg="#ffffff",
            activebackground="#ff5c9f",
            activeforeground="#ffffff",
            bd=0,
            padx=13,
            pady=5,
            command=self._on_toggle_warmup_recording
        )
        self.btn_warmup_record.pack(side="left", padx=5)

        self.btn_warmup_timer_reset = tk.Button(
            timer_row,
            text="↺ Reset",
            font=("Helvetica", 9, "bold"),
            bg="#231038",
            fg=COLOR_TEXT_MAIN,
            activebackground="#3b1b59",
            bd=1,
            relief="solid",
            padx=10,
            pady=4,
            command=self._reset_warmup_timer
        )
        self.btn_warmup_timer_reset.pack(side="left", padx=3)

        self.btn_warmup_next = tk.Button(
            timer_row,
            text="Next Step ➔",
            font=("Helvetica", 9, "bold"),
            bg="#2e144a",
            fg=COLOR_CYAN,
            activebackground="#461e70",
            bd=1,
            relief="solid",
            padx=14,
            pady=4,
            command=self._next_warmup_step
        )
        self.btn_warmup_next.pack(side="right")

        self.btn_warmup_prev = tk.Button(
            timer_row,
            text="⏮️ Prev Step",
            font=("Helvetica", 9, "bold"),
            bg="#231038",
            fg=COLOR_TEXT_MUTED,
            activebackground="#3b1b59",
            bd=1,
            relief="solid",
            padx=12,
            pady=4,
            command=self._prev_warmup_step
        )
        self.btn_warmup_prev.pack(side="right", padx=6)

        # Inline Warmup Pitch Evaluation Drawer (Hidden by default)
        self.warmup_eval_card = tk.LabelFrame(
            warmup_card,
            text=" 📊 WARMUP PITCH EVALUATION & COACH TIPS ",
            bg="#140b22",
            fg=COLOR_CYAN,
            font=("Helvetica", 10, "bold"),
            bd=1,
            relief="solid",
            padx=14,
            pady=12
        )
        # Hidden until take is recorded

        eval_header = tk.Frame(self.warmup_eval_card, bg="#140b22")
        eval_header.pack(fill="x", pady=(0, 6))

        self.lbl_eval_score = tk.Label(
            eval_header,
            text="Target Accuracy: --",
            font=("Helvetica", 11, "bold"),
            fg=COLOR_LIME,
            bg="#140b22"
        )
        self.lbl_eval_score.pack(side="left")

        self.lbl_eval_range = tk.Label(
            eval_header,
            text="Voiced Span: --  •  Pitch Steadiness: --",
            font=("Helvetica", 9),
            fg=COLOR_CYAN,
            bg="#140b22"
        )
        self.lbl_eval_range.pack(side="right")

        self.eval_chips_frame = tk.Frame(self.warmup_eval_card, bg="#140b22")
        self.eval_chips_frame.pack(fill="x", pady=(4, 6))

        self.lbl_eval_diagnosis = tk.Label(
            self.warmup_eval_card,
            text="",
            font=("Helvetica", 9),
            fg="#e0d6f5",
            bg="#140b22",
            wraplength=640,
            justify="left"
        )
        self.lbl_eval_diagnosis.pack(anchor="w", pady=(2, 6))

        eval_actions = tk.Frame(self.warmup_eval_card, bg="#140b22")
        eval_actions.pack(fill="x")

        btn_dismiss = tk.Button(
            eval_actions,
            text="✕ Hide Analysis Drawer",
            font=("Helvetica", 8, "bold"),
            bg="#201032",
            fg=COLOR_TEXT_MUTED,
            bd=1,
            relief="solid",
            padx=8,
            pady=2,
            command=self._dismiss_warmup_evaluation
        )
        btn_dismiss.pack(side="right")

        # Initialize Warmup Routine & Badges
        self.root.after_idle(self._init_warmup_studio)

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
        sug_tag = f" (Suggested: {self._recommended_key_shift:+d})" if getattr(self, "_recommended_key_shift", 0) != 0 and self._recommended_key_shift != shift else ""
        self.lbl_key_val.config(text=f"Key Shift: {sign}{sug_tag}")
        if self.player.is_playing:
            self.player.set_key_shift(shift)
        self._refresh_warmup_context()
        self._sync_warmup_to_current_key()

    def _get_or_extract_song_peaks(self) -> Dict[str, Any]:
        """Gets cached song melody peaks or scans the track audio file."""
        if getattr(self, "_song_peaks", None):
            return self._song_peaks
        if self.selected_track:
            local_path = self.selected_track.get("local_path") or self.selected_track.get("file_path")
            if local_path and Path(local_path).exists():
                peaks = self.analyzer.extract_song_melody_profile(Path(local_path))
                self._song_peaks = peaks
                return peaks
        # Pedagogical baseline based on singer calibration
        p = self.vocal_profile or self.calibrator.load_profile()
        if p and p.tessitura_max_note:
            m = note_name_to_midi(p.tessitura_max_note)
            if m:
                # Song climax defaults to ~3 semitones above singer's tessitura (e.g. D4 for B3 baritone)
                song_m = m + 3
                peaks = {"max_midi": song_m, "max_note": midi_to_note_name(song_m), "min_note": "C3"}
                return peaks
        return {"max_hz": 349.23, "max_note": "F4", "min_note": "C3"}

    def _calculate_active_climax_note(self) -> Tuple[str, str]:
        """
        Calculates (original_climax_note, shifted_climax_note) factoring in
        singer calibration, track melody peak, and current key shift.
        Strictly clamped so it never exceeds singer's safe physiological ceiling.
        """
        p = self.vocal_profile or self.calibrator.load_profile()
        peaks = self._get_or_extract_song_peaks()
        orig_note, shifted_note, shifted_midi = self.analyzer.get_song_climax_note(peaks, self.current_key_shift)

        # Safety clamp: if singer has calibrated tessitura ceiling, clamp shifted climax
        if p:
            ceiling_note = p.tessitura_max_note or p.range_max_note
            if ceiling_note:
                shifted_note = clamp_note_to_range(shifted_note, max_note=ceiling_note)

        return orig_note, shifted_note

    def _sync_warmup_to_current_key(self):
        """
        Dynamically updates the active warmup routine's target notes and UI chips
        to reflect the active key shift and song climax.
        """
        if not getattr(self, "_warmup_routine", None):
            return

        p = self.vocal_profile or self.calibrator.load_profile()
        anchor = p.anchor_note if (p and p.anchor_note) else "E3"
        orig_climax, shifted_climax = self._calculate_active_climax_note()

        # Update Exercise 3 (or any Climax step)
        for step in self._warmup_routine:
            cat = str(step.get("category", "")).lower()
            title = str(step.get("title", "")).lower()
            if "climax" in cat or "climax" in title or "high belt" in cat or "range extension" in cat:
                shift_tag = f" (Key {self.current_key_shift:+d})" if self.current_key_shift != 0 else ""
                step["target_notes"] = f"{anchor} ➔ {shifted_climax}{shift_tag}"
                if self.selected_track:
                    song_name = self.selected_track.get("title", "Song")
                    step["song_context"] = f"Conquers the climax notes of '{song_name}' (Peak {orig_climax} transposed to {shifted_climax} at Key {self.current_key_shift:+d})."

        # If currently viewing Exercise 3, refresh the chips immediately!
        if hasattr(self, "_warmup_step_idx") and 0 <= self._warmup_step_idx < len(self._warmup_routine):
            curr_step = self._warmup_routine[self._warmup_step_idx]
            cat = str(curr_step.get("category", "")).lower()
            title = str(curr_step.get("title", "")).lower()
            if "climax" in cat or "climax" in title or "high belt" in cat:
                self._display_warmup_step(self._warmup_step_idx)

    def _on_auto_match_key(self):
        if not self.selected_track:
            messagebox.showinfo("Select Track", "Please select a rehearsal track first.")
            return
        p = self.vocal_profile or self.calibrator.load_profile()
        if not p:
            messagebox.showinfo("Calibrate First", "Please complete vocal calibration in the 'Vocal Calibration & Warmup' tab to enable smart key matching.")
            return

        self.analyzer.update_profile(p)
        peaks = self._get_or_extract_song_peaks()
        rec_shift, reason = self.analyzer.recommend_key_for_track(peaks)
        self._recommended_key_shift = rec_shift
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
            messagebox.showinfo("Select Track", "Please select a song from the library first.", parent=self.root)
            return

        local_path = self.selected_track.get("local_path")
        if not local_path or not Path(local_path).exists():
            messagebox.showwarning(
                "Track Not Found",
                "The local media file for this track was not found. Please download it first.",
                parent=self.root
            )
            return

        # Prepare take file
        timestamp = int(time.time())
        take_path = TEMP_TAKES_DIR / f"take_{timestamp}.wav"
        self.current_take_wav = take_path

        # Start low-latency mic capture
        self.recorder.start_recording(take_path)

        # Start player playback
        started = self.player.play(Path(local_path), key_shift=self.current_key_shift)
        if not started:
            self.recorder.stop_recording()
            self.is_rehearsing = False
            self.btn_record_take.config(text="🎤 START REHEARSAL TAKE", bg=COLOR_MAGENTA)
            self.lbl_status.config(text="⚠️ Could not start rehearsal playback.", fg=COLOR_DANGER)
            messagebox.showerror(
                "Playback Error",
                f"Could not open media file:\n{Path(local_path).name}\n\n"
                f"Player binary: {self.player._mpv_path or 'None found'}\n"
                "Please verify the media file is valid.",
                parent=self.root
            )
            return

        self.is_rehearsing = True
        self.btn_record_take.config(text="⏹️ STOP REHEARSAL TAKE", bg=COLOR_DANGER)
        self.lbl_status.config(text="Rehearsing take... Playback and mic recording active.", fg=COLOR_CYAN)
        self._pitch_history.clear()
        self._draw_pitch_trace()

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

        # Playback track path for acoustic bleed cancellation & reference sing-along if available
        playback_path = None
        ref_path = None
        if self.selected_track:
            if self.selected_track.get("local_path"):
                playback_path = Path(self.selected_track["local_path"])
            sa_meta = self.db.find_sing_along_equivalent(
                self.selected_track.get("title", ""),
                self.selected_track.get("artist", "")
            )
            if sa_meta and sa_meta.get("local_path"):
                ref_path = Path(sa_meta["local_path"])

        report, lyric_alignment = self.parallel_analyzer.analyze_take_parallel(
            singer_wav=take_wav,
            reference_wav=ref_path,
            playback_audio_path=playback_path,
            key_shift=self.current_key_shift,
            lyric_track=self._current_lyric_track,
            progress_cb=_prog_cb
        )

        self._last_lyric_alignment = lyric_alignment
        take_dir = save_take_partitions(
            report=report,
            lyric_report=lyric_alignment,
            track_info=self.selected_track,
            take_wav=take_wav
        )
        self._last_take_dir = take_dir

        self.root.after_idle(lambda: self._display_report_card(report, lyric_alignment))

    def _display_report_card(
        self,
        report: TakeAnalysisReport,
        lyric_alignment: Optional[LyricAlignmentReport] = None,
        is_reloaded_take: bool = False
    ):
        # Hide analysis progress bar
        if hasattr(self, "analysis_prog_frame") and self.analysis_prog_frame.winfo_exists():
            self.analysis_prog_frame.pack_forget()

        self.lbl_score_num.config(text=str(report.overall_score))

        # Update Auditory Pleasingness / Artistic Styling Banner
        auditory_score = getattr(report, "auditory_appeal_score", 0)
        auditory_verdict = getattr(report, "auditory_appeal_verdict", "Harmonically Consonant")
        if auditory_score > 0:
            self.lbl_auditory_num.config(text=str(auditory_score))
            self.lbl_auditory_sub.config(text=auditory_verdict)
        else:
            self.lbl_auditory_num.config(text="--")
            self.lbl_auditory_sub.config(text="Harmonic musicality & listener appeal")

        self.tile_pitch._val_lbl.config(text=f"{report.pitch_accuracy_pct:.1f}%")
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
        self.lbl_drawer_scoop.config(text=f"{report.pitch_scoop_pct:.0f}% ({report.attack_velocity_verdict})")
        self.lbl_drawer_arc.config(text=f"{report.dynamic_contrast_db:.1f} dB ({report.dynamic_arc_verdict})")
        self.lbl_drawer_bloom.config(text=f"{report.vibrato_style_verdict} ({report.vibrato_bloom_ms:.0f}ms delay)")

        # Save last report and lyric alignment for conversational context
        self._last_take_report = report
        self._last_lyric_alignment = lyric_alignment

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

        # 1b. Add Lyric Phrasing Card if aligned
        if lyric_alignment and lyric_alignment.status == "aligned":
            row_l = tk.Frame(self.coach_feedback_container, bg="#140b20")
            row_l.pack(fill="x", pady=3, anchor="w")
            tk.Label(
                row_l,
                text="📖",
                font=("Helvetica", 9, "bold"),
                fg=COLOR_CYAN,
                bg="#140b20"
            ).pack(side="left", anchor="n", padx=(0, 6))
            tk.Label(
                row_l,
                text=f"Lyric Phrasing Alignment: {lyric_alignment.summary_text}",
                font=("Helvetica", 9),
                fg="#a2e8dd",
                bg="#140b20",
                justify="left",
                wraplength=avail_wrap
            ).pack(side="left", fill="x", expand=True)

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

        # 5. Automatically trigger or restore AI Take Debriefing
        if is_reloaded_take and self._last_take_dir:
            debrief_file = Path(self._last_take_dir) / "coach_debrief.txt"
            if debbrief_text := (debrief_file.read_text(encoding="utf-8").strip() if debrief_file.is_file() else ""):
                self._last_debrief_text = debbrief_text
                self.lbl_ai_debrief_text.config(text=debbrief_text, fg=COLOR_TEXT_MAIN)
                self._refresh_ai_coach_view()
            else:
                self._trigger_ai_take_debrief(report, record_progression=False)
        else:
            self._trigger_ai_take_debrief(report, record_progression=True)

        self.root.update_idletasks()
        if hasattr(self, "report_canvas"):
            self.report_canvas.configure(scrollregion=self.report_canvas.bbox("all"))

        status_msg = (
            f"Reloaded previous take for '{self.selected_track.get('title', 'rehearsal') if self.selected_track else 'rehearsal'}' (Score {report.overall_score}/100)."
            if is_reloaded_take
            else "Performance analysis complete. Great work!"
        )
        self.lbl_status.config(text=status_msg)

    def _load_most_recent_take_on_startup(self):
        """
        On application startup, automatically reloads and displays the most recent rehearsal take
        in the cockpit report card and AI debrief view so singers can immediately continue
        their vocal progression.
        """
        try:
            take_dir = get_most_recent_take_dir()
            if not take_dir or not take_dir.is_dir():
                return

            report, lyric_alignment, track_info = load_take_report_and_lyrics(take_dir)
            if not report:
                return

            self._last_take_dir = take_dir
            take_wav = take_dir / "singer_take.wav"
            if take_wav.is_file():
                self.current_take_wav = take_wav

            # Set up session key shift if present
            self.current_key_shift = report.current_key_shift
            self._update_key_display()

            # Find matching track in local library if possible
            track_title = track_info.get("title", "") if track_info else ""
            matching_tracks = self.db.search_tracks(track_title) if track_title else []
            if matching_tracks:
                # Load matched library track into cockpit so backing audio & lyrics are linked
                self._load_track_into_cockpit(matching_tracks[0])
            elif track_info:
                # Set up cockpit headers with the stored take track metadata
                self.selected_track = track_info
                self.lbl_track_title.config(text=track_info.get("title", "Rehearsal Take"))
                self.lbl_track_artist.config(text=track_info.get("artist", ""))
                dur = int(track_info.get("duration", 0))
                dur_min, dur_sec = dur // 60, dur % 60
                self.lbl_track_meta.config(
                    text=f"Duration: {dur_min}:{dur_sec:02d}  •  Last Take: {take_dir.name}"
                )
                self.notebook.select(self.tab_studio)

            # Display the report card as though the take just took place
            self._display_report_card(report, lyric_alignment, is_reloaded_take=True)
            self.lbl_status.config(
                text=f"Loaded previous take for '{track_title or 'rehearsal'}' (Score {report.overall_score}/100). Ready for your next take!"
            )
        except Exception as e:
            print(f"[StudioWindow] Error reloading recent take on startup: {e}", file=sys.stderr)

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
        accel = self.offline_coach.get_acceleration_mode().upper()
        accel_icon = "⚡" if accel == "GPU" else "🖥️"
        if installed:
            if hasattr(self, "lbl_ai_model_badge"):
                self.lbl_ai_model_badge.config(text=f"{accel_icon} Model: {installed.get('name', 'Offline')} • {accel}")
            self.ai_install_card.pack_forget()
            self.ai_debrief_card.pack(fill="x", pady=(8, 0))
            self.conversation_card.pack(fill="x", pady=(8, 0))
        else:
            self.ai_debrief_card.pack_forget()
            self.conversation_card.pack_forget()
            self.ai_install_card.pack(fill="x", pady=(8, 0))
            if hasattr(self, "lbl_ai_install_status"):
                self.lbl_ai_install_status.config(text="Ready to install")
            if hasattr(self, "prog_ai") and self.prog_ai.winfo_exists():
                self.prog_ai.pack_forget()

        if hasattr(self, "btn_header_ai"):
            if installed:
                self.btn_header_ai.config(
                    text=f"🤖 AI Coach ({installed.get('key', '').upper()} • {accel}) • Manage",
                    fg=COLOR_GOLD
                )
            else:
                self.btn_header_ai.config(
                    text="🤖 AI Coach: Manage / Install",
                    fg=COLOR_TEXT_MUTED
                )

        self.root.update_idletasks()
        if hasattr(self, "report_canvas"):
            self.report_canvas.configure(scrollregion=self.report_canvas.bbox("all"))

    def _open_manage_models_dialog(self):
        """Dedicated dialog to inspect and cleanly uninstall offline AI models (Option B)."""
        dlg = tk.Toplevel(self.root)
        dlg.title("⚙️ Manage Offline AI Models & Acceleration")
        dlg.geometry("540x480")
        dlg.resizable(False, False)
        dlg.configure(bg=COLOR_BG_MAIN)
        dlg.transient(self.root)
        dlg.grab_set()

        # Center dialog
        dlg.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 270
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 240
        dlg.geometry(f"+{max(0, x)}+{max(0, y)}")

        # Title Header
        hdr = tk.Frame(dlg, bg="#1e0f30", padx=16, pady=12)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text="🤖 Offline AI Vocal Coach Manager",
            font=("Helvetica", 12, "bold"),
            fg=COLOR_GOLD,
            bg="#1e0f30"
        ).pack(anchor="w")
        tk.Label(
            hdr,
            text="Manage local GGUF models for offline vocal analysis & Maestro Tiki debriefings.",
            font=("Helvetica", 8),
            fg=COLOR_TEXT_MUTED,
            bg="#1e0f30"
        ).pack(anchor="w", pady=(2, 0))

        content = tk.Frame(dlg, bg=COLOR_BG_MAIN, padx=16, pady=14)
        content.pack(fill="both", expand=True)

        installed = self.offline_coach.get_installed_model()
        if installed:
            card = tk.LabelFrame(
                content,
                text=" CURRENTLY ACTIVE MODEL ",
                bg="#1a0d2a",
                fg=COLOR_CYAN,
                font=("Helvetica", 9, "bold"),
                bd=1,
                padx=12,
                pady=10
            )
            card.pack(fill="x", pady=(0, 10))

            tk.Label(
                card,
                text=installed.get("name", "Unknown Model"),
                font=("Helvetica", 11, "bold"),
                fg="#ffffff",
                bg="#1a0d2a"
            ).pack(anchor="w")

            tk.Label(
                card,
                text=installed.get("tagline", ""),
                font=("Helvetica", 8, "italic"),
                fg=COLOR_TEXT_MUTED,
                bg="#1a0d2a"
            ).pack(anchor="w", pady=(2, 4))

            p = installed.get("path")
            size_mb = p.stat().st_size / (1024 * 1024) if p and p.exists() else 0
            tk.Label(
                card,
                text=f"• Disk Footprint: {size_mb:.1f} MB  ({size_mb / 1024:.2f} GB)",
                font=("Helvetica", 9),
                fg=COLOR_LIME,
                bg="#1a0d2a"
            ).pack(anchor="w")

            server_bin = self.offline_coach._find_llama_server_binary()
            engine_str = f"Standalone llama.cpp ({server_bin.name})" if server_bin else "Python llama_cpp"
            tk.Label(
                card,
                text=f"• AI Inference Engine: {engine_str}",
                font=("Helvetica", 9),
                fg=COLOR_CYAN,
                bg="#1a0d2a"
            ).pack(anchor="w")

            tk.Label(
                card,
                text=f"• Storage Location: {p.parent if p else self.offline_coach.models_dir}",
                font=("Helvetica", 8),
                fg=COLOR_TEXT_MUTED,
                bg="#1a0d2a",
                wraplength=480,
                justify="left"
            ).pack(anchor="w", pady=(2, 0))

            # Hardware Acceleration Selection Card
            accel_card = tk.LabelFrame(
                content,
                text=" HARDWARE ACCELERATION ",
                bg="#1a0d2a",
                fg=COLOR_GOLD,
                font=("Helvetica", 9, "bold"),
                bd=1,
                padx=12,
                pady=8
            )
            accel_card.pack(fill="x", pady=(0, 10))

            dlg_accel_var = tk.StringVar(value=self.offline_coach.get_acceleration_mode())

            lbl_accel_status = tk.Label(
                accel_card,
                text="",
                font=("Helvetica", 8, "italic"),
                fg=COLOR_CYAN,
                bg="#1a0d2a"
            )

            def _on_accel_change():
                mode = dlg_accel_var.get()
                self.offline_coach.set_acceleration_mode(mode)
                if hasattr(self, "accel_choice_var"):
                    self.accel_choice_var.set(mode)
                self._refresh_ai_coach_view()
                lbl_accel_status.config(
                    text=f"Active: {'⚡ GPU Acceleration (Layers offloaded: 99)' if mode == 'gpu' else '🖥️ CPU Only (Universal Threads)'}"
                )

            rb_dlg_gpu = tk.Radiobutton(
                accel_card,
                text="⚡ GPU Acceleration (VRAM Offload - Recommended for Dedicated GPUs)",
                variable=dlg_accel_var,
                value="gpu",
                fg=COLOR_CYAN,
                bg="#1a0d2a",
                selectcolor="#0d0814",
                activebackground="#1a0d2a",
                activeforeground=COLOR_CYAN,
                font=("Helvetica", 9, "bold"),
                command=_on_accel_change
            )
            rb_dlg_gpu.pack(anchor="w")

            rb_dlg_cpu = tk.Radiobutton(
                accel_card,
                text="🖥️ CPU Only (Universal Compatibility / Multi-threaded CPU)",
                variable=dlg_accel_var,
                value="cpu",
                fg=COLOR_TEXT_MAIN,
                bg="#1a0d2a",
                selectcolor="#0d0814",
                activebackground="#1a0d2a",
                activeforeground=COLOR_TEXT_MAIN,
                font=("Helvetica", 9),
                command=_on_accel_change
            )
            rb_dlg_cpu.pack(anchor="w")

            lbl_accel_status.pack(anchor="w", pady=(4, 0))
            cur_mode = dlg_accel_var.get()
            lbl_accel_status.config(
                text=f"Active: {'⚡ GPU Acceleration (Layers offloaded: 99)' if cur_mode == 'gpu' else '🖥️ CPU Only (Universal Threads)'}"
            )

            # Action Frame
            action_frame = tk.Frame(content, bg=COLOR_BG_MAIN)
            action_frame.pack(fill="x", pady=6)

            def _uninstall():
                confirmed = messagebox.askyesno(
                    "Confirm Model Removal",
                    f"Are you sure you want to uninstall '{installed.get('name')}'?\n\n"
                    f"This will free up ~{size_mb / 1024:.2f} GB of disk space and revert "
                    f"the cockpit back to the optional download choice screen.",
                    parent=dlg
                )
                if confirmed:
                    self.offline_coach.uninstall_model()
                    self._refresh_ai_coach_view()
                    dlg.destroy()
                    messagebox.showinfo(
                        "Model Uninstalled",
                        "Offline AI Model removed cleanly. Reverted to download choice screen.",
                        parent=self.root
                    )

            btn_del = tk.Button(
                action_frame,
                text="🗑️ Uninstall Model & Revert to Choice Screen",
                font=("Helvetica", 10, "bold"),
                bg="#401018",
                fg=COLOR_DANGER,
                activebackground="#601824",
                activeforeground="#ffffff",
                bd=1,
                relief="solid",
                padx=12,
                pady=6,
                command=_uninstall
            )
            btn_del.pack(side="left")

        else:
            tk.Label(
                content,
                text="No offline AI models are currently installed.\n\nYou can download the 7B, 3B, or 1.5B model anytime directly from the Rehearsal Cockpit.",
                font=("Helvetica", 10),
                fg=COLOR_TEXT_MUTED,
                bg=COLOR_BG_MAIN,
                justify="center"
            ).pack(expand=True, pady=20)

        # Close button at bottom
        btn_close = tk.Button(
            dlg,
            text="Close",
            font=("Helvetica", 9),
            bg="#25123a",
            fg=COLOR_TEXT_MAIN,
            padx=16,
            pady=4,
            command=dlg.destroy
        )
        btn_close.pack(side="bottom", pady=10)

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

    def _trigger_ai_take_debrief(self, report: TakeAnalysisReport, record_progression: bool = True):
        self._refresh_ai_coach_view()
        if not self.offline_coach.is_installed():
            return

        # If pure silence or filtered bleed, deliver concise coach advice immediately
        if report.overall_score == 0:
            msg = report.coach_feedback[0] if report.coach_feedback else "No vocal singing detected."
            self.lbl_ai_debrief_text.config(text=f"✦ Maestro Tiki: {msg}", fg=COLOR_GOLD)
            return

        self.lbl_ai_debrief_text.config(text="✦ Maestro Tiki is analyzing your take metrics offline...", fg=COLOR_CYAN)

        def _worker():
            try:
                track_title = self.selected_track.get("title", "Rehearsal Track") if self.selected_track else "Rehearsal Track"
                progression_data = self.progression_db.build_llm_progression_context(
                    track_title=track_title,
                    current_score=report.overall_score,
                    current_pitch_acc=report.pitch_accuracy_pct,
                    current_breath=report.breath_support_pct
                )
                profile_dict = self.vocal_profile.to_dict() if self.vocal_profile else None
                lyric_dict = self._last_lyric_alignment.to_dict() if self._last_lyric_alignment else None
                toc_dict = load_take_toc(self._last_take_dir) if self._last_take_dir else None
                debrief = self.offline_coach.generate_take_debrief(
                    report.to_dict(),
                    self.selected_track,
                    profile_dict,
                    progression_data=progression_data,
                    lyric_alignment=lyric_dict,
                    toc=toc_dict
                )
                self._last_debrief_text = debrief

                # Persist generated debrief into take partition folder for instant retrieval
                if self._last_take_dir and Path(self._last_take_dir).is_dir():
                    try:
                        (Path(self._last_take_dir) / "coach_debrief.txt").write_text(debrief, encoding="utf-8")
                    except Exception:
                        pass

                # Record take in progression database only if it's a new take
                if record_progression:
                    self.progression_db.record_take(
                        track_title=track_title,
                        overall_score=report.overall_score,
                        pitch_accuracy_pct=report.pitch_accuracy_pct,
                        intonation_cents=report.intonation_cents,
                        rhythm_offset_ms=report.rhythm_offset_ms,
                        breath_support_pct=report.breath_support_pct,
                        singers_formant_ratio=report.singers_formant_ratio,
                        balanced_onsets_pct=report.balanced_onsets_pct,
                        track_id=self.selected_track.get("id", 0) if self.selected_track else 0,
                        track_artist=self.selected_track.get("artist", "") if self.selected_track else "",
                        key_shift=self.current_key_shift
                    )
            except Exception as e:
                import traceback
                print(f"[StudioWindow] AI debrief error: {e}", file=sys.stderr)
                traceback.print_exc()
                debrief = self.offline_coach._generate_rule_based_debrief(
                    report.to_dict(),
                    self.selected_track,
                    self.vocal_profile.to_dict() if self.vocal_profile else None,
                    lyric_alignment=self._last_lyric_alignment.to_dict() if self._last_lyric_alignment else None
                )
                self._last_debrief_text = debrief

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
            try:
                rep_dict = self._last_take_report.to_dict() if self._last_take_report else None
                debrief_ctx = getattr(self, "_last_debrief_text", None)
                lyric_dict = self._last_lyric_alignment.to_dict() if getattr(self, "_last_lyric_alignment", None) else None
                toc_dict = load_take_toc(self._last_take_dir) if getattr(self, "_last_take_dir", None) else None
                reply = self.offline_coach.chat_with_coach(
                    msg,
                    self.chat_history,
                    rep_dict,
                    self.selected_track,
                    debrief_text=debrief_ctx,
                    lyric_alignment=lyric_dict,
                    toc=toc_dict
                )
            except Exception as e:
                import traceback
                print(f"[StudioWindow] Chat error: {e}", file=sys.stderr)
                traceback.print_exc()
                reply = "Coach Tiki: Keep focusing on forward resonance and relaxed, diaphragmatic breath support!"

            self.chat_history.append({"role": "assistant", "content": reply})

            def _update():
                coach_lbl.config(text=reply, font=("Helvetica", 8), fg=COLOR_TEXT_MAIN)
                self._is_ai_generating = False
                self.btn_send_chat.config(state="normal")
            self.root.after_idle(_update)

        threading.Thread(target=_worker, daemon=True).start()

    def _set_library_filter(self, filter_key: str):
        self._library_filter = filter_key
        for k, btn in self._filter_buttons.items():
            if k == filter_key:
                btn.config(bg="#2a1240", fg=COLOR_CYAN)
            else:
                btn.config(bg="#150a22", fg=COLOR_TEXT_MUTED)
        self._on_search_library()

    def _on_click_search_library(self):
        """Explicitly searches the offline local library and activates the Offline Ready filter."""
        self._set_library_filter("ready")

    def _on_search_library(self):
        query = self.ent_search.get().strip() if hasattr(self, "ent_search") else ""
        dl_only = (self._library_filter == "ready") or (not query and self._library_filter == "all")
        sa_only = (self._library_filter == "singalong")
        tracks = self.db.search_tracks(query, limit=100, sing_along_only=sa_only, downloaded_only=dl_only)

        if self._library_filter == "karaoke":
            tracks = [t for t in tracks if not t.get("is_sing_along")]

        # Clear existing
        for item in self.tree_tracks.get_children():
            self.tree_tracks.delete(item)

        for idx, t in enumerate(tracks):
            dur_min = t.get("duration", 0) // 60
            dur_sec = t.get("duration", 0) % 60
            m_type = "Sing-Along 🎙️" if t.get("is_sing_along") else "Karaoke 🎵"
            ready = "✅ Ready (Offline)" if t.get("is_downloaded") else "☁️ Fetch / Stream"
            row_tag = "row_odd" if idx % 2 == 0 else "row_even"

            self.tree_tracks.insert(
                "",
                "end",
                iid=f"db_{t.get('id')}",
                values=(
                    t.get("id"),
                    t.get("title"),
                    t.get("artist"),
                    f"{dur_min}:{dur_sec:02d}",
                    m_type,
                    ready
                ),
                tags=(row_tag,)
            )

        if hasattr(self, "btn_find_more"):
            self.btn_find_more.pack_forget()

        self.lbl_status.config(text=f"Library: Showing {len(tracks)} tracks.")

    def _on_search_unified(self):
        """Triggered on Enter/Return: Displays matching local tracks on top, and fetches 10 YouTube tracks below."""
        query = self.ent_search.get().strip() if hasattr(self, "ent_search") else ""
        if not query:
            self._on_search_library()
            return

        self._yt_current_query = query
        self._yt_current_page = 1
        self._yt_seen_ids.clear()
        self._yt_search_results.clear()

        # 1. Fetch matching local library tracks
        sa_only = (self._library_filter == "singalong")
        local_tracks = self.db.search_tracks(query, limit=50, sing_along_only=sa_only, downloaded_only=True)
        if self._library_filter == "karaoke":
            local_tracks = [t for t in local_tracks if not t.get("is_sing_along")]

        # Populate local tracks in table
        for item in self.tree_tracks.get_children():
            self.tree_tracks.delete(item)

        for idx, t in enumerate(local_tracks):
            vid = t.get("video_id")
            if vid:
                self._yt_seen_ids.add(vid)
            dur_min = t.get("duration", 0) // 60
            dur_sec = t.get("duration", 0) % 60
            m_type = "Sing-Along 🎙️" if t.get("is_sing_along") else "Karaoke 🎵"
            row_tag = "row_odd" if idx % 2 == 0 else "row_even"
            self.tree_tracks.insert(
                "",
                "end",
                iid=f"db_{t.get('id')}",
                values=(
                    t.get("id"),
                    t.get("title"),
                    t.get("artist"),
                    f"{dur_min}:{dur_sec:02d}",
                    m_type,
                    "✅ Ready (Offline)"
                ),
                tags=(row_tag,)
            )

        self.lbl_status.config(
            text=f"Found {len(local_tracks)} local tracks. Fetching YouTube suggestions for '{query}'...",
            fg=COLOR_GOLD
        )

        # 2. In background, fetch 10 YouTube results matching active filter
        threading.Thread(
            target=self._run_youtube_search_worker,
            args=(query, 1, False),
            daemon=True
        ).start()

    def _get_selected_quality_profile(self) -> str:
        """Determines the active video quality profile string."""
        val = getattr(self, "quality_var", None)
        if not val:
            return TIKI_VIDEO_QUALITY_PROFILE
        txt = val.get().lower()
        if "480" in txt or "compact" in txt or "sd" in txt:
            return "compact_480p"
        if "720" in txt or "balanced" in txt:
            return "balanced_720p"
        return "pro"

    def _load_track_into_cockpit(self, track: Dict[str, Any]):
        """Loads a ready track into the rehearsal cockpit with full melody and key sync."""
        self.selected_track = track
        self._song_peaks = None
        self._recommended_key_shift = 0
        self.lbl_track_title.config(text=track.get("title", "Unknown Title"))
        self.lbl_track_artist.config(text=track.get("artist", "Unknown Artist"))
        dur_min = int(track.get("duration", 0)) // 60
        dur_sec = int(track.get("duration", 0)) % 60
        self.lbl_track_meta.config(
            text=f"Duration: {dur_min}:{dur_sec:02d}  •  Source: {track.get('media_source', 'local').upper()}"
        )
        self.notebook.select(self.tab_studio)
        self.lbl_status.config(text=f"Loaded '{track.get('title')}' into rehearsal cockpit.")

        # Update warmup context immediately
        self._refresh_warmup_context()
        self._sync_warmup_to_current_key()

        # In background, scan track audio and recommend key shift
        local_path = track.get("local_path") or track.get("file_path")
        if local_path and Path(local_path).exists():
            def _scan_worker():
                peaks = self.analyzer.extract_song_melody_profile(Path(local_path))
                self._song_peaks = peaks
                p = self.vocal_profile or self.calibrator.load_profile()
                if p:
                    self.analyzer.update_profile(p)
                    rec_shift, reason = self.analyzer.recommend_key_for_track(peaks)
                    self._recommended_key_shift = rec_shift
                    def _ui():
                        self._update_key_display()
                        self._sync_warmup_to_current_key()
                    self.dispatch_to_main(_ui)
            threading.Thread(target=_scan_worker, daemon=True).start()

        # In background, resolve synced lyrics so they are ready before rehearsal completes
        self._current_lyric_track = None
        self._last_lyric_alignment = None

        def _lyrics_worker():
            try:
                lt = self.lyric_manager.resolve_track_lyrics(track)
                self._current_lyric_track = lt
                if lt and lt.lines:
                    def _ui_lyr():
                        self.lbl_status.config(
                            text=f"Loaded '{track.get('title')}' • Synced lyrics ready ({len(lt.lines)} lines)."
                        )
                    self.dispatch_to_main(_ui_lyr)
            except Exception as e:
                print(f"[StudioWindow] Background lyric load error: {e}", file=sys.stderr)

        threading.Thread(target=_lyrics_worker, daemon=True).start()

    def _on_select_track(self):
        """Triggered on Double-Click or 'Load Selected Song into Cockpit' button."""
        selected = self.tree_tracks.selection()
        if not selected:
            return
        item_id = selected[0]
        item_vals = self.tree_tracks.item(item_id, "values")
        raw_id = item_vals[0]

        if str(raw_id).upper() == "YT":
            # Un-downloaded YouTube search result: start download, then auto-load into cockpit!
            vid = item_id.replace("yt_", "") if item_id.startswith("yt_") else ""
            if not vid:
                for v_key, r in self._yt_search_results.items():
                    if r.get("title") == item_vals[1] and r.get("artist") == item_vals[2]:
                        vid = v_key
                        break
            if vid:
                self._start_track_download(vid, item_id, auto_load=True)
            return

        # It's an integer track ID in the database
        try:
            track_id = int(raw_id)
        except (ValueError, TypeError):
            return

        track = self.db.get_track_by_id(track_id)
        if not track:
            return

        local_path = track.get("local_path")
        if not track.get("is_downloaded") or not local_path or not Path(local_path).exists():
            vid = track.get("video_id")
            if vid:
                self._start_track_download(vid, item_id, auto_load=True, existing_track=track)
                return
            else:
                messagebox.showwarning("File Missing", f"Local file not found for '{track.get('title')}'.")
                return

        self._load_track_into_cockpit(track)

    def _on_download_selected_track(self):
        """Explicit '⬇️ Download to Library' button handler."""
        selected = self.tree_tracks.selection()
        if not selected:
            messagebox.showinfo("Select Song", "Please select a song from the library or search results to download.")
            return
        item_id = selected[0]
        item_vals = self.tree_tracks.item(item_id, "values")
        raw_id = item_vals[0]

        if str(raw_id).upper() == "YT":
            vid = item_id.replace("yt_", "") if item_id.startswith("yt_") else ""
            if not vid:
                for v_key, r in self._yt_search_results.items():
                    if r.get("title") == item_vals[1] and r.get("artist") == item_vals[2]:
                        vid = v_key
                        break
            if vid:
                self._start_track_download(vid, item_id, auto_load=True)
        else:
            try:
                track_id = int(raw_id)
                track = self.db.get_track_by_id(track_id)
                if track:
                    local_path = track.get("local_path")
                    if track.get("is_downloaded") and local_path and Path(local_path).exists():
                        messagebox.showinfo("Already Downloaded", f"'{track.get('title')}' is already downloaded and offline-ready.")
                        return
                    vid = track.get("video_id")
                    if vid:
                        self._start_track_download(vid, item_id, auto_load=True, existing_track=track)
            except Exception:
                pass

    def _on_edit_selected_track_metadata(self):
        """Validates selection and opens the track metadata editor dialog."""
        selected = self.tree_tracks.selection()
        if not selected:
            messagebox.showinfo("Select Track", "Please select a downloaded track from the library to edit.")
            return

        item_id = selected[0]
        item_vals = self.tree_tracks.item(item_id, "values")
        raw_id = item_vals[0]

        if str(raw_id).upper() == "YT":
            messagebox.showinfo(
                "Downloaded Tracks Only",
                "Only downloaded tracks in your offline library can be edited.\n\nDownload this song first to customize its artist, title, or song type."
            )
            return

        try:
            track_id = int(raw_id)
        except (ValueError, TypeError):
            messagebox.showinfo("Invalid Selection", "Selected item is not a valid library track.")
            return

        track = self.db.get_track_by_id(track_id)
        if not track or not track.get("is_downloaded"):
            messagebox.showinfo(
                "Downloaded Tracks Only",
                "Only downloaded tracks in your offline library can be edited."
            )
            return

        self._show_edit_metadata_dialog(item_id, track)

    def _show_edit_metadata_dialog(self, item_id: str, track: Dict[str, Any]):
        """Renders the Tiki-themed metadata editor modal dialog."""
        dlg = tk.Toplevel(self.root)
        dlg.title(f"✏️ Edit Track Details — #{track.get('id')}")
        dlg.geometry("540x510")
        dlg.configure(bg=COLOR_BG_MAIN)
        dlg.transient(self.root)
        dlg.grab_set()

        # Center dialog over main window
        dlg.update_idletasks()
        rx = self.root.winfo_rootx()
        ry = self.root.winfo_rooty()
        rw = self.root.winfo_width()
        rh = self.root.winfo_height()
        dlg.geometry(f"+{rx + max(0, (rw - 540) // 2)}+{ry + max(0, (rh - 510) // 2)}")

        # Header Frame
        hdr = tk.Frame(dlg, bg="#1a1025", padx=16, pady=12)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text="🌺 EDIT TRACK METADATA",
            font=("Helvetica", 11, "bold"),
            fg=COLOR_GOLD,
            bg="#1a1025"
        ).pack(anchor="w")
        tk.Label(
            hdr,
            text=f"Editing Library Track #{track.get('id')} • Changes sync immediately to database and disk.",
            font=("Helvetica", 8),
            fg=COLOR_TEXT_MUTED,
            bg="#1a1025"
        ).pack(anchor="w", pady=(2, 0))

        # Main Form Frame
        form = tk.Frame(dlg, bg=COLOR_BG_MAIN, padx=20, pady=14)
        form.pack(fill="both", expand=True)

        # 1. Title
        tk.Label(form, text="SONG TITLE:", font=("Helvetica", 9, "bold"), fg=COLOR_CYAN, bg=COLOR_BG_MAIN).pack(anchor="w", pady=(0, 2))
        ent_title = tk.Entry(form, font=("Helvetica", 10), bg="#150a22", fg=COLOR_TEXT_MAIN, insertbackground=COLOR_CYAN, bd=1, relief="solid")
        ent_title.pack(fill="x", pady=(0, 10))
        ent_title.insert(0, track.get("title", ""))

        # 2. Artist
        tk.Label(form, text="ARTIST / PERFORMER:", font=("Helvetica", 9, "bold"), fg=COLOR_CYAN, bg=COLOR_BG_MAIN).pack(anchor="w", pady=(0, 2))
        ent_artist = tk.Entry(form, font=("Helvetica", 10), bg="#150a22", fg=COLOR_TEXT_MAIN, insertbackground=COLOR_CYAN, bd=1, relief="solid")
        ent_artist.pack(fill="x", pady=(0, 10))
        ent_artist.insert(0, track.get("artist", ""))

        # 3. Song Type (Karaoke vs Sing-Along)
        tk.Label(form, text="SONG TYPE / MEDIA SUBTYPE:", font=("Helvetica", 9, "bold"), fg=COLOR_GOLD, bg=COLOR_BG_MAIN).pack(anchor="w", pady=(0, 4))
        type_frame = tk.Frame(form, bg="#150a22", padx=8, pady=6, bd=1, relief="solid")
        type_frame.pack(fill="x", pady=(0, 10))

        type_var = tk.IntVar(value=1 if track.get("is_sing_along") else 0)
        rb_sing = tk.Radiobutton(
            type_frame,
            text="🎙️ Sing-Along Guide (With Lead Vocals for Reference)",
            variable=type_var,
            value=1,
            font=("Helvetica", 9),
            fg=COLOR_CYAN,
            bg="#150a22",
            selectcolor="#0d0814",
            activebackground="#150a22",
            activeforeground=COLOR_CYAN
        )
        rb_sing.pack(anchor="w", pady=2)

        rb_karaoke = tk.Radiobutton(
            type_frame,
            text="🎵 Karaoke Backing Track (Instrumental Stems / No Lead Vocals)",
            variable=type_var,
            value=0,
            font=("Helvetica", 9),
            fg=COLOR_MAGENTA,
            bg="#150a22",
            selectcolor="#0d0814",
            activebackground="#150a22",
            activeforeground=COLOR_MAGENTA
        )
        rb_karaoke.pack(anchor="w", pady=2)

        # 4. Genre & Year (Two-column row)
        meta_row = tk.Frame(form, bg=COLOR_BG_MAIN)
        meta_row.pack(fill="x", pady=(0, 10))

        col_g = tk.Frame(meta_row, bg=COLOR_BG_MAIN)
        col_g.pack(side="left", fill="x", expand=True, padx=(0, 6))
        tk.Label(col_g, text="GENRE:", font=("Helvetica", 8, "bold"), fg=COLOR_TEXT_MUTED, bg=COLOR_BG_MAIN).pack(anchor="w")
        ent_genre = tk.Entry(col_g, font=("Helvetica", 9), bg="#150a22", fg=COLOR_TEXT_MAIN, insertbackground=COLOR_CYAN, bd=1, relief="solid")
        ent_genre.pack(fill="x")
        ent_genre.insert(0, track.get("genre", "") or "")

        col_y = tk.Frame(meta_row, bg=COLOR_BG_MAIN)
        col_y.pack(side="right", fill="x", expand=True, padx=(6, 0))
        tk.Label(col_y, text="RELEASE YEAR:", font=("Helvetica", 8, "bold"), fg=COLOR_TEXT_MUTED, bg=COLOR_BG_MAIN).pack(anchor="w")
        ent_year = tk.Entry(col_y, font=("Helvetica", 9), bg="#150a22", fg=COLOR_TEXT_MAIN, insertbackground=COLOR_CYAN, bd=1, relief="solid")
        ent_year.pack(fill="x")
        y_val = track.get("release_year")
        if y_val and int(y_val) > 0:
            ent_year.insert(0, str(y_val))

        # 5. File Location on Disk info & relocation checkbox (Option A)
        curr_local = track.get("local_path", "")
        file_info = tk.Frame(form, bg="#12081c", padx=8, pady=6, bd=1, relief="solid")
        file_info.pack(fill="x", pady=(0, 6))

        tk.Label(
            file_info,
            text=f"📁 Current File: {Path(curr_local).name if curr_local else 'None'}",
            font=("Helvetica", 8),
            fg=COLOR_TEXT_MUTED,
            bg="#12081c",
            anchor="w"
        ).pack(fill="x")

        var_relocate = tk.BooleanVar(value=True)
        cb_relocate = tk.Checkbutton(
            form,
            text="Move & rename file on disk to match new Artist & Song Type (Option A)",
            variable=var_relocate,
            font=("Helvetica", 8, "bold"),
            fg=COLOR_LIME,
            bg=COLOR_BG_MAIN,
            selectcolor="#0d0814",
            activebackground=COLOR_BG_MAIN,
            activeforeground=COLOR_LIME
        )
        cb_relocate.pack(anchor="w", pady=(0, 10))

        # Error banner (hidden initially)
        lbl_err = tk.Label(form, text="", font=("Helvetica", 8, "bold"), fg=COLOR_DANGER, bg=COLOR_BG_MAIN)
        lbl_err.pack(anchor="w")

        # Bottom Buttons
        btn_box = tk.Frame(dlg, bg="#1a1025", padx=16, pady=12)
        btn_box.pack(fill="x")

        def _on_save():
            new_title = ent_title.get().strip()
            new_artist = ent_artist.get().strip()
            if not new_title:
                lbl_err.config(text="Please provide a valid song title.")
                return
            if not new_artist:
                lbl_err.config(text="Please provide a valid artist name.")
                return

            new_is_sa = bool(type_var.get() == 1)
            new_genre = ent_genre.get().strip()
            raw_year = ent_year.get().strip()
            new_year = int(raw_year) if raw_year.isdigit() else 0

            # Option A: Relocate & rename file on disk if requested
            new_local_path = curr_local
            if var_relocate.get() and curr_local and Path(curr_local).exists():
                try:
                    moved = relocate_and_rename_track_file(
                        curr_local_path=curr_local,
                        new_title=new_title,
                        new_artist=new_artist,
                        new_is_sing_along=new_is_sa,
                        video_id=track.get("video_id", "")
                    )
                    if moved and moved.exists():
                        new_local_path = str(moved)
                except Exception as e:
                    print(f"[StudioCockpit] File move warning: {e}", file=sys.stderr)

            # Update database
            updates = {
                "title": new_title,
                "artist": new_artist,
                "original_title": f"{new_artist} - {new_title}",
                "is_sing_along": 1 if new_is_sa else 0,
                "local_path": new_local_path,
                "genre": new_genre,
                "release_year": new_year
            }
            ok = self.db.update_track_metadata(track.get("id"), updates)
            if not ok:
                lbl_err.config(text="Database update failed. Please try again.")
                return

            # Update Treeview in place
            if self.tree_tracks.exists(item_id):
                self.tree_tracks.set(item_id, "title", new_title)
                self.tree_tracks.set(item_id, "artist", new_artist)
                m_type = "Sing-Along 🎙️" if new_is_sa else "Karaoke 🎵"
                self.tree_tracks.set(item_id, "type", m_type)

            # Synchronize Cockpit if this song is currently loaded
            if self.selected_track and self.selected_track.get("id") == track.get("id"):
                self.selected_track.update(updates)
                self.lbl_track_title.config(text=new_title)
                self.lbl_track_artist.config(text=new_artist)

            self.lbl_status.config(
                text=f"✅ Updated metadata for '{new_title}' by {new_artist} (Option A disk sync complete).",
                fg=COLOR_LIME
            )
            dlg.destroy()

        btn_cancel = tk.Button(
            btn_box,
            text="Cancel",
            bg="#2a1545",
            fg=COLOR_TEXT_MUTED,
            font=("Helvetica", 9),
            bd=0,
            padx=14,
            pady=6,
            command=dlg.destroy
        )
        btn_cancel.pack(side="right", padx=(8, 0))

        btn_save = tk.Button(
            btn_box,
            text="💾 Save Changes",
            bg=COLOR_CYAN,
            fg="#000000",
            font=("Helvetica", 9, "bold"),
            bd=0,
            padx=16,
            pady=6,
            command=_on_save
        )
        btn_save.pack(side="right")

    def _start_track_download(
        self,
        video_id: str,
        item_id: str,
        auto_load: bool = True,
        existing_track: Optional[Dict[str, Any]] = None
    ):
        """Asynchronously downloads YouTube track with full Tiki Karaoke backend parity."""
        if video_id in self._downloading_tracks:
            messagebox.showinfo("In Progress", "This track is already downloading.")
            return

        self._downloading_tracks.add(video_id)

        # Retrieve metadata
        if existing_track:
            meta_src = existing_track
        else:
            meta_src = self._yt_search_results.get(video_id, {})

        title = meta_src.get("title", "Song")
        artist = meta_src.get("artist", "Artist")
        original_title = meta_src.get("original_title", "")
        duration = int(meta_src.get("duration", 0))
        is_sing_along = bool(meta_src.get("is_sing_along", False))
        channel = meta_src.get("channel", "")

        quality_prof = self._get_selected_quality_profile()

        def _progress(fraction: float, stage_text: str):
            def _ui():
                pct = int(fraction * 100)
                self.lbl_status.config(text=f"⬇️ {stage_text} [{title[:28]}]", fg=COLOR_GOLD)
                if self.tree_tracks.exists(item_id):
                    self.tree_tracks.set(item_id, "downloaded", f"⏳ {pct}%")
            self.dispatch_to_main(_ui)

        def _worker():
            meta = self.downloader.download_track(
                video_id=video_id,
                title=title,
                artist=artist,
                original_title=original_title,
                channel=channel,
                duration=duration,
                is_sing_along=is_sing_along,
                quality_profile=quality_prof,
                progress_callback=_progress
            )

            self._downloading_tracks.discard(video_id)

            def _done():
                track_id = meta.get("id") if meta else None
                if meta and not track_id:
                    db_row = self.db.get_track_by_video_id(video_id)
                    if db_row and db_row.get("id"):
                        track_id = db_row["id"]
                        meta["id"] = track_id

                has_file = bool(meta and meta.get("local_path") and Path(meta["local_path"]).exists())

                if meta and (track_id or has_file):
                    display_id = str(track_id) if track_id else "Ready"
                    if self.tree_tracks.exists(item_id):
                        self.tree_tracks.set(item_id, "id", display_id)
                        self.tree_tracks.set(item_id, "downloaded", "✅ Ready (Offline)")
                    self.lbl_status.config(
                        text=f"✅ Downloaded & EBU R128 normalized '{title}' (-{TIKI_TARGET_LUFS} LUFS). Ready for rehearsal!",
                        fg=COLOR_LIME
                    )
                    if auto_load:
                        self._load_track_into_cockpit(meta)
                else:
                    if self.tree_tracks.exists(item_id):
                        self.tree_tracks.set(item_id, "downloaded", "❌ Failed")
                    self.lbl_status.config(
                        text=f"❌ Download failed for '{title}'. Check network connection or video availability.",
                        fg=COLOR_DANGER
                    )

            self.dispatch_to_main(_done)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_search_youtube(self):
        query = self.ent_search.get().strip()
        if not query:
            messagebox.showinfo("Search Query", "Please enter a song title or artist to search YouTube.")
            return

        self._yt_current_query = query
        self._yt_current_page = 1
        self._yt_seen_ids.clear()
        self._yt_search_results.clear()

        for item in self.tree_tracks.get_children():
            self.tree_tracks.delete(item)

        if hasattr(self, "btn_find_more"):
            self.btn_find_more.pack_forget()

        self.lbl_status.config(text=f"Searching YouTube for '{query}'...", fg=COLOR_GOLD)
        threading.Thread(
            target=self._run_youtube_search_worker,
            args=(query, 1, True),
            daemon=True
        ).start()

    def _on_find_more_youtube(self):
        query = getattr(self, "_yt_current_query", "").strip()
        if not query:
            query = self.ent_search.get().strip() if hasattr(self, "ent_search") else ""
        if not query:
            return

        self._yt_current_page += 1
        if hasattr(self, "btn_find_more"):
            self.btn_find_more.config(text="⏳ Searching YouTube...", state="disabled")
        self.lbl_status.config(text=f"Fetching more YouTube results (page {self._yt_current_page})...", fg=COLOR_GOLD)
        threading.Thread(
            target=self._run_youtube_search_worker,
            args=(query, self._yt_current_page, False),
            daemon=True
        ).start()

    def _run_youtube_search_worker(self, query: str, page: int = 1, clear_first: bool = False):
        f_mode = self._library_filter if self._library_filter in ("singalong", "karaoke") else "all"
        results = self.downloader.search_youtube(
            query=query,
            limit=10,
            page=page,
            filter_mode=f_mode,
            seen_video_ids=self._yt_seen_ids
        )

        def _update():
            if clear_first:
                self._yt_search_results.clear()
                for item in self.tree_tracks.get_children():
                    self.tree_tracks.delete(item)

            start_idx = len(self.tree_tracks.get_children())
            added_count = 0
            last_iid = None

            for idx, r in enumerate(results):
                vid = r.get("video_id", "")
                if not vid:
                    continue
                self._yt_seen_ids.add(vid)
                self._yt_search_results[vid] = r
                dur_min = r.get("duration", 0) // 60
                dur_sec = r.get("duration", 0) % 60
                m_type = "Sing-Along 🎙️" if r.get("is_sing_along") else "Karaoke 🎵"
                row_tag = "row_odd" if (start_idx + idx) % 2 == 0 else "row_even"
                iid = f"yt_{vid}"
                if not self.tree_tracks.exists(iid):
                    self.tree_tracks.insert(
                        "",
                        "end",
                        iid=iid,
                        values=(
                            "YT",
                            r.get("title"),
                            r.get("artist"),
                            f"{dur_min}:{dur_sec:02d}",
                            m_type,
                            "☁️ (Click to fetch)"
                        ),
                        tags=(row_tag,)
                    )
                    last_iid = iid
                    added_count += 1

            if hasattr(self, "btn_find_more"):
                self.btn_find_more.pack(side="left")
                if added_count > 0:
                    self.btn_find_more.config(text="➕ Find More Results (YouTube)", state="normal")
                    self.lbl_status.config(
                        text=f"Loaded {added_count} YouTube tracks (Total: {len(self.tree_tracks.get_children())}). Double-click to download & rehearse.",
                        fg=COLOR_LIME
                    )
                else:
                    self.btn_find_more.config(text="✓ No More YouTube Results", state="disabled")
                    self.lbl_status.config(
                        text="No additional unique results found on YouTube.",
                        fg=COLOR_TEXT_MUTED
                    )

            if last_iid and page > 1:
                self.tree_tracks.see(last_iid)

        self.dispatch_to_main(_update)

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
                self.root.after_idle(lambda: messagebox.showinfo("Exercise 1 Complete", msg, parent=self.root))
            except Exception as e:
                self.root.after_idle(lambda err=str(e): self.lbl_status.config(text=f"⚠️ Siren Glide: {err}", fg=COLOR_GOLD))
                self.root.after_idle(lambda err=str(e): messagebox.showerror("Calibration Notice", err, parent=self.root))
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
                self.root.after_idle(lambda: messagebox.showinfo("Exercise 2 Complete", msg, parent=self.root))
            except Exception as e:
                self.root.after_idle(lambda err=str(e): self.lbl_status.config(text=f"⚠️ Speaking Anchor: {err}", fg=COLOR_GOLD))
                self.root.after_idle(lambda err=str(e): messagebox.showerror("Anchor Notice", err, parent=self.root))
            finally:
                self.root.after_idle(
                    lambda: self.btn_run_ex2.config(
                        text="🗣️ Record Speaking Anchor (25s)",
                        bg="#351428",
                        fg=COLOR_MAGENTA
                    )
                )
                self.root.after_idle(
                    lambda: self.lbl_status.config(text="Ready. Select a song or run calibration.")
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

        # Update warmup context with newly calibrated profile
        self._refresh_warmup_context()

    # -------------------------------------------------------------
    # AI VOCAL WARMUP STUDIO CONTROLLER METHODS
    # -------------------------------------------------------------
    def _init_warmup_studio(self):
        self._refresh_warmup_context()
        self._load_warmup_preset("3min")

    def _refresh_warmup_context(self):
        if not hasattr(self, "lbl_warmup_voice_badge") or not self.lbl_warmup_voice_badge.winfo_exists():
            return

        # Voice profile context
        p = self.vocal_profile or self.calibrator.load_profile()
        if p and (p.tessitura_min_note or p.vocal_type):
            v_type = p.vocal_type or "Contemporary Vocalist"
            min_n = p.tessitura_min_note or "C3"
            max_n = p.tessitura_max_note or "G4"
            self.lbl_warmup_voice_badge.config(
                text=f"👤 Voice: {v_type} ({min_n} ➔ {max_n})",
                fg=COLOR_CYAN
            )
        else:
            self.lbl_warmup_voice_badge.config(
                text="👤 Voice: Calibrated Vocalist (Default)",
                fg=COLOR_TEXT_MUTED
            )

        # Selected song context
        if self.selected_track:
            title = self.selected_track.get("title", "Rehearsal Song")
            artist = self.selected_track.get("artist", "")
            disp_title = f"{title[:26]}..." if len(title) > 26 else title
            song_txt = f"🎵 Track: {disp_title}" + (f" ({artist[:16]})" if artist else "")
            self.lbl_warmup_song_badge.config(text=song_txt, fg=COLOR_GOLD)
        else:
            self.lbl_warmup_song_badge.config(text="🎵 Track: No Track Loaded (Tab 2)", fg=COLOR_TEXT_MUTED)

        # Key shift & climax notes
        shift_str = f"{self.current_key_shift:+d}" if self.current_key_shift != 0 else "0"
        self.lbl_warmup_key_badge.config(text=f"🎹 Key: {shift_str} st", fg=COLOR_LIME)

        # Update instrument options sorted by vocal profile
        if hasattr(self, "cbo_instrument") and self.cbo_instrument.winfo_exists():
            v_type = p.vocal_type if p else "Contemporary Vocalist"
            inst_items = get_sorted_instruments(v_type)
            disp_vals = [i["display"] for i in inst_items]
            self._instrument_menu_map = {i["display"]: i["key"] for i in inst_items}
            self.cbo_instrument["values"] = disp_vals
            curr = self.instrument_var.get()
            if curr not in self._instrument_menu_map:
                self.instrument_var.set(disp_vals[0])
                self._selected_instrument = inst_items[0]["key"]
            else:
                self._selected_instrument = self._instrument_menu_map[curr]

    def _on_instrument_selected(self, event=None):
        chosen = self.instrument_var.get()
        self._selected_instrument = self._instrument_menu_map.get(chosen, "piano")

    def _load_warmup_preset(self, preset_key: str):
        p = self.vocal_profile or self.calibrator.load_profile()
        vocal_type = p.vocal_type if p else "Calibrated Vocalist"
        sweet_min = p.tessitura_min_note if (p and p.tessitura_min_note) else "C3"
        sweet_max = p.tessitura_max_note if (p and p.tessitura_max_note) else "G4"
        anchor = p.anchor_note if (p and p.anchor_note) else "E3"
        song_title = self.selected_track.get("title", "Your Song") if self.selected_track else "Your Song"

        orig_climax, shifted_climax = self._calculate_active_climax_note()
        shift_tag = f" (Key {self.current_key_shift:+d})" if self.current_key_shift != 0 else ""

        if preset_key == "climax":
            routine = [
                {
                    "step_num": 1,
                    "title": "Semi-Occluded Straw / Trill Glides",
                    "category": "SOVT Aerodynamic Balance",
                    "target_notes": f"{sweet_min} ➔ {anchor} (Gentle 5ths)",
                    "duration_s": 35,
                    "purpose": "Balances acoustic backpressure across vocal folds to safeguard cords before high belts.",
                    "mechanics": "Sing through a narrow straw or rolled 'Brrr' lips on gentle 5th sweeps. Keep cheeks puffed like a blowfish and abdominal wall gently engaged.",
                    "song_context": f"Safeguards folds from friction during dynamic surges in '{song_title}'."
                },
                {
                    "step_num": 2,
                    "title": "Pharyngeal Twang & Narrow Pharynx ('Nay / Gee')",
                    "category": "Upper Formant Focus",
                    "target_notes": f"{anchor} ➔ {sweet_max}",
                    "duration_s": 40,
                    "purpose": "Narrows the epilarynx tube to add intense vocal ring without pushing unnecessary breath pressure.",
                    "mechanics": "Sing a playful, brassy 'Nay-nay-nay' with a high tongue back and relaxed jaw. Keep the tone bright, laser-focused, and centered.",
                    "song_context": f"Prepares your mix voice for the melodic climb in '{song_title}'."
                },
                {
                    "step_num": 3,
                    "title": "Song Climax Target Sirens ('Ooo ➔ Aaa')",
                    "category": "High Belt & Range Extension",
                    "target_notes": f"{anchor} ➔ {shifted_climax}{shift_tag}",
                    "duration_s": 45,
                    "purpose": "Rehearses smooth vocal register navigation up to the peak notes demanded by the track.",
                    "mechanics": "Glide smoothly upward on a tall 'Ooo', morphing cleanly into 'Aaa' as you touch your ceiling note. Drop the jaw and lean into diaphragm support.",
                    "song_context": f"Targets cords specifically to conquer the high climax notes of '{song_title}' (Song peak: {orig_climax} transposed to {shifted_climax}{shift_tag})."
                },
                {
                    "step_num": 4,
                    "title": "Mixed Voice Belt Release ('Mum ➔ May')",
                    "category": "Cord Closure & Dynamic Recovery",
                    "target_notes": f"{anchor} (Resonant Center)",
                    "duration_s": 30,
                    "purpose": "Stabilizes balanced thyroarytenoid / cricothyroid cord engagement to prevent post-belt fatigue.",
                    "mechanics": "Execute sustained 3-note phrases on 'Mum... May...'. Feel effortless cord closure without raising your larynx or tightening your neck muscles.",
                    "song_context": f"Ensures sustained stamina across all rehearsals of '{song_title}'."
                }
            ]
        elif preset_key == "formant":
            routine = [
                {
                    "step_num": 1,
                    "title": "Forward Mask & Nasal Resonance Buzz ('Ming / Ring')",
                    "category": "Acoustic Ring (2.8–3.4 kHz)",
                    "target_notes": f"{sweet_min} ➔ {anchor}",
                    "duration_s": 35,
                    "purpose": "Places acoustic focus into the nasal bone and frontal mask for natural acoustic amplification.",
                    "mechanics": "Sustain a resonant 'Ming-ng-ng' feeling intense vibration right between your eyes and across your bridge of nose. Do not push volume.",
                    "song_context": f"Cuts through dense backing tracks in '{song_title}' without vocal strain."
                },
                {
                    "step_num": 2,
                    "title": "High Formant Twang Triads ('Kee-Kee-Kee')",
                    "category": "Singer's Formant Spike",
                    "target_notes": f"{anchor} ➔ {sweet_max}",
                    "duration_s": 40,
                    "purpose": "Spikes the 2.8–3.4 kHz frequency band where human hearing is sharpest.",
                    "mechanics": "Sing short, energetic staccato bursts on 'Kee-Kee-Kee' up and down a triad. Keep each note crisp, pointed, and resonant.",
                    "song_context": "Boosts your Formant Ratio metric into the 1.50+ Professional Singer tier."
                },
                {
                    "step_num": 3,
                    "title": "Wide Pharynx Vowel Morphing ('Ee ➔ Oh ➔ Ah')",
                    "category": "Resonant Timbre Uniformity",
                    "target_notes": f"{anchor} (5-Tone Scale)",
                    "duration_s": 40,
                    "purpose": "Preserves identical acoustic resonance while transitioning through changing vowels.",
                    "mechanics": "Sing a descending 5-tone legato line changing vowels each repetition. Keep the pharynx spacious and open behind the soft palate.",
                    "song_context": f"Polishes diction clarity and eliminates sibilance harshness in '{song_title}'."
                },
                {
                    "step_num": 4,
                    "title": "Dynamic Swell (Messa di Voce)",
                    "category": "Subglottic Pressure Control",
                    "target_notes": f"{anchor} (Sustained Sweet Spot)",
                    "duration_s": 35,
                    "purpose": "Masters gradual crescendo from pianissimo to forte and back without pitch drift.",
                    "mechanics": "On your comfortable anchor note, sing softly (pp), gradually swell to full room resonance (f) over 4 counts, then smoothly diminish (pp).",
                    "song_context": f"Maximizes your Energy Steadiness & Dynamic Control scores in '{song_title}'."
                }
            ]
        elif preset_key == "tension":
            routine = [
                {
                    "step_num": 1,
                    "title": "Sigh Drop & Diaphragm Release",
                    "category": "Laryngeal Decompression",
                    "target_notes": f"{anchor} ➔ Floor",
                    "duration_s": 30,
                    "purpose": "Releases involuntary throat constriction and resets laryngeal baseline height.",
                    "mechanics": "Take an open yawn-breath through mouth and nose, then gently let out a descending sigh from your chest to your lowest speaking rumble.",
                    "song_context": "Resets throat muscles and releases pre-performance tension."
                },
                {
                    "step_num": 2,
                    "title": "Jaw & Tongue Root De-constriction ('Kah-Lah-Mah')",
                    "category": "Articulatory Relaxation",
                    "target_notes": f"{sweet_min} (Comfortable 3rds)",
                    "duration_s": 35,
                    "purpose": "Eliminates jaw locking and tongue root cramping during rapid lyric passages.",
                    "mechanics": "Drop your jaw loose (two fingers vertical spacing). Sing 'Kah-Lah-Mah' allowing only the tongue tip and lips to move while the jaw remains still.",
                    "song_context": f"Maintains crisp articulation across fast phrases in '{song_title}'."
                },
                {
                    "step_num": 3,
                    "title": "Soft Palate Yawn-Sigh ('Whoooo')",
                    "category": "Pharyngeal Expansion",
                    "target_notes": f"{sweet_max} ➔ {sweet_min}",
                    "duration_s": 35,
                    "purpose": "Lifts the soft palate to create internal dome space, eliminating nasal pinch.",
                    "mechanics": "Inhale feeling cool air at the back of your throat. Sing a hollow, breath-supported 'Whooo' gliding downwards like an owl.",
                    "song_context": "Removes nasal harshness and creates warm, rounded vocal acoustics."
                },
                {
                    "step_num": 4,
                    "title": "Gentle Chest Rumble & Cord Cool-Down ('Zzz / Vvv')",
                    "category": "Vocal Cord Soothing",
                    "target_notes": f"{sweet_min} (Floor Grounding)",
                    "duration_s": 30,
                    "purpose": "Massages vocal folds with micro-vibrations to clear fatigue and prevent vocal fry.",
                    "mechanics": "Buzz loose lips on 'Vvv' or voiced 'Zzz' at your bottom comfortable pitch. Feel the warm vibrations massaging your lower chest.",
                    "song_context": f"Leaves cords calm, supple, and energized for '{song_title}'."
                }
            ]
        else: # Default 3-Minute Wakeup
            routine = self.offline_coach._generate_rule_based_warmup(
                p.to_dict() if p else None,
                self.selected_track,
                self.current_key_shift,
                climax_note=shifted_climax
            )

        self._warmup_routine = routine
        self._warmup_step_idx = 0
        self._display_warmup_step(0)
        self.lbl_warmup_status.config(text=f"Loaded '{preset_key.upper()}' routine preset.", fg=COLOR_CYAN)

    def _on_generate_ai_warmup(self):
        if self._is_generating_warmup:
            return

        self._is_generating_warmup = True
        self.btn_gen_warmup.config(state="disabled", text="⏳ Maestro Tiki is Composing Routine...")
        self.lbl_warmup_status.config(
            text="✨ Maestro Tiki (Offline Qwen LLM) is formulating progressive exercises tailored to your voice & song...",
            fg=COLOR_GOLD
        )

        def _worker():
            p_dict = self.vocal_profile.to_dict() if self.vocal_profile else None
            orig_climax, shifted_climax = self._calculate_active_climax_note()
            routine = self.offline_coach.generate_warmup_routine(
                profile_data=p_dict,
                track_info=self.selected_track,
                key_shift=self.current_key_shift,
                climax_note=shifted_climax
            )

            def _done():
                self._is_generating_warmup = False
                self.btn_gen_warmup.config(state="normal", text="✨ Generate Song-Adaptive Routine (Offline AI)")
                if routine and len(routine) >= 2:
                    self._warmup_routine = routine
                    self._warmup_step_idx = 0
                    self._display_warmup_step(0)
                    self.lbl_warmup_status.config(
                        text="✅ Tailored offline warmup routine ready! Exercise 1 active.",
                        fg=COLOR_LIME
                    )
                else:
                    self.lbl_warmup_status.config(
                        text="⚠️ Loaded pedagogical fallback routine.",
                        fg=COLOR_GOLD
                    )
                    self._load_warmup_preset("3min")

            self.root.after_idle(_done)

        threading.Thread(target=_worker, daemon=True).start()

    def _display_warmup_step(self, step_idx: int):
        if not self._warmup_routine or not (0 <= step_idx < len(self._warmup_routine)):
            return

        # Stop any active note or pattern audio playback
        tone_synth.stop_playback()
        if self._is_playing_pattern:
            self._is_playing_pattern = False
            self.btn_play_pattern.config(text="▶ Play Sequence", bg="#2a143f", fg=COLOR_CYAN)

        # Hide previous take evaluation drawer when changing steps
        self._dismiss_warmup_evaluation()

        self._warmup_step_idx = step_idx
        step = self._warmup_routine[step_idx]

        self.lbl_warmup_step_num.config(text=f"EXERCISE {step_idx + 1} OF {len(self._warmup_routine)}")
        self.lbl_warmup_category.config(text=str(step.get("category", "VOCAL CONDITIONING")).upper())
        self.lbl_warmup_title.config(text=str(step.get("title", f"Exercise {step_idx + 1}")))
        self.lbl_warmup_song_context.config(text=f"🎵 Song Connection: {step.get('song_context', '')}")
        self.lbl_warmup_purpose.config(text=f"🎯 Purpose: {step.get('purpose', '')}")
        self.lbl_warmup_mechanics.config(text=f"🛠️ Mechanics: {step.get('mechanics', '')}")

        # Parse target notes into interactive clickable audio chips
        raw_notes = step.get("target_notes", "")
        parsed_notes = parse_target_notes(raw_notes)
        self._active_target_notes = parsed_notes

        # Rebuild chip container
        for child in self.warmup_chips_container.winfo_children():
            child.destroy()
        self._warmup_chip_widgets = []

        if parsed_notes:
            for idx, note in enumerate(parsed_notes):
                btn_note = tk.Button(
                    self.warmup_chips_container,
                    text=f"🔊 {note}",
                    font=("Helvetica", 8, "bold"),
                    bg="#1f0e33",
                    fg=COLOR_CYAN,
                    activebackground="#3d1b64",
                    activeforeground="#ffffff",
                    bd=1,
                    relief="solid",
                    padx=6,
                    pady=2,
                    command=lambda n=note: (self._reset_warmup_chips(), tone_synth.play_note(n, self._selected_instrument))
                )
                btn_note.pack(side="left", padx=2)
                self._warmup_chip_widgets.append(btn_note)

                if idx < len(parsed_notes) - 1:
                    tk.Label(
                        self.warmup_chips_container,
                        text="➔",
                        font=("Helvetica", 8),
                        fg=COLOR_TEXT_MUTED,
                        bg="#10071a"
                    ).pack(side="left", padx=1)

            self.btn_play_pattern.config(state="normal")
        else:
            # Fallback label for non-note descriptive steps
            fallback_lbl = tk.Label(
                self.warmup_chips_container,
                text=raw_notes or "Comfort Sweet Spot",
                font=("Helvetica", 9, "bold"),
                fg=COLOR_GOLD,
                bg="#10071a"
            )
            fallback_lbl.pack(side="left")
            self.btn_play_pattern.config(state="disabled")

        # Stepper navigation button states
        self.btn_warmup_prev.config(state="normal" if step_idx > 0 else "disabled")
        self.btn_warmup_next.config(state="normal" if step_idx < len(self._warmup_routine) - 1 else "disabled")

        # Reset timer for this step
        self._reset_warmup_timer()

        # Update canvas scrollregion
        self.root.update_idletasks()
        if hasattr(self, "calib_canvas"):
            self.calib_canvas.configure(scrollregion=self.calib_canvas.bbox("all"))

    def _reset_warmup_chips(self):
        """Resets all target note chips to standard neon cyan appearance."""
        for chip in self._warmup_chip_widgets:
            if chip and chip.winfo_exists():
                chip.config(bg="#1f0e33", fg=COLOR_CYAN)

    def _on_play_warmup_pattern(self):
        """Plays the full musical interval sequence at steady tempo with live chip lighting."""
        if self._is_playing_pattern:
            tone_synth.stop_playback()
            self._is_playing_pattern = False
            self.btn_play_pattern.config(text="▶ Play Sequence", bg="#2a143f", fg=COLOR_CYAN)
            self._reset_warmup_chips()
            return

        if not self._active_target_notes:
            return

        self._is_playing_pattern = True
        self.btn_play_pattern.config(text="⏹️ Stop Sequence", bg=COLOR_GOLD, fg="#000000")

        def _on_step(idx: int, note: str):
            def _ui():
                # Highlight active playing note chip in neon gold
                for c_idx, chip in enumerate(self._warmup_chip_widgets):
                    if chip and chip.winfo_exists():
                        if c_idx == idx:
                            chip.config(bg="#ffd166", fg="#000000")
                        else:
                            chip.config(bg="#1f0e33", fg=COLOR_CYAN)
            self.dispatch_to_main(_ui)

        def _on_finished():
            def _ui():
                self._is_playing_pattern = False
                if hasattr(self, "btn_play_pattern") and self.btn_play_pattern.winfo_exists():
                    self.btn_play_pattern.config(text="▶ Play Sequence", bg="#2a143f", fg=COLOR_CYAN)
                self._reset_warmup_chips()
            self.dispatch_to_main(_ui)

        tone_synth.play_pattern(
            notes=self._active_target_notes,
            instrument=self._selected_instrument,
            bpm=47.5,
            on_step_cb=_on_step,
            on_finished_cb=_on_finished
        )

    def _on_toggle_warmup_recording(self):
        """Starts or stops warmup take recording with automated evaluation."""
        if self._is_recording_warmup:
            self._finish_warmup_recording()
        else:
            self._start_warmup_recording()

    def _start_warmup_recording(self):
        # Stop any active reference tones
        tone_synth.stop_playback()
        if self._is_playing_pattern:
            self._is_playing_pattern = False
            self.btn_play_pattern.config(text="▶ Play Sequence", bg="#2a143f", fg=COLOR_CYAN)

        self._dismiss_warmup_evaluation()
        self._is_recording_warmup = True

        self.btn_warmup_record.config(text="⏹️ Stop Early & Analyze", bg=COLOR_GOLD, fg="#000000")
        self.btn_warmup_timer_toggle.config(state="disabled")

        # Start microphone recording to temporary take file
        take_path = TEMP_TAKES_DIR / "calib_warmup_take.wav"
        self.recorder.start_recording(take_path)

        # Synchronize countdown timer
        dur = 35
        if self._warmup_routine and 0 <= self._warmup_step_idx < len(self._warmup_routine):
            dur = int(self._warmup_routine[self._warmup_step_idx].get("duration_s", 35))

        self._warmup_timer_seconds = dur
        self._warmup_timer_running = True
        self.lbl_warmup_status.config(
            text="🎙️ Recording warmup take... sing with the target notes!",
            fg=COLOR_GOLD
        )
        self._warmup_timer_tick()

    def _finish_warmup_recording(self):
        if not self._is_recording_warmup:
            return

        self._is_recording_warmup = False
        self._warmup_timer_running = False

        if self._warmup_timer_job:
            self.root.after_cancel(self._warmup_timer_job)
            self._warmup_timer_job = None

        self.recorder.stop_recording()

        self.btn_warmup_record.config(text="🎙️ Record & Check Take", bg="#ff2a85", fg="#ffffff")
        self.btn_warmup_timer_toggle.config(state="normal", text="▶ Start Timer")
        self.lbl_warmup_status.config(
            text="✨ Maestro Tiki is evaluating your warmup take pitch alignment...",
            fg=COLOR_CYAN
        )

        def _worker():
            take_path = TEMP_TAKES_DIR / "calib_warmup_take.wav"
            res = evaluate_warmup_take(take_path, self._active_target_notes)
            self.dispatch_to_main(lambda: self._display_warmup_evaluation(res))

        threading.Thread(target=_worker, daemon=True).start()

    def _display_warmup_evaluation(self, eval_res: Dict[str, Any]):
        if not eval_res.get("success"):
            err_msg = eval_res.get("error", "Evaluation failed.")
            self.lbl_warmup_status.config(text=f"⚠️ {err_msg}", fg=COLOR_GOLD)
            return

        hit = eval_res.get("notes_hit_count", 0)
        tot = eval_res.get("total_targets", 0)
        pct = int(eval_res.get("hit_ratio", 0) * 100)
        score_color = COLOR_LIME if pct >= 75 else (COLOR_GOLD if pct >= 50 else COLOR_DANGER)

        self.lbl_eval_score.config(
            text=f"Target Pitch Accuracy: {hit} / {tot} Notes Hit ({pct}%)",
            fg=score_color
        )
        self.lbl_eval_range.config(
            text=f"Voiced Span: {eval_res.get('min_note', '--')} ➔ {eval_res.get('max_note', '--')}  •  Pitch Steadiness: {eval_res.get('steadiness_pct', 0):.0f}%"
        )

        # Clear and populate result chips
        for child in self.eval_chips_frame.winfo_children():
            child.destroy()

        for item in eval_res.get("evaluations", []):
            note = item["note"]
            is_h = item["hit"]
            c_err = item["cents_err"]
            held = item["duration_held"]

            icon = "✅" if is_h else "⚠️"
            sign = f"{c_err:+0.0f}¢"
            chip_txt = f"{icon} {note} ({sign}, held {held:.1f}s)" if is_h else f"{icon} {note} ({sign})"
            c_bg = "#1b3326" if is_h else "#3a1c18"
            c_fg = COLOR_LIME if is_h else COLOR_GOLD

            lbl_chip = tk.Label(
                self.eval_chips_frame,
                text=chip_txt,
                font=("Helvetica", 8, "bold"),
                bg=c_bg,
                fg=c_fg,
                bd=1,
                relief="solid",
                padx=8,
                pady=3
            )
            lbl_chip.pack(side="left", padx=3)

        self.lbl_eval_diagnosis.config(text=eval_res.get("coach_diagnosis", ""))
        self.warmup_eval_card.pack(fill="x", pady=(10, 4))
        self.lbl_warmup_status.config(
            text="✅ Warmup evaluation ready! Check feedback below.",
            fg=COLOR_LIME
        )

        self.root.update_idletasks()
        if hasattr(self, "calib_canvas"):
            self.calib_canvas.configure(scrollregion=self.calib_canvas.bbox("all"))

    def _dismiss_warmup_evaluation(self):
        if hasattr(self, "warmup_eval_card") and self.warmup_eval_card.winfo_exists():
            self.warmup_eval_card.pack_forget()
        self.root.update_idletasks()
        if hasattr(self, "calib_canvas"):
            self.calib_canvas.configure(scrollregion=self.calib_canvas.bbox("all"))

    def _toggle_warmup_timer(self):
        if self._warmup_timer_running:
            if self._warmup_timer_job:
                self.root.after_cancel(self._warmup_timer_job)
                self._warmup_timer_job = None
            self._warmup_timer_running = False
            self.btn_warmup_timer_toggle.config(text="▶ Resume Timer", bg=COLOR_CYAN, fg="#000000")
        else:
            if self._warmup_timer_seconds <= 0:
                self._reset_warmup_timer()
            self._warmup_timer_running = True
            self.btn_warmup_timer_toggle.config(text="⏸ Pause", bg=COLOR_GOLD, fg="#000000")
            self._warmup_timer_tick()

    def _reset_warmup_timer(self):
        if self._warmup_timer_job:
            self.root.after_cancel(self._warmup_timer_job)
            self._warmup_timer_job = None
        self._warmup_timer_running = False

        dur = 35
        if self._warmup_routine and 0 <= self._warmup_step_idx < len(self._warmup_routine):
            dur = int(self._warmup_routine[self._warmup_step_idx].get("duration_s", 35))

        self._warmup_timer_seconds = dur
        m = dur // 60
        s = dur % 60
        self.lbl_warmup_timer.config(text=f"{m:02d}:{s:02d}", fg=COLOR_CYAN)
        self.btn_warmup_timer_toggle.config(text="▶ Start Timer", state="normal", bg=COLOR_CYAN, fg="#000000")

    def _warmup_timer_tick(self):
        if not self._warmup_timer_running:
            return

        if self._warmup_timer_seconds > 0:
            self._warmup_timer_seconds -= 1
            m = self._warmup_timer_seconds // 60
            s = self._warmup_timer_seconds % 60
            color = COLOR_CYAN if self._warmup_timer_seconds > 5 else COLOR_GOLD
            self.lbl_warmup_timer.config(text=f"{m:02d}:{s:02d}", fg=color)
            self._warmup_timer_job = self.root.after(1000, self._warmup_timer_tick)
        else:
            self._warmup_timer_running = False
            self.lbl_warmup_timer.config(text="00:00", fg=COLOR_LIME)

            if self._is_recording_warmup:
                # Automatic recording stop on timer completion
                self._finish_warmup_recording()
            else:
                self.btn_warmup_timer_toggle.config(
                    text="✓ Step Complete!",
                    state="disabled",
                    bg="#1b3d2b",
                    fg=COLOR_LIME
                )

    def _prev_warmup_step(self):
        if self._warmup_step_idx > 0:
            self._display_warmup_step(self._warmup_step_idx - 1)

    def _next_warmup_step(self):
        if self._warmup_step_idx < len(self._warmup_routine) - 1:
            self._display_warmup_step(self._warmup_step_idx + 1)

    def _open_settings_dialog(self):
        """Opens the Tiki-themed Studio Settings & Storage Configuration modal dialog."""
        cfg = load_studio_config()

        dlg = tk.Toplevel(self.root)
        dlg.title("⚙️ Studio Settings & Storage Configuration")
        dlg.geometry("580x710")
        dlg.configure(bg=COLOR_BG_MAIN)
        dlg.transient(self.root)
        dlg.grab_set()

        # Center over main window
        dlg.update_idletasks()
        rx = self.root.winfo_rootx()
        ry = self.root.winfo_rooty()
        rw = self.root.winfo_width()
        rh = self.root.winfo_height()
        dlg.geometry(f"+{rx + max(0, (rw - 580) // 2)}+{ry + max(0, (rh - 710) // 2)}")

        # Header Frame
        hdr = tk.Frame(dlg, bg="#1a1025", padx=18, pady=14)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text="⚙️ STUDIO STORAGE & SYSTEM PREFERENCES",
            font=("Helvetica", 11, "bold"),
            fg=COLOR_GOLD,
            bg="#1a1025"
        ).pack(anchor="w")
        tk.Label(
            hdr,
            text="Configure standalone vs integrated storage, audio targets, and library paths.",
            font=("Helvetica", 8),
            fg=COLOR_TEXT_MUTED,
            bg="#1a1025"
        ).pack(anchor="w", pady=(2, 0))

        # Main Form Container
        form = tk.Frame(dlg, bg=COLOR_BG_MAIN, padx=20, pady=12)
        form.pack(fill="both", expand=True)

        # 1. Storage Mode Card
        mode_card = tk.LabelFrame(
            form,
            text=" 📦 STORAGE ARCHITECTURE ",
            font=("Helvetica", 8, "bold"),
            fg=COLOR_CYAN,
            bg="#150a22",
            padx=12,
            pady=8
        )
        mode_card.pack(fill="x", pady=(0, 10))

        mode_var = tk.StringVar(value=cfg.get("storage_mode", "integrated"))

        rb_int = tk.Radiobutton(
            mode_card,
            text="🔗 Integrated with Tiki Karaoke (Share karaoke.db catalog & media folders)",
            variable=mode_var,
            value="integrated",
            font=("Helvetica", 9),
            fg=COLOR_TEXT_MAIN,
            bg="#150a22",
            selectcolor="#0d0814",
            activebackground="#150a22",
            activeforeground=COLOR_CYAN
        )
        rb_int.pack(anchor="w", pady=2)

        rb_std = tk.Radiobutton(
            mode_card,
            text="🏝️ Standalone Rehearsal Lounge (Custom independent library & database)",
            variable=mode_var,
            value="standalone",
            font=("Helvetica", 9),
            fg=COLOR_TEXT_MAIN,
            bg="#150a22",
            selectcolor="#0d0814",
            activebackground="#150a22",
            activeforeground=COLOR_CYAN
        )
        rb_std.pack(anchor="w", pady=2)

        # 2. Directory Paths Card
        paths_card = tk.LabelFrame(
            form,
            text=" 📁 DIRECTORY PATHS ",
            font=("Helvetica", 8, "bold"),
            fg=COLOR_GOLD,
            bg="#150a22",
            padx=12,
            pady=8
        )
        paths_card.pack(fill="x", pady=(0, 10))

        def _make_folder_row(parent, label_text, default_val):
            f = tk.Frame(parent, bg="#150a22")
            f.pack(fill="x", pady=4)
            tk.Label(f, text=label_text, font=("Helvetica", 8, "bold"), fg=COLOR_TEXT_MUTED, bg="#150a22").pack(anchor="w")
            sub_f = tk.Frame(f, bg="#150a22")
            sub_f.pack(fill="x", pady=(2, 0))
            ent = tk.Entry(sub_f, font=("Helvetica", 9), bg="#0d0814", fg=COLOR_TEXT_MAIN, insertbackground=COLOR_CYAN, bd=1, relief="solid")
            ent.pack(side="left", fill="x", expand=True, padx=(0, 6))
            ent.insert(0, str(default_val))
            def _browse():
                chosen = filedialog.askdirectory(title=f"Select {label_text}", initialdir=ent.get() or str(Path.home()))
                if chosen:
                    ent.delete(0, tk.END)
                    ent.insert(0, chosen)
            btn = tk.Button(sub_f, text="Browse...", font=("Helvetica", 8, "bold"), bg="#25123d", fg=COLOR_CYAN, padx=8, pady=2, command=_browse)
            btn.pack(side="right")
            return ent

        ent_lib = _make_folder_row(paths_card, "Song Library Directory (tracks & sing_along):", cfg.get("library_dir", ""))
        ent_data = _make_folder_row(paths_card, "Database Directory (karaoke.db location):", cfg.get("data_dir", ""))
        ent_rec = _make_folder_row(paths_card, "Performance Takes / Recordings Directory:", cfg.get("recordings_dir", ""))

        # 3. Audio & Video Standards Card
        media_card = tk.LabelFrame(
            form,
            text=" 🎛️ AUDIO & VIDEO TARGETS ",
            font=("Helvetica", 8, "bold"),
            fg=COLOR_LIME,
            bg="#150a22",
            padx=12,
            pady=8
        )
        media_card.pack(fill="x", pady=(0, 10))

        # Quality Row
        q_row = tk.Frame(media_card, bg="#150a22")
        q_row.pack(fill="x", pady=2)
        tk.Label(q_row, text="Video Quality Profile:", font=("Helvetica", 8, "bold"), fg=COLOR_TEXT_MUTED, bg="#150a22").pack(side="left", padx=(0, 10))
        cbo_q = ttk.Combobox(
            q_row,
            values=["pro (1080p Full HD AVC1)", "balanced_720p (720p HD)", "compact_480p (480p SD)"],
            state="readonly",
            width=28
        )
        cur_q = str(cfg.get("video_quality_profile", "pro")).lower()
        if "720" in cur_q:
            cbo_q.current(1)
        elif "480" in cur_q or "compact" in cur_q:
            cbo_q.current(2)
        else:
            cbo_q.current(0)
        cbo_q.pack(side="left")

        # LUFS Row
        lufs_row = tk.Frame(media_card, bg="#150a22")
        lufs_row.pack(fill="x", pady=4)
        tk.Label(lufs_row, text="Target Loudness (LUFS):", font=("Helvetica", 8, "bold"), fg=COLOR_TEXT_MUTED, bg="#150a22").pack(side="left", padx=(0, 10))
        cbo_lufs = ttk.Combobox(
            lufs_row,
            values=["-23.0 LUFS (EBU R128 Professional Default)", "-16.0 LUFS (Standard Web Streaming)"],
            state="readonly",
            width=28
        )
        cur_lufs = float(cfg.get("target_lufs", -16.0))
        cbo_lufs.current(0 if cur_lufs <= -20.0 else 1)
        cbo_lufs.pack(side="left")

        # 4. Offline AI Vocal Coach Section
        ai_card = tk.LabelFrame(
            form,
            text=" 🤖 OFFLINE AI VOCAL COACH MODULE ",
            font=("Helvetica", 8, "bold"),
            fg=COLOR_GOLD,
            bg="#150a22",
            padx=12,
            pady=8
        )
        ai_card.pack(fill="x", pady=(8, 10))

        installed_ai = self.offline_coach.get_installed_model()
        if installed_ai:
            p = installed_ai.get("path")
            size_mb = p.stat().st_size / (1024 * 1024) if p and p.exists() else 0
            tk.Label(
                ai_card,
                text=f"Active: {installed_ai.get('name')} ({size_mb / 1024:.2f} GB)",
                font=("Helvetica", 9, "bold"),
                fg=COLOR_LIME,
                bg="#150a22"
            ).pack(side="left")

            def _settings_uninstall():
                dlg.destroy()
                self._open_manage_models_dialog()

            tk.Button(
                ai_card,
                text="🗑️ Manage / Uninstall Model",
                font=("Helvetica", 8, "bold"),
                bg="#401018",
                fg=COLOR_DANGER,
                padx=10,
                pady=3,
                command=_settings_uninstall
            ).pack(side="right")
        else:
            tk.Label(
                ai_card,
                text="Status: No model installed (Using built-in rules)",
                font=("Helvetica", 8),
                fg=COLOR_TEXT_MUTED,
                bg="#150a22"
            ).pack(side="left")

            def _settings_download():
                dlg.destroy()
                self.notebook.select(self.tab_studio)

            tk.Button(
                ai_card,
                text="📥 Install AI Model",
                font=("Helvetica", 8, "bold"),
                bg="#2a143f",
                fg=COLOR_CYAN,
                padx=10,
                pady=3,
                command=_settings_download
            ).pack(side="right")

        # 5. Rehearsal Progression History Card
        hist_card = tk.LabelFrame(
            form,
            text=" 📈 REHEARSAL PROGRESSION HISTORY ",
            font=("Helvetica", 8, "bold"),
            fg=COLOR_GOLD,
            bg="#150a22",
            padx=12,
            pady=8
        )
        hist_card.pack(fill="x", pady=(0, 10))

        hist_stats = self.progression_db.get_lifetime_stats()
        tk.Label(
            hist_card,
            text=f"Total: {hist_stats['total_takes']} takes ({hist_stats['unique_songs']} songs) • Auto-pruning: max 15/song",
            font=("Helvetica", 8),
            fg=COLOR_TEXT_MAIN,
            bg="#150a22"
        ).pack(side="left")

        def _clear_history():
            if messagebox.askyesno("Reset Progression History", "Are you sure you want to clear your saved rehearsal progression history?", parent=dlg):
                self.progression_db.clear_history()
                dlg.destroy()
                messagebox.showinfo("History Cleared", "Rehearsal progression history has been reset.", parent=self.root)

        tk.Button(
            hist_card,
            text="🗑️ Reset History",
            font=("Helvetica", 8, "bold"),
            bg="#401018",
            fg=COLOR_DANGER,
            padx=10,
            pady=3,
            command=_clear_history
        ).pack(side="right")

        # Status / Feedback label
        lbl_msg = tk.Label(form, text="", font=("Helvetica", 8, "bold"), fg=COLOR_DANGER, bg=COLOR_BG_MAIN)
        lbl_msg.pack(anchor="w")

        # Bottom Buttons Bar
        btn_bar = tk.Frame(dlg, bg="#1a1025", padx=18, pady=12)
        btn_bar.pack(fill="x")

        def _on_save_settings():
            new_lib = ent_lib.get().strip()
            new_data = ent_data.get().strip()
            new_rec = ent_rec.get().strip()

            if not new_lib or not new_data:
                lbl_msg.config(text="Please specify valid Library and Database directories.", fg=COLOR_DANGER)
                return

            # Ensure paths exist
            try:
                Path(new_lib).mkdir(parents=True, exist_ok=True)
                Path(new_data).mkdir(parents=True, exist_ok=True)
                if new_rec:
                    Path(new_rec).mkdir(parents=True, exist_ok=True)
            except Exception as e:
                lbl_msg.config(text=f"Failed to create directories: {e}", fg=COLOR_DANGER)
                return

            q_sel = cbo_q.get().lower()
            new_q = "balanced_720p" if "720" in q_sel else ("compact_480p" if "480" in q_sel else "pro")
            new_lufs = -23.0 if "-23" in cbo_lufs.get() else -16.0

            updated_cfg = {
                "storage_mode": mode_var.get(),
                "library_dir": new_lib,
                "data_dir": new_data,
                "recordings_dir": new_rec or str(Path(new_lib) / "recordings"),
                "target_lufs": new_lufs,
                "video_quality_profile": new_q
            }

            ok = save_studio_config(updated_cfg)
            if not ok:
                lbl_msg.config(text="Failed to write configuration file.", fg=COLOR_DANGER)
                return

            # Update live instances
            self._storage_mode = updated_cfg["storage_mode"]
            new_db_path = Path(new_data) / "karaoke.db"
            self.db = TikiDatabaseBridge(db_path=new_db_path)
            self.downloader = TrackDownloader(db_bridge=self.db)

            # Update Header Indicator
            mode_text = "● Tiki Synced" if self._storage_mode == "integrated" else "● Standalone"
            self.db_status_lbl.config(
                text=mode_text,
                fg=COLOR_LIME if self.db.is_connected() else COLOR_GOLD
            )

            # Refresh library view
            self._on_search_library()

            self.lbl_status.config(
                text=f"✅ Settings applied! Using {self._storage_mode.title()} mode with database at {new_db_path.name}.",
                fg=COLOR_LIME
            )
            dlg.destroy()

        btn_cancel = tk.Button(
            btn_bar,
            text="Cancel",
            bg="#2a1545",
            fg=COLOR_TEXT_MUTED,
            font=("Helvetica", 9),
            bd=0,
            padx=14,
            pady=6,
            command=dlg.destroy
        )
        btn_cancel.pack(side="right", padx=(8, 0))

        btn_save = tk.Button(
            btn_bar,
            text="💾 Save & Apply Settings",
            bg=COLOR_CYAN,
            fg="#000000",
            font=("Helvetica", 9, "bold"),
            bd=0,
            padx=16,
            pady=6,
            command=_on_save_settings
        )
        btn_save.pack(side="right")

    def _on_close(self):
        tone_synth.stop_playback()
        if hasattr(self, "_warmup_timer_job") and self._warmup_timer_job:
            self.root.after_cancel(self._warmup_timer_job)
            self._warmup_timer_job = None
        if hasattr(self, "_is_recording_warmup") and self._is_recording_warmup:
            self.recorder.stop_recording()
        self.player.stop()
        self.recorder.stop_recording()
        self.root.destroy()

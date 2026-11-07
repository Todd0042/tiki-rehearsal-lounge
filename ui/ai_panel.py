"""
Tiki Rehearsal Lounge - Maestro Tiki AI Studio (optional side panel)
====================================================================
A self-contained Tkinter panel that owns *every* generative-AI feature of the studio:

  * the installer / model card (shown only when no offline model is installed)
  * the in-depth AI debrief of the latest take
  * the creative adaptation notes (melodic style, genre feel, lyric/phrasing changes)
  * the interactive "Maestro Tiki" chat

Architecture rules (enforced here, see README -> "Optional AI Side Panel"):

1. ONE-WAY DATA FLOW. The panel never receives live DSP objects. The core pipeline writes the
   take partitions (summary.json, acoustic_core.json, lyrics_alignment.json, ...) and then calls
   `AIStudioPanel.load_take(take_dir)`. The panel reads those partitions from disk, generates
   qualitative text and stores it next to them (coach_debrief.txt, ai_creative_notes.txt). Nothing
   the AI produces is ever written back into the numeric partitions or the Raw Analysis report.
2. NON-BLOCKING. All partition reads and LLM calls run on daemon worker threads. Results are
   marshalled back through the host's thread-safe `dispatch` queue and are discarded if the panel
   was suspended/destroyed or a newer take superseded them (epoch + take-generation tokens).
3. REMOVABLE. The host constructs this panel lazily and may `suspend()` / forget it at any time;
   the core studio has no references to any widget in this module.
"""

from __future__ import annotations
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import ttk, messagebox

from config import (
    COLOR_BG_CARD,
    COLOR_BG_MAIN,
    COLOR_CYAN,
    COLOR_GOLD,
    COLOR_LIME,
    COLOR_MAGENTA,
    COLOR_TEXT_MAIN,
    COLOR_TEXT_MUTED,
)
from ai.offline_coach import OfflineVocalCoach, AVAILABLE_MODELS
from storage.take_partitions import (
    load_take_report_and_lyrics,
    load_take_summary,
    load_take_toc,
)

DEBRIEF_CACHE_FILE = "coach_debrief.txt"
CREATIVE_CACHE_FILE = "ai_creative_notes.txt"

PANEL_BG = "#120a1d"
CARD_BG = "#1a0f2a"
INPUT_BG = "#0d0814"

QUICK_PROMPTS: List[Tuple[str, str, str]] = [
    ("🎯 Fix Pitch Droop", COLOR_TEXT_MAIN, "How can I fix pitch droop at the ends of my phrases?"),
    ("⏱️ Timing & Rhythm", COLOR_CYAN, "How was my line onset timing and rhythm? Did I start any sentences late or drag?"),
    ("🎹 Key Shift", COLOR_GOLD, "Would changing the key shift make this song fit my voice better?"),
    ("✨ Resonance", COLOR_LIME, "How can I get more singer's formant ring without throat strain?"),
]


class AIStudioPanel(tk.Frame):
    """Right-hand AI-only panel. Create it lazily; it is inert until `load_take` is called."""

    def __init__(
        self,
        parent: tk.Misc,
        coach: OfflineVocalCoach,
        dispatch: Callable[[Callable[[], None]], None],
        on_manage: Optional[Callable[[], None]] = None,
        on_close: Optional[Callable[[], None]] = None,
        on_state_changed: Optional[Callable[[], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ):
        super().__init__(parent, bg=PANEL_BG, bd=0, highlightthickness=1, highlightbackground="#3a2055")
        self.coach = coach
        self._dispatch = dispatch
        self._on_manage = on_manage
        self._on_close = on_close
        self._on_state_changed = on_state_changed
        self._on_status = on_status

        # Concurrency tokens: `_epoch` changes on suspend/destroy, `_take_gen` on every new take.
        self._epoch = 0
        self._take_gen = 0
        self._suspended = False

        # Read-only snapshot of the take currently interpreted by the AI
        self._take_dir: Optional[Path] = None
        self._report_dict: Optional[Dict[str, Any]] = None
        self._lyric_dict: Optional[Dict[str, Any]] = None
        self._toc: Optional[Dict[str, Any]] = None
        self._track_info: Optional[Dict[str, Any]] = None
        self._debrief_text: Optional[str] = None

        self.chat_history: List[Dict[str, str]] = []
        self._chat_busy = False
        self._chat_msg_counter = 0
        self._is_downloading = False
        self._wrap = 300
        self._wrapped_labels: List[tk.Label] = []

        self._build()
        self.refresh_state()

    # ------------------------------------------------------------------
    # Lifecycle (called by the host, always on the Tk main thread)
    # ------------------------------------------------------------------
    @property
    def current_take_dir(self) -> Optional[Path]:
        return self._take_dir

    def suspend(self):
        """Hide-safe: invalidates in-flight work so late results are dropped silently."""
        self._suspended = True
        self._epoch += 1
        self._chat_busy = False
        try:
            self.btn_send_chat.config(state="normal")
        except tk.TclError:
            pass

    def resume(self):
        self._suspended = False
        self.refresh_state()
        if self._take_dir and not self._debrief_text and self.coach.is_installed():
            self.load_take(self._take_dir, is_reloaded=True)

    def destroy(self):
        self._epoch += 1
        self._suspended = True
        super().destroy()

    def _token(self) -> Tuple[int, int]:
        return (self._epoch, self._take_gen)

    def _is_current(self, token: Tuple[int, int]) -> bool:
        try:
            return token == self._token() and not self._suspended and bool(self.winfo_exists())
        except tk.TclError:
            return False

    def _ui(self, token: Tuple[int, int], fn: Callable[[], None]):
        """Marshal `fn` onto the Tk thread and drop it if the world changed meanwhile."""
        def _run():
            if self._is_current(token):
                fn()
        self._dispatch(_run)

    def _ui_epoch(self, epoch: int, fn: Callable[[], None]):
        def _run():
            try:
                alive = bool(self.winfo_exists())
            except tk.TclError:
                alive = False
            if alive and epoch == self._epoch and not self._suspended:
                fn()
        self._dispatch(_run)

    def _status(self, msg: str):
        if self._on_status:
            try:
                self._on_status(msg)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build(self):
        # Header ------------------------------------------------------
        header = tk.Frame(self, bg="#1f1033")
        header.pack(fill="x")

        title_row = tk.Frame(header, bg="#1f1033")
        title_row.pack(fill="x", padx=12, pady=(10, 0))
        tk.Label(
            title_row, text="🌺 Maestro Tiki AI Studio",
            font=("Helvetica", 12, "bold"), fg=COLOR_GOLD, bg="#1f1033"
        ).pack(side="left")

        if self._on_close:
            tk.Button(
                title_row, text="✕", font=("Helvetica", 9, "bold"), bg="#1f1033", fg=COLOR_TEXT_MUTED,
                activebackground="#3b1b59", activeforeground="#ffffff", bd=0, padx=6, cursor="hand2",
                command=self._on_close
            ).pack(side="right")
        if self._on_manage:
            tk.Button(
                title_row, text="⚙ Model", font=("Helvetica", 8, "bold"), bg="#3b1548", fg=COLOR_GOLD,
                activebackground="#5a2070", activeforeground="#ffffff", bd=1, relief="solid",
                padx=6, pady=1, cursor="hand2", command=self._on_manage
            ).pack(side="right", padx=(0, 6))

        self.lbl_badge = tk.Label(
            header, text="", font=("Helvetica", 8, "italic"), fg=COLOR_CYAN, bg="#1f1033", anchor="w"
        )
        self.lbl_badge.pack(fill="x", padx=12)
        tk.Label(
            header,
            text="AI interpretation only • reads completed analysis • never changes your scores",
            font=("Helvetica", 7), fg=COLOR_TEXT_MUTED, bg="#1f1033", anchor="w"
        ).pack(fill="x", padx=12, pady=(0, 8))

        # Chat (bottom, fixed height) -------------------------------------
        self.chat_card = tk.LabelFrame(
            self, text=" 💬 CONVERSATION WITH MAESTRO TIKI ", bg="#150a22", fg=COLOR_CYAN,
            font=("Helvetica", 8, "bold"), bd=1, padx=8, pady=6
        )

        pills = tk.Frame(self.chat_card, bg="#150a22")
        pills.pack(fill="x", pady=(0, 4))
        for label, color, prompt in QUICK_PROMPTS:
            tk.Button(
                pills, text=label, font=("Helvetica", 7, "bold"), bg="#2a143f", fg=color,
                bd=0, padx=5, pady=2, cursor="hand2",
                command=lambda p=prompt: self._send_prompt(p)
            ).pack(side="left", padx=(0, 3))

        transcript_wrap = tk.Frame(self.chat_card, bg="#0d0615", bd=1, relief="solid")
        transcript_wrap.pack(fill="both", expand=True, pady=(0, 6))
        self.txt_chat = tk.Text(
            transcript_wrap, height=9, wrap="word", bg="#0d0615", fg=COLOR_TEXT_MAIN,
            font=("Helvetica", 9), bd=0, padx=6, pady=4, highlightthickness=0, state="disabled", cursor="arrow"
        )
        chat_scroll = ttk.Scrollbar(transcript_wrap, orient="vertical", command=self.txt_chat.yview)
        self.txt_chat.configure(yscrollcommand=chat_scroll.set)
        chat_scroll.pack(side="right", fill="y")
        self.txt_chat.pack(side="left", fill="both", expand=True)
        self.txt_chat.tag_configure("user_name", foreground=COLOR_CYAN, font=("Helvetica", 9, "bold"))
        self.txt_chat.tag_configure("coach_name", foreground=COLOR_GOLD, font=("Helvetica", 9, "bold"))
        self.txt_chat.tag_configure("muted", foreground=COLOR_TEXT_MUTED, font=("Helvetica", 8, "italic"))
        self._append_chat(
            "Ask Maestro Tiki about your take, breath technique, lyrics, or key adjustments.\n", "muted"
        )

        input_row = tk.Frame(self.chat_card, bg="#150a22")
        input_row.pack(fill="x")
        self.ent_chat = tk.Entry(
            input_row, font=("Helvetica", 9), bg=INPUT_BG, fg=COLOR_TEXT_MAIN,
            insertbackground=COLOR_CYAN, bd=1, relief="solid"
        )
        self.ent_chat.pack(side="left", fill="x", expand=True, padx=(0, 6), ipady=3)
        self.ent_chat.bind("<Return>", lambda e: self._on_submit_chat())
        self.btn_send_chat = tk.Button(
            input_row, text="Send ➔", font=("Helvetica", 8, "bold"), bg=COLOR_CYAN, fg="#000000",
            bd=0, padx=10, pady=4, cursor="hand2", command=self._on_submit_chat
        )
        self.btn_send_chat.pack(side="right")

        # Scrollable middle region: installer / debrief / creative notes ------------
        self.body = tk.Frame(self, bg=PANEL_BG)
        self.body.pack(side="top", fill="both", expand=True)
        self.canvas = tk.Canvas(self.body, bg=PANEL_BG, bd=0, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self.body, orient="vertical", command=self.canvas.yview)
        self.content = tk.Frame(self.canvas, bg=PANEL_BG)
        self.content.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        win = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self._on_canvas_resize(e, win))
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=6)
        self.scrollbar.pack(side="right", fill="y", pady=6)
        self._bind_mousewheel()

        self._build_install_card()
        self._build_debrief_card()
        self._build_creative_card()

    def _on_canvas_resize(self, event, win_id):
        self.canvas.itemconfig(win_id, width=event.width)
        self._wrap = max(220, event.width - 44)
        for lbl in self._wrapped_labels:
            try:
                lbl.config(wraplength=self._wrap)
            except tk.TclError:
                pass

    def _bind_mousewheel(self):
        self.canvas._scrollable = True

        def _wheel(event):
            curr = getattr(event, "widget", None)
            while curr:
                if curr == self.canvas:
                    if event.num == 4 or getattr(event, "delta", 0) > 0:
                        self.canvas.yview_scroll(-2, "units")
                    elif event.num == 5 or getattr(event, "delta", 0) < 0:
                        self.canvas.yview_scroll(2, "units")
                    return "break"
                curr = getattr(curr, "master", None)

        for seq in ("<Button-4>", "<Button-5>", "<MouseWheel>"):
            self.canvas.bind_all(seq, _wheel, add=True)

    def _wrapped_label(self, parent: tk.Misc, **kw) -> tk.Label:
        lbl = tk.Label(parent, wraplength=self._wrap, justify="left", anchor="w", **kw)
        self._wrapped_labels.append(lbl)
        return lbl

    def _build_install_card(self):
        self.install_card = tk.LabelFrame(
            self.content, text=" 📥 OFFLINE AI MODEL (OPTIONAL) ", bg=CARD_BG, fg=COLOR_CYAN,
            font=("Helvetica", 9, "bold"), bd=1, padx=10, pady=8
        )
        self._wrapped_label(
            self.install_card,
            text="Add in-depth debriefs, creative notes and chat. 100% offline • no accounts • no API keys.",
            font=("Helvetica", 9), fg=COLOR_TEXT_MAIN, bg=CARD_BG
        ).pack(fill="x", pady=(0, 6))

        self.model_choice_var = tk.StringVar(value="3b")
        for key, text, color in (
            ("7b", "Qwen 2.5 7B (~4.7 GB) — Master class, 6GB+ GPU", COLOR_LIME),
            ("3b", "Qwen 2.5 3B (~1.9 GB) — Recommended", COLOR_GOLD),
            ("1.5b", "Qwen 2.5 1.5B (~1 GB) — Ultra-fast", COLOR_CYAN),
        ):
            tk.Radiobutton(
                self.install_card, text=text, variable=self.model_choice_var, value=key, fg=color,
                bg=CARD_BG, selectcolor=INPUT_BG, activebackground=CARD_BG, activeforeground=color,
                font=("Helvetica", 8, "bold"), anchor="w"
            ).pack(fill="x")

        accel_row = tk.Frame(self.install_card, bg=CARD_BG)
        accel_row.pack(fill="x", pady=(6, 6))
        tk.Label(accel_row, text="Acceleration:", font=("Helvetica", 8, "bold"), fg=COLOR_GOLD, bg=CARD_BG).pack(side="left")
        self.accel_var = tk.StringVar(value=self.coach.get_acceleration_mode())
        for value, text in (("gpu", "⚡ GPU"), ("cpu", "🖥️ CPU")):
            tk.Radiobutton(
                accel_row, text=text, variable=self.accel_var, value=value, fg=COLOR_TEXT_MAIN, bg=CARD_BG,
                selectcolor=INPUT_BG, activebackground=CARD_BG, activeforeground=COLOR_TEXT_MAIN,
                font=("Helvetica", 8), command=lambda: self.coach.set_acceleration_mode(self.accel_var.get())
            ).pack(side="left", padx=(8, 0))

        btn_row = tk.Frame(self.install_card, bg=CARD_BG)
        btn_row.pack(fill="x")
        self.btn_install = tk.Button(
            btn_row, text="📥 Download & Install", bg="#3a1859", fg=COLOR_CYAN, font=("Helvetica", 9, "bold"),
            bd=1, padx=10, pady=5, cursor="hand2", command=self._on_download_model
        )
        self.btn_install.pack(side="left")
        self.lbl_install_status = tk.Label(btn_row, text="Ready to install", font=("Helvetica", 8), fg=COLOR_TEXT_MUTED, bg=CARD_BG)
        self.lbl_install_status.pack(side="left", padx=8)
        self.prog_install = ttk.Progressbar(self.install_card, orient="horizontal", mode="determinate")

    def _build_debrief_card(self):
        self.debrief_card = tk.LabelFrame(
            self.content, text=" 🔬 AI PERFORMANCE DEBRIEF ", bg="#1c0e2c", fg=COLOR_GOLD,
            font=("Helvetica", 9, "bold"), bd=1, padx=10, pady=8
        )
        head = tk.Frame(self.debrief_card, bg="#1c0e2c")
        head.pack(fill="x", pady=(0, 4))
        tk.Button(
            head, text="↻ Regenerate", font=("Helvetica", 7, "bold"), bg="#2a143f", fg=COLOR_CYAN, bd=0,
            padx=6, pady=2, cursor="hand2", command=self._on_regenerate
        ).pack(side="right")
        self.lbl_debrief = self._wrapped_label(
            self.debrief_card, text="Your debrief appears here once a take has been analyzed.",
            font=("Helvetica", 9), fg=COLOR_TEXT_MUTED, bg="#1c0e2c"
        )
        self.lbl_debrief.pack(fill="x")

    def _build_creative_card(self):
        self.creative_card = tk.LabelFrame(
            self.content, text=" 🎨 CREATIVE ADAPTATION NOTES ", bg="#1a1030", fg=COLOR_MAGENTA,
            font=("Helvetica", 9, "bold"), bd=1, padx=10, pady=8
        )
        self.lbl_creative = self._wrapped_label(
            self.creative_card,
            text="Style, genre-feel and lyric/phrasing observations appear here after a take.",
            font=("Helvetica", 9), fg=COLOR_TEXT_MUTED, bg="#1a1030"
        )
        self.lbl_creative.pack(fill="x")

    # ------------------------------------------------------------------
    # Engine / model state
    # ------------------------------------------------------------------
    def refresh_state(self):
        """Re-evaluates installed-model state and (re)packs the right cards. Main thread only."""
        installed = self.coach.get_installed_model()
        accel = self.coach.get_acceleration_mode().upper()
        icon = "⚡" if accel == "GPU" else "🖥️"

        for w in (self.install_card, self.debrief_card, self.creative_card, self.chat_card):
            w.pack_forget()

        if installed:
            self.lbl_badge.config(text=f"{icon} Model: {installed.get('name', 'Offline')} • {accel}")
            self.debrief_card.pack(fill="x", pady=(0, 8))
            self.creative_card.pack(fill="x", pady=(0, 8))
            self.chat_card.pack(side="bottom", fill="x", padx=8, pady=(0, 8), before=self.body)
        else:
            self.lbl_badge.config(text="No offline model installed")
            self.install_card.pack(fill="x", pady=(0, 8))
            self.lbl_install_status.config(text="Ready to install")
            self.prog_install.pack_forget()

    def _notify_state_changed(self):
        if self._on_state_changed:
            try:
                self._on_state_changed()
            except Exception as e:
                print(f"[AIStudioPanel] state-changed callback error: {e}", file=sys.stderr)

    def _on_download_model(self):
        if self._is_downloading:
            return
        choice = self.model_choice_var.get()
        meta = AVAILABLE_MODELS.get(choice, AVAILABLE_MODELS["3b"])
        epoch = self._epoch
        self._is_downloading = True
        self.btn_install.config(state="disabled", text="⏳ Downloading...")
        self.prog_install.pack(fill="x", pady=(6, 2))
        self.prog_install["value"] = 0
        self.lbl_install_status.config(text=f"Connecting ({meta['size_str']})...")

        def _progress(pct, msg):
            def _apply():
                self.prog_install["value"] = int(pct * 100)
                self.lbl_install_status.config(text=msg)
            self._dispatch(lambda: _apply() if self._alive() else None)

        def _worker():
            try:
                ok = self.coach.download_model(choice, progress_cb=_progress)
            except Exception as e:
                print(f"[AIStudioPanel] download error: {e}", file=sys.stderr)
                ok = False

            def _done():
                self._is_downloading = False
                if not self._alive():
                    return
                self.btn_install.config(state="normal", text="📥 Download & Install")
                if ok:
                    messagebox.showinfo(
                        "Installation Complete",
                        f"Offline AI model ({meta['name']}) installed.\n\nAll AI coaching is now 100% offline."
                    )
                    self.refresh_state()
                    self._notify_state_changed()
                    if self._take_dir:
                        self.load_take(self._take_dir, is_reloaded=True)
                else:
                    messagebox.showwarning(
                        "Download Notice",
                        "Could not complete the model download. Check your connection and try again."
                    )
            self._dispatch(_done)

        threading.Thread(target=_worker, daemon=True, name="ai-model-download").start()

    def _alive(self) -> bool:
        try:
            return bool(self.winfo_exists())
        except tk.TclError:
            return False

    # ------------------------------------------------------------------
    # Take interpretation (read partitions -> generate -> cache)
    # ------------------------------------------------------------------
    def load_take(self, take_dir: Path, is_reloaded: bool = False, force: bool = False):
        """
        Entry point used by the host once the core pipeline has finished persisting a take.
        Returns immediately; all disk and LLM work happens on a worker thread.
        """
        self._take_gen += 1
        self._take_dir = Path(take_dir)
        self._debrief_text = None
        if self._suspended or not self.coach.is_installed():
            return

        token = self._token()
        self._set_debrief("✦ Maestro Tiki is reading your analysis partitions...", COLOR_CYAN)
        self._set_creative("✦ Preparing creative notes...", COLOR_TEXT_MUTED)
        threading.Thread(
            target=self._take_worker, args=(token, self._take_dir, force),
            daemon=True, name="ai-take-interpreter"
        ).start()

    def _take_worker(self, token: Tuple[int, int], take_dir: Path, force: bool):
        try:
            report, lyric, track_info = load_take_report_and_lyrics(take_dir)
            toc = load_take_toc(take_dir) or {}
            summary = load_take_summary(take_dir) or {}
            if report is None:
                self._ui(token, lambda: self._set_debrief("No readable analysis partitions were found for this take.", COLOR_TEXT_MUTED))
                return

            report_dict = report.to_dict()
            lyric_dict = lyric.to_dict() if lyric else None
            track = track_info or summary.get("track") or toc.get("track") or {}
            profile = summary.get("vocal_profile")
            progression = summary.get("progression")

            def _publish_context():
                self._report_dict = report_dict
                self._lyric_dict = lyric_dict
                self._toc = toc
                self._track_info = track
            self._ui(token, _publish_context)

            # Silence / filtered bleed: concise rule-based note, no LLM round-trip.
            if report.overall_score == 0:
                msg = report.coach_feedback[0] if report.coach_feedback else "No vocal singing detected."
                self._ui(token, lambda: self._set_debrief(f"✦ Maestro Tiki: {msg}", COLOR_GOLD))
                self._ui(token, lambda: self._set_creative("Sing a take with voiced phrases to unlock creative notes.", COLOR_TEXT_MUTED))
                return

            debrief_file = take_dir / DEBRIEF_CACHE_FILE
            creative_file = take_dir / CREATIVE_CACHE_FILE

            # ---- Debrief ----
            debrief = self._read_cache(debrief_file) if not force else ""
            if debrief:
                self._ui(token, lambda: self._finish_debrief(debrief))
            else:
                self._ui(token, lambda: self._set_debrief("✦ Maestro Tiki is analyzing your take metrics offline...", COLOR_CYAN))
                try:
                    debrief = self.coach.generate_take_debrief(
                        report_dict, track, profile,
                        progression_data=progression, lyric_alignment=lyric_dict, toc=toc
                    )
                except Exception as e:
                    print(f"[AIStudioPanel] debrief error: {e}", file=sys.stderr)
                    debrief = self.coach._generate_rule_based_debrief(report_dict, track, profile, lyric_alignment=lyric_dict)
                if self._engine_ready():
                    self._write_cache(debrief_file, debrief)
                self._ui(token, lambda: self._finish_debrief(debrief))

            # ---- Creative adaptation notes ----
            creative = self._read_cache(creative_file) if not force else ""
            if not creative:
                self._ui(token, lambda: self._set_creative("✦ Listening for your creative choices...", COLOR_CYAN))
                try:
                    creative = self.coach.generate_creative_adaptation_notes(report_dict, track, lyric_dict)
                except Exception as e:
                    print(f"[AIStudioPanel] creative-notes error: {e}", file=sys.stderr)
                    creative = self.coach._generate_rule_based_creative_notes(report_dict, track, lyric_dict)
                if self._engine_ready():
                    self._write_cache(creative_file, creative)
            self._ui(token, lambda: self._set_creative(creative, COLOR_TEXT_MAIN))
            self._ui(token, lambda: self._status("AI interpretation ready."))
        except Exception as e:
            import traceback
            print(f"[AIStudioPanel] interpretation error: {e}", file=sys.stderr)
            traceback.print_exc()
            self._ui(token, lambda: self._set_debrief("The AI debrief could not be generated for this take.", COLOR_TEXT_MUTED))

    def _engine_ready(self) -> bool:
        try:
            return bool(self.coach.is_engine_ready())
        except Exception:
            return False

    @staticmethod
    def _read_cache(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8").strip() if path.is_file() else ""
        except Exception:
            return ""

    @staticmethod
    def _write_cache(path: Path, text: str):
        try:
            if path.parent.is_dir():
                path.write_text(text, encoding="utf-8")
        except Exception:
            pass

    def _finish_debrief(self, text: str):
        self._debrief_text = text
        self._set_debrief(text, COLOR_TEXT_MAIN)

    def _set_debrief(self, text: str, color: str):
        self.lbl_debrief.config(text=text, fg=color)

    def _set_creative(self, text: str, color: str):
        self.lbl_creative.config(text=text, fg=color)

    def _on_regenerate(self):
        if self._take_dir and self.coach.is_installed():
            self.load_take(self._take_dir, is_reloaded=True, force=True)

    # ------------------------------------------------------------------
    # Chat
    # ------------------------------------------------------------------
    def _append_chat(self, text: str, tag: Optional[str] = None):
        self.txt_chat.config(state="normal")
        if tag:
            self.txt_chat.insert("end", text, tag)
        else:
            self.txt_chat.insert("end", text)
        self.txt_chat.config(state="disabled")
        self.txt_chat.see("end")

    def _send_prompt(self, msg: str):
        self.ent_chat.delete(0, tk.END)
        self.ent_chat.insert(0, msg)
        self._on_submit_chat()

    def _on_submit_chat(self):
        msg = self.ent_chat.get().strip()
        if not msg or self._chat_busy:
            return
        self.ent_chat.delete(0, tk.END)
        self._chat_busy = True
        self.btn_send_chat.config(state="disabled")

        self._chat_msg_counter += 1
        think_tag = f"thinking_{self._chat_msg_counter}"
        self._append_chat("👤 You: ", "user_name")
        self._append_chat(f"{msg}\n")
        self._append_chat("🌺 Maestro Tiki: ", "coach_name")
        self.txt_chat.config(state="normal")
        self.txt_chat.insert("end", "Thinking...\n", ("muted", think_tag))
        self.txt_chat.config(state="disabled")
        self.txt_chat.see("end")

        self.chat_history.append({"role": "user", "content": msg})

        # Snapshot read-only context on the Tk thread (never touched by the worker afterwards)
        history = list(self.chat_history)
        report_dict, lyric_dict = self._report_dict, self._lyric_dict
        toc, track, debrief = self._toc, self._track_info, self._debrief_text
        epoch = self._epoch

        def _worker():
            try:
                reply = self.coach.chat_with_coach(
                    msg, history, report_dict, track,
                    debrief_text=debrief, lyric_alignment=lyric_dict, toc=toc
                )
            except Exception as e:
                import traceback
                print(f"[AIStudioPanel] chat error: {e}", file=sys.stderr)
                traceback.print_exc()
                reply = self.coach._generate_rule_based_chat_reply(msg, report_dict, track, lyric_dict)

            def _apply():
                self.chat_history.append({"role": "assistant", "content": reply})
                self._replace_thinking(think_tag, reply)
                self._chat_busy = False
                self.btn_send_chat.config(state="normal")
            self._ui_epoch(epoch, _apply)

        threading.Thread(target=_worker, daemon=True, name="ai-chat").start()

    def _replace_thinking(self, tag: str, reply: str):
        for prefix in ("✦ Maestro Tiki:", "Maestro Tiki:", "🌺 Coach Tiki:", "Coach Tiki:"):
            if reply.startswith(prefix):
                reply = reply[len(prefix):].lstrip()
                break
        ranges = self.txt_chat.tag_ranges(tag)
        self.txt_chat.config(state="normal")
        if ranges:
            self.txt_chat.delete(ranges[0], ranges[1])
            self.txt_chat.insert(ranges[0], reply + "\n\n")
        else:
            self.txt_chat.insert("end", reply + "\n\n")
        self.txt_chat.config(state="disabled")
        self.txt_chat.see("end")

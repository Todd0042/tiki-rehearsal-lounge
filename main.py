"""
🌺 Tiki Rehearsal Lounge - Main Application Entrypoint
======================================================
Desktop Vocal Analysis & Rehearsal Studio for Tiki Karaoke.
Launches the native Tkinter Studio Cockpit or Terminal Rehearsal Cockpit.
"""

from __future__ import annotations
import os
import sys
import argparse
import time
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import (
    STUDIO_VERSION,
    TIKI_DB_PATH,
    TIKI_LIBRARY_DIR,
    TEMP_TAKES_DIR,
    COLOR_CYAN,
    COLOR_MAGENTA,
    COLOR_LIME,
    COLOR_GOLD
)
from db.bridge import TikiDatabaseBridge
from audio.calibration import VocalCalibrationManager
from audio.recorder import AudioRecorder
from audio.player import RehearsalPlayer
from audio.vocal_analyzer import VocalTakeAnalyzer
from downloader import TrackDownloader


def run_cli_cockpit():
    """Interactive neon terminal cockpit for zero-dependency rehearsal and calibration."""
    db = TikiDatabaseBridge()
    calibrator = VocalCalibrationManager()
    profile = calibrator.load_profile()
    analyzer = VocalTakeAnalyzer(vocal_profile=profile)
    recorder = AudioRecorder()
    player = RehearsalPlayer()
    downloader = TrackDownloader(db_bridge=db)

    selected_track = None
    current_key_shift = 0

    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    LIME = "\033[92m"
    GOLD = "\033[93m"
    BOLD = "\033[1m"
    RESET = "\033[0m"

    print(f"\n{MAGENTA}{BOLD}🌺 ========================================================== 🌺{RESET}")
    print(f"{CYAN}{BOLD}              TIKI REHEARSAL LOUNGE (v{STUDIO_VERSION}){RESET}")
    print(f"{GOLD}        Vocal Calibration & Smart Key Rehearsal Studio{RESET}")
    print(f"{MAGENTA}{BOLD}🌺 ========================================================== 🌺{RESET}\n")

    stats = db.get_library_stats()
    print(f"{LIME}● Connected to Tiki Karaoke Database:{RESET} {TIKI_DB_PATH}")
    print(f"  • Total Tracks in Database: {stats['total_tracks']:,}")
    print(f"  • Sing-Along Guide Vocals:   {stats['sing_along_tracks']:,}")
    print(f"  • Local Video/Audio Ready:   {stats['local_ready_tracks']:,}\n")

    if profile and (profile.anchor_hz > 0 or profile.range_max_hz > 0):
        print(f"{GOLD}Singer Vocal Profile Loaded:{RESET}")
        print(f"  • Voice Category:   {BOLD}{profile.vocal_type}{RESET}")
        anch_str = f"{profile.anchor_note} ({profile.anchor_hz:.1f} Hz)" if profile.anchor_hz > 0 else "Pending Step 2"
        print(f"  • Speaking Anchor:  {anch_str}")
        if profile.range_max_hz > 0:
            range_str = f"{profile.range_min_note} to {profile.range_max_note} ({profile.range_semitones:.1f} semitones)"
        else:
            range_str = "Pending Step 1"
        print(f"  • Usable Range:     {range_str}")
        tess_str = f"{profile.tessitura_min_note} to {profile.tessitura_max_note}" if profile.tessitura_max_hz > 0 else "Pending Step 2"
        print(f"  • Power Sweet Spot: {LIME}{tess_str}{RESET}\n")
    else:
        print(f"{MAGENTA}Notice: No vocal profile calibrated yet. Run exercises [2] & [3] to calibrate!{RESET}\n")

    while True:
        track_name = f"{selected_track.get('title')} - {selected_track.get('artist')}" if selected_track else "None Selected"
        key_name = f"{current_key_shift:+d}" if current_key_shift != 0 else "0 (Original)"

        print(f"{CYAN}--- Cockpit Menu ---{RESET}")
        print(f"  [1] 🔍 Search & Select Rehearsal Track  (Current: {BOLD}{track_name}{RESET})")
        print(f"  [2] 🎙️ Run Exercise 1: Siren Pitch Sweep ('Ooo ➔ Aaa') (15s)")
        print(f"  [3] 🗣️ Run Exercise 2: Speaking Anchor ('Count 20 ➔ 0') (25s)")
        print(f"  [4] 🎹 Adjust Key Shift [-3, +3]       (Current: {BOLD}{key_name}{RESET})")
        print(f"  [5] ✨ Smart Key Auto-Match for Selected Song")
        print(f"  [6] 🎤 Start Rehearsal Take & Performance Report")
        print(f"  [7] 📥 Download Song from YouTube to Library")
        print(f"  [8] 🚪 Exit Studio")

        try:
            choice = input(f"\n{BOLD}Select an option [1-8]: {RESET}").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting. Aloha!")
            break

        if choice == "1":
            q = input("Search artist or song title: ").strip()
            tracks = db.search_tracks(q, limit=15)
            if not tracks:
                print("No matching tracks found.")
                continue
            print("\nMatching Tracks:")
            for i, t in enumerate(tracks, 1):
                sa = "🎙️ [Sing-Along]" if t.get("is_sing_along") else "🎵 [Karaoke]"
                ready = "✅ Ready" if t.get("is_downloaded") else "☁️ Cloud"
                print(f"  {i}. {t.get('artist')} - {t.get('title')} ({sa}, {ready})")
            sel_idx = input(f"\nSelect track number (1-{len(tracks)}) or Enter to cancel: ").strip()
            if sel_idx.isdigit() and 1 <= int(sel_idx) <= len(tracks):
                selected_track = tracks[int(sel_idx) - 1]
                print(f"{LIME}Selected:{RESET} {selected_track.get('title')}")

        elif choice == "2":
            take_path = TEMP_TAKES_DIR / "cli_calib_siren.wav"
            print(f"\n{CYAN}{BOLD}--- STEP 1: SIREN PITCH SWEEP ('Ooo ➔ Aaa') [15 Seconds] ---{RESET}")
            print(f"{GOLD}🎯 How to perform:{RESET}")
            print("  1. Take a full, relaxed breath from your diaphragm.")
            print("  2. Start at your lowest comfortable, chest-resonant pitch on a round 'Oooo'.")
            print("  3. Slowly glide upward like a siren to your highest head/falsetto ceiling, opening to 'Aaaa'.")
            print("  4. Hold the peak note briefly (1–2s), then smoothly glide all the way back down to your lowest note.")
            print(f"  • {BOLD}Tip:{RESET} Moderate volume only—do not shout or strain. We are mapping your true vocal boundaries.")
            print(f"  • {BOLD}Early Stop:{RESET} If you complete your glide early, simply press [Enter] to analyze immediately.\n")

            input(f"{BOLD}Press [Enter] when ready to begin 3-second countdown...{RESET}")
            for c in range(3, 0, -1):
                print(f"  Starting in {c}...", end="\r", flush=True)
                time.sleep(1.0)

            print(f"\n{LIME}● RECORDING (15s max)... Glide floor ➔ ceiling ➔ floor now!{RESET}")
            recorder.start_recording(take_path)

            import threading
            stop_early_event = threading.Event()

            def _wait_for_enter():
                try:
                    sys.stdin.readline()
                    stop_early_event.set()
                except Exception:
                    pass

            listener = threading.Thread(target=_wait_for_enter, daemon=True)
            listener.start()

            for remaining in range(15, 0, -1):
                if stop_early_event.is_set():
                    print(f"  {GOLD}[Finished early at {15 - remaining}s]{RESET}       ")
                    break
                print(f"  [{remaining:02d}s remaining] Sing siren glide... (Press Enter if done)  ", end="\r", flush=True)
                time.sleep(1.0)

            recorder.stop_recording()
            print(f"\n{CYAN}Analyzing siren sweep pitch contour...{RESET}")
            try:
                res = calibrator.analyze_siren_sweep(take_path)
                profile = calibrator.update_siren_sweep(res)
                analyzer.update_profile(profile)

                print(f"\n{LIME}{BOLD}Siren Sweep Results (Saved to vocal_profile.json):{RESET}")
                print(f"  • Vocal Floor:   {res['min_note']} ({res['min_hz']:.1f} Hz)")
                print(f"  • Vocal Ceiling: {res['max_note']} ({res['max_hz']:.1f} Hz)")
                print(f"  • Usable Span:   {res['range_semitones']:.1f} semitones ({res['octaves']} octaves)")
            except Exception as e:
                print(f"{MAGENTA}Notice: {e}{RESET}")

        elif choice == "3":
            take_path = TEMP_TAKES_DIR / "cli_calib_speaking.wav"
            print(f"\n{MAGENTA}{BOLD}--- STEP 2: SPEAKING PITCH ANCHOR ('20 ➔ 0') [25 Seconds] ---{RESET}")
            print(f"{GOLD}🎯 How to perform:{RESET}")
            print("  1. Count backwards aloud from 20 down to 0 at an unhurried, natural pace (~1s per number: 'Twenty... Nineteen... Eighteen...').")
            print("  2. Speak naturally, exactly as if talking casually to a friend across a table.")
            print(f"  • {BOLD}Important:{RESET} Do NOT sing or monotone chant. We need your authentic everyday conversational speaking pitch.")
            print(f"  • {BOLD}Early Stop:{RESET} If you reach zero early, press [Enter] to analyze immediately.\n")

            input(f"{BOLD}Press [Enter] when ready to begin 3-second countdown...{RESET}")
            for c in range(3, 0, -1):
                print(f"  Starting in {c}...", end="\r", flush=True)
                time.sleep(1.0)

            print(f"\n{LIME}● RECORDING (25s max)... Count backwards from 20 to 0 now!{RESET}")
            recorder.start_recording(take_path)

            import threading
            stop_early_event = threading.Event()

            def _wait_for_enter_spk():
                try:
                    sys.stdin.readline()
                    stop_early_event.set()
                except Exception:
                    pass

            listener = threading.Thread(target=_wait_for_enter_spk, daemon=True)
            listener.start()

            for remaining in range(25, 0, -1):
                if stop_early_event.is_set():
                    print(f"  {GOLD}[Finished early at {25 - remaining}s]{RESET}       ")
                    break
                print(f"  [{remaining:02d}s remaining] Counting backwards... (Press Enter if done)  ", end="\r", flush=True)
                time.sleep(1.0)

            recorder.stop_recording()
            print(f"\n{CYAN}Analyzing speaking fundamental...{RESET}")
            try:
                res = calibrator.analyze_speaking_anchor(take_path)
                profile = calibrator.update_speaking_anchor(res)
                analyzer.update_profile(profile)

                print(f"\n{LIME}{BOLD}Speaking Anchor Results (Saved to vocal_profile.json):{RESET}")
                print(f"  • Speaking Pitch:   {res['anchor_note']} ({res['anchor_hz']:.1f} Hz)")
                print(f"  • Power Sweet Spot: {LIME}{res['tessitura_min_note']} to {res['tessitura_max_note']}{RESET}")
                print(f"  • Voice Category:   {profile.vocal_type}")
            except Exception as e:
                print(f"{MAGENTA}Notice: {e}{RESET}")

        elif choice == "4":
            shift_input = input("Enter key shift in semitones (-3 to +3): ").strip()
            try:
                val = int(shift_input)
                current_key_shift = int(max(-3, min(3, val)))
                print(f"Key shift set to: {current_key_shift:+d}")
            except ValueError:
                print("Invalid integer.")

        elif choice == "5":
            if not selected_track:
                print("Please select a track first [Option 1].")
                continue
            shift, reason = analyzer.recommend_key_for_track({"max_hz": 330.0})
            current_key_shift = shift
            print(f"\n{GOLD}✨ Smart Key Recommendation:{RESET} {shift:+d} semitones")
            print(f"  {reason}\n")

        elif choice == "6":
            if not selected_track:
                print("Please select a track first [Option 1].")
                continue
            local_p = selected_track.get("local_path")
            if not local_p or not Path(local_p).exists():
                print("Track media file is not available locally. Download it first [Option 7].")
                continue

            take_wav = TEMP_TAKES_DIR / f"cli_take_{int(time.time())}.wav"
            print(f"\n{LIME}Launching Rehearsal Take! Press Enter at any time to stop take.{RESET}")
            recorder.start_recording(take_wav)
            player.play(Path(local_p), key_shift=current_key_shift)

            input(f"{BOLD}[Rehearsal Active - Press Enter to Stop Take]{RESET}")
            player.stop()
            recorder.stop_recording()

            print("\nGenerating Performance Report Card...")
            sa_meta = db.find_sing_along_equivalent(selected_track.get("title", ""), selected_track.get("artist", ""))
            ref_path = Path(sa_meta["local_path"]) if sa_meta and sa_meta.get("local_path") else None

            report = analyzer.analyze_take(take_wav, reference_wav=ref_path, key_shift=current_key_shift)
            print(f"\n{CYAN}{BOLD}--- PERFORMANCE REPORT CARD ---{RESET}")
            print(f"  • Overall Score:      {BOLD}{report.overall_score} / 100{RESET}")
            print(f"  • Pitch Accuracy:     {report.pitch_accuracy_pct}% (±50¢ tolerance)")
            print(f"  • Intonation Drift:   {report.intonation_cents:+0.1f}¢ ({report.intonation_verdict})")
            print(f"  • Rhythm Offset:      {report.rhythm_offset_ms:+0.1f}ms ({report.rhythm_verdict})")
            vib_str = f"{report.vibrato_rate_hz:.1f} Hz (depth ±{report.vibrato_depth_cents:.0f}¢)" if report.vibrato_present else "Straight tone"
            print(f"  • Vibrato Dynamics:   {vib_str}")
            print(f"  • Vocal Resonance:    {report.resonance_verdict} (ratio: {report.singers_formant_ratio:.3f})")
            print(f"  • Breath Support:     {report.breath_support_pct:.1f}% ({report.breath_support_verdict})")
            print(f"  • Glottal Attacks:    {report.onset_attack_verdict} ({report.balanced_onsets_pct:.1f}% balanced)")
            print(f"  • Coach Feedback:")
            for note in report.coach_feedback:
                print(f"    - {note}")
            print(f"  • Smart Key Note:     {report.key_recommendation_reason}\n")

        elif choice == "7":
            query = input("Enter song to search on YouTube: ").strip()
            print(f"Searching YouTube...")
            results = downloader.search_youtube(query, limit=5)
            if not results:
                print("No results found on YouTube.")
                continue
            for i, r in enumerate(results, 1):
                sa = "Sing-Along" if r.get("is_sing_along") else "Karaoke"
                print(f"  {i}. {r.get('artist')} - {r.get('title')} ({sa})")
            sel_dl = input(f"Select track to download (1-{len(results)}) or Enter to cancel: ").strip()
            if sel_dl.isdigit() and 1 <= int(sel_dl) <= len(results):
                chosen = results[int(sel_dl) - 1]
                print("Downloading and normalizing to -16.0 LUFS...")
                saved = downloader.download_track(
                    video_id=chosen["video_id"],
                    title=chosen["title"],
                    artist=chosen["artist"],
                    original_title=chosen["original_title"],
                    channel=chosen["channel"],
                    duration=chosen["duration"],
                    is_sing_along=chosen["is_sing_along"],
                    progress_callback=lambda p, msg: print(f"  [{int(p*100)}%] {msg}")
                )
                if saved:
                    print(f"{LIME}Downloaded successfully and inserted into karaoke.db!{RESET}")
                    selected_track = saved

        elif choice == "8":
            print("Mahalo for practicing at Tiki Rehearsal Lounge! Aloha! 🌺")
            break


def main():
    parser = argparse.ArgumentParser(description="🌺 Tiki Rehearsal Lounge")
    parser.add_argument("--cli", action="store_true", help="Launch interactive CLI cockpit directly")
    args = parser.parse_args()

    if args.cli:
        run_cli_cockpit()
        return

    # Check GUI availability
    try:
        import tkinter
        from ui.studio_window import StudioCockpitWindow
        app = StudioCockpitWindow()
        app.run()
    except Exception as e:
        print(f"Desktop GUI notice: {e}")
        print("Launching full-featured Interactive Neon Terminal Cockpit instead...\n")
        run_cli_cockpit()


if __name__ == "__main__":
    main()

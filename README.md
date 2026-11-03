# 🌺 Tiki Rehearsal Lounge

[![Version](https://img.shields.io/badge/version-1.0.0-cyan.svg)](https://github.com/Todd0042/tiki-rehearsal-lounge)
[![License](https://img.shields.io/badge/license-MIT-magenta.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-yellow.svg)](https://www.python.org/)

**Tiki Rehearsal Lounge** is the official sister studio to [Tiki Karaoke Lounge](https://github.com/Todd0042/tiki-karaoke). Designed specifically for vocalists and karaoke hosts, it provides a dedicated practice environment with formant-preserving real-time key shifting, normalized lead vocal extraction, scientific vocal range calibration, and post-take performance report cards.

---

## 🌴 Key Features

* **Direct Tiki Karaoke Database Bridge**:
  * Auto-discovers and connects directly to your active `karaoke.db` (auto-detected from `~/.config/TikiKaraoke/config.json`).
  * Instant FTS5 search across thousands of tracks with sing-along guide vocal pairing.
* **Dual Calibration Exercises**:
  * **Exercise 1: Siren Pitch Sweep ("Ooo ➔ Aaa")**: Maps physiological pitch floor ($F_{0,\text{min}}$) and ceiling ($F_{0,\text{max}}$).
  * **Exercise 2: Speaking Anchor ("Counting 20 ➔ 0")**: Extracts your natural speaking fundamental and calculates your true **Power Tessitura Zone** ($+3$ to $+14$ semitones above speaking fundamental).
  * Persists your vocal profile to `~/.config/TikiRehearsalLounge/vocal_profile.json`.
* **Smart Key Recommendation Engine**:
  * Analyzes melodic peak demand of any track against your calibrated vocal comfort zone.
  * Recommends optimal transposition strictly within the safe **`[-3, +3]` semitone range** to prevent audio degradation.
* **Synchronized Transport & Recording**:
  * Low-latency microphone capture using PipeWire (`pw-record`), PulseAudio, or ALSA.
  * Synchronized playback with real-time `librubberband` pitch shifting.
* **Post-Take Performance Report Card**:
  * **Pitch Accuracy %**: Evaluated against a $\pm 50$ cents quarter-tone tolerance window.
  * **Intonation Drift**: Exact cents deviation (identifying tendency to push sharp or sag flat).
  * **Rhythm Pocket**: Onset timing precision in milliseconds.
  * **Vibrato Dynamics**: Detection of natural modulation rate (4.5–7.0 Hz) and depth.
* **Integrated Downloader**:
  * Downloads YouTube tracks via `yt-dlp`.
  * EBU R128 loudness normalization (-16.0 LUFS) with immediate database sync.
* **Dual Cockpit Interfaces**:
  * **Desktop GUI**: Native Tkinter window styled after Tiki Karaoke's Control Hub (`#0d0814` midnight violet palette).
  * **Interactive Terminal Cockpit**: Colorful ANSI CLI mode (`--cli`) that runs seamlessly in any environment.

---

## 🚀 Quick Start

### 1. Requirements
* Linux (PipeWire / PulseAudio / ALSA)
* Python 3.10+
* `mpv` and `ffmpeg` (installed via system package manager)
* `tk` (for the native desktop window):
  ```bash
  # Arch Linux
  sudo pacman -S tk mpv ffmpeg
  
  # Ubuntu / Debian
  sudo apt-get install python3-tk mpv ffmpeg
  ```

### 2. Installation
```bash
git clone https://github.com/Todd0042/tiki-rehearsal-lounge.git
cd tiki-rehearsal-lounge
pip install -r requirements.txt
```

### 3. Launching
```bash
# Launch Desktop Studio Cockpit (Tkinter)
python main.py

# Or launch Interactive Terminal Cockpit directly
python main.py --cli
```

---

## 🌺 Architecture & Upstream Portability

Tiki Rehearsal Lounge is engineered as a modular package that can be imported directly into Tiki Karaoke as a first-class feature tab or standalone app:

```text
tiki-rehearsal-lounge/
├── audio/
│   ├── calibration.py     # Siren glide & speaking fundamental pitch tracking
│   ├── player.py          # MPV IPC wrapper with librubberband key shifting
│   ├── recorder.py        # Low-latency PipeWire / FFmpeg microphone capture
│   └── vocal_analyzer.py  # Pitch accuracy, intonation, vibrato & smart key engine
├── db/
│   └── bridge.py          # Concurrent WAL SQLite bridge to karaoke.db
├── ui/
│   └── studio_window.py   # Native Control Hub-styled Tkinter studio cockpit
├── config.py              # Path resolution and Tiki Karaoke configuration bridge
├── downloader.py          # yt-dlp downloader with -16.0 LUFS normalization
└── main.py                # Dual Desktop / CLI entrypoint
```

---

## 📜 License
MIT License. Created with aloha for Tiki Karaoke Lounge. 🌴🍹

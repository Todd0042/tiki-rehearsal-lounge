# 🌴 Download & Installation Guide (Windows, Linux & Steam Deck)

Welcome to **Tiki Karaoke Lounge** and **Tiki Rehearsal Lounge**! 
All editions are 100% self-hosted, offline-capable, and require zero cloud accounts or subscriptions.

---

## 🪟 Windows Quick Start (Windows 10 / 11)

### Files Available:
* **Tiki Karaoke Lounge (Pro)**: `TikiKaraokeSetup-v1.0.x.exe` (or Portable `.zip`)
* **Tiki Karaoke Lounge (Free)**: `TikiKaraokeSetup-Free-v1.0.x.exe`
* **Tiki Rehearsal Lounge**: `TikiRehearsalLoungeSetup-v1.0.x.exe` (or Portable `.zip`)

### Installation Steps:
1. Download the `.exe` installer for your desired application.
2. Double-click the installer and follow the setup wizard (creates Desktop and Start Menu shortcuts).
3. Launch the application!

### If Blocked by Windows Security:
Because this is independent, community-developed software without an enterprise code-signing certificate, Windows may show one of two prompts:

#### 1. Windows Defender SmartScreen ("Windows protected your PC")
1. Click **"More info"**.
2. Click **"Run anyway"**.
3. The app will launch normally.

#### 2. Windows 11 Smart App Control (SAC)
Windows 11 includes an AI feature called **Smart App Control (SAC)** that automatically blocks unsigned indie software:
1. Open the **Windows Start Menu**, type **"Windows Security"**, and press **Enter**.
2. Click **App & browser control** in the sidebar.
3. Under **Smart App Control**, click **Smart App Control settings**.
4. Set it to **Evaluation** (if available) or **Off**.
5. Re-launch the installer or app—it will launch smoothly without interference. *(Standard Windows Defender Antivirus remains 100% active and protecting your PC).*

---

## 🐧 Linux Quick Start (Ubuntu, Debian, Mint, Fedora, Arch, CachyOS)

### Files Available:
* **Tiki Karaoke Lounge**: `TikiKaraoke-v1.0.x-x86_64.AppImage` (Pro or Free)
* **Tiki Rehearsal Lounge**: `TikiRehearsalLounge-v1.0.x-x86_64.AppImage`

### Running the Universal AppImage:
1. Download the `.AppImage` file.
2. Make it executable:
   * **Via File Manager**: Right-click the `.AppImage` → **Properties** → **Permissions** → Check **"Allow executing file as program"**.
   * **Via Terminal**:
     ```bash
     chmod +x TikiKaraoke-*-x86_64.AppImage
     # or
     chmod +x TikiRehearsalLounge-*-x86_64.AppImage
     ```
3. Double-click the file to launch!

> **Note on FUSE (Ubuntu 24.04+, Fedora):** If your Linux distribution does not have `libfuse2` pre-installed, you can either install it via your package manager (`sudo apt install libfuse2` or `sudo dnf install fuse-libs`), or run the AppImage with:
> ```bash
> ./TikiKaraoke-*-x86_64.AppImage --appimage-extract-and-run
> ```

---

## 🎮 Valve Steam Deck (SteamOS)

1. Switch to **Desktop Mode** (*Steam Button → Power → Switch to Desktop*).
2. Download the desired Linux `.AppImage` via your browser.
3. Open the **Dolphin** file manager, go to `Downloads`, right-click the file → **Properties** → **Permissions** → Check **"Is executable"**.
4. *(Optional: Add to Steam Gaming Mode)*:
   * Open the **Steam** desktop client.
   * In the top menu, click **Games** → **Add a Non-Steam Game to My Library...**
   * Click **Browse...**, select the `.AppImage` in your Downloads folder, and click **Add Selected Programs**.
5. Switch back to **Gaming Mode**—you can now launch Tiki Karaoke Lounge or Tiki Rehearsal Lounge straight from your Steam handheld or docked to your living room TV!

---

## 🤖 Offline AI Vocal Coach Setup (Tiki Rehearsal Lounge)
* The vocal coach engine (`llama-server`) is pre-bundled inside the app.
* To enable full offline AI debriefings, open **Settings (⚙️) → Offline AI Vocal Coach** inside the app.
* Click **Download & Install Offline AI Coach** to download your choice of local open-weights model (**~986 MB** for Qwen 2.5 1.5B or **~1.95 GB** for Qwen 2.5 3B).
* Once downloaded, all vocal analysis and coaching runs **100% locally on your CPU** with zero internet connection required.

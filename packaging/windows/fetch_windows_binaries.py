#!/usr/bin/env python3
"""
Downloads precompiled 64-bit Windows standalone binaries for Tiki Rehearsal Lounge packaging:
- ffmpeg.exe & ffprobe.exe & ffplay.exe (from Gyan / BtbN release)
"""
import os
import sys
import json
import shutil
import zipfile
import subprocess
import urllib.request
from pathlib import Path

# Force UTF-8 encoding on Windows consoles
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_BIN_DIR = BASE_DIR / "bin"

BINARIES = {
    "ffmpeg": {
        "filename": "ffmpeg.exe",
        "url": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
        "is_zip": True,
        "zip_exe_name": "ffmpeg.exe",
        "also_extract": ["ffprobe.exe"]
    }
}

def get_latest_mpv_url() -> str:
    """Queries GitHub Releases API for latest shinchiro mpv 64-bit standalone build."""
    try:
        api_url = "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest"
        req = urllib.request.Request(api_url, headers={"User-Agent": "TikiRehearsalPackager/1.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            for asset in data.get("assets", []):
                name = asset.get("name", "")
                if name.startswith("mpv-x86_64-") and not name.startswith("mpv-x86_64-v3-") and name.endswith(".7z"):
                    url = asset.get("browser_download_url")
                    if url:
                        print(f" [MPV] Resolved latest mpv release asset: {name}")
                        return url
    except Exception as e:
        print(f"   [Notice] Could not query GitHub API for mpv: {e}")

    # Fallback known release URL
    return "https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/20261103/mpv-x86_64-20261103-git-a687796d9d.7z"

def download_file(url: str, dest_path: Path):
    print(f" [Download] {url} -> {dest_path.name}")
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dest = dest_path.with_suffix(dest_path.suffix + ".download")

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TikiRehearsalPackager/1.0"}
    req = urllib.request.Request(url, headers=headers)

    with urllib.request.urlopen(req) as response, open(temp_dest, "wb") as out_file:
        total_size = int(response.info().get("Content-Length", 0))
        downloaded = 0
        chunk_size = 1024 * 1024  # 1 MB chunks
        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                break
            out_file.write(chunk)
            downloaded += len(chunk)
            if total_size > 0:
                percent = (downloaded / total_size) * 100
                mb_down = downloaded / (1024 * 1024)
                mb_total = total_size / (1024 * 1024)
                sys.stdout.write(f"\r   Progress: {percent:5.1f}% ({mb_down:5.1f} MB / {mb_total:5.1f} MB)")
                sys.stdout.flush()
    print("\n   Download complete.")
    temp_dest.rename(dest_path)

def extract_from_zip(zip_path: Path, target_names: list, dest_dir: Path):
    print(f" [Extract] Extracting {target_names} from {zip_path.name}...")
    with zipfile.ZipFile(zip_path, 'r') as z:
        for member in z.infolist():
            member_base = Path(member.filename).name.lower()
            for target in target_names:
                if member_base == target.lower():
                    dest_file = dest_dir / target
                    print(f"   -> Extracting {member.filename} to {dest_file.name}")
                    with z.open(member) as source, open(dest_file, "wb") as target_f:
                        shutil.copyfileobj(source, target_f)

def extract_from_7z(archive_path: Path, target_names: list, dest_dir: Path):
    print(f" [Extract] Extracting {target_names} from {archive_path.name}...")
    seven_z = shutil.which("7z") or shutil.which("7za")
    if not seven_z and sys.platform == "win32":
        for p in [r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"]:
            if os.path.exists(p):
                seven_z = p
                break

    if seven_z:
        cmd = [seven_z, "e", "-y", f"-o{dest_dir}", str(archive_path)] + target_names
        subprocess.run(cmd, check=True)
    elif shutil.which("tar"):
        cmd = ["tar", "-xf", str(archive_path), "-C", str(dest_dir)] + target_names
        subprocess.run(cmd, check=True)
    else:
        raise RuntimeError("No 7z or tar tool found to extract .7z archive")

def fetch_all_binaries(output_dir: Path = DEFAULT_BIN_DIR):
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"[Staging] Staging Windows 64-bit Binaries into: {output_dir}\n")

    # 1. Fetch FFmpeg & FFprobe
    for key, info in BINARIES.items():
        target_file = output_dir / info["filename"]
        if target_file.exists():
            print(f" [OK] {info['filename']} already exists in {output_dir.name}/. Skipping.")
            continue

        if info["is_zip"]:
            zip_dest = output_dir / f"{key}_temp.zip"
            try:
                download_file(info["url"], zip_dest)
                targets = [info["zip_exe_name"]]
                if "also_extract" in info:
                    targets.extend(info["also_extract"])
                extract_from_zip(zip_dest, targets, output_dir)
            finally:
                if zip_dest.exists():
                    zip_dest.unlink()
        else:
            download_file(info["url"], target_file)

    # 2. Fetch Standalone MPV
    mpv_exe = output_dir / "mpv.exe"
    if mpv_exe.exists():
        print(f" [OK] mpv.exe already exists in {output_dir.name}/. Skipping.")
    else:
        mpv_url = get_latest_mpv_url()
        temp_7z = output_dir / "mpv_temp.7z"
        try:
            download_file(mpv_url, temp_7z)
            extract_from_7z(temp_7z, ["mpv.exe", "d3dcompiler_43.dll"], output_dir)
        finally:
            if temp_7z.exists():
                temp_7z.unlink()

    # Validate critical binaries
    if not mpv_exe.exists():
        raise RuntimeError(f"mpv.exe was not successfully extracted to {output_dir}")

    print("\n[Complete] Windows 64-bit binary staging complete:")
    for f in output_dir.glob("*.exe"):
        size_mb = f.stat().st_size / (1024 * 1024)
        print(f"   * {f.name:<16} ({size_mb:.2f} MB)")
    for f in output_dir.glob("*.dll"):
        size_mb = f.stat().st_size / (1024 * 1024)
        print(f"   * {f.name:<16} ({size_mb:.2f} MB)")

if __name__ == "__main__":
    out = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_BIN_DIR
    fetch_all_binaries(out)

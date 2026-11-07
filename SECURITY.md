# Security Policy

## Supported Versions

Security updates and dependency patches are actively maintained for the latest release line of **Tiki Rehearsal Lounge**:

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |
| < 1.0.0 | :x:                |

## Architectural Security Guarantees

* **100% Local & Offline Inference**: The Maestro Tiki AI Vocal Coach (`llama-server`) and Whisper STT engine (`whisper-cli`) bind strictly to localhost (`127.0.0.1`) on an ephemeral port and execute with zero external network telemetry or cloud API calls.
* **Subprocess & Path Sanitization**: All external audio/video subprocesses (`ffmpeg`, `mpv`, `yt-dlp`) are invoked using argument arrays (`shell=False`) and sanitized filesystem paths to prevent command injection and path traversal.
* **Deterministic Dependency Pinning**: Direct and transitive Python dependencies in `requirements.txt` and GitHub Actions workflows are pinned to verified immutable versions/commits.

## Reporting a Vulnerability

If you discover a potential security issue, please report it privately via email to **todd.0042.git@gmail.com** or through [GitHub Private Vulnerability Reporting](https://github.com/Todd0042/tiki-rehearsal-lounge/security/advisories/new) rather than opening a public issue. You will receive an acknowledgment within 48 hours.

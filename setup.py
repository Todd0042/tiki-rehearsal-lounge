from pathlib import Path
from setuptools import find_packages, setup

ROOT = Path(__file__).parent
README = (ROOT / "README.md").read_text(encoding="utf-8")

setup(
    name="tiki-rehearsal-lounge",
    version="1.0.8",
    description="Vocal range calibration, smart key rehearsal, multi-core DSP analysis, and offline AI vocal coach studio.",
    long_description=README,
    long_description_content_type="text/markdown",
    author="Todd",
    url="https://github.com/Todd0042/tiki-rehearsal-lounge",
    packages=find_packages(exclude=("tests", "packaging", "dist", "bin")),
    py_modules=["main", "config", "downloader"],
    python_requires=">=3.10",
    install_requires=[
        "numpy>=1.24.0",
        "scipy>=1.10.0",
        "Pillow>=10.0.0",
        "sounddevice>=0.4.6",
        "yt-dlp>=2024.01.01",
    ],
    entry_points={
        "console_scripts": [
            "tiki-rehearsal-lounge=main:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
        "Topic :: Multimedia :: Sound/Audio :: Analysis",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ],
)

"""Where Silver keeps its data (downloaded game data, the market and kill stores, sessions).

Run from a source checkout (the repo), that is the repo's data/ folder. Installed as a tool
(`uv tool install ...`), it's the user's own data folder: %LOCALAPPDATA%\\Silver on Windows,
~/Library/Application Support/Silver on macOS, $XDG_DATA_HOME/silver (~/.local/share/silver)
elsewhere. The SILVER_DATA environment variable overrides both.
"""
import os
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent


def data_dir(environ=os.environ, platform=sys.platform, package=PACKAGE):
    if environ.get("SILVER_DATA"):
        return Path(environ["SILVER_DATA"]).expanduser()
    repo = package.parent
    if (repo / "pyproject.toml").is_file() and (repo / ".git").exists():
        return repo / "data"
    home = Path.home()
    if platform == "win32":
        return Path(environ.get("LOCALAPPDATA") or home / "AppData" / "Local") / "Silver"
    if platform == "darwin":
        return home / "Library" / "Application Support" / "Silver"
    return Path(environ.get("XDG_DATA_HOME") or home / ".local" / "share") / "silver"


DATA_DIR = data_dir()

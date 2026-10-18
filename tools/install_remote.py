"""Copy the Claude codec and maps into Reason's user Remote folder.

Windows: %APPDATA%/Propellerhead Software/Remote
macOS:   ~/Library/Application Support/Propellerhead Software/Remote
Restart Reason afterwards.
"""

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def remote_dir():
    if sys.platform == "win32":
        return Path(os.environ["APPDATA"]) / "Propellerhead Software" / "Remote"
    return Path.home() / "Library" / "Application Support" / "Propellerhead Software" / "Remote"


def main():
    dest = remote_dir()
    for sub in ("Codecs/Lua Codecs/Claude", "Maps/Claude"):
        src, dst = ROOT / "remote" / sub, dest / sub
        dst.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            shutil.copy2(f, dst / f.name)
            print("installed", dst / f.name)


if __name__ == "__main__":
    main()

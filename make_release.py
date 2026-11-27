"""
Build the zips to send to friends:   python make_release.py
  release/Form-Auto-Filler-Windows.zip
  release/Form-Auto-Filler-Mac.zip

Why not just zip the folder by hand:
- the Mac scripts (.command) must be marked "executable" inside the zip, or the Mac refuses to
  open them ("you don't have access privileges"). Windows' "Compressed folder" cannot do that.
- your own things stay out: the browser profile (your Google sign-in!), documents, logs, tests,
  the .venv folder.
- data.json goes in with the document paths emptied (yours point to files on YOUR computer);
  friends choose theirs with "Documents to upload...".
"""

import json
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "release")
TOP = "Form-Auto-Filler"  # the folder friends get when they unzip

COMMON = ["autofill_app.py", "matching.py", "requirements.txt", "README.txt"]
PLATFORMS = {
    "Windows": ["setup.bat", "start.bat"],
    "Mac": ["setup.command", "start.command", "README_MAC.txt"],
}
EXECUTABLE = (".command",)


def friend_data():
    """data.json without the paths of your own documents."""
    with open(os.path.join(HERE, "data.json"), encoding="utf-8-sig") as handle:
        data = json.load(handle)
    for field in data.get("fields", []):
        if str(field.get("type", "")).lower() == "file":
            field["value"] = ""
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def add(zf, name, content, executable=False):
    info = zipfile.ZipInfo(f"{TOP}/{name}", date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3  # "made on Unix", so the Mac reads the permissions below
    info.external_attr = (0o100755 if executable else 0o100644) << 16
    zf.writestr(info, content)


def build(platform, files):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"{TOP}-{platform}.zip")
    with zipfile.ZipFile(path, "w") as zf:
        for name in COMMON + files:
            with open(os.path.join(HERE, name), "rb") as handle:
                add(zf, name, handle.read(), executable=name.endswith(EXECUTABLE))
        add(zf, "data.json", friend_data().encode("utf-8"))
    return path


if __name__ == "__main__":
    for platform, files in PLATFORMS.items():
        print("built", build(platform, files))

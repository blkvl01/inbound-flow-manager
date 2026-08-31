"""
Downloads Bootstrap Cyborg CSS and Inter font files into assets/vendor/ and assets/fonts/
so the portable EXE has no external CDN dependencies.
Run this before PyInstaller build (called automatically by build.bat).
"""
import os
import re
import sys
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(BASE, "assets")
VENDOR = os.path.join(ASSETS, "vendor")
FONTS  = os.path.join(ASSETS, "fonts")

os.makedirs(VENDOR, exist_ok=True)
os.makedirs(FONTS,  exist_ok=True)


def download(url, dest, label):
    print(f"  Downloading {label}...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    with open(dest, "wb") as f:
        f.write(data)
    print(f"  OK  ({len(data)//1024} KB)")


def keep_existing_or_fail(dest, label, exc):
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        print(f"  Offline fallback: keeping existing {label}")
        return True
    print(f"  ERROR: {label} is missing and download failed: {exc}", file=sys.stderr)
    return False


# ── 1. Bootstrap Cyborg ──────────────────────────────────────────────────────
print("[1/2] Bootstrap Cyborg CSS")
CYBORG_URL = "https://cdn.jsdelivr.net/npm/bootswatch@5.3.3/dist/cyborg/bootstrap.min.css"
cyborg_dest = os.path.join(VENDOR, "bootstrap-cyborg.min.css")
try:
    download(CYBORG_URL, cyborg_dest, "bootstrap-cyborg.min.css")
except Exception as exc:
    if not keep_existing_or_fail(cyborg_dest, "bootstrap-cyborg.min.css", exc):
        raise

# ── 2. Inter font (Google Fonts → local woff2 + CSS) ─────────────────────────
print("[2/2] Inter font")
INTER_URL = (
    "https://fonts.googleapis.com/css2"
    "?family=Inter:wght@300;400;500;600;700;800;900&display=swap"
)
req = urllib.request.Request(INTER_URL, headers={
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/124 Safari/537.36"
    )
})
inter_css_dest = os.path.join(VENDOR, "inter-font.css")
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        inter_css = r.read().decode("utf-8")

    woff2_urls = re.findall(r'url\((https://[^)]+\.woff2[^)]*)\)', inter_css)
    print(f"  Found {len(woff2_urls)} woff2 files")

    for i, url in enumerate(woff2_urls):
        fname = f"inter_{i:02d}.woff2"
        dest  = os.path.join(FONTS, fname)
        download(url, dest, fname)
        inter_css = inter_css.replace(url, f"../fonts/{fname}")

    # Write the modified font CSS (references local woff2 files)
    with open(inter_css_dest, "w", encoding="utf-8") as f:
        f.write(inter_css)
    print(f"  Saved inter-font.css")
except Exception as exc:
    existing_fonts = [
        name for name in os.listdir(FONTS)
        if name.lower().endswith(".woff2") and os.path.getsize(os.path.join(FONTS, name)) > 0
    ] if os.path.isdir(FONTS) else []
    if os.path.exists(inter_css_dest) and os.path.getsize(inter_css_dest) > 0 and existing_fonts:
        print(f"  Offline fallback: keeping existing Inter CSS + {len(existing_fonts)} font files")
    else:
        print(f"  ERROR: Inter font assets are missing and download failed: {exc}", file=sys.stderr)
        raise

print()
print("All vendor assets ready.")

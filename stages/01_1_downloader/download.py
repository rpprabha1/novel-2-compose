"""
Video Finder & Downloader — Search YouTube by description and download clips.
Uses yt-dlp for both search and download (no API key needed).

Install dependencies:
    pip install yt-dlp

Optional (needed for merging/converting):
    Install ffmpeg — https://ffmpeg.org/download.html

Usage:
    python download.py                              # interactive mode
    python download.py "dog running uphill forest"  # one-shot: download top 3 matches
"""

from __future__ import annotations

import sys
import json
from pathlib import Path

try:
    import yt_dlp
    YT_DLP_AVAILABLE = True
except ImportError:
    YT_DLP_AVAILABLE = False


# ── Default download folder ────────────────────────────────────────────
DOWNLOAD_DIR = Path(__file__).parent / "outputs"

# How many of the top-ranked search results one-shot / batch mode downloads.
TOP_N_DEFAULT = 3

# YouTube increasingly blocks the default "web" client with a "page needs to be
# reloaded" extraction error; the android client still resolves formats
# reliably. Applied to the download call so clips actually fetch. If YouTube
# shifts again, swap the client here (e.g. "ios", "tv", "mweb").
YT_EXTRACTOR_ARGS = {"youtube": {"player_client": ["android"]}}


# ── Search ──────────────────────────────────────────────────────────────
def search_videos(query: str, max_results: int = 10) -> list[dict]:
    """Search YouTube for videos matching *query* using yt-dlp."""
    if not YT_DLP_AVAILABLE:
        return []

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }

    results = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
        for entry in (info.get("entries") or []):
            results.append({
                "title":     entry.get("title", "No title"),
                "url":       f"https://www.youtube.com/watch?v={entry.get('id', '')}",
                "channel":   entry.get("uploader") or entry.get("channel", "Unknown"),
                "duration":  _fmt_duration(entry.get("duration")),
                "views":     entry.get("view_count"),
                "description": entry.get("description", ""),
            })
    return results


def _fmt_duration(seconds) -> str:
    if not seconds:
        return "N/A"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ── Display ─────────────────────────────────────────────────────────────
def display_results(results: list[dict]) -> None:
    if not results:
        print("\n  No videos found. Try a different description.\n")
        return

    print(f"\n  Found {len(results)} video(s):\n")
    print("=" * 72)

    for i, video in enumerate(results, 1):
        title    = video.get("title", "No title")
        url      = video.get("url", "N/A")
        channel  = video.get("channel", "Unknown")
        duration = video.get("duration", "N/A")
        views    = video.get("views")
        desc     = video.get("description", "")

        print(f"  [{i}]  {title}")
        print(f"       Channel  : {channel}")
        print(f"       Duration : {duration}")
        if views is not None:
            print(f"       Views    : {views:,}")
        print(f"       URL      : {url}")
        if desc:
            short = (desc[:120] + "…") if len(desc) > 120 else desc
            print(f"       Summary  : {short}")
        print("-" * 72)

    print()


# ── Download helpers ────────────────────────────────────────────────────
QUALITY_PRESETS = {
    "1": {
        "label": "Best quality (video + audio)",
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    },
    "2": {
        "label": "720p (good balance)",
        "format": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/"
                  "best[height<=720][ext=mp4]/best",
    },
    "3": {
        "label": "480p (smaller file)",
        "format": "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/"
                  "best[height<=480][ext=mp4]/best",
    },
    "4": {
        "label": "Audio only (mp3)",
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    },
}


def _progress_hook(d: dict) -> None:
    if d["status"] == "downloading":
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        downloaded = d.get("downloaded_bytes", 0)
        speed = d.get("speed")
        eta = d.get("eta")

        if total > 0:
            pct = downloaded / total * 100
            bar_len = 30
            filled = int(bar_len * downloaded // total)
            bar = "█" * filled + "░" * (bar_len - filled)
            parts = [f"\r  ⬇  [{bar}] {pct:5.1f}%"]
        else:
            mb = downloaded / 1_048_576
            parts = [f"\r  ⬇  {mb:.1f} MB"]

        if speed:
            parts.append(f"  {speed / 1_048_576:.1f} MB/s")
        if eta:
            m, s = divmod(int(eta), 60)
            parts.append(f"  ETA {m}:{s:02d}")

        print("".join(parts), end="", flush=True)

    elif d["status"] == "finished":
        print("\r  ✅  Download complete — merging/converting …        ")


def pick_quality() -> dict:
    print("\n  Choose quality:")
    for key, preset in QUALITY_PRESETS.items():
        print(f"    [{key}] {preset['label']}")
    print()

    while True:
        choice = input("  Quality (1-4, default 1): ").strip() or "1"
        if choice in QUALITY_PRESETS:
            return QUALITY_PRESETS[choice]
        print("  Invalid choice — enter 1, 2, 3, or 4.")


def download_video(url: str, dest_dir: Path | None = None, preset: dict | None = None) -> None:
    if not YT_DLP_AVAILABLE:
        print("\n  ⚠  yt-dlp is not installed. Run:  pip install yt-dlp\n")
        return

    dest = dest_dir or DOWNLOAD_DIR
    dest.mkdir(parents=True, exist_ok=True)

    # Prompt for quality only when the caller hasn't already chosen one. Batch
    # downloads pick a preset once and pass it in, rather than re-asking per clip.
    if preset is None:
        preset = pick_quality()

    ydl_opts: dict = {
        "format": preset["format"],
        "outtmpl": str(dest / "%(title)s.%(ext)s"),
        "progress_hooks": [_progress_hook],
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "extractor_args": YT_EXTRACTOR_ARGS,
    }

    if "postprocessors" in preset:
        ydl_opts["postprocessors"] = preset["postprocessors"]

    print(f"\n  📂 Saving to : {dest}")
    print(f"  🔗 URL       : {url}\n")

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if preset.get("postprocessors"):
                filename = str(Path(filename).with_suffix(".mp3"))
            print(f"  📁 Saved as  : {filename}\n")
    except yt_dlp.utils.DownloadError as e:
        print(f"\n  ⚠  Download failed: {e}\n")
    except Exception as e:
        print(f"\n  ⚠  Unexpected error: {e}\n")


def download_prompt(results: list[dict]) -> None:
    if not results:
        return

    choice = input("  ⬇  Enter a number to download (or Enter to skip): ").strip()
    if not choice:
        return

    if not choice.isdigit() or not (1 <= int(choice) <= len(results)):
        print(f"  Invalid choice — pick 1 to {len(results)}.\n")
        return

    video = results[int(choice) - 1]
    url = video.get("url")
    if not url:
        print("  ⚠  No URL found for that video.\n")
        return

    download_video(url)


# ── Batch download the top-ranked results ───────────────────────────────
def download_top_results(
    results: list[dict],
    count: int = TOP_N_DEFAULT,
    dest_dir: Path | None = None,
    preset: dict | None = None,
) -> None:
    """Download the first *count* results in search-rank order.

    Quality is chosen once and reused for the whole batch, so it's a single
    prompt instead of one per clip. Missing-URL entries are skipped, not fatal.
    """
    if not results:
        print("\n  Nothing to download.\n")
        return
    if not YT_DLP_AVAILABLE:
        print("\n  ⚠  yt-dlp is not installed. Run:  pip install yt-dlp\n")
        return

    top = results[:count]
    if preset is None:
        preset = pick_quality()

    print(f"\n  Downloading top {len(top)} result(s) …")
    for i, video in enumerate(top, 1):
        title = video.get("title", "No title")
        url = video.get("url")
        print(f"\n  ── [{i}/{len(top)}] {title}")
        if not url:
            print("  ⚠  No URL for that result — skipping.\n")
            continue
        download_video(url, dest_dir, preset=preset)


# ── Save results ────────────────────────────────────────────────────────
def save_results(results: list[dict], filename: str = "video_results.json") -> None:
    dest = DOWNLOAD_DIR / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"  Results saved to {dest}\n")


# ── Interactive loop ────────────────────────────────────────────────────
def interactive_mode() -> None:
    dl_tag = "✅ ready" if YT_DLP_AVAILABLE else "❌ pip install yt-dlp"

    print("\n╔══════════════════════════════════════════════════╗")
    print("║        🎬  Video Finder & Downloader            ║")
    print("╠══════════════════════════════════════════════════╣")
    print("║  Type a description to search YouTube.          ║")
    print("║                                                  ║")
    print("║  Commands:                                       ║")
    print("║    /dl N       — download result #N              ║")
    print("║    /dltop [N]  — download the top N (default 3)  ║")
    print("║    /dl URL     — download any video URL          ║")
    print("║    /save       — save last results to JSON       ║")
    print("║    /num N      — results per search (1–20)       ║")
    print("║    /dir PATH   — change download folder          ║")
    print("║    /quit       — exit                            ║")
    print("╠══════════════════════════════════════════════════╣")
    print(f"║  Downloads : {dl_tag:<37}║")
    print(f"║  Save to   : {'stages/01_1_downloader/outputs':<37}║")
    print("╚══════════════════════════════════════════════════╝\n")

    max_results = 10
    last_results: list[dict] = []
    download_dir = DOWNLOAD_DIR

    while True:
        try:
            query = input("  🔍 Search or command: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n  Goodbye!\n")
            break

        if not query:
            continue

        low = query.lower()

        if low in ("/quit", "/exit", "/q"):
            print("  Goodbye!\n")
            break

        if low == "/save":
            if last_results:
                save_results(last_results)
            else:
                print("  Nothing to save yet.\n")
            continue

        if low.startswith("/num"):
            parts = query.split()
            if len(parts) == 2 and parts[1].isdigit():
                max_results = min(max(int(parts[1]), 1), 20)
                print(f"  Results per search set to {max_results}.\n")
            else:
                print("  Usage: /num N  (1–20)\n")
            continue

        if low.startswith("/dir"):
            parts = query.split(maxsplit=1)
            if len(parts) == 2:
                download_dir = Path(parts[1]).expanduser()
                print(f"  Download folder → {download_dir}\n")
            else:
                print(f"  Current folder: {download_dir}")
                print("  Usage: /dir ~/Videos\n")
            continue

        if low.startswith("/dltop"):
            parts = query.split()
            count = TOP_N_DEFAULT
            if len(parts) == 2 and parts[1].isdigit():
                count = max(int(parts[1]), 1)
            elif len(parts) >= 2:
                print("  Usage: /dltop [N]  (defaults to 3)\n")
                continue
            if not last_results:
                print("  Run a search first.\n")
            else:
                download_top_results(last_results, count=count, dest_dir=download_dir)
            continue

        if low.startswith("/dl"):
            parts = query.split(maxsplit=1)
            arg = parts[1].strip() if len(parts) > 1 else ""

            if arg.startswith("http"):
                download_video(arg, download_dir)
                continue

            if arg.isdigit():
                idx = int(arg)
                if not last_results:
                    print("  Run a search first.\n")
                elif 1 <= idx <= len(last_results):
                    video = last_results[idx - 1]
                    url = video.get("url")
                    if url:
                        download_video(url, download_dir)
                    else:
                        print("  ⚠  No URL for that result.\n")
                else:
                    print(f"  Pick 1–{len(last_results)}.\n")
            else:
                print("  Usage:  /dl 3   or   /dl https://…\n")
            continue

        print(f'\n  Searching YouTube: "{query}" …')
        try:
            last_results = search_videos(query, max_results=max_results)
            display_results(last_results)
            if last_results:
                print("  💡 Type /dl N to download (e.g. /dl 1)\n")
        except Exception as e:
            print(f"\n  ⚠ Search error: {e}\n")


# ── Entry point ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        print(f'\n  Searching YouTube: "{query}" …')
        try:
            results = search_videos(query)
            display_results(results)
            download_top_results(results)
        except Exception as e:
            print(f"\n  ⚠ Search error: {e}\n")
            sys.exit(1)
    else:
        interactive_mode()

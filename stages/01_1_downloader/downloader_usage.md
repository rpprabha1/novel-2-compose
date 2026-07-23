# Downloader — Usage Guide

A small command-line tool that searches YouTube by description and downloads
clips. This guide covers how to run it and where the results land. Nothing
here describes how it works internally.

---

## Before you start

- **Python 3.10 or newer** is recommended.
- Install the downloader's one dependency:
  ```
  pip install yt-dlp
  ```
- Install **ffmpeg** so downloaded video and audio can be merged/converted:
  https://ffmpeg.org/download.html

---

## Running it

From the `stages/01_1_downloader/` folder.

### Quick search + download (one-shot)

Pass your description in quotes:

```
python download.py "a crow throwing stones in bottle"
```

This searches, lists what it found, and downloads the **top 3 matches**. You'll
be asked once to choose a quality (see below), and that choice applies to all
three clips.

### Interactive mode

Run it with no description to open the menu:

```
python download.py
```

Type any description at the prompt to search. Then use the commands below.

| Command | What it does |
|---|---|
| `/dl N` | Download result number **N** from the last search |
| `/dltop [N]` | Download the top **N** results (defaults to **3**) |
| `/dl URL` | Download any video from a pasted link |
| `/save` | Save the last search results to a file |
| `/num N` | Set how many results each search returns (1–20) |
| `/dir PATH` | Change the download folder |
| `/quit` | Exit |

---

## Choosing quality

When a download starts you'll be asked to pick one:

1. **Best quality** (video + audio)
2. **720p** (good balance)
3. **480p** (smaller file)
4. **Audio only** (mp3)

Press Enter to accept the default (Best quality).

---

## Where the output goes

- Downloaded videos (and any saved results file) are placed in:
  ```
  stages/01_1_downloader/outputs/
  ```
- Each file is named after the video's title.
- To send downloads somewhere else, use `/dir PATH` in interactive mode.

---

## Windows display note

If the menu or progress bars show boxes or errors instead of symbols, your
terminal is using a non-UTF-8 code page. Fix it for the session with either:

```
chcp 65001
```

or by setting `PYTHONIOENCODING=utf-8` before running the tool. This only
affects how text is displayed — downloads work either way.

---

## Troubleshooting

- **"yt-dlp is not installed"** — run `pip install yt-dlp`.
- **Merging/converting fails** — make sure ffmpeg is installed and on your PATH.
- **A download is refused by YouTube** — try again; if it persists, the tool
  may need updating (`pip install -U yt-dlp`).

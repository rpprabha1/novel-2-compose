#!/usr/bin/env python3
"""Cross-platform environment setup for novel-2-compose.

Detects the machine's OS and hardware (RAM, GPU/VRAM), then:
  1. Bootstraps config/.env from config/.env.example (never overwrites).
  2. Installs core Python dependencies (requirements.txt), and the ML tier
     (requirements-ml.txt) unless --core-only is passed.
  3. Ensures ffmpeg/ffprobe are on PATH, attempting an OS-appropriate
     package-manager install if missing.
  4. Ensures Ollama is installed and running, pulls a model sized to the
     detected hardware (same "fit this machine" reasoning already used by
     config/agents.yaml, config/embeddings.yaml, config/image_gen.yaml,
     config/tts.yaml - see ARCHITECTURE.md's change log for precedent).
  5. Downloads the configured Piper TTS voice model into shared/models/piper/.

Every step is best-effort and independently reported: one failing step
never aborts the rest, since this script may run on a machine missing
several optional package managers. Nothing here is silently destructive -
existing config/.env and config/agents.yaml are never overwritten unless
you pass --apply-model-config, and every package-manager install is
printed before it runs.

Usage:
    python scripts/setup.py                  # full setup, sized to this machine
    python scripts/setup.py --core-only       # skip ffmpeg/ollama/ML/piper entirely
    python scripts/setup.py --dry-run         # detect + report only, no installs
    python scripts/setup.py --apply-model-config   # also write the recommended
                                                     # Ollama model into config/agents.yaml
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class StepResult:
    name: str
    status: str  # "ok" | "installed" | "skipped" | "failed" | "manual"
    detail: str = ""


RESULTS: list[StepResult] = []


def record(name: str, status: str, detail: str = "") -> None:
    RESULTS.append(StepResult(name, status, detail))
    marker = {"ok": "[ok]", "installed": "[+]", "skipped": "[-]", "failed": "[FAIL]", "manual": "[MANUAL]"}[status]
    print(f"{marker} {name}" + (f" - {detail}" if detail else ""))


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    # encoding/errors explicit: several tools we shell out to (ollama pull's
    # progress bar, winget) print Unicode box-drawing/spinner characters that
    # Windows' default text-mode decode (the console's cp1252 codepage, not
    # UTF-8) can't handle - undeclared this crashes a subprocess reader
    # thread with UnicodeDecodeError even though the command itself succeeds.
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kwargs)


def which(name: str) -> str | None:
    return shutil.which(name)


# ---------------------------------------------------------------------------
# Hardware detection
# ---------------------------------------------------------------------------

def detect_os() -> str:
    system = platform.system()
    return {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(system, system.lower())


def detect_ram_gb() -> float:
    system = platform.system()
    try:
        if system == "Windows":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return round(stat.ullTotalPhys / (1024 ** 3), 1)
        elif system == "Darwin":
            out = run(["sysctl", "-n", "hw.memsize"]).stdout.strip()
            return round(int(out) / (1024 ** 3), 1)
        else:  # Linux and anything /proc-compatible
            meminfo = Path("/proc/meminfo").read_text(encoding="utf-8")
            m = re.search(r"MemTotal:\s+(\d+)\s+kB", meminfo)
            if m:
                return round(int(m.group(1)) / (1024 ** 2), 1)
    except Exception:
        pass
    return 0.0


def detect_vram_gb() -> float:
    """Best-effort NVIDIA-only detection via nvidia-smi. Returns 0.0 (unknown/
    no discrete GPU) otherwise - Ollama still runs fine on CPU+RAM, just
    slower, so this is a bonus signal, not a requirement."""
    if not which("nvidia-smi"):
        return 0.0
    try:
        out = run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"]).stdout.strip()
        first_line = out.splitlines()[0]
        return round(int(first_line.strip()) / 1024, 1)
    except Exception:
        return 0.0


def recommend_ollama_model(ram_gb: float, vram_gb: float) -> str:
    """Tiered by system RAM (Ollama offloads to CPU+RAM when VRAM is
    insufficient, so RAM is the safe floor), with a bump if a decent GPU is
    present. Calibrated against the one documented real data point in
    config/agents.yaml: 2GB VRAM + ~8GB RAM -> llama3.2:3b comfortably."""
    if ram_gb < 8:
        tier = "llama3.2:1b"
    elif ram_gb < 16:
        tier = "llama3.2:3b"
    else:
        tier = "llama3.1:8b"
    if vram_gb >= 8 and tier == "llama3.2:1b":
        tier = "llama3.2:3b"
    return tier


def detect_package_manager(os_name: str) -> str | None:
    if os_name == "windows":
        if which("winget"):
            return "winget"
        if which("choco"):
            return "choco"
    elif os_name == "macos":
        if which("brew"):
            return "brew"
    elif os_name == "linux":
        for candidate in ("apt-get", "dnf", "yum", "pacman", "zypper"):
            if which(candidate):
                return candidate
    return None


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def step_env_file(dry_run: bool) -> None:
    env_path = REPO_ROOT / "config" / ".env"
    example_path = REPO_ROOT / "config" / ".env.example"
    if env_path.exists():
        record("config/.env", "ok", "already exists, left untouched")
        return
    if dry_run:
        record("config/.env", "manual", f"would copy from {example_path.name}")
        return
    env_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
    record("config/.env", "installed", "created from config/.env.example - fill in API keys before running stages 03/09")


def step_pip_requirements(core_only: bool, dry_run: bool) -> None:
    files = [REPO_ROOT / "requirements.txt"]
    if not core_only:
        files.append(REPO_ROOT / "requirements-ml.txt")
    for req_file in files:
        label = f"pip install -r {req_file.name}"
        if dry_run:
            record(label, "manual", "dry-run, not installed")
            continue
        cmd = [sys.executable, "-m", "pip", "install", "-r", str(req_file)]
        if req_file.name == "requirements-ml.txt":
            # torch CPU wheel - matches config/embeddings.yaml + config/image_gen.yaml,
            # both hardcoding device: cpu. Smaller download, no CUDA toolkit needed.
            cmd = [
                sys.executable, "-m", "pip", "install",
                "--extra-index-url", "https://download.pytorch.org/whl/cpu",
                "-r", str(req_file),
            ]
        result = run(cmd)
        if result.returncode == 0:
            record(label, "ok")
        else:
            record(label, "failed", result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "see output above")


def step_ffmpeg(os_name: str, pkg_mgr: str | None, dry_run: bool, skip: bool) -> None:
    if skip:
        record("ffmpeg/ffprobe", "skipped", "--skip-ffmpeg")
        return
    if which("ffmpeg") and which("ffprobe"):
        record("ffmpeg/ffprobe", "ok", "already on PATH")
        return

    install_cmd = None
    if os_name == "windows" and pkg_mgr == "winget":
        install_cmd = ["winget", "install", "--id", "Gyan.FFmpeg", "-e", "--silent", "--accept-package-agreements", "--accept-source-agreements"]
    elif os_name == "windows" and pkg_mgr == "choco":
        install_cmd = ["choco", "install", "ffmpeg", "-y"]
    elif os_name == "macos" and pkg_mgr == "brew":
        install_cmd = ["brew", "install", "ffmpeg"]
    elif os_name == "linux" and pkg_mgr == "apt-get":
        install_cmd = ["sudo", "apt-get", "install", "-y", "ffmpeg"]
    elif os_name == "linux" and pkg_mgr in ("dnf", "yum"):
        install_cmd = ["sudo", pkg_mgr, "install", "-y", "ffmpeg"]
    elif os_name == "linux" and pkg_mgr == "pacman":
        install_cmd = ["sudo", "pacman", "-S", "--noconfirm", "ffmpeg"]

    if install_cmd is None:
        record("ffmpeg/ffprobe", "manual", "no supported package manager detected - install ffmpeg (with ffprobe) manually and re-run")
        return
    if dry_run:
        record("ffmpeg/ffprobe", "manual", f"dry-run, would run: {' '.join(install_cmd)}")
        return

    print(f"    running: {' '.join(install_cmd)}")
    result = run(install_cmd)
    if which("ffmpeg") and which("ffprobe"):
        record("ffmpeg/ffprobe", "installed", f"via {pkg_mgr}")
    else:
        record("ffmpeg/ffprobe", "failed", (result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "not found on PATH after install - may need a new shell session"))


def _ollama_server_reachable(host: str = "http://localhost:11434") -> bool:
    try:
        urllib.request.urlopen(f"{host}/api/tags", timeout=3)
        return True
    except Exception:
        return False


def step_ollama(os_name: str, pkg_mgr: str | None, model: str, dry_run: bool, skip: bool, apply_model_config: bool) -> None:
    if skip:
        record("ollama", "skipped", "--skip-ollama")
        return

    if not which("ollama"):
        install_cmd = None
        if os_name == "windows" and pkg_mgr == "winget":
            install_cmd = ["winget", "install", "--id", "Ollama.Ollama", "-e", "--silent", "--accept-package-agreements", "--accept-source-agreements"]
        elif os_name == "windows" and pkg_mgr == "choco":
            install_cmd = ["choco", "install", "ollama", "-y"]
        elif os_name == "macos" and pkg_mgr == "brew":
            install_cmd = ["brew", "install", "ollama"]
        elif os_name == "linux":
            install_cmd = ["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"]

        if install_cmd is None:
            record("ollama", "manual", "no supported install path detected - see https://ollama.com/download")
            return
        if dry_run:
            record("ollama", "manual", f"dry-run, would run: {' '.join(install_cmd)}")
            return
        print(f"    running: {' '.join(install_cmd)}")
        run(install_cmd)
        if not which("ollama"):
            record("ollama", "failed", "not found on PATH after install - may need a new shell session")
            return
        record("ollama binary", "installed")
    else:
        record("ollama binary", "ok", "already on PATH")

    if dry_run:
        record(f"ollama pull {model}", "manual", "dry-run, not pulled")
        return

    if not _ollama_server_reachable():
        try:
            subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(10):
                time.sleep(1)
                if _ollama_server_reachable():
                    break
        except Exception:
            pass

    if not _ollama_server_reachable():
        record("ollama server", "manual", "not reachable at localhost:11434 - start it with `ollama serve` and re-run")
        return

    result = run(["ollama", "pull", model])
    if result.returncode == 0:
        record(f"ollama pull {model}", "ok")
    else:
        record(f"ollama pull {model}", "failed", result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "see output above")
        return

    if apply_model_config:
        _apply_ollama_model_to_config(model)
    else:
        record("config/agents.yaml", "manual", f"recommended model for this machine is {model} - re-run with --apply-model-config to write it, or edit config/agents.yaml's default_model yourself")


def _apply_ollama_model_to_config(model: str) -> None:
    """Updates both ollama.default_model AND every stage_models entry.
    config/agents.yaml's stage_models section overrides default_model per
    stage (02/06/07/09 are all currently pinned there) - resolve_model()
    prefers stage_models when a stage is listed, so only touching
    default_model would silently leave every real stage on the old model."""
    agents_yaml = REPO_ROOT / "config" / "agents.yaml"
    lines = agents_yaml.read_text(encoding="utf-8").splitlines(keepends=True)
    pattern = re.compile(r"^(\s*(?:default_model|\d{2}_[a-z_]+):\s*)(\S+)(\s*\n?)$")
    changed = 0
    for i, line in enumerate(lines):
        m = pattern.match(line)
        if m:
            lines[i] = f"{m.group(1)}{model}{m.group(3)}"
            changed += 1
    if changed == 0:
        record("config/agents.yaml", "failed", "no default_model/stage_models lines found - edit manually")
        return
    agents_yaml.write_text("".join(lines), encoding="utf-8")
    record("config/agents.yaml", "installed", f"updated {changed} model reference(s) (default_model + all stage_models) to {model}")


def step_piper_voice(dry_run: bool, skip: bool, core_only: bool) -> None:
    if skip or core_only:
        record("piper voice model", "skipped", "--skip-piper-voice or --core-only")
        return

    import yaml  # PyYAML is a core dependency, already installed by this point

    tts_cfg = yaml.safe_load((REPO_ROOT / "config" / "tts.yaml").read_text(encoding="utf-8"))
    model_path = REPO_ROOT / tts_cfg["model_path"]
    config_path = REPO_ROOT / tts_cfg["config_path"]
    voice_name = model_path.stem  # e.g. "en_US-lessac-medium"

    if model_path.exists() and config_path.exists():
        record("piper voice model", "ok", f"{voice_name} already present")
        return
    if dry_run:
        record("piper voice model", "manual", f"dry-run, would download {voice_name}")
        return

    model_path.parent.mkdir(parents=True, exist_ok=True)
    if not which("piper") and shutil.which(sys.executable):
        # piper-tts (pip package) exposes its voice downloader as a module,
        # independent of whether the `piper` console-script is also on PATH.
        pass
    result = run([sys.executable, "-m", "piper.download_voices", voice_name], cwd=str(model_path.parent))
    if result.returncode == 0 and model_path.exists():
        record("piper voice model", "installed", voice_name)
    else:
        record(
            "piper voice model",
            "manual",
            f"automated download failed - fetch {voice_name}.onnx (+ .onnx.json) manually into {model_path.parent} "
            "(see https://github.com/rhasspy/piper for voice sources)",
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--core-only", action="store_true", help="skip ffmpeg/ollama/ML deps/piper entirely - just core pip requirements + config/.env")
    parser.add_argument("--skip-ffmpeg", action="store_true")
    parser.add_argument("--skip-ollama", action="store_true")
    parser.add_argument("--skip-piper-voice", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="detect and report only, no installs")
    parser.add_argument("--apply-model-config", action="store_true", help="write the recommended Ollama model into config/agents.yaml")
    args = parser.parse_args()

    os_name = detect_os()
    ram_gb = detect_ram_gb()
    vram_gb = detect_vram_gb()
    model = recommend_ollama_model(ram_gb, vram_gb)
    pkg_mgr = detect_package_manager(os_name)

    print("=== novel-2-compose setup ===")
    print(f"OS: {os_name}   RAM: {ram_gb or 'unknown'} GB   GPU VRAM: {vram_gb or 'none/unknown'} GB   package manager: {pkg_mgr or 'none detected'}")
    print(f"Recommended Ollama model for this machine: {model}")
    print()

    step_env_file(args.dry_run)
    step_pip_requirements(args.core_only, args.dry_run)
    step_ffmpeg(os_name, pkg_mgr, args.dry_run, args.skip_ffmpeg or args.core_only)
    step_ollama(os_name, pkg_mgr, model, args.dry_run, args.skip_ollama or args.core_only, args.apply_model_config)
    step_piper_voice(args.dry_run, args.skip_piper_voice, args.core_only)

    print("\n=== Summary ===")
    for r in RESULTS:
        marker = {"ok": "[ok]", "installed": "[+]", "skipped": "[-]", "failed": "[FAIL]", "manual": "[MANUAL]"}[r.status]
        print(f"{marker:9} {r.name}" + (f" - {r.detail}" if r.detail else ""))

    manual_or_failed = [r for r in RESULTS if r.status in ("manual", "failed")]
    if manual_or_failed:
        print(f"\n{len(manual_or_failed)} item(s) need attention (see [MANUAL]/[FAIL] above).")
    else:
        print("\nAll steps completed automatically.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

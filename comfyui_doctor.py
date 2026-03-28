#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════╗
║              ComfyUI Doctor — Universal Repair & Launcher            ║
║  Finds, fixes, updates and launches any ComfyUI installation         ║
║  Works with portable, standalone, venv, and system installs          ║
╚══════════════════════════════════════════════════════════════════════╝
"""

import os
import sys
import re
import json
import time
import shutil
import signal
import hashlib
import logging
import platform
import argparse
import textwrap
import importlib
import importlib.util
import subprocess
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

# Global flag for Ctrl+C handling
_shutdown_requested = False


def _signal_handler(signum, frame):
    global _shutdown_requested
    print("\n\nCtrl+C detected! Shutting down...", flush=True)
    _shutdown_requested = True


# Register signal handlers
if platform.system() != "Windows":
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)
else:
    signal.signal(signal.SIGINT, _signal_handler)
    # SIGTERM is not available on Windows

# Fix Windows console encoding for unicode characters
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    # Try to set console mode for UTF-8
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass

# ─────────────────────────── Logging Setup ────────────────────────────

LOG_DIR = Path.home() / ".comfyui_doctor_logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"comfyui_doctor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger("comfyui_doctor")

# ─────────────────────────── Console Colours ──────────────────────────


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    MAGENTA = "\033[95m"


def _c(color, msg):
    return f"{color}{msg}{C.RESET}"


def banner():
    print(
        _c(
            C.CYAN,
            C.BOLD
            + """
╔══════════════════════════════════════════════════════════════════════╗
║          🩺  ComfyUI Doctor — Universal Repair & Launcher            ║
╚══════════════════════════════════════════════════════════════════════╝
"""
            + C.RESET,
        )
    )


def section(title: str):
    bar = "─" * 62
    print(f"\n{_c(C.BLUE, bar)}")
    print(_c(C.BOLD + C.WHITE, f"  {title}"))
    print(_c(C.BLUE, bar))
    log.info(f"=== {title} ===")


def ok(msg):
    print(_c(C.GREEN, f"  ✔  {msg}"))
    log.info(f"OK: {msg}")


def warn(msg):
    print(_c(C.YELLOW, f"  ⚠  {msg}"))
    log.warning(f"WARN: {msg}")


def err(msg):
    print(_c(C.RED, f"  ✖  {msg}"))
    log.error(f"ERR: {msg}")


def info(msg):
    print(_c(C.CYAN, f"  ℹ  {msg}"))
    log.info(f"INFO: {msg}")


def fix(msg):
    print(_c(C.MAGENTA, f"  🔧 {msg}"))
    log.info(f"FIX: {msg}")


def step(msg):
    print(_c(C.WHITE, f"     {msg}"))
    log.debug(f"STEP: {msg}")


def dim(msg):
    print(_c(C.DIM, f"     {msg}"))
    log.debug(msg)


# ══════════════════════════════════════════════════════════════════════
#  PHASE 1 — DISCOVERY
# ══════════════════════════════════════════════════════════════════════


class ComfyInstall:
    """Holds everything discovered about the ComfyUI installation."""

    def __init__(self):
        self.comfy_root: Optional[Path] = None
        self.custom_nodes_dir: Optional[Path] = None
        self.python_exe: Optional[Path] = None
        self.pip_exe: Optional[Path] = None
        self.main_script: Optional[Path] = None
        self.is_portable: bool = False
        self.venv_dir: Optional[Path] = None
        self.git_exe: Optional[str] = "git"
        self.os_name: str = platform.system()  # Windows / Linux / Darwin
        self.arch: str = platform.machine()


def find_git() -> str:
    """Return path to git or raise."""
    for candidate in [
        "git",
        "/usr/bin/git",
        "/usr/local/bin/git",
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
    ]:
        if shutil.which(candidate):
            return candidate
    return "git"  # let it fail naturally later with a clear error


def _is_comfy_root(path: Path) -> bool:
    """Heuristic: a directory looks like ComfyUI root if it has main.py + comfy/ subdir."""
    return (path / "main.py").exists() and (path / "comfy").is_dir()


def discover_comfy_root(hint: Optional[Path] = None) -> Optional[Path]:
    """
    Search strategy (most-specific first):
    1. Explicit --path argument
    2. CWD or any ancestor up to filesystem root
    3. Common default install locations on each OS
    4. Recursive scan of drives / home (slow, last resort)
    """
    candidates = []

    if hint:
        candidates.append(hint)

    # Walk up from CWD
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidates.append(parent)

    # Common locations
    home = Path.home()
    common = [
        home / "ComfyUI",
        home / "comfyui",
        home / "Desktop" / "ComfyUI",
        home / "Desktop" / "ComfyUI_portable",
        Path("/opt/ComfyUI"),
        Path("/opt/comfyui"),
        Path("C:/ComfyUI"),
        Path("C:/ComfyUI_portable"),
        Path("C:/Users/Public/ComfyUI"),
        Path("D:/ComfyUI"),
        Path("D:/ComfyUI_portable"),
        home / "Documents" / "ComfyUI",
        home / "stable-diffusion-webui",  # sometimes nested
    ]

    # Check sibling directories next to the script location (common for portable installs)
    script_dir = Path(__file__).parent.resolve()
    for sibling in script_dir.iterdir():
        if sibling.is_dir() and _is_comfy_root(sibling):
            candidates.append(sibling)
    candidates.extend(common)

    for p in candidates:
        try:
            if p.exists() and _is_comfy_root(p):
                return p
        except PermissionError:
            continue

    # Last resort: scan home directory tree (depth ≤ 5)
    info("Doing deep scan of home directory for ComfyUI (this may take a moment)…")
    for root, dirs, files in os.walk(home):
        depth = len(Path(root).relative_to(home).parts)
        if depth > 5:
            dirs.clear()
            continue
        dirs[:] = [
            d
            for d in dirs
            if not d.startswith(".")
            and d not in ("__pycache__", "node_modules", ".git", "venv", ".venv")
        ]
        if "main.py" in files and _is_comfy_root(Path(root)):
            return Path(root)

    return None


def _find_py_in_dir(d: Path) -> tuple[Optional[Path], Optional[Path]]:
    """Given a directory, return (python_exe, pip_exe) or (None, None)."""
    os_name = platform.system()
    if os_name == "Windows":
        py_candidates = [d / "python.exe", d / "Scripts" / "python.exe"]
        pip_candidates = [
            d / "Scripts" / "pip.exe",
            d / "pip.exe",
            d / "Scripts" / "pip3.exe",
        ]
    else:
        py_candidates = [
            d / "bin" / "python3",
            d / "bin" / "python",
            d / "python3",
            d / "python",
        ]
        pip_candidates = [d / "bin" / "pip3", d / "bin" / "pip", d / "pip3", d / "pip"]
    py = next((p for p in py_candidates if p.exists()), None)
    pip = next((p for p in pip_candidates if p.exists()), None)
    return (py, pip) if py else (None, None)


def discover_python(comfy_root: Path) -> tuple[Optional[Path], Optional[Path]]:
    """
    Find the Python interpreter that belongs to this ComfyUI install.

    The ComfyUI Windows portable release (2024+) uses this layout:
      ComfyUI_Windows_portable/
        ComfyUI/            <- comfy_root
        python_standalone/  <- Python lives HERE, as a sibling of ComfyUI/

    Search order (first match wins):
      1. Sibling dirs next to comfy_root  (python_standalone, python_embeded, etc.)
      2. Same names as subdirs inside comfy_root
      3. venv / virtualenv inside comfy_root
      4. System Python in PATH (last resort — may be wrong environment)
    """
    PORTABLE_NAMES = [
        "python_standalone",  # ComfyUI Windows portable 2024+
        "python_embeded",  # older portable (original typo)
        "python_embedded",  # corrected spelling
        "python",  # some repacks
        "Python",
        "py",
    ]

    # 1. Sibling of comfy_root (most common for portable installs)
    for name in PORTABLE_NAMES:
        d = comfy_root.parent / name
        if d.is_dir():
            py, pip = _find_py_in_dir(d)
            if py:
                log.info(f"Found portable Python (sibling): {py}")
                return py, pip

    # 2. Inside comfy_root
    for name in PORTABLE_NAMES:
        d = comfy_root / name
        if d.is_dir():
            py, pip = _find_py_in_dir(d)
            if py:
                log.info(f"Found portable Python (inside root): {py}")
                return py, pip

    # 3. venv inside comfy_root
    for venv_name in ["venv", ".venv", "env", ".env"]:
        d = comfy_root / venv_name
        if d.is_dir():
            py, pip = _find_py_in_dir(d)
            if py:
                log.info(f"Found venv Python: {py}")
                return py, pip

    # 4. System Python (last resort)
    warn(
        "No portable/venv Python found next to ComfyUI — falling back to system Python."
    )
    warn("If wrong, pass the path explicitly:  --path /path/to/ComfyUI")
    for candidate in ["python3", "python", sys.executable]:
        resolved = shutil.which(candidate)
        if resolved:
            py = Path(resolved)
            pip_candidate = shutil.which("pip3") or shutil.which("pip")
            return py, (Path(pip_candidate) if pip_candidate else None)

    return None, None


def build_install(hint: Optional[Path] = None) -> ComfyInstall:
    inst = ComfyInstall()
    inst.git_exe = find_git()

    section("1 / 5  —  Discovering ComfyUI Installation")

    root = discover_comfy_root(hint)
    if not root:
        err("Could not find ComfyUI root directory.")
        info(
            "Try running from inside the ComfyUI folder, or pass --path /path/to/ComfyUI"
        )
        sys.exit(1)

    inst.comfy_root = root
    inst.custom_nodes_dir = root / "custom_nodes"
    inst.main_script = root / "main.py"
    ok(f"ComfyUI root: {root}")

    py, pip = discover_python(root)
    if not py:
        err("Could not find a Python interpreter for this install.")
        sys.exit(1)

    inst.python_exe = py
    inst.pip_exe = pip
    ok(f"Python:       {py}")

    # Detect portable
    for embed in ["python_embeded", "python_embedded", "python"]:
        if embed in str(py).lower():
            inst.is_portable = True
            break
    if "venv" in str(py).lower() or ".venv" in str(py).lower():
        inst.venv_dir = py.parent.parent

    ok(
        f"Install type: {'Portable / embedded' if inst.is_portable else 'venv / system'}"
    )

    if not inst.custom_nodes_dir.exists():
        warn("custom_nodes/ directory not found — creating it.")
        inst.custom_nodes_dir.mkdir(parents=True, exist_ok=True)

    log.info(f"comfy_root={root}  python={py}  portable={inst.is_portable}")
    return inst


# ══════════════════════════════════════════════════════════════════════
#  PHASE 2 — ENVIRONMENT SCAN  (GPU detection + PyTorch mismatch fix)
# ══════════════════════════════════════════════════════════════════════


def detect_gpu_hardware() -> dict:
    """
    Detect GPU vendor/model directly from the OS — completely independent
    of PyTorch.  Works on Windows (wmic + dxdiag), Linux (lspci), macOS (system_profiler).
    Returns dict with keys: vendor, name, vram_gb, cuda_capable, rocm_capable, arc_capable
    """
    gpu = {
        "vendor": "unknown",
        "name": "unknown",
        "vram_gb": 0,
        "cuda_capable": False,
        "rocm_capable": False,
        "arc_capable": False,
        "mps_capable": False,
    }

    os_name = platform.system()

    # ── Windows GPU detection ──────────────────────────────────────────
    if os_name == "Windows":
        # Strategy 1: nvidia-smi (most accurate for NVIDIA — no 32-bit overflow)
        try:
            r = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                for line in r.stdout.strip().splitlines():
                    parts = line.split(",")
                    if len(parts) >= 2:
                        name = parts[0].strip()
                        try:
                            vram_mb = int(parts[1].strip())
                            vram_gb = round(vram_mb / 1024, 1)
                        except ValueError:
                            vram_gb = 0
                        if name:
                            gpu["name"] = name
                            gpu["vram_gb"] = vram_gb
                            log.info(f"nvidia-smi: {name}  {vram_gb} GB")
                            break
        except Exception as e:
            log.warning(f"nvidia-smi failed: {e}")

        # Strategy 2: PowerShell Get-CimInstance (works for all vendors)
        # NOTE: Win32_VideoController.AdapterRAM is a UINT32 and overflows at 4GB.
        # We only use it for the GPU name here; VRAM from nvidia-smi takes priority.
        if gpu["name"] == "unknown":
            try:
                ps_cmd = (
                    "Get-CimInstance Win32_VideoController | "
                    "Select-Object Name,AdapterRAM | "
                    "ConvertTo-Json -Compress"
                )
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps_cmd],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if r.returncode == 0 and r.stdout.strip():
                    import json as _json

                    data = _json.loads(r.stdout.strip())
                    if isinstance(data, dict):
                        data = [data]
                    # Pick entry with most AdapterRAM (still useful for choosing discrete vs integrated)
                    best = max(
                        data, key=lambda x: int(x.get("AdapterRAM") or 0), default=None
                    )
                    if best and best.get("Name"):
                        gpu["name"] = best["Name"]
                        # Only use wmic VRAM if nvidia-smi didn't give us one
                        if gpu["vram_gb"] == 0:
                            raw = int(best.get("AdapterRAM") or 0)
                            gpu["vram_gb"] = round(raw / 1024**3, 1)
                        log.info(f"CimInstance GPU: {gpu['name']}")
            except Exception as e:
                log.warning(f"CimInstance gpu detection failed: {e}")

        # Strategy 3: wmic fallback (name only — VRAM unreliable due to 32-bit overflow)
        if gpu["name"] == "unknown":
            try:
                r = subprocess.run(
                    ["wmic", "path", "win32_VideoController", "get", "Name", "/value"],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                for line in r.stdout.splitlines():
                    if line.strip().startswith("Name=") and line.strip() != "Name=":
                        gpu["name"] = line.strip().split("=", 1)[1].strip()
                        break
            except Exception as e:
                log.warning(f"wmic fallback failed: {e}")

    # ── Linux: lspci ───────────────────────────────────────────────────
    elif os_name == "Linux":
        try:
            r = subprocess.run(["lspci"], capture_output=True, text=True, timeout=10)
            for line in r.stdout.splitlines():
                if any(kw in line for kw in ("VGA", "3D", "Display")):
                    gpu["name"] = line.split(":", 2)[-1].strip()
                    break
        except Exception:
            pass
        # Try nvidia-smi for VRAM
        try:
            r = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if r.returncode == 0 and r.stdout.strip():
                parts = r.stdout.strip().split(",")
                gpu["name"] = parts[0].strip()
                gpu["vram_gb"] = round(int(parts[1].strip()) / 1024, 1)
        except Exception:
            pass

    # ── macOS ──────────────────────────────────────────────────────────
    elif os_name == "Darwin":
        try:
            r = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            for line in r.stdout.splitlines():
                if "Chipset Model" in line:
                    gpu["name"] = line.split(":", 1)[-1].strip()
                if "VRAM" in line:
                    m = re.search(r"(\d+)\s*(MB|GB)", line, re.IGNORECASE)
                    if m:
                        val = int(m.group(1))
                        gpu["vram_gb"] = (
                            val if m.group(2).upper() == "GB" else round(val / 1024, 1)
                        )
            # Apple Silicon always has MPS
            if platform.processor() == "arm" or "Apple" in gpu["name"]:
                gpu["mps_capable"] = True
        except Exception:
            pass

    # ── Classify vendor ────────────────────────────────────────────────
    name_lower = gpu["name"].lower()
    if any(
        k in name_lower for k in ("nvidia", "geforce", "quadro", "tesla", "rtx", "gtx")
    ):
        gpu["vendor"] = "nvidia"
        gpu["cuda_capable"] = True
    elif any(k in name_lower for k in ("amd", "radeon", "rx ", "vega", "navi", "rdna")):
        gpu["vendor"] = "amd"
        gpu["rocm_capable"] = True
    elif any(k in name_lower for k in ("intel arc", "arc a", "xe")):
        gpu["vendor"] = "intel"
        gpu["arc_capable"] = True
    elif any(k in name_lower for k in ("apple", "m1", "m2", "m3", "m4")):
        gpu["vendor"] = "apple"
        gpu["mps_capable"] = True

    log.info(f"gpu_hardware={gpu}")
    return gpu


def get_recommended_torch(gpu: dict) -> tuple[list[str], str]:
    """
    Return (pip install command args, description) for the correct PyTorch
    build based on detected GPU hardware.
    """
    vendor = gpu["vendor"]

    if vendor == "nvidia":
        # Latest stable CUDA 12.1 build — works on CUDA 11.8+ drivers too
        return (
            [
                "torch",
                "torchvision",
                "torchaudio",
                "--index-url",
                "https://download.pytorch.org/whl/cu121",
            ],
            "PyTorch with CUDA 12.1 (NVIDIA GPU)",
        )
    elif vendor == "amd":
        if platform.system() == "Linux":
            return (
                [
                    "torch",
                    "torchvision",
                    "torchaudio",
                    "--index-url",
                    "https://download.pytorch.org/whl/rocm6.0",
                ],
                "PyTorch with ROCm 6.0 (AMD GPU, Linux)",
            )
        else:
            # ROCm not officially supported on Windows — DirectML is the alternative
            return (
                ["torch-directml"],
                "PyTorch DirectML (AMD GPU, Windows — limited support)",
            )
    elif vendor == "intel":
        return (
            [
                "torch",
                "torchvision",
                "torchaudio",
                "--index-url",
                "https://download.pytorch.org/whl/xpu",
            ],
            "PyTorch with XPU/Arc support (Intel GPU)",
        )
    elif vendor == "apple":
        # MPS is bundled in standard PyTorch on macOS
        return (
            ["torch", "torchvision", "torchaudio"],
            "PyTorch with MPS (Apple Silicon)",
        )
    else:
        return (
            ["torch", "torchvision", "torchaudio"],
            "PyTorch CPU (no discrete GPU detected)",
        )


def is_torch_mismatched(gpu: dict, torch_info: dict) -> bool:
    """
    Return True if the installed PyTorch build doesn't match the hardware.
    e.g. NVIDIA GPU present but +cpu build installed.
    """
    tv = torch_info.get("TORCH_VERSION", "")
    if not tv or tv == "NOT_INSTALLED":
        return False  # handled separately

    has_cuda_build = "+cu" in tv or torch_info.get("CUDA_AVAILABLE") == "True"
    has_rocm_build = "+rocm" in tv
    has_cpu_only = "+cpu" in tv or (
        not has_cuda_build
        and not has_rocm_build
        and torch_info.get("CUDA_AVAILABLE") != "True"
        and torch_info.get("MPS") != "True"
    )

    if gpu["cuda_capable"] and has_cpu_only:
        return True
    if gpu["rocm_capable"] and not has_rocm_build and platform.system() == "Linux":
        return True
    return False


def reinstall_torch(inst: ComfyInstall, gpu: dict):
    """Uninstall current torch and install the correct build for the GPU."""
    pkg_args, description = get_recommended_torch(gpu)
    fix(f"Reinstalling PyTorch: {description}")

    # Uninstall existing
    run_cmd(
        [
            str(inst.python_exe),
            "-m",
            "pip",
            "uninstall",
            "-y",
            "torch",
            "torchvision",
            "torchaudio",
            "torch-directml",
        ],
        env=_build_pip_env(inst),
        timeout=120,
    )

    # Install correct build
    cmd = [str(inst.python_exe), "-m", "pip", "install"] + pkg_args + ["--quiet"]
    rc, _, er = run_cmd(cmd, env=_build_pip_env(inst), timeout=900)
    if rc == 0:
        ok(f"PyTorch reinstalled successfully: {description}")
        return True
    else:
        err(f"PyTorch reinstall failed: {er[:300]}")
        return False


def scan_environment(inst: ComfyInstall) -> tuple[dict, dict]:
    """
    Returns (torch_info, gpu_info)
    Also auto-fixes PyTorch if the wrong build (e.g. +cpu) is installed
    for the detected hardware.
    """
    section("2 / 5  —  Scanning Hardware & Environment")

    # OS
    ok(
        f"OS:           {platform.system()} {platform.release()}  ({platform.machine()})"
    )

    # Python version
    r = subprocess.run(
        [str(inst.python_exe), "--version"], capture_output=True, text=True
    )
    py_ver = r.stdout.strip() or r.stderr.strip()
    ok(f"Python:       {py_ver}")
    log.info(f"python_version={py_ver}")

    ver_match = re.search(r"(\d+)\.(\d+)", py_ver)
    if ver_match:
        major, minor = int(ver_match.group(1)), int(ver_match.group(2))
        if (major, minor) < (3, 9):
            warn(
                f"Python {major}.{minor} is below recommended 3.9+. Some nodes may fail."
            )

    # ── GPU hardware (OS-level, independent of PyTorch) ────────────────
    info("Detecting GPU hardware…")
    gpu = detect_gpu_hardware()

    if gpu["name"] != "unknown":
        vram_str = f"  |  VRAM: {gpu['vram_gb']} GB" if gpu["vram_gb"] else ""
        ok(f"GPU:          {gpu['name']}{vram_str}")
        ok(
            f"Vendor:       {gpu['vendor'].upper()}  "
            f"({'CUDA' if gpu['cuda_capable'] else 'ROCm' if gpu['rocm_capable'] else 'Arc/XPU' if gpu['arc_capable'] else 'MPS' if gpu['mps_capable'] else 'CPU'})"
        )
    else:
        warn("Could not detect GPU hardware — defaulting to CPU PyTorch.")

    # ── PyTorch check ──────────────────────────────────────────────────
    torch_script = textwrap.dedent("""
        try:
            import torch
            print("TORCH_VERSION=" + torch.__version__)
            print("CUDA_AVAILABLE=" + str(torch.cuda.is_available()))
            if torch.cuda.is_available():
                print("CUDA_VERSION=" + str(torch.version.cuda))
                print("GPU_TORCH=" + torch.cuda.get_device_name(0))
                print("VRAM_TORCH=" + str(round(torch.cuda.get_device_properties(0).total_memory/1024**3,1)))
            elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
                print("MPS=True")
            else:
                print("COMPUTE=CPU-only")
        except ImportError:
            print("TORCH=NOT_INSTALLED")
    """)
    result = subprocess.run(
        [str(inst.python_exe), "-c", torch_script], capture_output=True, text=True
    )
    torch_info = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            torch_info[k] = v

    if torch_info.get("TORCH") == "NOT_INSTALLED":
        warn("PyTorch is NOT installed — will install automatically.")
    else:
        tv = torch_info.get("TORCH_VERSION", "?")
        ok(f"PyTorch:      {tv}")
        if torch_info.get("CUDA_AVAILABLE") == "True":
            ok(
                f"CUDA:         {torch_info.get('CUDA_VERSION', '?')}  |  "
                f"GPU (torch): {torch_info.get('GPU_TORCH', '?')}  |  "
                f"VRAM: {torch_info.get('VRAM_TORCH', '?')} GB"
            )
        elif torch_info.get("MPS") == "True":
            ok("Compute:      Apple MPS (Metal)")
        else:
            ok("Compute:      CPU-only build installed")

        # ── Mismatch check ─────────────────────────────────────────────
        if is_torch_mismatched(gpu, torch_info):
            print()
            warn(f"PyTorch MISMATCH detected!")
            warn(f"  Installed: {tv}  (CPU-only or wrong build)")
            warn(f"  Hardware:  {gpu['name']}  ({gpu['vendor'].upper()})")
            fix("Auto-fixing: reinstalling correct PyTorch build for your GPU…")
            if reinstall_torch(inst, gpu):
                # Re-probe torch after reinstall
                result2 = subprocess.run(
                    [str(inst.python_exe), "-c", torch_script],
                    capture_output=True,
                    text=True,
                )
                torch_info = {}
                for line in result2.stdout.splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        torch_info[k] = v
                tv2 = torch_info.get("TORCH_VERSION", "?")
                ok(f"PyTorch now:  {tv2}")
                if torch_info.get("CUDA_AVAILABLE") == "True":
                    ok(
                        f"GPU active:   {torch_info.get('GPU_TORCH', '?')}  "
                        f"({torch_info.get('VRAM_TORCH', '?')} GB VRAM)"
                    )
                else:
                    warn("GPU still not active after reinstall — check CUDA drivers.")
        else:
            if gpu["cuda_capable"] and torch_info.get("CUDA_AVAILABLE") == "True":
                ok("PyTorch ↔ GPU: match confirmed ✔")
            elif gpu["mps_capable"] and torch_info.get("MPS") == "True":
                ok("PyTorch ↔ GPU: MPS match confirmed ✔")

    log.info(f"torch_info={torch_info}  gpu={gpu}")
    return torch_info, gpu


# ══════════════════════════════════════════════════════════════════════
#  PHASE 3 — NODE AUDIT
# ══════════════════════════════════════════════════════════════════════


class NodeStatus:
    def __init__(self, path: Path):
        self.path = path
        self.name = path.name
        self.has_git = (path / ".git").exists()
        self.has_requirements = (path / "requirements.txt").exists()
        self.has_install_py = (path / "install.py").exists()
        self.missing_packages: list[str] = []
        self.errors: list[str] = []
        self.updated = False
        self.deps_installed = False


def audit_nodes(inst: ComfyInstall) -> list[NodeStatus]:
    section("3 / 5  —  Auditing Custom Nodes")

    nodes = []
    if not inst.custom_nodes_dir or not inst.custom_nodes_dir.exists():
        info("No custom_nodes directory found — skipping node audit.")
        return nodes

    try:
        node_dirs = [
            d
            for d in inst.custom_nodes_dir.iterdir()
            if d.is_dir() and not d.name.startswith(".") and d.name != "__pycache__"
        ]
    except Exception as e:
        info(f"Could not read custom_nodes directory: {e} — skipping node audit.")
        return nodes

    if not node_dirs:
        info("No custom nodes installed.")
        return nodes

    info(f"Found {len(node_dirs)} custom node(s).")

    for nd in sorted(node_dirs):
        ns = NodeStatus(nd)
        status_parts = []
        if ns.has_git:
            status_parts.append("git")
        if ns.has_requirements:
            status_parts.append("requirements.txt")
        if ns.has_install_py:
            status_parts.append("install.py")
        step(f"{nd.name}  [{', '.join(status_parts) or 'no manifest'}]")
        nodes.append(ns)
        log.info(f"node={nd.name}  has_git={ns.has_git}  has_req={ns.has_requirements}")

    return nodes


# ══════════════════════════════════════════════════════════════════════
#  PHASE 4 — UPDATE & DEPENDENCY INSTALLATION
# ══════════════════════════════════════════════════════════════════════


def run_cmd(
    cmd: list,
    cwd: Optional[Path] = None,
    timeout: int = 300,
    env: Optional[dict] = None,
) -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr)."""
    log.debug(f"CMD: {' '.join(str(c) for c in cmd)}  cwd={cwd}")
    try:
        result = subprocess.run(
            [str(c) for c in cmd],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        log.debug(f"RETURNCODE: {result.returncode}")
        if result.stdout:
            log.debug(f"STDOUT: {result.stdout[:2000]}")
        if result.stderr:
            log.debug(f"STDERR: {result.stderr[:2000]}")
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        log.error(f"TIMEOUT: {' '.join(str(c) for c in cmd)}")
        return -1, "", "Timed out"
    except Exception as e:
        log.error(f"CMD EXCEPTION: {e}")
        return -1, "", str(e)


def git_update_node(inst: ComfyInstall, ns: NodeStatus) -> bool:
    """git fetch + pull, return True if successful."""
    rc, out, er = run_cmd([inst.git_exe, "fetch", "--all"], cwd=ns.path)
    if rc != 0:
        warn(f"  git fetch failed for {ns.name}: {er.strip()}")
        return False

    rc, out, er = run_cmd(
        [inst.git_exe, "pull", "--rebase", "--autostash"], cwd=ns.path
    )
    if rc == 0:
        ns.updated = True
        pulled = "Already up to date." not in out
        ok(f"  {ns.name}: {'updated ✔' if pulled else 'already up to date'}")
        return True
    else:
        warn(f"  {ns.name}: git pull failed — {er.strip()}")
        log.warning(f"git_pull_error node={ns.name} stderr={er}")
        return False


def robust_git_fix(node_path: Path, git_exe: str, max_retries: int = 2) -> bool:
    """
    Attempt to fix a broken git repository with multiple strategies:
    1. git pull --rebase
    2. git fetch + reset --hard to origin/{branch}
    3. Delete and re-clone from origin (if git remote exists)
    Returns True if any fix succeeded.
    """
    import subprocess as sp

    for attempt in range(1, max_retries + 1):
        fix(f"  Git fix attempt {attempt}/{max_retries} for {node_path.name}")

        # Try regular git pull first
        result = sp.run(
            [git_exe, "pull", "--rebase", "--autostash"],
            cwd=node_path,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            ok(f"  {node_path.name}: git pull succeeded")
            return True

        warn(f"  git pull failed: {result.stderr.strip()[:200]}")

        # Fetch all remotes
        sp.run([git_exe, "fetch", "--all"], cwd=node_path, capture_output=True)

        # Get current branch or default to main/master
        branch_result = sp.run(
            [git_exe, "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=node_path,
            capture_output=True,
            text=True,
        )
        current_branch = (
            branch_result.stdout.strip() if branch_result.returncode == 0 else "main"
        )

        # Try resetting to origin/{branch}
        reset_result = sp.run(
            [git_exe, "reset", "--hard", f"origin/{current_branch}"],
            cwd=node_path,
            capture_output=True,
            text=True,
        )
        if reset_result.returncode == 0:
            ok(f"  {node_path.name}: git reset --hard succeeded")
            return True

        warn(f"  git reset --hard failed: {reset_result.stderr.strip()[:200]}")

        # Try to find the remote URL and re-clone
        remote_result = sp.run(
            [git_exe, "remote", "get-url", "origin"],
            cwd=node_path,
            capture_output=True,
            text=True,
        )
        if remote_result.returncode == 0:
            remote_url = remote_result.stdout.strip()
            if remote_url and remote_url.startswith("http"):
                warn(f"  Attempting to re-clone from {remote_url[:60]}...")
                try:
                    # Save .git folder and restore it after delete
                    git_dir = node_path / ".git"
                    # Backup remote URL
                    backup_info = {"url": remote_url, "branch": current_branch}

                    # Delete everything except .git
                    for item in node_path.iterdir():
                        if item.name != ".git":
                            if item.is_dir():
                                shutil.rmtree(item)
                            else:
                                item.unlink()

                    # Re-create the repo by fetching
                    # Instead of full re-clone, try git clone --depth 1
                    parent = node_path.parent
                    node_name = node_path.name
                    temp_name = f"{node_name}_temp_{int(time.time())}"

                    clone_result = sp.run(
                        [git_exe, "clone", "--depth", "1", remote_url, temp_name],
                        cwd=parent,
                        capture_output=True,
                        text=True,
                        timeout=300,
                    )
                    if clone_result.returncode == 0:
                        # Move cloned files to original location
                        temp_path = parent / temp_name
                        for item in temp_path.iterdir():
                            if item.name != ".git":
                                dest = node_path / item.name
                                if item.is_dir():
                                    shutil.move(str(item), str(dest))
                                else:
                                    shutil.move(str(item), str(dest))
                        # Clean up temp
                        if temp_path.exists():
                            shutil.rmtree(temp_path, ignore_errors=True)
                        ok(f"  {node_path.name}: re-clone succeeded")
                        return True
                    else:
                        warn(f"  Re-clone failed: {clone_result.stderr.strip()[:200]}")
                except Exception as e:
                    warn(f"  Re-clone error: {e}")

        warn(f"  All git fix attempts failed for {node_path.name}")

    return False


def delete_and_reclone_node(node_path: Path, git_exe: str, python_exe: Path) -> bool:
    """
    Completely delete a custom node directory and re-clone it fresh from git.
    This is the most thorough way to fix broken nodes.
    Returns True if successful.
    """
    import subprocess as sp

    # Get remote URL first
    result = sp.run(
        [git_exe, "remote", "get-url", "origin"],
        cwd=node_path,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        err(f"  Could not get remote URL for {node_path.name}")
        return False

    remote_url = result.stdout.strip()
    if not remote_url:
        err(f"  No remote URL found for {node_path.name}")
        return False

    node_name = node_path.name
    parent = node_path.parent

    warn(f"  Completely reinstalling {node_name} from {remote_url[:60]}...")

    # Delete the entire node directory
    try:
        shutil.rmtree(node_path)
        ok(f"  Deleted {node_name}")
    except Exception as e:
        err(f"  Could not delete {node_name}: {e}")
        return False

    # Clone fresh
    try:
        clone_result = sp.run(
            [git_exe, "clone", "--depth", "1", remote_url, node_name],
            cwd=parent,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if clone_result.returncode == 0:
            ok(f"  Fresh clone of {node_name} succeeded")

            # Run install.py if exists
            new_path = parent / node_name
            if (new_path / "install.py").exists():
                warn(f"  Running install.py...")
                run_cmd(
                    [str(python_exe), str(new_path / "install.py")],
                    env=_build_pip_env(ComfyInstall()),
                    timeout=300,
                )

            # Run requirements.txt if exists
            if (new_path / "requirements.txt").exists():
                warn(f"  Installing requirements...")
                run_cmd(
                    [
                        str(python_exe),
                        "-m",
                        "pip",
                        "install",
                        "-r",
                        str(new_path / "requirements.txt"),
                        "--quiet",
                    ],
                    env=_build_pip_env(ComfyInstall()),
                    timeout=600,
                )

            return True
        else:
            err(f"  Clone failed: {clone_result.stderr.strip()[:200]}")
            return False
    except Exception as e:
        err(f"  Clone error: {e}")
        return False


def _build_pip_env(inst: ComfyInstall) -> dict:
    """Environment dict for pip calls (handles portable Python path issues)."""
    env = os.environ.copy()
    if not inst.python_exe:
        return env
    py_dir = inst.python_exe.parent
    env["PATH"] = (
        str(py_dir)
        + os.pathsep
        + str(py_dir / "Scripts")
        + os.pathsep
        + env.get("PATH", "")
    )
    return env


def cleanup_broken_pip_entries(inst: ComfyInstall):
    """
    Remove corrupted/partial package entries from site-packages that cause pip
    to fail with "Ignoring invalid distribution ~xxxx" errors.
    These are left behind by interrupted installs and have filenames starting with ~.
    """
    try:
        r = subprocess.run(
            [
                str(inst.python_exe),
                "-c",
                "import site; print(site.getsitepackages()[0])",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env=_build_pip_env(inst),
        )
        if r.returncode != 0 or not r.stdout.strip():
            return
        site_packages = Path(r.stdout.strip())
        broken = list(site_packages.glob("~*"))
        if broken:
            warn(
                f"  Found {len(broken)} corrupted package entry(ies) in site-packages — cleaning up…"
            )
            for b in broken:
                try:
                    if b.is_dir():
                        import shutil as _shutil

                        _shutil.rmtree(b)
                    else:
                        b.unlink()
                    fix(f"  Removed broken entry: {b.name}")
                    log.info(f"Removed broken pip entry: {b}")
                except Exception as e:
                    warn(f"  Could not remove {b.name}: {e}")
        else:
            log.info("No corrupted pip entries found.")
    except Exception as e:
        log.warning(f"cleanup_broken_pip_entries failed: {e}")


def fix_pip_conflicts(inst: ComfyInstall):
    """
    Detect pip packages that conflict with node modules by shadowing them.
    NOTE: We do NOT remove pip packages - that breaks other dependencies.
    The proper fix is ensuring node modules have priority via sys.path.insert(0, ...)
    This function just logs the conflict for awareness.
    Known conflicts:
    - security-check (PyPI) shadows ComfyUI-Manager's security_check
    - impact (PyPI) shadows ComfyUI-Impact-Pack's impact module
    - img-utils (PyPI) shadows eden_comfy_pipelines' img_utils
    """
    conflicts = {
        "security-check": "security_check",
        "impact": "impact",
        "img-utils": "img_utils",
    }

    rc, out, er = run_cmd(
        [str(inst.python_exe), "-m", "pip", "list", "--format=json"],
        env=_build_pip_env(inst),
        timeout=30,
    )

    if rc != 0:
        return

    try:
        import json as _json

        packages = _json.loads(out)
        installed = {p["name"].lower(): p["name"] for p in packages}
    except Exception:
        return

    for pip_name, node_name in conflicts.items():
        if pip_name.lower() in installed:
            info(f"  Found: {pip_name} (node '{node_name}' may need priority)")
            info(f"    -> Fix applied via sys.path.insert(0, ...) in node init files")


def fix_version_conflicts(inst: ComfyInstall):
    """
    Detect and fix package version conflicts that cause issues.
    This includes packages with multiple CUDA variants (e.g., cupy-cuda12x vs cupy-cuda13x).
    Only handles actual CUDA variant conflicts, not unrelated packages.
    Uses --no-deps to avoid breaking other dependencies.
    """
    # Get installed packages
    rc, out, er = run_cmd(
        [str(inst.python_exe), "-m", "pip", "list", "--format=json"],
        env=_build_pip_env(inst),
        timeout=30,
    )

    if rc != 0:
        return

    try:
        import json as _json

        packages = _json.loads(out)
    except Exception:
        return

    # Detect CUDA version for choosing correct variant
    cuda_version = "12"
    try:
        rc_cuda, out_cuda, _ = run_cmd(
            [str(inst.python_exe), "-c", "import torch; print(torch.version.cuda)"],
            env=_build_pip_env(inst),
            timeout=10,
        )
        if rc_cuda == 0:
            cuda_ver = out_cuda.strip()
            if cuda_ver:
                cuda_version = (
                    cuda_ver.split(".")[0]
                    .replace("13", "13")
                    .replace("12", "12")
                    .replace("11", "11")
                )
    except Exception:
        pass

    # Find ONLY packages with CUDA variants (e.g., cupy-cuda12x, cupy-cuda13x, cupy)
    # These have the pattern: name-cuda[11,12,13]x OR just "name" for base packages
    cuda_packages: dict[str, list[dict]] = {}
    import re

    cuda_pattern = re.compile(r"^(.+)-cuda(11|12|13)x$", re.IGNORECASE)

    for p in packages:
        name = p["name"]
        match = cuda_pattern.match(name)
        if match:
            base_name = match.group(1)
            cuda_ver = match.group(2)
            if base_name not in cuda_packages:
                cuda_packages[base_name] = []
            cuda_packages[base_name].append({"name": p["name"], "cuda": cuda_ver})
        # Also detect base packages that start with the same name (e.g., "cupy" from "cupy-cuda13x")
        elif name == "cupy":
            if "cupy" not in cuda_packages:
                cuda_packages["cupy"] = []
            cuda_packages["cupy"].append({"name": p["name"], "cuda": "base"})

    # Handle packages with multiple CUDA variants
    for base_name, variants in cuda_packages.items():
        if len(variants) > 1:
            # Multiple CUDA variants of the same package - this causes conflicts
            warn(f"  Multiple {base_name} variants: {[v['name'] for v in variants]}")

            # Determine which variant to keep based on CUDA version
            target_variant = f"{base_name}-cuda{cuda_version}x"

            # First, completely uninstall ALL variants
            warn(f"    Uninstalling ALL {base_name} variants first...")
            for v in variants:
                run_cmd(
                    [str(inst.python_exe), "-m", "pip", "uninstall", "-y", v["name"]],
                    env=_build_pip_env(inst),
                    timeout=60,
                )

            # Then clean up any remaining files
            run_cmd(
                [str(inst.python_exe), "-m", "pip", "cache", "purge"],
                env=_build_pip_env(inst),
                timeout=30,
            )

            # Now install the correct variant fresh
            fix(f"  Installing {target_variant} for CUDA {cuda_version}...")
            rc, out, er = run_cmd(
                [
                    str(inst.python_exe),
                    "-m",
                    "pip",
                    "install",
                    "--force-reinstall",
                    target_variant,
                    "--no-cache-dir",
                ],
                env=_build_pip_env(inst),
                timeout=300,
            )
            if rc != 0:
                warn(f"    Install failed: {er[:200] if er else 'unknown error'}")

    # Also check numpy version for numba compatibility
    numpy_pkg = next((p for p in packages if p["name"].lower() == "numpy"), None)
    if numpy_pkg:
        version = numpy_pkg["version"]
        try:
            major_minor = re.match(r"(\d+)\.(\d+)", version)
            if major_minor:
                major = int(major_minor.group(1))
                minor = int(major_minor.group(2))
                if major > 2 or (major == 2 and minor >= 4):
                    # Check if numba is installed - if so, need to fix numpy
                    numba_pkg = next(
                        (p for p in packages if p["name"].lower() == "numba"), None
                    )
                    if numba_pkg:
                        warn(
                            f"  numpy {version} conflicts with numba - downgrading to <2.4..."
                        )
                        run_cmd(
                            [
                                str(inst.python_exe),
                                "-m",
                                "pip",
                                "install",
                                "numpy<2.4",
                                "--quiet",
                                "--no-warn-script-location",
                            ],
                            env=_build_pip_env(inst),
                            timeout=120,
                        )
        except Exception:
            pass

    info(f"  Version conflict detection complete")


def fix_node_init_files(inst: ComfyInstall):
    """
    Fix common node issues:
    1. Create missing __init__.py files in node subdirectories
    2. Fix sys.path.append() to sys.path.insert(0, ...) for priority
    """
    if not inst.custom_nodes_dir:
        return

    # Known nodes that need __init__.py in specific subdirs
    init_needed = {
        "ComfyUI-Impact-Pack": ["modules", "modules/impact", "modules/thirdparty"],
        "eden_comfy_pipelines": ["img_utils", "video_utils"],
    }

    for node_name, subdirs in init_needed.items():
        node_path = inst.custom_nodes_dir / node_name
        if not node_path.exists():
            continue

        for subdir in subdirs:
            init_file = node_path / subdir / "__init__.py"
            if not init_file.exists():
                try:
                    init_file.parent.mkdir(parents=True, exist_ok=True)
                    init_file.touch()
                    fix(f"  Created __init__.py: {node_name}/{subdir}")
                except Exception as e:
                    warn(f"  Could not create {node_name}/{subdir}/__init__.py: {e}")

    # Fix sys.path.append to sys.path.insert(0, ...) in Impact-Pack
    impact_init = inst.custom_nodes_dir / "ComfyUI-Impact-Pack" / "__init__.py"
    if impact_init.exists():
        try:
            content = impact_init.read_text(encoding="utf-8")
            if "sys.path.append(modules_path)" in content:
                content = content.replace(
                    "sys.path.append(modules_path)", "sys.path.insert(0, modules_path)"
                )
                impact_init.write_text(content, encoding="utf-8")
                fix(f"  Fixed sys.path order in ComfyUI-Impact-Pack")
        except Exception as e:
            warn(f"  Could not fix Impact-Pack sys.path: {e}")

    # Fix sys.path.append to sys.path.insert(0, ...) in ComfyUI-Manager (prestartup_script.py)
    manager_pre = inst.custom_nodes_dir / "ComfyUI-Manager" / "prestartup_script.py"
    if manager_pre.exists():
        try:
            content = manager_pre.read_text(encoding="utf-8")
            # Fix the glob_path sys.path.append
            if "sys.path.append(glob_path)" in content:
                content = content.replace(
                    "sys.path.append(glob_path)", "sys.path.insert(0, glob_path)"
                )
                manager_pre.write_text(content, encoding="utf-8")
                fix(f"  Fixed sys.path order in ComfyUI-Manager")
        except Exception as e:
            warn(f"  Could not fix Manager sys.path: {e}")


def install_requirements(inst: ComfyInstall, ns: NodeStatus) -> bool:
    """pip install -r requirements.txt with robust error handling"""
    req_file = ns.path / "requirements.txt"
    fix(f"  Installing requirements for {ns.name}…")

    # First try: basic install
    rc, out, er = run_cmd(
        [
            str(inst.python_exe),
            "-m",
            "pip",
            "install",
            "-r",
            str(req_file),
            "--quiet",
            "--no-warn-script-location",
            "--no-deps",  # Don't pull deps, avoid conflicts
        ],
        env=_build_pip_env(inst),
        timeout=600,
    )

    if rc == 0:
        ok(f"  {ns.name}: requirements installed")
        ns.deps_installed = True
        return True

    # Second try: with deps but allow failures
    warn(f"  First attempt failed, trying with dependencies...")
    rc2, out2, er2 = run_cmd(
        [
            str(inst.python_exe),
            "-m",
            "pip",
            "install",
            "-r",
            str(req_file),
            "--quiet",
            "--no-warn-script-location",
            "--ignore-requires-python",
        ],
        env=_build_pip_env(inst),
        timeout=600,
    )

    if rc2 == 0:
        ok(f"  {ns.name}: requirements installed (with deps)")
        ns.deps_installed = True
        return True

    # Check if failure is caused by corrupted pip entries (~xxxx dirs)
    if "invalid distribution" in er or "~" in er:
        warn(
            f"  {ns.name}: pip failed due to corrupted package entries — cleaning and retrying…"
        )
        cleanup_broken_pip_entries(inst)
        rc3, out3, er3 = run_cmd(
            [
                str(inst.python_exe),
                "-m",
                "pip",
                "install",
                "-r",
                str(req_file),
                "--quiet",
                "--no-warn-script-location",
                "--no-deps",
            ],
            env=_build_pip_env(inst),
            timeout=600,
        )
        if rc3 == 0:
            ok(f"  {ns.name}: requirements installed after cleanup")
            ns.deps_installed = True
            return True
        else:
            err(f"  {ns.name}: requirements install failed even after cleanup")
            ns.errors.append(f"pip install requirements failed: {er3[:300]}")
            log.error(f"pip_req_fail node={ns.name} stderr={er3}")
            return False

    # Not a corruption issue, just version conflict - not fatal
    warn(f"  {ns.name}: requirements have version conflicts (common, continuing)")
    log.info(f"pip_req_conflict node={ns.name} stderr={er[:500]}")
    return True  # Don't fail the whole process for this


def run_install_py(inst: ComfyInstall, ns: NodeStatus) -> bool:
    """Run the node's install.py if present."""
    fix(f"  Running install.py for {ns.name}…")
    rc, out, er = run_cmd(
        [str(inst.python_exe), "install.py"],
        cwd=ns.path,
        env=_build_pip_env(inst),
        timeout=300,
    )
    if rc == 0:
        ok(f"  {ns.name}: install.py succeeded")
        return True
    else:
        warn(f"  {ns.name}: install.py exited with code {rc}")
        ns.errors.append(f"install.py failed: {er[:300]}")
        return False


# ComfyUI internal modules that nodes import but are NOT PyPI packages.
# Trying to pip install these is always wrong.
# ComfyUI internal modules — never pip-installable
COMFYUI_INTERNAL_MODULES = {
    "comfy",
    "comfy_extras",
    "comfy_execution",
    "folder_paths",
    "node_helpers",
    "nodes",
    "server",
    "app",
    "execution",
    "latent_preview",
    "model_management",
    "model_detection",
    "model_patcher",
    "clip_vision",
    "controlnet",
    "diffusers_convert",
    "hooks",
    "lora",
    "lora_types",
    "ops",
    "patcher_extension",
    "sampler_helpers",
    "samplers",
    "sd",
    "sd1_clip",
    "sd2_clip",
    "sdxl_clip",
    "sd3_clip",
    "text_encoders",
    "utils",
    "weight_adapter",
    "comfy_api",
    "comfy_api_nodes",
    "src",
    "lib",
    "custom_nodes",
    "config",
    "helpers",
    "common",
    "base",
    "core",
    "types",
}

# Patterns that strongly indicate a node-local or ComfyUI-internal name
# rather than a real PyPI package — checked against the import name itself.
_INTERNAL_PATTERNS = re.compile(
    r"""
    ^(
        # ComfyUI naming conventions used in custom node internals
        .*_models$          |   # vfi_models, depth_models, upscale_models, ...
        .*_nodes$           |   # any_nodes
        .*_utils$           |   # node_utils, image_utils, ...
        .*_helpers?$        |   # any_helper / any_helpers
        .*_ops$             |   # custom ops modules
        .*_types$           |   # type definition modules
        .*_config$          |   # config modules
        .*_base$            |   # base class modules
        .*_core$            |   # core modules
        .*_global$          |   # cm_global, any_global
        .*_server$          |   # manager_server, any_server
        .*_downloader$      |   # manager_downloader
        .*_compatibility$   |   # comfy_compatibility
        .*_chainner$        |   # r_chainner (chaiNNer integration)
        .*_groundingdino$   |   # local_groundingdino
        .*_controlnet.*$    |   # custom_controlnet_aux and variants
        .*_sageattn$        |   # sparse_sageattn
        .*_adapter.*$       |   # ip_adapter_utils, lora_adapter, ...
        .*_conditioning$    |   # random_conditioning
        .*_3rdparty$        |   # share_3rdparty
        .*_extension$       |   # any custom extension module
        .*_subpack$         |   # impact-subpack style
        comfyui_.*          |   # ComfyUI_ADV_CLIP_emb, comfyui_* prefixed internals
        ComfyUI_.*          |   # same with capital C
        # Common single-word generic names that are always local in node context
        main$               |   # from main import ... (node entry point)
        pipeline$           |   # pipeline module
        inference$          |   # inference module
        # Very short generic names unlikely to be real PyPI packages
        [a-z]{1,3}              # 1-3 char names (real ones like cv2, PIL in remap list)
    )$
    """,
    re.VERBOSE,
)

# Known PyPI packages that have import names different from their pip name.
# These ARE real packages and should never be blocked.
KNOWN_PYPI_REMAPS = {
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "skimage": "scikit-image",
    "yaml": "PyYAML",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "attr": "attrs",
    "gi": "PyGObject",
    "usb": "pyusb",
    "serial": "pyserial",
    "wx": "wxPython",
    "pkg_resources": "setuptools",
}


def _is_likely_internal(mod: str, node_path: Path) -> bool:
    """
    Return True if this import name looks like a node-local or ComfyUI-internal
    module rather than a real installable PyPI package.
    """
    # Hard blocklist
    if mod in COMFYUI_INTERNAL_MODULES:
        return True
    # Known real packages — never block these
    if mod in KNOWN_PYPI_REMAPS:
        return False
    # Pattern-based heuristics
    if _INTERNAL_PATTERNS.match(mod):
        return True
    # If a same-named directory exists anywhere in the node tree, it's local
    if any(node_path.rglob(mod)):
        return True
    return False


def check_node_imports(inst: ComfyInstall, ns: NodeStatus) -> list[str]:
    """
    Scan node .py files for imports that might be missing PyPI packages.
    Skips ComfyUI internals, node-local modules, stdlib, and
    pattern-matched internal names (e.g. vfi_models, depth_utils, ...).
    """
    missing = []
    py_files = list(ns.path.glob("*.py"))[:10]

    import_re = re.compile(
        r"^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE
    )
    stdlib_mods = (
        set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else set()
    )

    # All local names: .py file stems + subdirs that are packages
    known_local = {f.stem for f in ns.path.glob("*.py")}
    try:
        known_local |= {
            d.name
            for d in ns.path.iterdir()
            if d.is_dir() and (d / "__init__.py").exists()
        }
    except Exception:
        pass

    candidates = set()
    for py_file in py_files:
        try:
            text = py_file.read_text(encoding="utf-8", errors="ignore")
            for m in import_re.finditer(text):
                mod = m.group(1)
                if (
                    mod not in stdlib_mods
                    and mod not in known_local
                    and mod not in ("__future__",)
                    and not mod.startswith("_")
                    and not _is_likely_internal(mod, ns.path)
                ):
                    candidates.add(mod)
        except Exception:
            pass

    if not candidates:
        ns.missing_packages = []
        return []

    check_script = "import importlib.util\nmissing=[]\n"
    for mod in sorted(candidates):
        check_script += f"missing.append('{mod}') if importlib.util.find_spec('{mod}') is None else None\n"
    check_script += "print(','.join(missing))"

    rc, out, er = run_cmd([str(inst.python_exe), "-c", check_script], timeout=30)
    if rc == 0 and out.strip():
        missing = [m for m in out.strip().split(",") if m]

    ns.missing_packages = missing
    return missing


def try_install_missing(inst: ComfyInstall, missing: list[str]) -> list[str]:
    """Attempt to pip install a list of packages. Return still-missing ones."""
    still_missing = []

    # Known packages that don't exist on PyPI (node-internal packages)
    skip_packages = {"manager_util", "clipseg", "sam2"}  # Add known problematic ones

    for pkg in missing:
        # Skip known non-existent packages
        if pkg.lower() in skip_packages:
            warn(f"  Skipping {pkg} (not available on PyPI)")
            continue

        fix(f"  Attempting: pip install {pkg}")
        rc, out, er = run_cmd(
            [
                str(inst.python_exe),
                "-m",
                "pip",
                "install",
                pkg,
                "--quiet",
                "--no-warn-script-location",
                "--no-deps",  # Avoid dependency conflicts
            ],
            env=_build_pip_env(inst),
            timeout=180,
        )

        if rc == 0:
            ok(f"  Installed: {pkg}")
        else:
            # Try with deps if --no-deps failed
            rc2, out2, er2 = run_cmd(
                [
                    str(inst.python_exe),
                    "-m",
                    "pip",
                    "install",
                    pkg,
                    "--quiet",
                    "--no-warn-script-location",
                ],
                env=_build_pip_env(inst),
                timeout=180,
            )
            if rc2 == 0:
                ok(f"  Installed: {pkg} (with deps)")
            else:
                # Check for version conflict vs truly missing
                if "could not find" in er.lower() or "not found" in er.lower():
                    warn(f"  Package not found on PyPI: {pkg}")
                else:
                    warn(f"  Could not install {pkg} (version conflict likely)")
                still_missing.append(pkg)
    return still_missing


def update_and_fix_nodes(inst: ComfyInstall, nodes: list[NodeStatus]):
    section("4 / 5  —  Updating Nodes & Installing Dependencies")

    if not nodes:
        info("No nodes to process.")
        return

    for ns in nodes:
        print()
        info(f"Processing: {ns.name}")

        # Git update
        if ns.has_git:
            git_path = inst.git_exe or "git"
            if robust_git_fix(ns.path, git_path, max_retries=1):
                ok(f"  {ns.name}: updated ✓")
            else:
                warn(f"  {ns.name}: update failed")
        else:
            dim(f"  No .git dir — skipping update (manually installed node)")

        # install.py
        if ns.has_install_py:
            run_install_py(inst, ns)

        # requirements.txt
        if ns.has_requirements:
            install_requirements(inst, ns)

        # Import scan
        missing = check_node_imports(inst, ns)
        if missing:
            warn(f"  Potentially missing packages: {', '.join(missing)}")
            still = try_install_missing(inst, missing)
            if still:
                err(f"  Could not auto-install: {', '.join(still)}")
                ns.errors.extend([f"Missing package: {p}" for p in still])
        else:
            ok(f"  Import scan: no obvious missing packages")

    print()
    ok("Node update pass complete.")


# ══════════════════════════════════════════════════════════════════════
#  PHASE 5 — LAUNCH, WATCH & AUTO-FIX
# ══════════════════════════════════════════════════════════════════════

# Patterns that indicate a recoverable error in ComfyUI output
ERROR_PATTERNS = [
    # (regex, description)
    (re.compile(r"ModuleNotFoundError: No module named '([^']+)'"), "missing_module"),
    (
        re.compile(r"ImportError: cannot import name '([^']+)' from '([^']+)'"),
        "import_error",
    ),
    (re.compile(r"cannot import name '([^']+)'"), "import_error"),
    (re.compile(r"No module named '([^']+)'"), "missing_module"),
    (re.compile(r"Failed to import module '([^']+)': (.+)"), "failed_import"),
    (re.compile(r"AttributeError: ([^']+)"), "attribute_error"),
    (
        re.compile(r"AttributeError: module '([^']+)' has no attribute '([^']+)'"),
        "module_attribute_error",
    ),
    # Numba numpy conflict
    (re.compile(r"Numba needs NumPy.*Got NumPy (\d+\.\d+)"), "numba_numpy_conflict"),
    (re.compile(r"ImportError: Numba needs NumPy"), "numba_numpy_conflict"),
    # CuPy multiple packages conflict
    (
        re.compile(
            r"CuPy may not function correctly because multiple CuPy packages are installed[:\s]+([\w-]+(?:,\s*[\w-]+)*)",
            re.IGNORECASE,
        ),
        "cupy_multiple_packages",
    ),
    (
        re.compile(r"multiple.*cupy.*packages.*installed", re.IGNORECASE),
        "cupy_multiple_packages",
    ),
    (
        re.compile(r"cupy-cuda\d+x.*cupy-cuda\d+x", re.IGNORECASE),
        "cupy_multiple_packages",
    ),
    (re.compile(r"RuntimeError: CUDA out of memory"), "cuda_oom"),
    (re.compile(r"torch\.cuda\.OutOfMemoryError"), "cuda_oom"),
    (re.compile(r"xformers.*not.*install", re.IGNORECASE), "missing_xformers"),
    (re.compile(r"triton.*not.*install", re.IGNORECASE), "missing_triton"),
    (re.compile(r"ERROR:.*custom_nodes[/\\]([^/\\]+)", re.IGNORECASE), "node_error"),
    (re.compile(r"PRESTARTUP FAILED.*[/\\]([^/\\]+)"), "prestartup_failed"),
    (re.compile(r"Traceback \(most recent call last\)"), "traceback"),
]

FATAL_PATTERNS = [
    re.compile(r"\bFATAL\b|fatal\b|segfault|Segmentation fault\b|MemoryError\b"),
    re.compile(r"torch.*not.*install", re.IGNORECASE),
]

# Patterns for warnings that should NOT be treated as fatal errors
IGNORE_PATTERNS = [
    re.compile(r"FutureWarning", re.IGNORECASE),
    re.compile(r"UserWarning", re.IGNORECASE),
    re.compile(r"warnings.warn", re.IGNORECASE),
    re.compile(r"pynvml", re.IGNORECASE),
]

NO_IMPORT_ERRORS_PATTERNS = [
    re.compile(r"ModuleNotFoundError"),
    re.compile(r"ImportError: cannot import"),
    re.compile(r"ImportError: No module named"),
    re.compile(r"ImportError: Numba needs NumPy"),
    re.compile(r"Numba needs NumPy"),
    re.compile(r"CuPy may not function correctly because multiple"),
    re.compile(r"AttributeError: module '[^']+' has no attribute"),
    re.compile(r"AttributeError: partially initialized module"),
    re.compile(r"IMPORT FAILED"),
]


class ComfyLauncher:
    def __init__(self, inst: ComfyInstall, max_fix_rounds: int = 2):
        self.inst = inst
        self.max_fix_rounds = max_fix_rounds
        self.process: Optional[subprocess.Popen] = None
        self.log_lines: list[str] = []
        self.error_counts: dict[str, int] = {}
        self.installed_packages: set[str] = set()
        self.fix_round = 0
        self.running = False
        self._lock = threading.Lock()

    def _pip_install(self, package: str) -> bool:
        if package in self.installed_packages:
            return True

        cleanup_broken_pip_entries(self.inst)

        fix(f"  Auto-fix: pip install {package}")
        rc, _, er = run_cmd(
            [
                str(self.inst.python_exe),
                "-m",
                "pip",
                "install",
                package,
                "--quiet",
                "--no-warn-script-location",
            ],
            env=_build_pip_env(self.inst),
            timeout=300,
        )
        success = rc == 0
        if success:
            ok(f"  Installed: {package}")
            self.installed_packages.add(package)
        else:
            err(f"  pip install {package} failed: {er[:200]}")
        return success

    def _handle_error(self, error_type: str, match: re.Match) -> bool:
        """Attempt to fix an error. Return True if a fix was attempted."""
        if error_type == "missing_module":
            pkg = match.group(1).split(".")[0].replace("-", "_")
            remap = {
                "cv2": "opencv-python",
                "sklearn": "scikit-learn",
                "PIL": "Pillow",
                "skimage": "scikit-image",
                "yaml": "PyYAML",
                "bs4": "beautifulsoup4",
                "gi": "PyGObject",
                "wx": "wxPython",
                "dotenv": "python-dotenv",
                "google.protobuf": "protobuf",
                "safetensors": "safetensors",
                "einops": "einops",
                "omegaconf": "omegaconf",
                "onnxruntime": "onnxruntime",
                "transformers": "transformers",
                "diffusers": "diffusers",
                "accelerate": "accelerate",
                "compel": "compel",
                "spandrel": "spandrel",
            }

            # Map to node names for node-like packages that need git fix instead of pip
            node_fix_map = {
                "impact": "ComfyUI-Impact-Pack",
                "security_check": "ComfyUI-Manager",
                "cupy": "ComfyUI-GIMM-VFI",
                "video_utils": "eden_comfy_pipelines",
                "img_utils": "eden_comfy_pipelines",
            }

            # Check if this is a node-like package that needs git fix, not pip install
            if pkg.lower() in node_fix_map:
                node_name = node_fix_map[pkg.lower()]
                warn(f"  {pkg} error - attempting to fix node: {node_name}")
                if self.inst.custom_nodes_dir:
                    node_path = self.inst.custom_nodes_dir / node_name
                    if node_path.exists():
                        # First try git fix
                        if robust_git_fix(
                            node_path, str(self.inst.git_exe), max_retries=1
                        ):
                            fix(f"  Fixed node: {node_name}")
                            return True
                        # If git fix failed, completely delete and re-clone
                        if self.inst.python_exe:
                            warn(
                                f"  Git fix failed, completely reinstalling {node_name}..."
                            )
                            if delete_and_reclone_node(
                                node_path, str(self.inst.git_exe), self.inst.python_exe
                            ):
                                fix(f"  Reinstalled node: {node_name}")
                                return True
                            else:
                                err(f"  Could not fix {node_name}")
                        else:
                            err(f"  Could not fix {node_name} (no python)")
                return False

            pip_name = remap.get(pkg, pkg)
            if not pip_name:
                return False

            return self._pip_install(pip_name)

        elif error_type == "attribute_error":
            attr = match.group(1)
            warn(f"  Attribute error detected: {attr}")
            # Try to extract module from attribute (e.g., "module 'cupy' has no attribute 'memoize'" -> cupy)
            if "module '" in attr:
                try:
                    module_name = attr.split("module '")[1].split("'")[0]
                    warn(f"  Attempting to reinstall module: {module_name}")
                    return self._pip_install(module_name)
                except:
                    pass
            return False

        elif error_type == "module_attribute_error":
            mod = match.group(1)
            attr = match.group(2)
            warn(f"  Module {mod} missing attribute {attr}")

            # Special case: cupy.memoize - try updating the node that uses it
            if mod == "cupy" and attr == "memoize":
                warn(
                    f"  cupy.memoize not found - trying to update ComfyUI-GIMM-VFI node"
                )
                if self.inst.custom_nodes_dir:
                    node_path = self.inst.custom_nodes_dir / "ComfyUI-GIMM-VFI"
                    if node_path.exists():
                        # First try git fix
                        if robust_git_fix(
                            node_path, str(self.inst.git_exe), max_retries=1
                        ):
                            fix(f"  Successfully fixed ComfyUI-GIMM-VFI")
                            return True
                        # If git fix failed, completely delete and re-clone
                        warn(
                            f"  Git fix failed, completely reinstalling ComfyUI-GIMM-VFI..."
                        )
                        if self.inst.python_exe and delete_and_reclone_node(
                            node_path, str(self.inst.git_exe), self.inst.python_exe
                        ):
                            fix(f"  Successfully reinstalled ComfyUI-GIMM-VFI")
                            return True
                        else:
                            err(f"  Could not fix ComfyUI-GIMM-VFI")
                # Don't fall through to pip install - cupy is tricky
                return False

            # Skip trying to pip install node-like packages that aren't real pip packages
            node_like = {"cupy"}
            if mod.lower() in node_like:
                warn(f"  {mod} is not a standard pip package - skipping pip install")
                return False

            # Try to reinstall the module
            return self._pip_install(mod)

        elif error_type == "numba_numpy_conflict":
            # Numba needs older numpy - need to downgrade numpy
            warn(f"  Numba conflict: numpy too new for numba - downgrading numpy...")
            run_cmd(
                [
                    str(self.inst.python_exe),
                    "-m",
                    "pip",
                    "install",
                    "numpy<2.4",
                    "--quiet",
                    "--no-warn-script-location",
                ],
                env=_build_pip_env(self.inst),
                timeout=120,
            )
            return True

        elif error_type == "cupy_multiple_packages":
            # Multiple cupy packages installed (cupy-cuda12x, cupy-cuda13x, etc.)
            # Fix: Uninstall ALL cupy packages, then reinstall correct one for CUDA version
            warn(
                f"  CuPy multiple packages detected - fixing CUDA variant conflicts..."
            )

            # Get CUDA version from PyTorch
            cuda_version = "12"
            try:
                rc_cuda, out_cuda, _ = run_cmd(
                    [
                        str(self.inst.python_exe),
                        "-c",
                        "import torch; print(torch.version.cuda)",
                    ],
                    env=_build_pip_env(self.inst),
                    timeout=10,
                )
                if rc_cuda == 0 and out_cuda.strip():
                    cuda_ver = out_cuda.strip()
                    cuda_version = cuda_ver.split(".")[0]
            except Exception:
                pass

            # Get list of all cupy packages installed
            rc, out, _ = run_cmd(
                [str(self.inst.python_exe), "-m", "pip", "list", "--format=json"],
                env=_build_pip_env(self.inst),
                timeout=30,
            )

            cupy_packages = []
            if rc == 0:
                try:
                    import json

                    packages = json.loads(out)
                    for p in packages:
                        name = p["name"].lower()
                        if "cupy" in name:
                            cupy_packages.append(p["name"])
                except Exception:
                    pass

            if cupy_packages:
                warn(f"    Uninstalling all cupy packages: {cupy_packages}")
                for pkg in cupy_packages:
                    run_cmd(
                        [
                            str(self.inst.python_exe),
                            "-m",
                            "pip",
                            "uninstall",
                            "-y",
                            pkg,
                        ],
                        env=_build_pip_env(self.inst),
                        timeout=60,
                    )

                # Install correct cupy variant for CUDA version
                target_cupy = f"cupy-cuda{cuda_version}x"
                fix(f"    Installing {target_cupy} for CUDA {cuda_version}...")

                run_cmd(
                    [
                        str(self.inst.python_exe),
                        "-m",
                        "pip",
                        "install",
                        target_cupy,
                        "--no-cache-dir",
                    ],
                    env=_build_pip_env(self.inst),
                    timeout=300,
                )
                return True

            return False

        elif error_type == "prestartup_failed":
            # Extract node name from path like "F:\ComfyUI_Windows_portable\ComfyUI\custom_nodes\ComfyUI-Manager"
            line = match.group(0)
            node_name = ""
            if "custom_nodes" in line:
                try:
                    parts = line.split("custom_nodes")
                    if len(parts) > 1:
                        node_part = parts[1].strip("/\\").split("/")[0].split("\\")[0]
                        node_name = node_part
                except:
                    pass
            warn(f"  Prestartup failed for: {node_name}")
            if node_name and self.inst.custom_nodes_dir:
                node_path = self.inst.custom_nodes_dir / node_name
                if node_path.exists():
                    warn(f"  Updating node: {node_name}")

                    if robust_git_fix(node_path, str(self.inst.git_exe), max_retries=1):
                        fix(f"  Successfully fixed {node_name}")

                        # Run install.py if exists
                        if (node_path / "install.py").exists():
                            rc2, _, _ = run_cmd(
                                [
                                    str(self.inst.python_exe),
                                    str(node_path / "install.py"),
                                ],
                                env=_build_pip_env(self.inst),
                                timeout=300,
                            )

                        if (node_path / "requirements.txt").exists():
                            rc2, _, _ = run_cmd(
                                [
                                    str(self.inst.python_exe),
                                    "-m",
                                    "pip",
                                    "install",
                                    "-r",
                                    str(node_path / "requirements.txt"),
                                    "--quiet",
                                    "--no-warn-script-location",
                                ],
                                env=_build_pip_env(self.inst),
                                timeout=300,
                            )
                        return True
                    else:
                        err(
                            f"  Could not fix {node_name} - may need manual intervention"
                        )
            return False

        elif error_type == "missing_xformers":
            return self._pip_install("xformers")

        elif error_type == "missing_triton":
            if platform.system() == "Linux":
                return self._pip_install("triton")
            else:
                warn("  Triton is not available on Windows — this is usually fine.")
                return True

        elif error_type == "cuda_oom":
            warn("  CUDA OOM detected. Consider lowering --vram settings.")
            warn("  Attempting to add --lowvram flag on next launch.")
            return True  # handled by launch args adjustment below

        elif error_type in ("import_error", "failed_import", "node_error", "traceback"):
            # Generic: log it, no automatic fix available
            return False

        return False

    def _parse_and_fix(self, line: str) -> bool:
        """Check a log line for errors and attempt fixes. Return True if fix applied."""
        for pattern, error_type in ERROR_PATTERNS:
            m = pattern.search(line)
            if m:
                log.warning(f"DETECTED [{error_type}]: {line.strip()}")
                # Always try to fix - don't skip repeated errors
                return self._handle_error(error_type, m)
        return False

    def _stream_output(self, stream, prefix=""):
        """Thread target: read process output and pipe to console + log."""
        try:
            for raw_line in stream:
                if raw_line is None:
                    break
                try:
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\n\r")
                except Exception:
                    line = str(raw_line).rstrip("\n\r")
                with self._lock:
                    self.log_lines.append(line)
                log.info(f"COMFY{prefix}: {line}")
                try:
                    print(
                        _c(
                            C.RED
                            if any(
                                w in line
                                for w in ("ERROR", "Error", "error", "CRITICAL")
                            )
                            else C.YELLOW
                            if any(w in line for w in ("WARNING", "Warning", "warn"))
                            else C.GREEN,
                            f"  │ {line}",
                        )
                    )
                except Exception:
                    print(f"  │ {line}")
        except Exception as e:
            log.error(f"Stream error: {e}")

    def launch(self, extra_args: list[str] = []) -> bool:
        """
        Launch ComfyUI, watch output, auto-fix errors, relaunch if needed.
        Returns True when ComfyUI is running cleanly.
        """
        section("5 / 5  —  Launching ComfyUI")

        # Fix version conflicts one more time right before launch
        # (ComfyUI-Manager may have reinstalled packages during node setup)
        section("Final version conflict check")
        fix_version_conflicts(self.inst)

        base_cmd = [str(self.inst.python_exe), str(self.inst.main_script)]
        launch_args = list(extra_args)

        for attempt in range(1, self.max_fix_rounds + 1):
            self.fix_round = attempt
            cmd = base_cmd + launch_args
            info(f"Launch attempt {attempt}/{self.max_fix_rounds}")
            step(f"Command: {' '.join(cmd)}")

            self.log_lines.clear()
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(self.inst.comfy_root),
                text=True,
                bufsize=1,
            )
            self.running = True

            stdout_thread = threading.Thread(
                target=self._stream_output,
                args=(self.process.stdout,),
                daemon=True,
            )
            stdout_thread.start()

            start_time = time.time()
            last_progress_print = time.time()
            success = False

            while self.process.poll() is None:
                time.sleep(0.2)
                elapsed = time.time() - start_time

                # Check for import errors while waiting
                with self._lock:
                    all_since_start = list(self.log_lines)

                has_any_import_error = False
                for line in all_since_start:
                    for p in NO_IMPORT_ERRORS_PATTERNS:
                        if p.search(line):
                            has_any_import_error = True
                            break
                    if has_any_import_error:
                        break

                # Print progress every 5 seconds
                if elapsed - last_progress_print > 5:
                    info(f"Starting ComfyUI... ({int(elapsed)}s)")
                    last_progress_print = elapsed

                # Success = no import errors for at least 30 seconds
                if not has_any_import_error and elapsed > 30:
                    success = True

                if success:
                    ok("ComfyUI started successfully! ✔")
                    break

                # If import errors detected after 10s, stop to fix them
                if has_any_import_error and elapsed > 10:
                    warn("Import errors detected, stopping to fix...")
                    # Terminate the process
                    if self.process.poll() is None:
                        self.process.terminate()
                        try:
                            self.process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            self.process.kill()
                    break

            rc = self.process.wait()
            stdout_thread.join(timeout=5)

            with self._lock:
                all_lines = list(self.log_lines)

            # Check final status
            has_import_errors = False
            for line in all_lines:
                for p in NO_IMPORT_ERRORS_PATTERNS:
                    if p.search(line):
                        has_import_errors = True
                        break
                if has_import_errors:
                    break

            info(f"Process exited with code {rc}")

            # Override success if import errors were detected
            if has_import_errors:
                success = False

            if success and not has_import_errors:
                ok("ComfyUI is running as a server!")
                return True

            # Process exited with errors — try to fix
            err(f"ComfyUI has import errors. Analysing logs to apply fixes...")
            info(f"Total log lines captured: {len(self.log_lines)}")

            with self._lock:
                all_lines = list(self.log_lines)

            # Print last few lines for debugging
            if all_lines:
                info("Last 5 lines from log:")
                for line in all_lines[-5:]:
                    info(f"  {line}")

            fixes_applied = 0
            for line in all_lines:
                if self._parse_and_fix(line):
                    fixes_applied += 1

            info(f"Fixes applied in this round: {fixes_applied}")

            # Always attempt to relaunch - don't break unless we've hit max AND no fixes applied
            if attempt == self.max_fix_rounds:
                if fixes_applied > 0:
                    warn(
                        f"Applied {fixes_applied} fix(es) but still failing. One more try..."
                    )
                    time.sleep(2)
                    continue
                err(f"Reached maximum fix attempts ({self.max_fix_rounds}).")
                break

            # If fixes were applied, always relaunch
            if fixes_applied > 0:
                fix(f"Applied {fixes_applied} fix(es). Relaunching…")
                time.sleep(2)
            else:
                # No fixes but process exited - check exit code
                if rc == 0:
                    # Clean exit - this should have been caught as success
                    warn("ComfyUI exited cleanly (code 0)")
                    break
                else:
                    # Non-zero exit - try again
                    warn(f"ComfyUI exited with code {rc}, relaunching...")
                    time.sleep(2)

        return False


def get_pip_list(inst: ComfyInstall) -> dict:
    """Get installed pip packages as a dict of name->version."""
    rc, out, er = run_cmd(
        [str(inst.python_exe), "-m", "pip", "list", "--format=json"],
        env=_build_pip_env(inst),
        timeout=60,
    )
    if rc == 0:
        try:
            import json as _json

            packages = _json.loads(out)
            return {p["name"].lower(): p["version"] for p in packages}
        except Exception:
            pass
    return {}


def count_node_classes(inst: ComfyInstall, node_path: Path) -> int:
    """Count node classes in a custom node by parsing __init__.py or .py files."""
    count = 0
    try:
        if node_path.is_file():
            return 0
        for py_file in node_path.rglob("*.py"):
            if py_file.name == "__init__.py":
                try:
                    content = py_file.read_text(encoding="utf-8", errors="ignore")
                    if "NODE_CLASS_MAPPINGS" in content:
                        import re as _re

                        matches = _re.findall(r"[\"'](\w+)[\"']\s*:", content)
                        count += len(matches)
                except Exception:
                    pass
    except Exception:
        pass
    return count


def update_context_file(inst: ComfyInstall, nodes: list[NodeStatus], success: bool):
    """Update comfyui_context.md with current environment state."""
    if not inst.comfy_root:
        info("ComfyUI root not set — skipping context file update.")
        return

    context_file = inst.comfy_root.parent / "comfyui_context.md"
    if not context_file.exists():
        info(f"Context file not found at {context_file} — skipping update.")
        return

    info(f"Updating context file: {context_file}")

    pip_list = get_pip_list(inst)
    key_packages = [
        "torch",
        "torchvision",
        "torchaudio",
        "xformers",
        "triton",
        "sageattention",
        "gguf",
        "transformers",
        "diffusers",
        "accelerate",
        "numpy",
        "Pillow",
        "opencv-python",
        "scipy",
        "huggingface-hub",
        "safetensors",
        "aiohttp",
        "pyyaml",
    ]

    gpu = detect_gpu_hardware()

    torch_script = textwrap.dedent("""
        try:
            import torch
            print("TORCH_VERSION=" + torch.__version__)
            print("CUDA_AVAILABLE=" + str(torch.cuda.is_available()))
            if torch.cuda.is_available():
                print("CUDA_VERSION=" + str(torch.version.cuda))
        except ImportError:
            print("TORCH=NOT_INSTALLED")
    """)
    result = subprocess.run(
        [str(inst.python_exe), "-c", torch_script], capture_output=True, text=True
    )
    torch_info = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            torch_info[k] = v

    lines = []
    lines.append("\U0001fbb9 ComfyUI Environment Context")
    lines.append("")
    lines.append(f"**Scanned:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if success:
        lines.append(
            f"**Last Clean Run:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    else:
        lines.append("**Last Clean Run:** No clean run recorded yet")
    lines.append(f"**Python:** `{inst.python_exe}`")
    lines.append(f"**ComfyUI Dir:** `{inst.comfy_root}`")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## System")
    lines.append("")
    lines.append(
        f"- OS: {platform.system()} {platform.release()} ({platform.machine()})"
    )

    cpu_info = "Unknown"
    try:
        import subprocess as _sp2

        if platform.system() == "Windows":
            r = _sp2.run(
                ["wmic", "cpu", "get", "name"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            lines_cpu = r.stdout.strip().split("\n")
            if len(lines_cpu) > 1:
                cpu_info = lines_cpu[1].strip()
    except Exception:
        pass
    lines.append(f"- CPU: {cpu_info}")

    ram_gb = "Unknown"
    try:
        import subprocess as _sp3

        if platform.system() == "Windows":
            r = _sp3.run(
                ["wmic", "ComputerSystem", "get", "TotalPhysicalMemory"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            lines_ram = r.stdout.strip().split("\n")
            if len(lines_ram) > 1:
                ram_bytes = int(lines_ram[1].strip())
                ram_gb = f"{ram_bytes / (1024**3):.0f} GB"
    except Exception:
        pass
    lines.append(f"- RAM: {ram_gb}")

    if gpu["name"] != "unknown":
        driver = gpu.get("driver", "unknown")
        vram_mb = gpu.get("vram_mb", gpu.get("vram_gb", 0) * 1024)
        gpu_str = f"- GPU: {gpu['name']}, {driver}, {vram_mb} MiB"
        if gpu.get("vram_free_mb"):
            gpu_str += f", {gpu['vram_free_mb']} MiB"
        lines.append(gpu_str)
    else:
        lines.append("- GPU: Unknown")

    torch_ver = torch_info.get("TORCH_VERSION", "NOT INSTALLED")
    cuda_ver = torch_info.get("CUDA_VERSION", "")
    if cuda_ver:
        lines.append(f"- PyTorch: {torch_ver} | CUDA: {cuda_ver}")
    else:
        lines.append(f"- PyTorch: {torch_ver}")

    lines.append("")
    lines.append("## Key Python Packages")
    lines.append("")
    for pkg in key_packages:
        version = pip_list.get(pkg.lower())
        if version:
            lines.append(f"- {pkg}: `{version}`")
        else:
            lines.append(f"- {pkg}: `NOT INSTALLED [X]`")

    lines.append("")
    lines.append(f"## Custom Nodes ({len(nodes)} installed)")
    lines.append("")
    for ns in nodes:
        node_classes = count_node_classes(inst, ns.path)
        if ns.errors:
            lines.append(f"- [ERR] **{ns.name}** ({node_classes} node classes)")
        else:
            lines.append(f"- [OK] **{ns.name}** ({node_classes} node classes)")

    lines.append("")
    lines.append("## Missing Requirements")
    lines.append("")
    missing_reqs = [n for n in nodes if n.errors]
    if missing_reqs:
        for ns in missing_reqs:
            for err in ns.errors:
                if "Missing package:" in err:
                    pkg = err.split("Missing package:")[1].strip()
                    lines.append(f"- `{pkg}` required by **{ns.name}**")
    else:
        lines.append("No missing requirements detected.")

    try:
        context_file.write_text("\n".join(lines), encoding="utf-8")
        ok(f"Context file updated successfully.")
    except Exception as e:
        warn(f"Could not update context file: {e}")


# ══════════════════════════════════════════════════════════════════════
#  SUMMARY REPORT
# ══════════════════════════════════════════════════════════════════════


def write_summary(inst: ComfyInstall, nodes: list[NodeStatus], success: bool):
    section("Summary Report")

    # Console summary
    total = len(nodes)
    updated = sum(1 for n in nodes if n.updated)
    deps_fixed = sum(1 for n in nodes if n.deps_installed)
    errored = sum(1 for n in nodes if n.errors)

    ok(f"ComfyUI root:     {inst.comfy_root}")
    ok(f"Python:           {inst.python_exe}")
    ok(f"Nodes found:      {total}")
    ok(f"Nodes updated:    {updated}")
    ok(f"Deps installed:   {deps_fixed}")
    if errored:
        warn(f"Nodes with errors: {errored}")
        for n in nodes:
            if n.errors:
                err(f"  {n.name}: {'; '.join(n.errors)}")

    if success:
        print()
        print(_c(C.GREEN + C.BOLD, "  ✔  ComfyUI should be working now!"))
    else:
        print()
        print(_c(C.YELLOW + C.BOLD, "  ⚠  ComfyUI could not be started automatically."))
        warn("Check the full log for details:")
        info(f"  {LOG_FILE}")

    # Write JSON summary alongside the log
    summary_path = LOG_FILE.with_suffix(".json")
    summary = {
        "timestamp": datetime.now().isoformat(),
        "comfy_root": str(inst.comfy_root),
        "python_exe": str(inst.python_exe),
        "is_portable": inst.is_portable,
        "os": platform.system(),
        "success": success,
        "log_file": str(LOG_FILE),
        "nodes": [
            {
                "name": n.name,
                "has_git": n.has_git,
                "updated": n.updated,
                "deps_installed": n.deps_installed,
                "errors": n.errors,
            }
            for n in nodes
        ],
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    ok(f"Log:   {LOG_FILE}")
    ok(f"JSON:  {summary_path}")


# ══════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════


def parse_args():
    parser = argparse.ArgumentParser(
        prog="comfyui_doctor",
        description="Scan, fix, update, and launch any ComfyUI installation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
        Examples:
          python comfyui_doctor.py                    # auto-detect ComfyUI
          python comfyui_doctor.py --path ~/ComfyUI   # explicit path
          python comfyui_doctor.py --no-launch        # fix only, don't launch
          python comfyui_doctor.py --no-update        # skip git pull
          python comfyui_doctor.py -- --listen 0.0.0.0 --port 8189  # pass args to ComfyUI
        """),
    )
    parser.add_argument(
        "--path", type=Path, default=None, help="Path to ComfyUI root directory"
    )
    parser.add_argument(
        "--no-update", action="store_true", help="Skip git pull on custom nodes"
    )
    parser.add_argument(
        "--no-deps", action="store_true", help="Skip dependency installation"
    )
    parser.add_argument(
        "--no-launch",
        action="store_true",
        help="Perform scan/fix only, do not launch ComfyUI",
    )
    parser.add_argument(
        "--max-fix-rounds",
        type=int,
        default=2,
        help="Max relaunch+fix attempts (default: 2)",
    )
    parser.add_argument(
        "extra",
        nargs=argparse.REMAINDER,
        help="Extra arguments passed to ComfyUI main.py (after --)",
    )
    return parser.parse_args()


def main():
    banner()
    args = parse_args()

    # Strip leading '--' separator if present
    extra_comfy_args = [a for a in (args.extra or []) if a != "--"]

    # ── Discovery ──
    inst = build_install(hint=args.path)

    # ── Environment scan (includes GPU detection + PyTorch mismatch fix) ──
    torch_info, gpu_info = scan_environment(inst)

    # ── Install torch if completely missing ──
    if torch_info.get("TORCH") == "NOT_INSTALLED":
        section("Installing PyTorch")
        reinstall_torch(inst, gpu_info)

    # ── Clean up any corrupted pip entries before installing anything ──
    section("Checking pip integrity")
    cleanup_broken_pip_entries(inst)

    # ── Fix common pip package conflicts that shadow node modules ──
    section("Fixing pip package conflicts")
    fix_pip_conflicts(inst)

    # ── Fix version-specific package conflicts ──
    section("Fixing package version conflicts")
    fix_version_conflicts(inst)

    # ── Install core ComfyUI requirements ──
    if inst.comfy_root:
        core_req = inst.comfy_root / "requirements.txt"
        if core_req.exists():
            section("Core ComfyUI Requirements")
            fix("Installing/verifying core requirements…")
            rc, _, er = run_cmd(
                [
                    str(inst.python_exe),
                    "-m",
                    "pip",
                    "install",
                    "-r",
                    str(core_req),
                    "--quiet",
                    "--no-warn-script-location",
                ],
                env=_build_pip_env(inst),
                timeout=600,
            )
            if rc == 0:
                ok("Core requirements satisfied.")
            else:
                warn(f"Core requirements had issues: {er[:300]}")

    # ── Node audit ──
    nodes = audit_nodes(inst)

    # ── Fix node init files (__init__.py, sys.path ordering) ──
    section("Fixing node init files")
    fix_node_init_files(inst)

    # ── Update & fix nodes ──
    if not args.no_update and not args.no_deps:
        update_and_fix_nodes(inst, nodes)
    elif args.no_update and not args.no_deps:
        section("4 / 5  —  Installing Dependencies (no git update)")
        for ns in nodes:
            if ns.has_requirements:
                install_requirements(inst, ns)
            if ns.has_install_py:
                run_install_py(inst, ns)
    else:
        section("4 / 5  —  Skipping updates & dependency install")
        info("(--no-update and/or --no-deps flags set)")

    # ── Launch ──
    success = False
    if args.no_launch:
        section("5 / 5  —  Launch Skipped")
        info("--no-launch specified. ComfyUI was not started.")
        success = True  # treat as success for summary
    else:
        launcher = ComfyLauncher(inst, max_fix_rounds=args.max_fix_rounds)
        success = launcher.launch(extra_args=extra_comfy_args)

    # ── Summary ──
    write_summary(inst, nodes, success)

    # ── Update context file ──
    if inst.comfy_root:
        update_context_file(inst, nodes, success)

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())

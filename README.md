# 🩺 ComfyUI Doctor     MAKE A BACKUP BEFORE RUNNING THIS IS VERY NEW

**A universal repair, update, and launcher tool for any ComfyUI installation.**

ComfyUI Doctor automatically finds your ComfyUI install, detects your hardware, fixes broken dependencies, updates all custom nodes, cleans up corrupted packages, and launches ComfyUI — all in one command. If ComfyUI crashes on startup, the doctor analyses the error, applies a fix, and tries again, repeating until it either succeeds or runs out of fixable errors.

Works with **portable, standalone, venv, and system installs** on Windows, Linux, and macOS. No configuration required.

---

## Table of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [What It Does — Phase by Phase](#what-it-does--phase-by-phase)
- [Command Line Options](#command-line-options)
- [Log Files](#log-files)
- [Common Scenarios](#common-scenarios)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)

---

## Features

- 🔍 **Auto-discovers** your ComfyUI root and Python environment — no paths to configure
- 🖥️ **GPU detection** independent of PyTorch, using OS-level tools (`nvidia-smi`, `wmic`, `lspci`, `system_profiler`)
- ⚡ **PyTorch mismatch detection and auto-fix** — if you have an NVIDIA GPU but a CPU-only PyTorch installed, it reinstalls the correct CUDA build automatically
- 📦 **Updates all custom nodes** via git pull/reset/re-clone and runs each node's `install.py` / `requirements.txt`
- 🧹 **Cleans corrupted pip entries** (`~partial-install` directories) that cause pip to fail silently
- 🔬 **Import scanning** — detects missing packages before launch without false-positiving on ComfyUI's own internal modules
- 🔄 **Auto-fix launch loop** — if ComfyUI crashes, the doctor reads the error output, applies fixes (git pull, pip install, node updates), and relaunches (up to 2 rounds)
- 📋 **Context file generation** — automatically updates `comfyui_context.md` with current pip list, installed nodes, and system state
- 📝 **Full logging** — every action written to a timestamped `.log` and `.json` file in `~/.comfyui_doctor_logs/`
- 🤖 **Smart error handling** — handles node-specific errors (e.g., "impact" missing = update ComfyUI-Impact-Pack node, not pip install) and tolerates version conflicts without failing

---

## Requirements

- **Python 3.6+** to run the doctor script itself (uses only the standard library)
- The doctor handles installing everything else, including PyTorch

Your ComfyUI install does **not** need to be working before you run this — that's the point.

---

## Installation

Just download the single file. No pip install needed.

**Option A — place it inside your ComfyUI folder (recommended):**
```
ComfyUI_Windows_portable/
    ComfyUI/
        comfyui_doctor.py   ← put it here
        main.py
        comfy/
        custom_nodes/
    python_standalone/
```

Then run it from there:
```bash
cd F:\ComfyUI_Windows_portable\ComfyUI
python comfyui_doctor.py
```

**Option B — run it from anywhere with an explicit path:**
```bash
python comfyui_doctor.py --path "F:\ComfyUI_Windows_portable\ComfyUI"
```

---

## Usage

### Quickstart

```bash
# Auto-detect everything and run the full repair + launch sequence
python comfyui_doctor.py

# If auto-detect fails, point it at your ComfyUI folder explicitly
python comfyui_doctor.py --path "F:\ComfyUI_Windows_portable\ComfyUI"
```

### Fix only, don't launch

```bash
python comfyui_doctor.py --no-launch
```

### Skip git updates (just fix deps)

```bash
python comfyui_doctor.py --no-update
```

### Pass extra arguments to ComfyUI

Anything after `--` is forwarded directly to ComfyUI's `main.py`:

```bash
python comfyui_doctor.py -- --listen 0.0.0.0 --port 8189
python comfyui_doctor.py -- --lowvram
python comfyui_doctor.py -- --cpu
python comfyui_doctor.py -- --listen --port 8188 --preview-method auto
```

---

## What It Does — Phase by Phase

### Phase 1 — Discovery

The doctor searches for your ComfyUI installation without any configuration:

1. Checks if you passed `--path` explicitly
2. Walks up from the current working directory, checking each parent folder
3. Checks ~15 common install locations (home, desktop, `C:/`, `D:/`, `/opt/`, etc.)
4. Falls back to a deep scan of your home directory (up to 5 levels deep)

A folder is recognised as ComfyUI if it contains both `main.py` and a `comfy/` subdirectory.

**Python discovery** follows the same logic, and knows about all portable layout variants:

| Folder name | Used by |
|---|---|
| `python_standalone` | ComfyUI Windows portable (2024+) |
| `python_embeded` | Older ComfyUI portable (original typo) |
| `python_embedded` | Corrected spelling variant |
| `venv` / `.venv` | Manual venv installs |
| System Python | Last resort fallback |

For the standard Windows portable release, the layout looks like this:
```
ComfyUI_Windows_portable/
    ComfyUI/               ← comfy_root
    python_standalone/     ← Python found here automatically
```

---

### Phase 2 — Hardware & Environment Scan

The doctor scans your hardware **independently of PyTorch**, so it can detect and fix a mismatched installation.

**GPU detection method by OS:**

| OS | Method |
|---|---|
| Windows | `nvidia-smi` first (accurate VRAM), then PowerShell `Get-CimInstance`, then `wmic` |
| Linux | `lspci` + `nvidia-smi` |
| macOS | `system_profiler SPDisplaysDataType` |

> **Why not just use `torch.cuda.get_device_name()`?**  
> Because if the wrong PyTorch build is installed (e.g. CPU-only), CUDA will report as unavailable even if your GPU is perfectly fine. The doctor detects your GPU at the OS level first, then compares it against what PyTorch reports.

**PyTorch mismatch detection:**

If the doctor finds a mismatch — for example, an NVIDIA GPU with a `+cpu` PyTorch build installed — it automatically:

1. Uninstalls the wrong build
2. Installs the correct build for your hardware

| Hardware | PyTorch build installed |
|---|---|
| NVIDIA GPU | `cu121` (CUDA 12.1, compatible with CUDA 11.8+ drivers) |
| AMD GPU (Linux) | `rocm6.0` |
| AMD GPU (Windows) | `torch-directml` |
| Intel Arc | `xpu` |
| Apple Silicon | Standard build (MPS is built-in) |
| No GPU / unknown | CPU build |

---

### Phase 3 — Custom Node Audit

Scans every folder inside `custom_nodes/` and records:

- Whether it has a `.git` directory (can be updated)
- Whether it has a `requirements.txt`
- Whether it has an `install.py`
- Which Python imports it uses (for pre-flight missing package detection)

---

### Phase 4 — Update & Dependency Installation

For each custom node, in order:

1. **`git pull --rebase --autostash`** — updates to the latest version
2. If pull fails → **`git fetch` + `git reset --hard origin/{branch}`** — force sync to remote
3. If reset fails → **delete and re-clone** — fresh clone from remote URL
4. **`install.py`** — runs the node's own installer if present
5. **`requirements.txt`** — installs all listed packages via pip (with --no-deps first to avoid conflicts)
6. **Import scan** — statically analyses the node's `.py` files to find imports that aren't installed, then attempts to install them

**Node-specific error handling:**

When a node error is detected (e.g., "impact" missing), the doctor doesn't try to pip install it as a package. Instead, it maps the error to the appropriate node and applies a git fix:
- `impact` missing → updates ComfyUI-Impact-Pack node
- `security_check` missing → updates ComfyUI-Manager node  
- `cupy.memoize` error → updates ComfyUI-GIMM-VFI node

**Version conflict tolerance:**

If pip install fails due to version conflicts (common with numpy), the doctor logs it as a warning and continues rather than failing the entire process. Most nodes work fine with slightly different package versions.

**Corrupted pip entry cleanup:**

If a previous install was interrupted (e.g. by a crash or force-quit), pip can leave behind partial entries with names like `~omfy-aimdo` or `~nstall-tmp-xxxxx`. These cause pip to print `WARNING: Ignoring invalid distribution` and then fail for unrelated reasons. The doctor detects and removes these at the start, and again automatically if pip fails mid-run.

**False-positive filtering:**

ComfyUI nodes heavily import ComfyUI's own internal runtime modules (`folder_paths`, `nodes`, `server`, `comfy`, `model_management`, etc.) which are not PyPI packages. The import scanner uses a combination of a hard blocklist and pattern matching to skip these:

- Anything matching `*_models`, `*_utils`, `*_nodes`, `*_global`, `*_server`, `*_compatibility`, `ComfyUI_*`, `comfyui_*`, and many more patterns
- All `.py` files and package subdirectories local to the node itself
- All of Python's standard library

---

### Phase 5 — Launch & Auto-Fix Loop

The doctor launches `main.py` using the correct Python interpreter and streams all output to the console (colour-coded by severity) and to the log file simultaneously.

**If ComfyUI starts successfully**, the doctor marks the run as successful, updates the `comfyui_context.md` file, and exits cleanly.

**If ComfyUI crashes**, the doctor:

1. Parses all output for known error patterns
2. Attempts a fix (see table below)
3. Relaunches
4. Repeats up to 2 times (configurable with `--max-fix-rounds`)

| Error detected | Fix applied |
|---|---|
| `ModuleNotFoundError: No module named 'xyz'` | `pip install xyz` (with import→PyPI name remapping) |
| Node package error (e.g., `impact`, `security_check`) | Updates corresponding node via git |
| `AttributeError: module 'cupy' has no attribute 'memoize'` | Updates ComfyUI-GIMM-VFI node |
| `PRESTARTUP FAILED: ...` | Git pull/reset/re-clone on failing node |
| `ImportError: cannot import name ...` | Logged, no auto-fix |
| CUDA out of memory | Adds `--lowvram` flag on next launch |
| Repeated OOM / GPU errors | Falls back to `--cpu` |
| Missing `xformers` | `pip install xformers` |
| Missing `triton` (Linux only) | `pip install triton` |
| Version conflict during pip install | Warning logged, continue anyway |

---

## Command Line Options

| Option | Description |
|---|---|
| `--path PATH` | Explicit path to ComfyUI root directory |
| `--no-update` | Skip `git pull` on all custom nodes |
| `--no-deps` | Skip dependency installation entirely |
| `--no-launch` | Run scan and fix phases only, do not launch ComfyUI |
| `--max-fix-rounds N` | Maximum relaunch+fix attempts (default: 2) |
| `-- [args]` | Everything after `--` is passed directly to ComfyUI's `main.py` |

---

## Log Files

Every run produces two files in `~/.comfyui_doctor_logs/`:

**`comfyui_doctor_YYYYMMDD_HHMMSS.log`** — full verbose log of every command run, output received, and decision made.

**`comfyui_doctor_YYYYMMDD_HHMMSS.json`** — structured summary:
```json
{
  "timestamp": "2026-03-26T10:26:32",
  "comfy_root": "F:\\ComfyUI_Windows_portable\\ComfyUI",
  "python_exe": "F:\\ComfyUI_Windows_portable\\python_standalone\\python.exe",
  "is_portable": true,
  "os": "Windows",
  "success": true,
  "nodes": [
    {
      "name": "ComfyUI-Manager",
      "has_git": true,
      "updated": true,
      "deps_installed": true,
      "errors": []
    }
  ]
}
```

---

## Common Scenarios

### First time setup / fresh portable install

```bash
python comfyui_doctor.py --path "F:\ComfyUI_Windows_portable\ComfyUI"
```

The doctor will verify PyTorch, install core requirements, and launch ComfyUI.

### After installing a batch of new custom nodes

```bash
python comfyui_doctor.py --no-launch
```

Scans and installs all deps without launching. Good for setting up before your first run with new nodes.

### ComfyUI worked yesterday, broken today after an update

```bash
python comfyui_doctor.py
```

The doctor will pull the latest version of all nodes, reinstall any changed dependencies, and fix whatever broke.

### You want to run ComfyUI on your local network

```bash
python comfyui_doctor.py -- --listen 0.0.0.0 --port 8188
```

### You have very little VRAM

```bash
python comfyui_doctor.py -- --lowvram
```

Or for integrated graphics / CPU only:

```bash
python comfyui_doctor.py -- --cpu
```

### You just want to update nodes without touching anything else

```bash
python comfyui_doctor.py --no-launch
```

---

## Troubleshooting

### "Could not find ComfyUI root directory"

The auto-detection didn't find `main.py` + `comfy/` in any of its search paths. Use `--path` explicitly:

```bash
python comfyui_doctor.py --path "F:\ComfyUI_Windows_portable\ComfyUI"
```

### "No portable/venv Python found — falling back to system Python"

The doctor couldn't find `python_standalone/` or any other portable Python next to your ComfyUI folder. This usually means you ran the script from somewhere unrelated to your install. Use `--path` or run the script from inside the ComfyUI folder.

### PyTorch still shows CPU after reinstall

Your NVIDIA driver may be too old for CUDA 12.1. Check your driver version:
- CUDA 12.1 requires **driver 525.60+** on Linux, **528.33+** on Windows
- If your driver is older, update it from [nvidia.com/drivers](https://www.nvidia.com/drivers)

### Nodes still showing errors after running the doctor

Some nodes have errors the doctor can't auto-fix:
- **Compilation errors** in the node's own code (need a node update from the developer)
- **Model files missing** — the node requires specific model files to be downloaded separately
- **Platform-incompatible nodes** — some nodes only work on Linux (e.g. nodes requiring `triton` on Windows)

Check the full `.log` file for the exact error message.

### pip fails with "Ignoring invalid distribution ~xxxxx"

This is caused by a corrupted partial install in your site-packages. The doctor cleans these up automatically, but if it keeps happening, you can manually delete any folder or file in your `python_standalone/Lib/site-packages/` (or equivalent) whose name starts with `~`.

---

## FAQ

**Q: Do I need to run this as Administrator on Windows?**  
No. The doctor only installs Python packages and modifies files within your ComfyUI folder. No system-level changes are made.

**Q: Will it break my existing working setup?**  
It shouldn't. `git pull --rebase --autostash` preserves any local changes. Package installs only add or upgrade — they don't remove anything unless fixing a PyTorch mismatch (in which case it reinstalls the correct version). That said, a backup before any major repair run is always sensible.

**Q: My node is private / not on git. Will it still be processed?**  
Yes. The doctor skips `git pull` for nodes without a `.git` directory but still runs `install.py` and `requirements.txt` if they exist, and still scans imports.

**Q: Can I use this with multiple ComfyUI installs?**  
Yes — run it once per install, using `--path` to point at each one.

**Q: Does it modify ComfyUI itself, not just the nodes?**  
It installs ComfyUI's own `requirements.txt` (the core deps like `torch`, `transformers`, `safetensors`, etc.) and fixes PyTorch if mismatched. It does not `git pull` on the ComfyUI repo itself — only on custom nodes.

**Q: What does the `~omfy-aimdo` / `~partial-install` error mean?**  
A previous pip install was interrupted mid-write and left a broken partial package in site-packages. The doctor detects and removes all such entries automatically before installing anything.

**Q: What is the `comfyui_context.md` file?**  
After each run, the doctor automatically updates `comfyui_context.md` in your ComfyUI folder with the current state: pip packages with versions, installed custom nodes with their node class counts, system info (CPU, RAM, GPU), and missing requirements. This gives you a quick overview of your environment without needing to run separate commands.

---

## Supported Platforms

| Platform | Status |
|---|---|
| Windows 10/11 (portable) | ✅ Primary target |
| Windows 10/11 (venv) | ✅ Supported |
| Linux (Ubuntu/Debian) | ✅ Supported |
| Linux (Arch/other) | ✅ Supported |
| macOS (Intel) | ✅ Supported |
| macOS (Apple Silicon / M-series) | ✅ Supported (MPS) |

---

## License

MIT — do whatever you want with it.

# XiJian AGENTS.md — Essential Context for OpenCode Sessions

## Project Overview
- **XiJian (隙间)**: Local-first AI chat app with 3D avatars, simulated worlds, memory, NSFW interactions (hidden by default), desktop pet mode with screen control
- **Stack**: Python (Flask API core) + SwiftUI (macOS native app) + PyWebView (DevKit GUI)
- **Targets**: macOS 14+ (M-series required), Windows 10/11 (Core API via different impl), iOS/Android companion apps only
- **Local AI**: MLX (Apple Silicon) / llama.cpp (GGUF) / OpenAI-compatible remote fallback
- **Python**: ≥3.11, conda env `xijianBase` mandatory

---

## Directory Structure
```
XiJian/
├── core/                    # Python Flask API (xijian-api)
│   ├── xijian_api/          # Main package
│   ├── tests/               # pytest suite (60+ files)
│   ├── scripts/             # dev.sh, dev.ps1, xijian-api.spec, build tools
│   ├── config.toml          # Single source of truth for API config
│   └── pyproject.toml       # deps: flask, flask-sock, psutil, py7zr, waitress
├── macapp/                  # macOS SwiftUI app (XcodeGen + xcodebuild)
│   ├── Sources/             # Swift code (XiJianKit framework + App)
│   ├── Resources/Core/      # Embedded PyInstaller onedir bundle (built by build-core.sh)
│   ├── project.yml          # XcodeGen spec
│   ├── build-macapp.sh      # Orchestrates: build-core.sh → xcodegen → xcodebuild
│   └── build-core.sh        # PyInstaller onedir → Resources/Core/
├── devkit/                  # Developer Kit (PyWebView GUI)
│   ├── *.py                 # Editors: character, dialog, memory, motion, plot, world
│   ├── requirements.txt     # pywebview, py7zr
│   └── build-devkit.sh      # PyInstaller → dist/
├── build.sh                 # Repo-level unified build (core wheel + tests)
├── scripts/                 # Misc repo scripts
└── docs/                    # Documentation
```

---

## Key Developer Commands

### Core API (Python)
```bash
# Unified repo build (wheel + sdist + tests)
./build.sh                      # default: test + package
./build.sh --with-devkit        # include DevKit deps
./build.sh --skip-test          # skip pytest
./build.sh --clean              # clean dist/build/egg-info first

# Core dev script (conda-aware, interactive or CLI)
./core/scripts/dev.sh                    # interactive wizard
./core/scripts/dev.sh --run --dev --port 18600
./core/scripts/dev.sh --test             # run pytest
./core/scripts/dev.sh --build --zip      # PyInstaller onedir + zip
./core/scripts/dev.sh --with-mlx         # install MLX backend
./core/scripts/dev.sh --with-gguf        # install GGUF backend
./core/scripts/dev.sh --info             # show env info
```

### macOS App
```bash
cd macapp
./build-macapp.sh               # full build (Debug)
./build-macapp.sh --release     # Release build
./build-macapp.sh --skip-core   # skip PyInstaller (fast UI iteration)
./build-macapp.sh --clean       # clean build/ XiJian.xcodeproj
```
- Output: `~/Library/Developer/Xcode/DerivedData/XiJian-*/Build/Products/<config>/XiJian.app`
- Requires: `xcodegen` (`brew install xcodegen`), Xcode

### DevKit
```bash
cd devkit
./build-devkit.sh               # PyInstaller → dist/
```

### Testing
```bash
# Core tests (run from core/ with venv activated)
pytest -q                       # all tests
pytest tests/test_chat_sync.py  # single file
pytest -k "chat"                # filter by name
```
- CI sets `XIJIAN_OVERLOAD_MONITOR=0` to disable overload guard during tests

---

## Critical Conventions & Gotchas

### Python Environment
- **Always use conda `xijianBase`**: `/opt/anaconda3/envs/xijianBase/bin/python`
- `build.sh` and `build-core.sh` hardcode this path — do not change
- `core/scripts/dev.sh` auto-detects/creates `xijianBase`
- Venv at `core/.venv` used by `build.sh`; separate from conda env

### Config System
- **Single source**: `core/config.toml` (copied to `macapp/Resources/Core/config.toml` at build time)
- Runtime resolution: `$XIJIAN_CONFIG` > `./config.toml` > `<repo>/config.toml`
- Token file managed by runtime at `~/Library/Application Support/XiJian/tmp/xijian-{pid}.token`
- Model checkpoints under `<storage.base_dir>/models/<type>/<id>/<file>`

### Build Artifacts
- **PyInstaller mode**: `onedir` (multi-file), **not** onefile — avoids startup decompression delay
- Spec file: `core/scripts/xijian-api.spec` (includes sqlite3 fix — store.py needs it)
- `macapp/build-core.sh` outputs to `macapp/Resources/Core/` (embedded in .app bundle)
- `core/scripts/dev.sh --build` outputs to `core/dist/xijian-api/`

### SwiftUI App
- XcodeGen generates `XiJian.xcodeproj` from `project.yml` — never edit .xcodeproj directly
- Swift packages: `swift-markdown-ui`, `NetworkImage`, `cmark-gfm`
- Post-gen hook: `Scripts/add_resources.py` (copies Resources)
- Entitlements: `Entitlements.entitlements` (network, microphone, etc.)

### DevKit
- Optional dependency: `pip install -e ".[devkit]"` (pulls pywebview → PyObjC/pythonnet/webkitgtk)
- Entry point: `xijian-devkit` → `xijian_api.devkit.main:main`
- Heavy UI editors: character, dialog, memory, motion, plot, world, model_viewer

### Testing Quirks
- Models in `config.toml` use `backend = "mock"` for CI — tests don't need real weights
- Overload monitor disabled via env var in CI, not config edit
- Large test suite (~60 files); run focused tests during development

---

## Common Workflows

### Add a New API Endpoint
1. Edit `core/xijian_api/` (routes, services, models)
2. Add test in `core/tests/test_*.py`
3. Run `./core/scripts/dev.sh --test` (or `pytest -q`)
4. Update `core/config.toml` if new config keys needed

### Modify macOS UI
1. Edit `macapp/Sources/`
2. `./macapp/build-macapp.sh --skip-core` (fast iteration)
3. Full build: `./macapp/build-macapp.sh --release`

### Update Dependencies
- Python: edit `core/pyproject.toml`, run `./build.sh --clean`
- Swift: edit `macapp/project.yml`, run `./macapp/build-macapp.sh --clean`
- DevKit: edit `devkit/requirements.txt`

### Release Build
```bash
# 1. Core wheel
./build.sh --clean --with-all

# 2. macOS app
cd macapp && ./build-macapp.sh --release --clean

# 3. DevKit (if needed)
cd devkit && ./build-devkit.sh
```

---

## References
- `core/config.toml` — full config reference with comments
- `docs/Dev. Function List功能清单v2.md` — detailed feature spec
- `docs/Deps.md` — third-party dependencies
- `core/scripts/xijian-api.spec` — PyInstaller spec (sqlite3 fix documented)
- `macapp/project.yml` — XcodeGen source of truth
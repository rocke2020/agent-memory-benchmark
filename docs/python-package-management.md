# Python Package Management in This Repository

## Terms

| Term | Meaning here |
| --- | --- |
| **Distribution package** | The installable project named `omb`. This is the name recorded in package metadata and `uv.lock`. |
| **Import package** | The Python package imported as `memory_bench`. Its source is under `src/memory_bench/`. |
| **Wheel** | Python's standard built-package archive. A normal installation copies a wheel's packaged files into the environment. |
| **Build backend** | Hatchling, the tool that implements Python's standardized build interface, Python Enhancement Proposal (PEP) 517, for this project. |
| **Editable install** | A development installation that imports directly from the checkout, so most source edits take effect without reinstalling the project. |
| **Path configuration (`.pth`) file** | A file processed during normal Python startup that adds paths to `sys.path`, Python's module search path. |
| **Command-line interface (CLI) entry point** | A named shell command generated from a Python object. This project declares `omb` as a command backed by `memory_bench.cli:app`. |
| **Lockfile** | `uv.lock`, the generated record of the dependency versions and sources selected for a reproducible environment. |

> **TL;DR:** [`pyproject.toml`](../pyproject.toml) is the package configuration source of truth: it names the distribution `omb`, declares dependencies and Python compatibility, exposes the `omb` command, maps the wheel to `src/memory_bench`, and selects Hatchling. By default, `uv sync` and `uv run` install the project in editable mode. In the current environment, Hatchling generated editable metadata and `uv` installed a `.pth` file that adds this repository's `src/` directory to Python's module search path. The `.pth` file is generated under the ignored `.venv/`; it must not be edited or committed.

## 1. What `pyproject.toml` controls

The repository controls package identity, dependencies, commands, source layout, and build tooling in one checked-in file. It does not directly prescribe the generated `.pth` filename or its absolute contents.

```toml
[project]
name = "omb"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "datasets>=2.0",
    "typer>=0.12",
    "rich>=13",
    "rank-bm25>=0.2",
    "google-genai>=1.66.0",
    "mem0ai>=1.0.5",
    "cognee>=0.5.4",
    "sentence-transformers>=3.0",
    "python-dotenv>=1.0",
    "hindsight-all>=0.4",
    "supermemory>=0.1",
    "httpx[socks]>=0.27",
    "qdrant-client>=1.13",
    "fastapi[standard]>=0.135.1",
    "uvicorn>=0.41.0",
    "tiktoken>=0.12.0",
    "groq>=1.1.1",
]

[project.scripts]
omb = "memory_bench.cli:app"

[tool.hatch.build.targets.wheel]
packages = ["src/memory_bench"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

These sections have separate responsibilities:

| Section | Repository decision | Result |
| --- | --- | --- |
| `[project]` | Name, version, supported Python, and direct dependencies | Install metadata identifies the distribution as `omb==0.1.0` and installs its dependencies. |
| `[project.scripts]` | Command-to-object mapping | Installation generates an `omb` launcher that loads `app` from `memory_bench.cli`. |
| `[tool.hatch.build.targets.wheel]` | Source package included in the wheel | Hatchling packages `src/memory_bench` under the import name `memory_bench`. |
| `[build-system]` | PEP 517 build backend and its build dependency | Installers such as `uv` ask Hatchling to build regular or editable installation artifacts. |

The distribution name and import name do not need to match:

```text
Distribution name: omb
Import package:    memory_bench
CLI command:       omb
```

Package installers reason about the distribution name. Python source code reasons about the import package name.

## 2. How an editable installation is created

Editable mode is selected by `uv` during project synchronization, while Hatchling determines how this package layout is represented. No checked-in line explicitly says "create `_editable_impl_omb.pth`".

The current flow is:

```text
pyproject.toml
    defines omb and src/memory_bench
        |
        v
uv run / uv sync
    locks and synchronizes the project environment
        |
        v
Hatchling editable build
    creates editable wheel metadata
        |
        v
uv installation into .venv
    installs distribution metadata and a .pth file
        |
        v
normal Python startup
    processes the .pth file and adds ./src to sys.path
```

The current generated file is:

```text
.venv/lib/python3.12/site-packages/_editable_impl_omb.pth
```

Its single line is the absolute path to this checkout's `src/` directory. The filename, Python-version directory, and absolute path are generated details and may change after recreating the environment, changing Python versions, changing build backends, or moving the checkout.

The installed metadata provides the evidence for each tool's role:

```text
omb-0.1.0.dist-info/INSTALLER       -> uv
omb-0.1.0.dist-info/WHEEL           -> Generator: hatchling 1.32.0
omb-0.1.0.dist-info/direct_url.json -> editable: true
```

## 3. How Python resolves imports from `src/`

Python does not treat a directory named `src` specially. The editable installation works because the generated `.pth` file makes `src/` one entry in `sys.path`.

Given this layout:

```text
src/
└── memory_bench/
    ├── __init__.py
    ├── dataset/
    │   └── ama_bench.py
    └── memory/
        └── hindsight.py
```

Python resolves imports as follows:

```python
import memory_bench
from memory_bench.dataset.ama_bench import AmaBenchDataset
from memory_bench.memory.hindsight import HindsightMemoryProvider
```

For `import memory_bench`, Python searches each `sys.path` entry for a matching module or package. Once it reaches this repository's `src/` entry, it finds `src/memory_bench/__init__.py`.

Adding `src/` to `sys.path` does not make every nested directory a top-level package. For example, `src/memory_bench/dataset/` remains `memory_bench.dataset`, not top-level `dataset`.

There is one editable-mode caveat: because the generated `.pth` file exposes the whole `src/` directory as a search root, another valid top-level module or package placed directly under `src/` may become importable locally even if the wheel configuration still includes only `src/memory_bench`. A clean wheel or non-editable installation is the stronger packaging-boundary check.

## 4. Editable and non-editable modes

Editable mode is appropriate for local development; non-editable mode better represents deployment because it removes the runtime dependency on the checkout's source path.

| Mode | Command example | Where imports come from | Effect of editing `src/` |
| --- | --- | --- | --- |
| Editable, the `uv` default | `uv sync --locked` | The checkout through generated editable metadata | Most Python source changes are visible immediately. |
| Non-editable | `uv sync --locked --no-editable` | Files installed from the built wheel | Source changes require another build/install synchronization. |

`uv run` also installs the project in editable mode by default and accepts `--no-editable`. Use the source-declared command with a checked lockfile as follows:

```bash
uv run --locked omb --help
```

Editable mode does not eliminate every rebuild step. Changes to package metadata, dependencies, entry points, build configuration, or compiled extensions still require synchronization or rebuilding.

## 5. What `uv.lock` records

`uv.lock` records the resolved environment; it is generated state, not the place to design the package. Change `pyproject.toml` or use a `uv` dependency command, then regenerate the lockfile.

The current root-project entry includes:

```toml
[[package]]
name = "omb"
version = "0.1.0"
source = { editable = "." }
```

This records that the local root distribution is sourced from the repository and installed editably. The following commands enforce different lock behavior:

```bash
# Verify that uv.lock agrees with pyproject.toml without updating it.
uv lock --check

# Refuse to run if uv.lock is stale.
uv run --locked omb --help
```

Do not hand-edit `uv.lock`. A manual edit can disagree with the resolver's model and will be overwritten or rejected later.

## 6. Repository files versus generated files

Only the declarative package inputs and lockfile belong in version control. Environment-specific installation artifacts stay under `.venv/`, which this repository ignores.

| Path | Checked in? | Owner | Purpose |
| --- | --- | --- | --- |
| `pyproject.toml` | Yes | Project maintainers | Declares metadata, dependencies, command entry points, source packages, and the build backend. |
| `uv.lock` | Yes | Generated by `uv`, reviewed by maintainers | Pins the resolved dependency graph and records the editable root source. |
| `src/memory_bench/` | Yes | Project maintainers | Contains the importable application source. |
| `.venv/` | No | `uv` | Holds the checkout-specific Python environment. |
| `.venv/.../_editable_impl_omb.pth` | No | Hatchling/`uv` installation pipeline | Adds this checkout's `src/` path during normal Python startup. |
| `.venv/.../omb-0.1.0.dist-info/` | No | Hatchling/`uv` installation pipeline | Records installed distribution metadata, entry points, and provenance. |

Never copy or commit the generated `.pth` file. Its absolute path makes it specific to one checkout on one machine.

## 7. Where to make common changes

Package-management changes should be made at their authoritative layer, followed by a lock and environment refresh where needed.

| Goal | Authoritative change |
| --- | --- |
| Add or remove a runtime dependency | Use `uv add` or `uv remove`, or edit `[project].dependencies`, then update `uv.lock`. |
| Change the supported Python versions | Edit `[project].requires-python`, then relock and recreate or resync incompatible environments. |
| Add or rename a CLI command | Edit `[project.scripts]`, then resync so the launcher metadata is regenerated. |
| Rename the distribution | Edit `[project].name`; assess publishing, lockfile, and launcher consequences separately. |
| Rename the import package | Move the source package and update `[tool.hatch.build.targets.wheel].packages` plus all imports. |
| Change the build backend | Edit `[build-system]`; recreate the environment and verify both editable and wheel installations. |
| Use deployment-style installation | Pass `--no-editable` to `uv sync` or `uv run`; do not modify the generated `.pth` file. |

## 8. Verification and troubleshooting

The strongest quick check verifies the lockfile and then asks Python's import machinery for the resolved package path. Checking only that `.venv` or a `.pth` file exists is insufficient because it does not prove the import target is valid.

```bash
uv lock --check
uv run --locked python -c \
  'from importlib.util import find_spec; print(find_spec("memory_bench").origin)'
```

For the current editable checkout, the second command should print a path ending in:

```text
agent-memory-benchmark/src/memory_bench/__init__.py
```

Common failure patterns:

| Symptom | Likely boundary to inspect |
| --- | --- |
| `ModuleNotFoundError: No module named 'memory_bench'` | Confirm the command uses the project environment through `uv run`, the project has been synchronized, and the build-system/package configuration is present. |
| A source edit is not visible | Confirm Python resolved `memory_bench` from this checkout rather than another installation; resync after metadata or build-configuration changes. |
| A command exists although `pyproject.toml` does not declare it | Treat the launcher as potentially stale environment residue; the checked-in entry-point declaration is authoritative for a fresh installation. |
| Editable imports work but a wheel install fails | Check whether editable mode exposed an undeclared sibling under `src/`; verify the Hatchling wheel package selection. |
| A moved checkout still resolves the old path | Recreate or resync `.venv`; never patch the generated `.pth` file manually. |

## Further reading

The behavior described above follows the repository's checked-in configuration and the documented contracts of its tools:

- [uv: Configuring projects, including editable mode](https://docs.astral.sh/uv/concepts/projects/config/)
- [uv: Locking and syncing project environments](https://docs.astral.sh/uv/concepts/projects/sync/)
- [Hatch: Wheel package selection](https://hatch.pypa.io/1.10/config/build/#packages)
- [Python 3.11: `site` and `.pth` path configuration files](https://docs.python.org/3.11/library/site.html)

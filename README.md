# buildmgr - A simple Python-based build system

This module lets you define targets, dependencies, and commands in pure Python
to manage your project's build workflow, similar to Make.

## Usage

1. Define your own targets:
   - Use `BuildTarget` for normal build steps:
       • `name`: unique identifier
       • `help_msg`: short description shown in CLI help
       • `log_msg`: message printed when the target runs
       • `commands`: list of shell commands (each is a `Sequence[str|Path]`)
       • `dependencies`: other `BuildTarget` instances this target relies on
       • `inputs` / `outputs`: files to determine staleness
   - Use `CleanupTarget` to remove files or directories:
       • `patterns`: glob patterns to delete
       • `directories`: `Path` objects to remove entirely

2. Customize global behavior:
   - Call `get_logger()` at import to configure ANSI-colored logging.
   - Use `global_config.dry_run = True` to simulate without executing.

3. Wire up your build graph:

   ```python
   setup = BuildTarget(
       name="setup",
       help_msg="Create virtualenv & install dependencies",
       log_msg="Setting up virtual environment",
       commands=[
           ["python3", "-m", "venv", ".venv"],
           [Path(".venv/bin/python"), "-m", "pip", "install", "-r", "requirements.txt"],
       ],
       outputs=[Path(".venv")],
   )

   build_docs = BuildTarget(
       name="docs",
       help_msg="Build Sphinx documentation",
       log_msg="Generating docs",
       commands=[["make", "html", "-C", "docs"]],
       dependencies=[setup],
       inputs=list(Path("docs").rglob("*.rst")),
       outputs=[Path("docs/_build/html/index.html")],
   )
   ```

Instantiate and run the build system:

```python
from build import BuildSystem
import sys

all_target = BuildTarget(
    name="all",
    help_msg="Build everything",
    log_msg="Building all targets",
    commands=[],
    dependencies=[build_docs, /* your other targets here */],
)

system = BuildSystem(
    targets=[setup, build_docs, all_target, /* others */],
    default=all_target
)

if __name__ == "__main__":
    sys.exit(system())  # Parses CLI args, determines outdated targets, and runs them in order

```

## Command-line interface

```sh
$ buildmgr [target]

  target     Name of the target to run (default: 'all')
Options:
  -d, --dry-run    Show commands but do not execute
  -v, --verbose    Enable DEBUG-level logging
  -q, --quiet      Show only WARNING and above
```

## Key features

Incremental builds: only targets whose outputs are missing or older than any inputs will be run.

Automatic dependency topological sort with cycle detection.

ANSI-colored logging with optional verbosity levels.

Easy cleanup via CleanupTarget.

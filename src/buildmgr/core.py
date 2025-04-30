from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence, TextIO

__all__ = ["BuildSystem", "CleanupTarget", "BuildTarget"]


# region: Utility Functions
def supports_color(stream: TextIO) -> bool:
    """Return True if the given stream supports ANSI colors."""
    return hasattr(stream, "isatty") and stream.isatty()


def get_logger(use_color: Optional[bool] = None) -> logging.Logger:
    """
    Create and configure a logger with optional ANSI-colored output.
    """
    if use_color is None:
        use_color = supports_color(sys.stdout) and supports_color(sys.stderr)

    class Colors:
        GREY = "\x1b[38;21m"
        BLUE = "\x1b[38;5;39m"
        YELLOW = "\x1b[38;5;226m"
        RED = "\x1b[38;5;196m"
        BOLD_RED = "\x1b[31;1m"
        RESET = "\x1b[0m"

    class ColorFormatter(logging.Formatter):
        FORMATS = {
            logging.DEBUG: Colors.GREY
            + "%(levelname)-8s"
            + Colors.RESET
            + ": %(message)s",
            logging.INFO: Colors.BLUE
            + "%(levelname)-8s"
            + Colors.RESET
            + ": %(message)s",
            logging.WARNING: Colors.YELLOW
            + "%(levelname)-8s"
            + Colors.RESET
            + ": %(message)s",
            logging.ERROR: Colors.RED
            + "%(levelname)-8s"
            + Colors.RESET
            + ": %(message)s",
            logging.CRITICAL: Colors.BOLD_RED
            + "%(levelname)-8s"
            + Colors.RESET
            + ": %(message)s",
        }

        def format(self, record: logging.LogRecord) -> str:
            fmt = self.FORMATS.get(record.levelno, "%(levelname)s: %(message)s")
            msg = record.getMessage()
            if not use_color:
                import re

                msg = re.sub(r"\x1b\[[0-9;]*m", "", msg)
                fmt = "%(levelname)-8s: %(message)s"
            record.message = msg
            return logging.Formatter(fmt).format(record)

    logger = logging.getLogger("make")
    logger.handlers.clear()

    stdout_handler = logging.StreamHandler(sys.stdout)
    stderr_handler = logging.StreamHandler(sys.stderr)
    formatter = ColorFormatter()
    stdout_handler.setFormatter(formatter)
    stderr_handler.setFormatter(formatter)
    stdout_handler.addFilter(lambda record: record.levelno <= logging.INFO)
    stderr_handler.setLevel(logging.WARNING)
    logger.addHandler(stdout_handler)
    logger.addHandler(stderr_handler)
    logger.setLevel(logging.INFO)
    return logger


# endregion
# region: Global values
@dataclass
class GlobalConfig:
    """Shared configuration for build-time flags."""

    dry_run: bool = False


global_config = GlobalConfig()
logger = get_logger()
COMMAND = Sequence[str | Path]
MAX_CALL_DEPTH = 1000


# endregion
# region: Build Process
def run(command: COMMAND) -> int:
    """
    Execute a shell command, streaming stdout/stderr into the logger.
    Honors the dry_run flag from global_config.
    """
    cmd = [str(c) for c in command]
    logger.debug(f"+ {' '.join(cmd)}")
    if global_config.dry_run:
        logger.info("(dry-run) Skipping execution")
        return 0
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            errors="replace",
        )
        assert proc.stdout and proc.stderr

        def pump(stream: TextIO, log_fn: Callable[[str], None]):
            for line in stream:
                log_fn(line.rstrip())

        out_t = threading.Thread(target=pump, args=(proc.stdout, logger.debug))
        err_t = threading.Thread(target=pump, args=(proc.stderr, logger.error))
        out_t.start()
        err_t.start()
        out_t.join()
        err_t.join()
        return proc.wait()
    except FileNotFoundError:
        logger.error(f"Command not found: {cmd[0]}")
        return 127
    except KeyboardInterrupt:
        logger.error("Interrupted")
        return -2


@dataclass
class BuildTarget:
    name: str
    help_msg: str
    log_msg: str
    commands: list[COMMAND]
    dependencies: list[BuildTarget] = field(default_factory=list)
    inputs: list[Path] = field(default_factory=list)
    outputs: list[Path] = field(default_factory=list)

    def is_outdated(self) -> bool:
        if not self.outputs:
            return True
        return any(
            not out.exists()
            or any(
                inp.stat().st_mtime > out.stat().st_mtime for inp in self.inputs
            )
            for out in self.outputs
        )

    def execute(self) -> int:
        """Run this target's own commands (deps handled by BuildSystem)."""
        logger.info(self.log_msg)
        status = 0
        for cmd in self.commands:
            status |= run(cmd)
        return status


@dataclass
class CleanupTarget(BuildTarget):
    patterns: list[str] = field(default_factory=list)
    directories: list[Path] = field(default_factory=list)

    def execute(self) -> int:
        logger.info(self.log_msg)
        for pat in self.patterns:
            for f in Path(".").glob(pat):
                logger.info(f"Removing {f}")
                f.unlink(missing_ok=True)
        for d in self.directories:
            if d.exists():
                logger.info(f"Removing directory {d}")
                shutil.rmtree(d)
        return 0


class BuildSystem:
    def __init__(
        self, targets: List[BuildTarget], default: BuildTarget | None = None
    ):
        self.targets = targets
        self.default = default
        if default and default not in self.targets:
            self.targets.append(default)
        self._target_map = {t.name: t for t in self.targets}

    def _build_parser(self) -> argparse.ArgumentParser:
        p = argparse.ArgumentParser()
        p.add_argument(
            "target", help="Target to be run", nargs="?", default=None
        )
        p.add_argument(
            "-d",
            "--dry-run",
            action="store_true",
            help="Show commands without executing",
        )
        p.add_argument(
            "--man",
            action="store_true",
            help="Show documentation for building your own build system",
        )
        g = p.add_mutually_exclusive_group()
        g.add_argument(
            "-v",
            "--verbose",
            action="store_true",
            help="Enable verbose logging",
        )
        g.add_argument(
            "-q", "--quiet", action="store_true", help="Enable quiet logging"
        )
        return p

    def get_args(self) -> str:
        parser = self._build_parser()
        args = parser.parse_args()
        global_config.dry_run = args.dry_run  # type: ignore
        if args.man:
            print(self.get_man())
            sys.exit(0)
        if args.verbose:
            logger.setLevel(logging.DEBUG)
        elif args.quiet:
            logger.setLevel(logging.WARNING)
        if not args.target:
            if self.default is None:
                print(parser.format_help())
                sys.exit(0)
            return self.default.name
        return args.target

    def get_man(self) -> str:
        return """\
build.py — A simple Python-based build system

This module lets you define targets, dependencies, and commands in pure Python
to manage your project's build workflow, similar to Make.

Usage
-----
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
    sys.exit(system())  # Parses CLI args, determines outdated targets, and runs
    them in order

```
Command-line interface

$ python build.py [target]
  target     Name of the target to run (default: 'all')
Options:
  -d, --dry-run    Show commands but do not execute
  -v, --verbose    Enable DEBUG-level logging
  -q, --quiet      Show only WARNING and above
Key features
Incremental builds: only targets whose outputs are missing or older than any inputs will be run.

Automatic dependency topological sort with cycle detection.

ANSI-colored logging with optional verbosity levels.

Easy cleanup via CleanupTarget.
"""

    def get_queue(self, root: BuildTarget) -> List[BuildTarget]:
        """
        Build graph in topological order, but only include outdated targets (prune up-to-date subtrees).
        """
        visited: set[str] = set()
        temp: set[str] = set()
        order: List[BuildTarget] = []

        def visit(node: BuildTarget, depth: int = 0):
            if depth > MAX_CALL_DEPTH:
                raise ValueError(
                    f"Exceeded maximum dependency depth ({MAX_CALL_DEPTH}); possible cycle or too deep graph"
                )
            # skip this whole branch if node is up-to-date
            if not node.is_outdated():
                return
            if node.name in temp:
                raise ValueError(
                    f"Dependency cycle detected involving: {node.name}"
                )
            if node.name in visited:
                return
            temp.add(node.name)
            # traverse each dependency in defined order (pruned)
            for dep in node.dependencies:
                visit(dep, depth + 1)
            temp.remove(node.name)
            visited.add(node.name)
            order.append(node)

        visit(root)
        return order

    def usage_message(self) -> str:
        help_text = self._build_parser().format_help()
        target_lines = [help_text, "available targets:"]
        for name, target in self._target_map.items():
            target_lines.append(f"  {name:<16} {target.help_msg}")
        target_lines.append(f"  {'help':<16} Show this message and exit")
        return "\n".join(target_lines)

    def __call__(self) -> int:
        if len(sys.argv) == 2 and sys.argv[1] in ("help", "-h", "--help"):
            print(self.usage_message())
            return 0
        name = self.get_args()
        target = self._target_map.get(name)
        if not target:
            logger.error(f"Unknown target: {name}")
            print(self._build_parser().format_help())
            return 1
        try:
            queue = self.get_queue(target)
        except ValueError as e:
            logger.error(str(e))
            return 1
        status = 0
        for t in queue:
            status |= t.execute()
        return status


# endregion

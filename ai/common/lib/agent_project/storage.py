from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import tempfile

from . import fail
from .exclude import ensure_local_exclusion, warn_if_tracked
from .schema import DEFAULT_SETTINGS, SCHEMA, validate_structure, validate_values


def find_project_root():
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
    except OSError as error:
        fail(f"cannot run git: {error}")

    if result.returncode == 0:
        return Path(result.stdout.strip())

    fail("current directory is not inside a Git worktree")


def config_path():
    return find_project_root() / ".ai" / "project.json"


def load_settings():
    path = config_path()

    if not path.exists():
        return deepcopy(DEFAULT_SETTINGS)

    try:
        with path.open(encoding="utf-8") as file:
            settings = json.load(file)
    except json.JSONDecodeError as error:
        fail(f"invalid JSON in {path}: {error}")
    except OSError as error:
        fail(f"cannot read {path}: {error}")

    validate_structure(settings, SCHEMA)
    validate_values(settings)
    return settings


def write_settings(settings):
    path = config_path()
    ensure_local_exclusion(path.parent.parent)
    warn_if_tracked(path.parent.parent)

    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".project.json.",
            delete=False,
        ) as file:
            temporary = Path(file.name)
            json.dump(settings, file, indent=2)
            file.write("\n")
        os.replace(temporary, path)
    except OSError as error:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        fail(f"cannot write {path}: {error}")

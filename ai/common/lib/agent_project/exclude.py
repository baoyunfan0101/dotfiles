import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

from . import fail


EXCLUDE_BEGIN = b"# BEGIN dotfiles workflow"

EXCLUDE_RULE = b"/.ai/project.json"

EXCLUDE_END = b"# END dotfiles workflow"

EXCLUDE_MARKER_ATTEMPT = re.compile(
    rb"^\s*#\s*(?:BEGIN|END)\b[\s:]+dotfiles\s+workflow", re.IGNORECASE)


def local_exclude_path(root):
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-path", "info/exclude"],
            cwd=root, capture_output=True, text=True,
        )
    except OSError as error:
        fail(f"cannot locate Git exclude file: {error}")
    if result.returncode != 0 or not result.stdout.strip():
        fail("cannot locate Git exclude file")
    path = Path(result.stdout.strip())
    return path if path.is_absolute() else root / path


def updated_exclude(content):
    lines = content.splitlines(keepends=True)
    markers = []
    for index, line in enumerate(lines):
        value = line.rstrip(b"\r\n")
        if value in (EXCLUDE_BEGIN, EXCLUDE_END):
            markers.append((index, value))
        elif EXCLUDE_MARKER_ATTEMPT.match(value):
            fail("malformed dotfiles workflow block in Git exclude file")

    if markers and (len(markers) != 2 or markers[0][1] != EXCLUDE_BEGIN
                    or markers[1][1] != EXCLUDE_END):
        fail("malformed dotfiles workflow block in Git exclude file")

    if markers:
        begin, end = markers[0][0], markers[1][0]
        newline = b"\r\n" if lines[begin].endswith(b"\r\n") else b"\n"
        body = EXCLUDE_RULE + newline
        if b"".join(lines[begin + 1:end]) == body:
            return content
        return b"".join(lines[:begin + 1]) + body + b"".join(lines[end:])

    newline = b"\r\n" if b"\r\n" in content else b"\n"
    separator = b""
    if content:
        if content.endswith(newline + newline):
            separator = b""
        elif content.endswith(newline):
            separator = newline
        else:
            separator = newline + newline
    block = newline.join((EXCLUDE_BEGIN, EXCLUDE_RULE, EXCLUDE_END)) + newline
    return content + separator + block


def ensure_local_exclusion(root):
    path = local_exclude_path(root)
    temporary = None
    try:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            mode = None
        if mode is not None and not stat.S_ISREG(mode):
            fail(f"Git exclude file is not a regular file: {path}")
        content = path.read_bytes() if mode is not None else b""
        updated = updated_exclude(content)
        if updated == content:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".exclude.",
                                         delete=False) as file:
            temporary = Path(file.name)
            if mode is not None:
                os.fchmod(file.fileno(), stat.S_IMODE(mode))
            file.write(updated)
        os.replace(temporary, path)
    except OSError as error:
        fail(f"cannot update Git exclude file {path}: {error}")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def warn_if_tracked(root):
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--", ".ai/project.json"],
            cwd=root, capture_output=True, text=True,
        )
    except OSError as error:
        fail(f"cannot check Git tracking for .ai/project.json: {error}")
    if result.returncode != 0:
        fail("cannot check Git tracking for .ai/project.json")
    if result.stdout.strip() == ".ai/project.json":
        print("agent-project: warning: .ai/project.json is already tracked; "
              "local exclusion does not remove Git tracking. "
              "Remove it from the index separately if authorized.", file=sys.stderr)

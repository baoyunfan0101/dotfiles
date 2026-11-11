import os
from pathlib import Path
import re
import subprocess
import sys


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def add_date_steps(todo):
    helper = 'exec python3 "$DOTFILES_REBASE_DATES"'
    rewritten = []
    for line in todo.read_bytes().splitlines(keepends=True):
        source = re.match(rb"(?:pick|merge -[Cc]) ([0-9a-f]+)(?:\s|$)", line)
        if source:
            rewritten.append(f"{helper} remember\n".encode())
        rewritten.append(line)
        if source:
            if not line.endswith(b"\n"):
                rewritten.append(b"\n")
            rewritten.append(f"{helper} preserve {source[1].decode()}\n".encode())
    todo.write_bytes(b"".join(rewritten))


def head_record():
    return Path(git("rev-parse", "--git-path", "rebase-merge")) / "dotfiles-previous-head"


def preserve_dates(source):
    # A dropped pick leaves HEAD at the commit before the pick.
    head = git("rev-parse", "HEAD")
    record = head_record()
    if head == record.read_text().strip():
        return
    date_args = ("show", "-s", "--no-show-signature", "--no-notes", "--format=%aI %cI")
    dates = git(*date_args, source)
    if git(*date_args, head) == dates:
        return
    author, committer = dates.split()
    subprocess.run(
        ["git", "-c", f"core.hooksPath={os.devnull}", "commit", "--quiet", "--amend",
         "--no-edit", "--allow-empty", f"--date={author}"],
        env={**os.environ, "GIT_COMMITTER_DATE": committer}, check=True,
    )
    # Git records the pick before running exec. Publish the amended hash to hooks.
    mapping = record.with_name("rewritten-list")
    if mapping.is_file():
        amended = git("rev-parse", "HEAD")
        mapping.write_text(re.sub(rf" {head}$", f" {amended}", mapping.read_text(), flags=re.MULTILINE))


if __name__ == "__main__":
    operation = sys.argv[1]
    if operation == "edit":
        add_date_steps(Path(sys.argv[2]))
    elif operation == "remember":
        head_record().write_text(git("rev-parse", "HEAD"))
    elif operation == "preserve":
        preserve_dates(sys.argv[2])

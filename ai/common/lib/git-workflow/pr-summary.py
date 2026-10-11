#!/usr/bin/env python3
"""Select an open PR and update only its workflow-managed Summary."""

import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit


START = "<!-- git-workflow:summary:start -->"
END = "<!-- git-workflow:summary:end -->"


def fail(message, code=1):
    print(message, file=sys.stderr)
    raise SystemExit(code)


def write_body(value, path):
    if not isinstance(value, str):
        fail("PR description is not text")
    Path(path).write_text(value)


def remote_repository(remote_url):
    match = re.fullmatch(
        r"(?:(?:https://|ssh://git@)([^/:?#@\s]+)(?::\d+)?/|git@([^/:?#@\s]+):)"
        r"([^/:?#@\s]+)/([^/:?#@\s]+?)(?:\.git)?/?",
        remote_url,
        re.IGNORECASE,
    )
    if not match or any(part in (".", "..") for part in match.groups()[2:]):
        fail("cannot verify GitHub remote repository")
    host = match.group(1) or match.group(2)
    return host.casefold(), f"{match.group(3)}/{match.group(4)}".casefold()


def select(prs_path, body_path, remote_url):
    prs = json.loads(Path(prs_path).read_text())
    if not isinstance(prs, list) or any(not isinstance(pr, dict) for pr in prs):
        fail("invalid open PR response")
    expected_host, expected_repo = remote_repository(remote_url)
    matches = []
    for pr in prs:
        repository = pr.get("headRepository")
        identity = repository.get("nameWithOwner") if isinstance(repository, dict) else None
        if not isinstance(identity, str) or not re.fullmatch(r"[^/\s]+/[^/\s]+", identity):
            fail("open PR repository identity is unavailable")
        if identity.casefold() == expected_repo:
            url = pr.get("url")
            parsed_url = urlsplit(url) if isinstance(url, str) else None
            if not parsed_url or parsed_url.scheme != "https" or parsed_url.hostname != expected_host:
                fail("open PR host does not match remote")
            matches.append(pr)
    if not matches:
        raise SystemExit(3)
    if len(matches) != 1:
        fail("ambiguous open PRs for pushed branch")
    pr = matches[0]
    url, base = pr.get("url"), pr.get("baseRefName")
    if not isinstance(url, str) or not url or not isinstance(base, str) or not base:
        fail("open PR has no usable URL or base")
    write_body(pr.get("body"), body_path)
    print(f"{url}\t{base}")


def extract(view_path, body_path):
    view = json.loads(Path(view_path).read_text())
    if not isinstance(view, dict):
        fail("invalid PR description response")
    write_body(view.get("body"), body_path)


def summary(path):
    subjects = Path(path).read_text().rstrip("\n")
    return f"## Summary\n\n{subjects}" if subjects else ""


def render(existing_path, old_path, new_path, output_path):
    existing = Path(existing_path).read_text()
    old = summary(old_path)
    new = summary(new_path)
    if not new:
        fail("PR has no commits relative to its base")
    managed = f"{START}\n{new}\n{END}"
    starts = list(re.finditer(rf"(?m)^{re.escape(START)}$", existing))
    ends = list(re.finditer(rf"(?m)^{re.escape(END)}$", existing))
    if starts or ends:
        if len(starts) != 1 or len(ends) != 1 or starts[0].start() >= ends[0].start():
            fail("PR description has malformed Summary markers")
        updated = existing[:starts[0].start()] + managed + existing[ends[0].end():]
    elif not existing:
        updated = managed
    elif old and existing.startswith(old):
        suffix = existing[len(old):]
        if suffix and not suffix.startswith("\n\n"):
            fail("legacy Summary has manual content inside it; add managed markers before retrying")
        updated = managed + suffix
    elif re.search(r"(?m)^#{1,6} Summary\s*$", existing):
        fail("PR description has an unmanaged Summary; add managed markers before retrying")
    else:
        updated = managed + "\n\n" + existing
    Path(output_path).write_text(updated)


if __name__ == "__main__":
    try:
        action, *args = sys.argv[1:]
        if action == "select" and len(args) == 3:
            select(*args)
        elif action == "extract" and len(args) == 2:
            extract(*args)
        elif action == "render" and len(args) == 4:
            render(*args)
        else:
            fail("invalid pr-summary arguments")
    except (OSError, ValueError, KeyError) as error:
        fail(f"cannot process PR description: {error}")

import sys


def fail(message):
    print(f"agent-project: {message}", file=sys.stderr)
    raise SystemExit(1)

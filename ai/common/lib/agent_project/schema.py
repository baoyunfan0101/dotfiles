import json
from copy import deepcopy

from . import fail


SCHEMA_VERSION = 1

SETTING_SPECS = {
    "schemaVersion": {"type": int, "default": SCHEMA_VERSION,
                      "description": "Project settings schema version.", "configurable": False},
    "workflow.enabled": {"type": bool, "default": False,
                         "description": "Whether this repository uses git-workflow."},
    "git.sync.mode": {"type": str, "default": "update",
                      "values": {"none", "fetch", "update"},
                      "description": "Synchronization performed before workflow operations."},
    "git.sync.updateMethod": {"type": str, "default": "ffOnly",
                              "values": {"ffOnly", "rebase", "merge"},
                              "description": "How an upstream update is applied."},
    "git.backup.mode": {"type": str, "default": "all",
                        "values": {"none", "tracked", "untracked", "all"},
                        "description": "Local changes backed up and discarded before repository preparation."},
    "git.backup.method": {"type": str, "default": "stash",
                          "values": {"stash", "commit"},
                          "description": "How selected local changes are protected."},
    "git.commit.mode": {"type": str, "default": "autoCommitAndPush",
                        "values": {"manual", "autoCommit", "autoCommitAndPush"},
                        "description": "Policy for ordinary workflow commits and post-commit pushes."},
    "git.branch.baseBranches": {"type": list, "default": ["main"],
                                "description": "Allowed bases for new work and delivery.",
                                "constraints": ["unique non-empty strings"]},
    "git.branch.deleteAfterIntegration": {"type": bool, "default": False,
                                          "description": "Whether local and remote working branches are deleted after successful integration."},
    "git.integration.mode": {"type": str, "default": "localMerge",
                             "values": {"localMerge", "pullRequest"},
                             "description": "Selects local integration or pull-request delivery."},
    "git.integration.mergeMethod": {"type": str, "default": "mergeCommit",
                                    "values": {"squash", "mergeCommit"},
                                    "description": "How local integration and explicitly authorized PR merges integrate the working branch."},
}


def nested_specs(field):
    result = {}
    for path, spec in SETTING_SPECS.items():
        current = result
        keys = path.split(".")
        for key in keys[:-1]:
            current = current.setdefault(key, {})
        current[keys[-1]] = deepcopy(spec[field]) if field == "default" else spec[field]
    return result

DEFAULT_SETTINGS = nested_specs("default")

SCHEMA = nested_specs("type")

ALLOWED_VALUES = {path: spec["values"] for path, spec in SETTING_SPECS.items()
                  if "values" in spec}

SCHEMA_TYPE_NAMES = {
    bool: "boolean",
    int: "integer",
    str: "string",
    list: "list[string]",
}

NON_CONFIGURABLE_PATHS = {path for path, spec in SETTING_SPECS.items()
                          if not spec.get("configurable", True)}


def validate_structure(value, schema, path=""):
    if not isinstance(value, dict):
        fail(f"{path or 'root'} must be an object")

    unknown_keys = set(value) - set(schema)

    if unknown_keys:
        key = sorted(unknown_keys)[0]
        full_path = f"{path}.{key}" if path else key
        fail(f"unknown setting: {full_path}")

    for key in schema:
        if key not in value:
            if not path and key == "schemaVersion":
                fail("schemaVersion is required")
            full_path = f"{path}.{key}" if path else key
            fail(f"missing setting: {full_path}")

    for key, item in value.items():
        expected = schema[key]
        current_path = f"{path}.{key}" if path else key

        if isinstance(expected, dict):
            validate_structure(item, expected, current_path)
        elif type(item) is not expected:
            fail(f"{current_path} has an invalid type")


def schema_type(path):
    value = SCHEMA

    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            fail(f"unknown setting: {path}")
        value = value[key]

    if isinstance(value, dict):
        fail(f"setting path must identify a value: {path}")

    return value


def leaf_paths(schema, prefix=""):
    for key, value in schema.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            yield from leaf_paths(value, path)
        else:
            yield path


def configurable_paths():
    return tuple(path for path in leaf_paths(SCHEMA)
                 if path not in NON_CONFIGURABLE_PATHS)


def configurable_type(path):
    if path not in configurable_paths():
        fail(f"unknown setting: {path}")
    return schema_type(path)


def schema_entry(path):
    expected = configurable_type(path)
    entry = {
        "type": SCHEMA_TYPE_NAMES[expected],
        "default": get_value(DEFAULT_SETTINGS, path),
    }

    if path in ALLOWED_VALUES:
        entry["type"] = "enum"
        entry["values"] = sorted(ALLOWED_VALUES[path])

    entry["description"] = SETTING_SPECS[path]["description"]
    if "constraints" in SETTING_SPECS[path]:
        entry["constraints"] = SETTING_SPECS[path]["constraints"]
    return entry


def project_schema(path=None):
    if path is not None:
        return schema_entry(path)
    return {setting_path: schema_entry(setting_path)
            for setting_path in configurable_paths()}


def get_value(settings, path):
    value = settings

    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            fail(f"unknown setting: {path}")
        value = value[key]

    return value


def validate_values(settings):
    if settings["schemaVersion"] != SCHEMA_VERSION:
        fail(f"unsupported schemaVersion: {settings['schemaVersion']}")

    for path, allowed_values in ALLOWED_VALUES.items():
        value = get_value(settings, path)

        if value not in allowed_values:
            allowed = ", ".join(sorted(allowed_values))
            fail(f"{path} must be one of: {allowed}")

    base_branches = settings["git"]["branch"]["baseBranches"]

    if not all(isinstance(branch, str) and branch for branch in base_branches):
        fail("git.branch.baseBranches must contain non-empty strings")

    if len(base_branches) != len(set(base_branches)):
        fail("git.branch.baseBranches must not contain duplicates")


def parse_value(path, raw_value):
    expected = configurable_type(path)

    if expected is str:
        value = raw_value
    elif expected is bool:
        if raw_value == "true":
            value = True
        elif raw_value == "false":
            value = False
        else:
            fail(f"{path} must be true or false")
    elif expected is int:
        try:
            value = int(raw_value)
        except ValueError:
            fail(f"{path} must be an integer")
    else:
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError as error:
            fail(f"{path} must be valid JSON: {error.msg}")
        if not isinstance(value, expected):
            fail(f"{path} has an invalid type")

    return value


def set_setting(settings, path, value):
    keys = path.split(".")
    current = settings

    for key in keys[:-1]:
        current = current.setdefault(key, {})
        if not isinstance(current, dict):
            fail(f"cannot set nested value under {key}")

    current[keys[-1]] = value


def reset_to_default(settings, path):
    default = deepcopy(get_value(DEFAULT_SETTINGS, path))
    changed = get_value(settings, path) != default
    set_setting(settings, path, default)
    return changed

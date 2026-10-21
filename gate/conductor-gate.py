#!/usr/bin/env python3
"""Conductor gate: a PreToolUse hook for Claude Code (part of the ladder plugin).

The main session (the conductor) and each sub-orchestrator may only
dispatch sub-agents, keep a ledger, and run a short list of safe commands.
Workers pass. The hook fails open: a bug here must never lock the user out.

The gate runs in one of two places:
- Plugin mode: the ladder plugin runs this file from its gate/ folder.
  The rules come from gate/config.json, with an optional user file on top
  (~/.claude/conductor-gate/config.json). If a locked copy of the gate is in
  the managed settings, the plugin copy does nothing (no double gate).
- Locked mode: an admin copied this file to the managed folder with
  gate/lock/install.sh. The rules come from config.json in that folder. The
  user file can only add "enforce_projects".

Inputs: stdin, the arguments, and two environment variables: HOME (if it is
not set, the gate asks the system for the home folder) and CLAUDE_CONFIG_DIR
(the .claude folder, if it is not ~/.claude). Test arguments: --root <dir>,
--log <file>, --managed <file>, --user-config <file>, --locked.

Exit 0 = allow (the JSON on stdout can add context, ask, or fill input).
Exit 2 = block, with a message on stderr.
"""
import fnmatch
import json
import os
import re
import sys
import time


def _home():
    h = os.environ.get("HOME")
    if isinstance(h, str) and h.startswith("/") and len(h) > 1:
        return h.rstrip("/")
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_dir.rstrip("/") or "/"
    except Exception:
        return "/nonexistent"


def _claude_dir(home):
    d = os.environ.get("CLAUDE_CONFIG_DIR")
    if isinstance(d, str) and d.startswith("/") and len(d) > 1:
        return d.rstrip("/")
    return home + "/.claude"


HOME = _home()
CLAUDE_DIR = _claude_dir(HOME)
# The folder of this script (the plugin's gate/ folder, or the locked folder).
GATE_DIR = os.path.dirname(os.path.realpath(os.path.abspath(__file__)))
# The plugin root (one level up from gate/). In locked mode it is the managed folder.
PLUGIN_ROOT = os.path.dirname(GATE_DIR)
ROOT = GATE_DIR


def _env_plugin_root():
    """The plugin root as Claude Code names it ($CLAUDE_PLUGIN_ROOT), if it differs.

    A clone behind a symlink has two names: the path as added and the realpath.
    The env value counts only if it is absolute and resolves to PLUGIN_ROOT.
    """
    v = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if not (isinstance(v, str) and v.startswith("/")):
        return None
    v = os.path.normpath(v)
    if v == "/" or v.casefold() == PLUGIN_ROOT.casefold():
        return None
    if os.path.realpath(v).casefold() != PLUGIN_ROOT.casefold():
        return None
    return v


ENV_PLUGIN_ROOT = _env_plugin_root()
# Every name of the plugin root: the realpath first, then the env form.
PLUGIN_ROOTS = (PLUGIN_ROOT,) + ((ENV_PLUGIN_ROOT,) if ENV_PLUGIN_ROOT else ())
USER_DIR = CLAUDE_DIR + "/conductor-gate"
LOG_FILE = USER_DIR + "/log.jsonl"
USER_CONFIG = USER_DIR + "/config.json"
# Managed settings files that can hold a locked copy of the gate (macOS, Linux).
MANAGED_SETTINGS = (
    "/Library/Application Support/ClaudeCode/managed-settings.json",
    "/etc/claude-code/managed-settings.json",
)
# A script under one of these folders is the locked copy.
LOCK_ROOTS = ("/Library/Application Support/ClaudeCode", "/etc/claude-code")
# The agent type of the plugin's sub-orchestrator (named in the deny message).
SUBORCH_TYPE = "ladder:suborchestrator"
SAFE_ARG = r"[^;&|`$<>()\n\r\\*?\[\]{}~!]*"
TAIL_BYTES = 256 * 1024
LOG_LIMIT = 5 * 1024 * 1024
TIER_RANK = {"haiku": 1, "sonnet": 2, "opus": 3, "fable": 4}
KNOWN_MODEL = re.compile(r"^claude-(fable|opus|sonnet|haiku)", re.I)
HEAD_BYTES = 64 * 1024

# Direct turn (/direct). The scan reads the transcript backward in chunks, up to the cap.
DIRECT_CAP = 8 * 1024 * 1024
DIRECT_CHUNK = 256 * 1024
# The command marker that Claude Code writes when the user types /direct.
# User skills put the message tag first. Built-in commands put the name tag first.
# A plugin skill has the plugin name in front: ladder:direct and /ladder:direct.
def _skill_tags(name):
    forms = []
    for n in (name, "ladder:" + name):
        forms.append(r"<command-message>%s</command-message>\s*<command-name>/%s</command-name>" % (n, n))
        forms.append(r"<command-name>/%s</command-name>\s*<command-message>%s</command-message>" % (n, n))
    return "(?:" + "|".join(forms) + ")"


DIRECT_MARKER = re.compile(_skill_tags("direct") + r"(?:\s|<|$)")

# The macOS open command (conductor Bash only).
OPEN_FLAGS = ("-R", "-e", "-t")
OPEN_WORD_CHARS = "._/:@%+,-"
# Round 5b: allow lists. Anything that is not on a list is denied.
# The -a value must be one of these exact app names.
OPEN_APPS = (
    "Preview", "TextEdit", "Finder", "Safari", "Google Chrome", "Firefox", "Microsoft Word",
    "Microsoft Excel", "Microsoft PowerPoint", "Pages", "Numbers", "Keynote",
    "QuickTime Player", "Music", "Photos",
)
# A file target must have one of these suffixes (viewer types only). Compared with casefold.
OPEN_FILE_TYPES = frozenset((
    "pdf", "png", "jpg", "jpeg", "gif", "heic", "heif", "tif", "tiff", "bmp", "webp", "svg",
    "txt", "md", "rtf", "csv", "tsv", "json", "log", "html", "htm", "docx", "doc", "xlsx",
    "xls", "pptx", "ppt", "pages", "numbers", "key", "mov", "mp4", "m4v", "mp3", "m4a",
    "wav", "aiff", "epub",
))
# A folder with one of these suffixes is a bundle, not a plain folder.
OPEN_BUNDLE_SUFFIXES = (
    ".app", ".bundle", ".framework", ".pkg", ".mpkg", ".plugin", ".kext", ".appex",
    ".prefpane", ".saver", ".qlgenerator", ".mdimporter", ".xpc", ".wdgt", ".workflow",
    ".action", ".scptd", ".osax", ".service", ".dext", ".systemextension",
)
# Interactive entrypoints. A /direct turn needs one of these (first 64 KB of the transcript).
DIRECT_ENTRYPOINTS = ("cli", "claude-desktop")
# Round 5b: a Bash or Monitor command that SELECTS a fable model. The word alone
# (a path, a grep pattern, a file name, echo text) does not match.
# 1. A model id: claude-fable.
# 2. A model flag: --model fable, --model="fable", --fallback-model x-fable.
# 3. A setting or an env var: model: fable, "model": "x-fable", ANTHROPIC_MODEL=x-fable.
# 4. The slash command: /model fable.
# Limit: this is a text match. Quotes inside the word or a shell variable hide it.
FABLE_SELECT = re.compile(r"""
    claude-fable
  | -model(?:\s+|=)["']?[^\s"']*fable
  | model["']?\s*[:=]\s*["']?[^\s"',;&|)}]*fable
  | /model\s+["']?[^\s"']*fable
""", re.I | re.X)
# A web link with a path part that ends with one of these is denied.
OPEN_DENY_SUFFIXES = (
    ".command", ".sh", ".zsh", ".bash", ".csh", ".ksh", ".fish", ".app", ".pkg", ".mpkg",
    ".terminal", ".scpt", ".scptd", ".applescript", ".tool", ".workflow", ".wflow",
    ".action", ".py", ".rb", ".pl", ".jar", ".webloc", ".inetloc", ".fileloc",
    ".shortcut", ".prefpane", ".mobileconfig", ".saver", ".osax", ".kext", ".plugin",
    ".bundle", ".appex", ".qlgenerator", ".mdimporter",
)
URL_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*):")

# Round 6: the /conduct switch. The user types /conduct on or /conduct off.
# The bytes that a typed /conduct skill line holds (also /ladder:conduct). The scan
# looks for these first.
CONDUCT_MARK = b"conduct</command-name>"
CONDUCT_RE = re.compile(
    _skill_tags("conduct") + r"\s*<command-args>([^<]*)</command-args>\s*$"
)
# Modes that a config entry or the default may name.
MODES = ("enforce", "warn", "off", "log", "guards")
# A /conduct line is short. The bookmark check reads at most this many bytes.
LINE_CAP = 64 * 1024
STATE_FILE_CAP = 4096
STATE_MAX_AGE = 30 * 24 * 3600
# Round 6: wait loops and read-only commands (conductor and sub-orchestrator).
WORD_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/:@%+,=-^")
SLEEP_ARG = re.compile(r"^\d{1,6}(?:\.\d{1,3})?[smhd]?$")
TEST_FLAGS = ("-e", "-f", "-s", "-d", "-r", "-L")
GREP_WAIT_FLAGS = re.compile(r"^-[qsiFEwxv]+$")
READ_CMDS = ("ls", "cat", "grep")
# grep options that take the next word as a value (the value is not a path).
GREP_VALUE_OPTS = ("-A", "-B", "-C", "-m", "--max-count", "--context", "--after-context",
                   "--before-context", "--include", "--exclude", "--exclude-dir", "--label", "--color", "--colour")

# Run state, set in main().
STATE = {"session_id": None, "cwd": None, "level": None, "mode": None, "model": None, "tool": None,
         "cfg": {}, "transcript_path": None, "model_read": False}


def parse_args(argv):
    """Return a dict: root, log_file, managed (a tuple of paths), user_config, locked."""
    out = {"root": ROOT, "log_file": LOG_FILE, "managed": MANAGED_SETTINGS, "managed_owner": 0,
           "user_config": USER_CONFIG, "locked": under(GATE_DIR, LOCK_ROOTS[0]) or under(GATE_DIR, LOCK_ROOTS[1])}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--locked":
            out["locked"] = True
            i += 1
        elif a in ("--root", "--log", "--managed", "--managed-owner", "--user-config") and i + 1 < len(argv):
            v = argv[i + 1]
            if a == "--managed-owner":
                # Tests only: the uid that must own a managed file (default 0, root).
                out["managed_owner"] = int(v) if v.isdigit() else 0
            elif a == "--root":
                out["root"] = v
            elif a == "--log":
                out["log_file"] = v
            elif a == "--managed":
                out["managed"] = (v,)
            else:
                out["user_config"] = v
            i += 2
        else:
            i += 1
    return out


def managed_gate_present(paths, owner=0):
    """True if a managed settings file has a hook command that runs a locked gate.

    The command must name conductor-gate.py and must not name this file (so the
    locked copy never turns itself off). A missing or bad file counts as no gate.
    The drop-in folder managed-settings.d next to each file is read too.
    The file must be owned by root (owner) and not be writable by group or others.
    A symlink does not count. So a user process cannot fake a locked gate.
    """
    me = os.path.realpath(os.path.abspath(__file__)).casefold()
    files = []
    for p in paths:
        files.append(p)
        d = os.path.join(os.path.dirname(p), "managed-settings.d")
        try:
            files.extend(os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".json"))
        except OSError:
            pass
    for p in files:
        try:
            fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                st = os.fstat(fd)
                if st.st_uid != owner or st.st_mode & 0o022:
                    continue
                data = json.loads(os.read(fd, 1 << 20).decode("utf-8"))
            finally:
                os.close(fd)
        except Exception:
            continue
        hooks = data.get("hooks") if isinstance(data, dict) else None
        entries = hooks.get("PreToolUse") if isinstance(hooks, dict) else None
        if not isinstance(entries, list):
            continue
        for e in entries:
            for h in (e.get("hooks") if isinstance(e, dict) else None) or []:
                cmd = h.get("command") if isinstance(h, dict) else None
                if isinstance(cmd, str) and "conductor-gate.py" in cmd and me not in cmd.casefold():
                    return True
    return False


# Keys of a project entry that hold a list of strings (build_rules adds them to the
# global list of the same name).
PROJECT_LIST_KEYS = frozenset((
    "allow_tools", "conductor_prefixes", "suborch_prefixes", "conductor_deny_prefixes",
    "suborch_deny_prefixes", "deny_tools", "writable_dirs", "deny_write_dirs", "read_dirs",
    "deny_write_suffixes", "deny_write_parts", "conductor_bash", "suborch_bash",
    "note_suffixes", "deny_write_names", "tamper_names", "tamper_text"))
# Keys that hold regular expressions. Each item must compile.
REGEX_KEYS = frozenset(("conductor_bash", "suborch_bash", "tamper_text"))
# Keys that hold a mode name.
MODE_KEYS = frozenset(("default_mode", "headless_mode", "suborchestrator_default_mode", "mode"))


def regex_ok(key, item):
    """True if one item of a regex list compiles the way the gate uses it."""
    try:
        if key == "tamper_text":
            re.compile(item)
        else:
            compile_patterns([item])
        return True
    except Exception:
        return False


def clean_value(key, value, shipped, errors, where):
    """Check one user value against the type of the shipped value.

    Return (True, value) if the value can be used. A list with one bad item is
    dropped as a whole (the shipped value stays). Each problem is added to errors.
    A bad value never reaches the rules, so it cannot crash the gate, and the
    money guards stay on.
    """
    name = where + key
    if type(value) is not type(shipped):
        errors.append("%s: expected %s, got %s" % (name, type(shipped).__name__, type(value).__name__))
        return False, None
    if key in MODE_KEYS and value not in MODES:
        errors.append("%s: unknown mode %r" % (name, value))
        return False, None
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, str):
                errors.append("%s: an item is not a string" % name)
                return False, None
            if key in REGEX_KEYS and not regex_ok(key, item):
                errors.append("%s: bad regex %r" % (name, item[:60]))
                return False, None
    return True, value


def clean_project(path, entry, errors):
    """Check one project entry. Return the good part, or None."""
    if not isinstance(path, str) or not path or not isinstance(entry, dict):
        errors.append("projects: entry %r is not a path with an object" % (str(path)[:60],))
        return None
    out = {}
    for k, v in entry.items():
        if k == "mode":
            good, v = clean_value(k, v, "", errors, "projects[%s]." % path[:60])
        elif k == "label":
            good, v = clean_value(k, v, "", errors, "projects[%s]." % path[:60])
        elif k in PROJECT_LIST_KEYS:
            good, v = clean_value(k, v, [], errors, "projects[%s]." % path[:60])
        else:
            errors.append("projects[%s]: unknown key %r" % (path[:60], k))
            good = False
        if good:
            out[k] = v
    return out


def load_config(root, user_config, locked):
    """The gate config: root/config.json, then the user file on top.

    Plugin mode: the user file may set any key that the shipped file has. "projects"
    entries merge. Each path in "enforce_projects" becomes a project with mode enforce.
    Locked mode: only "enforce_projects" counts (it can only make a window stricter).
    A missing user file is fine. A bad user file, a bad key, a wrong type or a bad
    regex is skipped, and one USER-CONFIG-ERR row names every problem.
    The shipped config is kept in STATE["shipped_cfg"] as a fallback.
    """
    with open(os.path.join(root, "config.json")) as fh:
        cfg = json.load(fh)
    if not isinstance(cfg, dict):
        raise ValueError("config is not an object")
    STATE["shipped_cfg"] = json.loads(json.dumps(cfg))
    user = None
    try:
        fd = os.open(user_config, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            user = json.loads(os.read(fd, 1 << 20).decode("utf-8"))
        finally:
            os.close(fd)
    except FileNotFoundError:
        user = None
    except Exception as exc:
        STATE["user_config_error"] = "user config error: %r" % (exc,)
        user = None
    if user is not None and not isinstance(user, dict):
        STATE["user_config_error"] = "user config is not an object"
        user = None
    if not user:
        return cfg
    errors = []
    projects = dict(cfg.get("projects") or {})
    for k, v in user.items():
        if k in ("projects", "enforce_projects"):
            continue
        if locked:
            continue
        if k not in cfg:
            errors.append("unknown key %r" % (k,))
            continue
        good, v = clean_value(k, v, cfg[k], errors, "")
        if good:
            cfg[k] = v
    if "projects" in user and not locked:
        up = user["projects"]
        if not isinstance(up, dict):
            errors.append("projects: expected dict, got %s" % type(up).__name__)
        else:
            for k, v in up.items():
                entry = clean_project(k, v, errors)
                if entry is not None:
                    projects[k] = entry
    ep = user.get("enforce_projects")
    if ep is not None and not isinstance(ep, list):
        errors.append("enforce_projects: expected list, got %s" % type(ep).__name__)
        ep = None
    for p in ep or []:
        if isinstance(p, str) and p:
            entry = dict(projects.get(p) or {})
            entry["mode"] = "enforce"
            entry.setdefault("label", os.path.basename(p.rstrip("/")) or p)
            projects[p] = entry
        else:
            errors.append("enforce_projects: an item is not a path")
    cfg["projects"] = projects
    if errors:
        STATE["user_config_error"] = "; ".join(errors)[:1000]
    return cfg


def log(decision, detail=""):
    try:
        path = STATE["log_file"]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            if os.path.getsize(path) > LOG_LIMIT:
                os.replace(path, path + ".1")
        except OSError:
            pass
        row = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "session_id": STATE["session_id"],
            "cwd": STATE["cwd"],
            "level": STATE["level"],
            "mode": STATE["mode"],
            "model": get_model(),
            "tool": STATE["tool"],
            "decision": decision,
            "detail": str(detail)[:200],
        }
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, (json.dumps(row) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:
        pass


def get_model():
    """The session tier, read once and only when it is needed (Round 6).

    The model no longer picks the mode. It is needed for the fork rule and the log.
    """
    if not STATE.get("model_read"):
        STATE["model_read"] = True
        cfg = STATE.get("cfg") or {}
        if STATE.get("level") == "conductor" and cfg.get("model_signal", "transcript") != "ignore":
            STATE["model"] = read_model(STATE.get("transcript_path"))
    return STATE.get("model")


def under(path, root):
    path, root = path.casefold(), root.casefold()
    return path == root or path.startswith(root.rstrip("/") + "/")


def glob_under(path, pattern):
    """True if the path is the pattern folder or under it.

    In the pattern, "*" matches one folder name and "**" matches zero or more folders.
    The compare uses casefold (APFS here is case-insensitive).
    """
    if not (isinstance(path, str) and isinstance(pattern, str) and path and pattern):
        return False
    ps = [s for s in path.casefold().split("/") if s]
    pat = [s for s in os.path.normpath(expand(pattern)).casefold().split("/") if s]

    def match(i, j):
        if i == len(pat):
            return True
        if pat[i] == "**":
            return any(match(i + 1, k) for k in range(j, len(ps) + 1))
        if j >= len(ps):
            return False
        return fnmatch.fnmatchcase(ps[j], pat[i]) and match(i + 1, j + 1)

    return match(0, 0)


def expand(path, cwd=None):
    """Expand ~ (the home folder), ~/.claude (the Claude folder), {cwd} and {PLUGIN_ROOT}."""
    if cwd and "{cwd}" in path:
        path = path.replace("{cwd}", cwd)
    if "{PLUGIN_ROOT}" in path:
        path = path.replace("{PLUGIN_ROOT}", PLUGIN_ROOT)
    if path == "~/.claude" or path.startswith("~/.claude/"):
        return CLAUDE_DIR + path[len("~/.claude"):]
    if path == "~" or path.startswith("~/"):
        return HOME + path[1:]
    return path


def norm(path, base=None):
    p = expand(path)
    if not os.path.isabs(p):
        if not base:
            return None
        p = os.path.join(base, p)
    return os.path.realpath(os.path.normpath(p))


def slug_of(path):
    return path.replace("/", "-").replace(".", "-")


def compile_patterns(items):
    out = []
    for item in items:
        text = (item.replace("{SAFE_ARG}", SAFE_ARG).replace("{HOME}", re.escape(HOME))
                .replace("{CLAUDE_DIR}", re.escape(CLAUDE_DIR))
                .replace("{PLUGIN_ROOT}", "(?:%s)" % "|".join(re.escape(r) for r in PLUGIN_ROOTS)))
        out.append(re.compile(text))
    return out


def tier_of(model):
    low = model.lower()
    if low in TIER_RANK:
        return low
    m = KNOWN_MODEL.match(low)
    return m.group(1).lower() if m else None


def read_chunks(transcript_path):
    """Return the first 256 KB and the last 256 KB of the file as text."""
    with open(transcript_path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(0)
        head = fh.read(TAIL_BYTES)
        if size > TAIL_BYTES:
            fh.seek(max(TAIL_BYTES, size - TAIL_BYTES))
            tail = fh.read()
        else:
            tail = b""
    return head.decode("utf-8", "replace"), tail.decode("utf-8", "replace")


def read_model(transcript_path):
    """Return the highest known tier among model lines in the first and last 256 KB, or None.

    Values that are not a known model (for example "<synthetic>") are skipped.
    A worker that appends lines cannot remove the early lines.
    """
    try:
        if not isinstance(transcript_path, str) or not transcript_path:
            return None
        best = None
        for chunk in read_chunks(transcript_path):
            for line in chunk.splitlines():
                if '"assistant"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not (isinstance(row, dict) and row.get("type") == "assistant"):
                    continue
                msg = row.get("message")
                if not (isinstance(msg, dict) and isinstance(msg.get("model"), str)):
                    continue
                tier = tier_of(msg["model"])
                if tier is not None and (best is None or TIER_RANK[tier] > TIER_RANK[best]):
                    best = tier
        return best
    except Exception:
        return None


def first_entrypoint(transcript_path):
    """The value of the FIRST "entrypoint" key in the first 64 KB, or None."""
    try:
        if not isinstance(transcript_path, str) or not transcript_path:
            return None
        with open(transcript_path, "rb") as fh:
            head = fh.read(HEAD_BYTES).decode("utf-8", "replace")
        for line in head.splitlines():
            if '"entrypoint"' not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and "entrypoint" in row:
                return row["entrypoint"]
        return None
    except Exception:
        return None


def is_headless(transcript_path):
    """True if the FIRST line with an "entrypoint" key (first 64 KB only) says sdk-cli."""
    return first_entrypoint(transcript_path) == "sdk-cli"


def lines_backward(path):
    """Yield the lines of the file from the last to the first, as bytes.

    The read stops with an error after DIRECT_CAP bytes.
    A line that crosses a chunk border is joined before it is given.
    """
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        carry = b""
        done = 0
        while pos > 0:
            if done >= DIRECT_CAP:
                raise ValueError("direct scan cap")
            n = min(DIRECT_CHUNK, pos)
            pos -= n
            fh.seek(pos)
            parts = (fh.read(n) + carry).split(b"\n")
            done += n
            carry = parts[0]
            for line in reversed(parts[1:]):
                yield line
        yield carry


def is_direct_marker(content):
    """True if the prompt text starts with the /direct command marker."""
    return isinstance(content, str) and DIRECT_MARKER.match(content) is not None


def is_direct_turn(transcript_path):
    """True if the current turn started with a genuine /direct prompt from the user.

    Read the transcript backward. Skip lines that are not user lines, tool results,
    and the skill text that Claude Code adds after the prompt. The first other user
    line decides. It must be a human prompt with the /direct marker at position 0.
    Any other line (a new prompt, a task notification, a hand-back, a scheduled
    prompt, a compaction summary, a sidechain line) ends the direct turn.
    A message that arrives during the turn (attachment queued_command) also ends it.
    Any error, or no decision in the first DIRECT_CAP bytes: False (fail closed).
    """
    try:
        if not isinstance(transcript_path, str) or not transcript_path:
            return False
        children = []
        for raw in lines_backward(transcript_path):
            if not raw.strip():
                continue
            if b'"user"' not in raw and b"queued_command" not in raw:
                continue
            row = json.loads(raw.decode("utf-8"))
            if not isinstance(row, dict):
                return False
            kind = row.get("type")
            if kind == "attachment":
                att = row.get("attachment")
                if isinstance(att, dict) and att.get("type") == "queued_command":
                    return False
                continue
            msg = row.get("message")
            role = msg.get("role") if isinstance(msg, dict) else None
            if kind != "user" and role != "user":
                continue
            if row.get("isSidechain") or row.get("isCompactSummary") or row.get("isVisibleInTranscriptOnly"):
                return False
            content = msg.get("content") if isinstance(msg, dict) else None
            if (isinstance(content, list) and content
                    and all(isinstance(x, dict) and x.get("type") == "tool_result" for x in content)):
                # A note line that Claude Code adds to this tool result (for example
                # "[Image: ...]" after a Read of an image) is tool output too.
                children = [p for p in children if p != row.get("uuid")]
                continue
            if row.get("isMeta") is True and not any(k in row for k in ("origin", "turnOrigin", "promptSource")):
                source = row.get("sourceToolUseID")
                if isinstance(source, str) and source:
                    # The text of a skill that the model loaded with the Skill tool.
                    continue
                # Text that Claude Code adds to a prompt (for example the skill text).
                # It counts only if its parent is the deciding prompt.
                children.append(row.get("parentUuid"))
                continue
            origin = row.get("origin")
            uuid = row.get("uuid")
            return (
                kind == "user"
                and isinstance(origin, dict) and origin.get("kind") == "human"
                and row.get("isMeta") is not True
                and row.get("turnOrigin") in (None, "human")
                and row.get("entrypoint") in DIRECT_ENTRYPOINTS
                and isinstance(uuid, str) and bool(uuid)
                and is_direct_marker(content)
                and all(p == uuid for p in children)
            )
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Round 6: the /conduct switch and the session bookmark.
# ---------------------------------------------------------------------------

def genuine_conduct(raw):
    """Return (uuid, "on" or "off") if the line is a genuine /conduct line that the user typed.

    The checks are the same as for a /direct prompt: a user line, origin human, not
    meta, turnOrigin human or absent, an interactive entrypoint, a uuid, not a sidechain
    line, not a compaction summary, and the typed-skill tag form at position 0 with the
    argument "on" or "off". Else None.
    """
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        row = json.loads(raw)
    except Exception:
        return None
    if not isinstance(row, dict) or row.get("type") != "user":
        return None
    origin = row.get("origin")
    uuid = row.get("uuid")
    msg = row.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if not (isinstance(origin, dict) and origin.get("kind") == "human"):
        return None
    if (row.get("isMeta") is True or row.get("isSidechain") or row.get("isCompactSummary")
            or row.get("isVisibleInTranscriptOnly")):
        return None
    if row.get("turnOrigin") not in (None, "human"):
        return None
    if row.get("entrypoint") not in DIRECT_ENTRYPOINTS:
        return None
    if not (isinstance(uuid, str) and uuid):
        return None
    if not isinstance(content, str):
        return None
    m = CONDUCT_RE.match(content)
    if not m:
        return None
    value = m.group(1).strip().lower()
    if value not in ("on", "off"):
        return None
    return uuid, value


def scan_conduct(fh, size, stop, cap):
    """Read the file backward from size down to stop. Return the last genuine /conduct line.

    The result is ((offset, uuid, value), capped). stop must be the start of a line.
    The scan reads at most cap bytes. Only chunks that hold the /conduct tag bytes are
    split into lines and parsed, so a scan with no /conduct line is fast.
    """
    pos, carry, done = size, b"", 0
    while pos > stop:
        if done >= cap:
            return None, True
        n = min(DIRECT_CHUNK, pos - stop)
        pos -= n
        fh.seek(pos)
        buf = fh.read(n) + carry
        done += n
        if pos == stop:
            region, region_off, carry = buf, pos, b""
        else:
            nl = buf.find(b"\n")
            if nl == -1:
                carry = buf
                continue
            region, region_off, carry = buf[nl + 1:], pos + nl + 1, buf[:nl]
        if CONDUCT_MARK not in region:
            continue
        parts = region.split(b"\n")
        offs, o = [], region_off
        for p in parts:
            offs.append(o)
            o += len(p) + 1
        for p, o in zip(reversed(parts), reversed(offs)):
            if CONDUCT_MARK in p:
                g = genuine_conduct(p)
                if g:
                    return (o, g[0], g[1]), False
    return None, False


def line_at(fh, offset, size):
    """Return (line bytes, end offset) for the line that starts at offset, or None.

    The offset must be the start of a line: 0, or the byte before it is a newline.
    """
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0 or offset >= size:
        return None
    if offset > 0:
        fh.seek(offset - 1)
        if fh.read(1) != b"\n":
            return None
    fh.seek(offset)
    data = fh.read(LINE_CAP)
    nl = data.find(b"\n")
    if nl == -1:
        if offset + len(data) < size:
            return None
        return data, offset + len(data)
    return data[:nl], offset + nl + 1


def conduct_switch(transcript_path, state):
    """The window switch from the last genuine /conduct line: "on", "off" or None.

    state is the session state file (a dict). The function can change its
    bookmark keys. A bookmark counts only if the line at its offset is a genuine
    /conduct line with the same uuid. The value always comes from the line, never
    from the file. A newer genuine line after the bookmark wins. If the bookmark
    fails a check, the gate scans back up to the cap (8 MB) from the end.
    """
    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    try:
        with open(transcript_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            mark = None
            if state.get("transcript_path") == transcript_path:
                got = line_at(fh, state.get("offset"), size)
                if got:
                    g = genuine_conduct(got[0])
                    if g and g[0] == state.get("uuid"):
                        mark = (state["offset"], g[0], g[1], got[1])
            if mark:
                found, _capped = scan_conduct(fh, size, mark[3], DIRECT_CAP)
                # If the scan meets the cap before the bookmark, the bookmark still
                # counts: the gate moves it at the first call after each new line.
                best = found or mark[:3]
            else:
                found, _capped = scan_conduct(fh, size, 0, DIRECT_CAP)
                best = found
        if best:
            if (state.get("transcript_path"), state.get("offset"), state.get("uuid"), state.get("value")) != (
                    transcript_path, best[0], best[1], best[2]):
                state.update(transcript_path=transcript_path, offset=best[0], uuid=best[1], value=best[2])
                state["_dirty"] = True
            return best[2]
        if any(k in state for k in ("offset", "uuid", "value")):
            for k in ("offset", "uuid", "value"):
                state.pop(k, None)
            state["transcript_path"] = transcript_path
            state["_dirty"] = True
        return None
    except Exception:
        return None


def state_dir():
    """Return (folder, None) for the session state folder (next to the log), or (None, reason).

    The folder must be a real folder (not a symlink), owned by this user, and not
    writable by the group or others. The gate makes it with mode 700.
    The reason is a short text for the STATE-ERR log row (Round 6 fix F5).
    """
    import stat
    try:
        base = os.path.dirname(STATE["log_file"])
        d = os.path.join(base, "sessions")
        try:
            st = os.lstat(d)
        except FileNotFoundError:
            os.makedirs(base, exist_ok=True)
            os.mkdir(d, 0o700)
            st = os.lstat(d)
        if stat.S_ISLNK(st.st_mode):
            return None, "sessions folder is a symlink"
        if not stat.S_ISDIR(st.st_mode):
            return None, "sessions is not a folder"
        if st.st_uid != os.getuid():
            return None, "sessions folder has another owner"
        if st.st_mode & 0o022:
            return None, "sessions folder is writable by group or others"
        return d, None
    except Exception as exc:
        return None, "sessions folder error: %r" % (exc,)


def log_tail_rows(session_id, limit=64 * 1024):
    """Rows of this session in the last 64 KB of the log, newest first. Any error gives []."""
    out = []
    try:
        key = '"session_id": %s' % json.dumps(session_id)
        with open(STATE["log_file"], "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - limit))
            data = fh.read().decode("utf-8", "replace")
        for line in reversed(data.splitlines()):
            if key not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    except Exception:
        pass
    return out


def state_fallback(session_id, mode, source, reason):
    """The state file cannot be used. Log it once, and log MODE only when the mode changes.

    The gate compares with the rows of this session in the log tail, so it does not
    write a STATE-ERR row or a MODE row on every call (Round 6 fix F5).
    """
    rows = log_tail_rows(session_id)
    if not any(r.get("decision") == "STATE-ERR" for r in rows):
        log("STATE-ERR", reason)
    last = next((r for r in rows if r.get("decision") == "MODE"), None)
    if last is None or last.get("mode") != mode:
        log("MODE", "mode set by: " + source)


def state_name(session_id):
    """A safe file name for the session id, or None."""
    if not isinstance(session_id, str):
        return None
    name = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)[:128]
    return name + ".json" if name.strip("_") else None


def load_state(d, name):
    """Read the session state file. Any problem gives an empty dict."""
    import stat
    try:
        fd = os.open(os.path.join(d, name), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return {}
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_size > STATE_FILE_CAP:
            return {}
        data = json.loads(os.read(fd, STATE_FILE_CAP).decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
    finally:
        os.close(fd)


def save_state(d, name, state):
    """Write the session state file atomically (a temp file, then a rename). Never fails a call.

    Return None if the write worked, else a short reason (Round 6 fix F5).
    """
    keep = dict((k, v) for k, v in state.items() if not k.startswith("_"))
    keep["v"] = 1
    final = os.path.join(d, name)
    tmp = os.path.join(d, ".%s.%d.tmp" % (name, os.getpid()))
    try:
        new = not os.path.lexists(final)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, json.dumps(keep).encode("utf-8"))
        finally:
            os.close(fd)
        os.replace(tmp, final)
        if new:
            # A new session: remove state files that are older than 30 days.
            now = time.time()
            for f in os.listdir(d):
                p = os.path.join(d, f)
                try:
                    if f.endswith(".json") and now - os.lstat(p).st_mtime > STATE_MAX_AGE:
                        os.unlink(p)
                except OSError:
                    pass
        return None
    except Exception as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return "state file save error: %r" % (exc,)


# ---------------------------------------------------------------------------
# Round 6: wait loops and read-only commands.
# ---------------------------------------------------------------------------

def tokenize(cmd):
    """Split a command into words and the operators ; && || and 2>/dev/null.

    Return a list of ("word", text, quoted) and ("op", text) items, or None if the
    command uses any other shell feature: $, backtick, backslash outside single quotes,
    a pipe, &, a redirect, a subshell, a glob or brace character outside quotes.
    A newline counts as ";".
    """
    toks, i, n = [], 0, len(cmd)
    while i < n:
        ch = cmd[i]
        if ch in " \t":
            i += 1
            continue
        if ch in ";\n":
            toks.append(("op", ";"))
            i += 1
            continue
        if cmd.startswith("&&", i) or cmd.startswith("||", i):
            toks.append(("op", cmd[i:i + 2]))
            i += 2
            continue
        if cmd.startswith("2>/dev/null", i) and (i + 11 == n or cmd[i + 11] in " \t;\n"):
            toks.append(("op", "2>"))
            i += 11
            continue
        cur, quoted = [], False
        while i < n and cmd[i] not in " \t;\n":
            ch = cmd[i]
            if ch == "'":
                j = cmd.find("'", i + 1)
                if j == -1:
                    return None
                seg = cmd[i + 1:j]
                if any(ord(c) < 32 or ord(c) == 127 for c in seg):
                    return None
                cur.append(seg)
                quoted, i = True, j + 1
                continue
            if ch == '"':
                j = cmd.find('"', i + 1)
                if j == -1:
                    return None
                seg = cmd[i + 1:j]
                if any(c in "$`\\!" or ord(c) < 32 or ord(c) == 127 for c in seg):
                    return None
                cur.append(seg)
                quoted, i = True, j + 1
                continue
            if cmd.startswith("&&", i) or cmd.startswith("||", i):
                break
            nxt_end = i + 1 == n or cmd[i + 1] in " \t;\n"
            if ch in WORD_CHARS:
                cur.append(ch)
            elif ch == "~" and not cur and not quoted and (nxt_end or cmd[i + 1] == "/"):
                cur.append(ch)
            elif ch in "[]!" and not cur and not quoted and nxt_end:
                cur.append(ch)
            else:
                return None
            i += 1
        toks.append(("word", "".join(cur), quoted))
    return toks


def _kw(toks, i, value):
    """True if item i is the unquoted word value."""
    return i < len(toks) and toks[i][0] == "word" and not toks[i][2] and toks[i][1] == value


def _word(toks, i):
    return i < len(toks) and toks[i][0] == "word"


def wait_command_ok(cmd):
    """True if the command only waits: sleep, date, echo text, and until loops.

    An until loop may only test files ([ -f x ], test -s x, grep -q pattern file) and
    sleep. The command must hold at least one sleep or until. Statements may be joined
    with ";" or "&&". Anything else (a pipe, a redirect, $, a loop body that does other
    work, any other command) gives False.
    """
    toks = tokenize(cmd)
    if not toks:
        return False
    n = len(toks)

    def sleep_at(i):
        if _kw(toks, i, "sleep") and _word(toks, i + 1) and SLEEP_ARG.match(toks[i + 1][1]):
            return i + 2
        return -1

    def cond_at(i):
        if _kw(toks, i, "!"):
            i += 1
        if _kw(toks, i, "[") or _kw(toks, i, "test"):
            bracket = _kw(toks, i, "[")
            j = i + 1
            if _kw(toks, j, "!"):
                j += 1
            if not (_word(toks, j) and not toks[j][2] and toks[j][1] in TEST_FLAGS and _word(toks, j + 1)):
                return -1
            j += 2
            if bracket:
                return j + 1 if _kw(toks, j, "]") else -1
            return j
        if _kw(toks, i, "grep"):
            j, has_q = i + 1, False
            while _word(toks, j) and not toks[j][2] and toks[j][1].startswith("-"):
                if not GREP_WAIT_FLAGS.match(toks[j][1]):
                    return -1
                has_q = has_q or "q" in toks[j][1]
                j += 1
            if not has_q or not _word(toks, j):
                return -1
            j += 1
            files = 0
            while _word(toks, j):
                files += 1
                j += 1
            if not files:
                return -1
            if j < n and toks[j] == ("op", "2>"):
                j += 1
            return j
        return -1

    def until_at(i):
        i = cond_at(i + 1)
        while i >= 0 and i < n and toks[i] in (("op", "&&"), ("op", "||")):
            i = cond_at(i + 1)
        if i < 0 or not (i < n and toks[i] == ("op", ";")):
            return -1
        i += 1
        while i < n and toks[i] == ("op", ";"):
            i += 1
        if not _kw(toks, i, "do"):
            return -1
        i += 1
        while i < n and toks[i] == ("op", ";"):
            i += 1
        i = sleep_at(i)
        while i >= 0 and i < n and toks[i] == ("op", ";") and _kw(toks, i + 1, "sleep"):
            i = sleep_at(i + 1)
        if i < 0 or not (i < n and toks[i] == ("op", ";")):
            return -1
        while i < n and toks[i] == ("op", ";"):
            i += 1
        return i + 1 if _kw(toks, i, "done") else -1

    def stmt_at(i):
        if _kw(toks, i, "sleep"):
            j = sleep_at(i)
            return j, j >= 0
        if _kw(toks, i, "until"):
            j = until_at(i)
            return j, j >= 0
        if _kw(toks, i, "date"):
            j = i + 1
            while _word(toks, j) and j - i <= 2:
                v = toks[j][1]
                if not (v == "-u" or v.startswith("+")):
                    return -1, False
                j += 1
            return j, False
        if _kw(toks, i, "echo"):
            j = i + 1
            while _word(toks, j):
                j += 1
            return j, False
        return -1, False

    i, waits = 0, 0
    while i < n and toks[i] == ("op", ";"):
        i += 1
    if i >= n:
        return False
    while True:
        j, is_wait = stmt_at(i)
        if j < 0:
            return False
        waits += 1 if is_wait else 0
        i = j
        if i >= n:
            break
        if toks[i] not in (("op", ";"), ("op", "&&")):
            return False
        i += 1
        while i < n and toks[i] == ("op", ";"):
            i += 1
        if i >= n:
            break
    return waits > 0


def read_command_ok(cmd, cwd, dirs):
    """True if cmd is one plain ls, cat or grep that reads only inside the given folders.

    No operators, no chains, no shell features. At least one path is needed (with no
    path, ls and grep -r use the cwd and cat reads stdin). grep options that read
    another file (-f, --file, *-from) or follow symlinks (-R) are denied. Each path is
    resolved (realpath) before the folder check.
    """
    toks = tokenize(cmd)
    if not toks or any(t[0] != "word" for t in toks):
        return False
    if toks[0][2] or toks[0][1] not in READ_CMDS:
        return False
    prog, args = toks[0][1], [t[1] for t in toks[1:]]
    paths, need_pattern, end_opts, i = [], prog == "grep", False, 0
    while i < len(args):
        w = args[i]
        if not end_opts and w == "--":
            end_opts = True
            i += 1
            continue
        if not end_opts and w.startswith("-") and w != "-":
            if prog == "grep":
                if w in ("-e", "--regexp"):
                    if i + 1 >= len(args):
                        return False
                    need_pattern = False
                    i += 2
                    continue
                if w.startswith("--regexp="):
                    need_pattern = False
                    i += 1
                    continue
                if w in GREP_VALUE_OPTS:
                    i += 2
                    continue
                if w.startswith("--"):
                    name = w.split("=", 1)[0]
                    if "file" in name or "from" in name or "dereference" in name:
                        return False
                elif any(c in w[1:] for c in "efRdD"):
                    return False
            i += 1
            continue
        if w == "-":
            return False
        if need_pattern:
            need_pattern = False
            i += 1
            continue
        paths.append(w)
        i += 1
    if not paths:
        return False
    for raw in paths:
        p = norm(raw, cwd)
        if not (p and any(d and glob_under(p, d) for d in dirs)):
            return False
    return True


# Round 6 fix F1: a Bash or Monitor command that names a tamper path must only read.
# These command words only read (or print). Anything else counts as a write.
TAMPER_READ_CMDS = frozenset((
    "cat", "head", "tail", "grep", "egrep", "fgrep", "rg", "ls", "wc", "stat",
    "shasum", "sha256sum", "sha1sum", "md5", "md5sum", "cksum", "diff", "cmp", "cut", "tr", "jq",
    "column", "nl", "od", "hexdump", "strings", "echo", "printf", "date", "sleep", "test", "[",
    "[[", "true", "false", "cd", "pwd", "basename", "dirname", "realpath", "readlink", "which",
    "du", "find", "sort", "uniq",
))
# A script runner is allowed only as "runner path/to/script [args]" (no -c, no -e, no stdin).
TAMPER_RUNNERS = frozenset(("python", "python3", "bash", "sh", "zsh", "node"))
# Shell words that come before a command word in a loop or a test.
SHELL_PREFIX = frozenset(("until", "while", "do", "done", "if", "then", "else", "elif", "fi", "!", "{", "}", "time"))
FIND_WRITE_OPTS = frozenset(("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"))
SHELL_PUNCT = "();<>|&\n"
ASSIGN_WORD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Round 6 fix R2-3: only these NAME=value words may come before a command word.
# Others (LESSOPEN, BASH_ENV, PYTHONPATH, NODE_OPTIONS, DYLD_*) can make a read run a program.
SAFE_ASSIGN = frozenset(("GATE_PY", "GATE_CONFIG", "LC_ALL", "LANG", "TZ", "COLUMNS"))
# Round 6 fix R2-3: options that make a read command run a program or write a file.
RUN_OPTS = {
    "rg": ("--pre", "--pre-glob", "--hostname-bin"),
    "sort": ("--compress-program", "-T", "--temporary-directory", "-o", "--output"),
}


def runs_program(word, args):
    """True if an option of a read command runs a program or writes a file."""
    for a in args:
        for opt in RUN_OPTS.get(word, ()):
            if a == opt or a.startswith(opt + "=") or (not opt.startswith("--") and a.startswith(opt)):
                return True
    return False


def norm_text(text):
    """Path text for the tamper match: "//" -> "/", "/./" -> "/", "x/../" resolved (Round 6 fix R2-2)."""
    t = re.sub(r"/{2,}", "/", text)
    prev = None
    while prev != t:
        prev = t
        t = re.sub(r"/\.(?=/|$|[\s'\"])", "", t)
        t = re.sub(r"/(?!\.\.(?:/|$|[\s'\"]))[^/\s'\"]+/\.\.(?=/|$|[\s'\"])", "", t)
    return t


def command_may_write(cmd):
    """True unless every command in cmd is on the read list (Round 6 fix F1).

    This is a text check. It catches a slip, not a determined trick. A redirect to a
    file, a command substitution, an unknown command word, find -delete or -exec,
    sort -o, uniq with an output file, and a script runner with -c count as a write.
    """
    import shlex
    try:
        lx = shlex.shlex(cmd, posix=True, punctuation_chars=SHELL_PUNCT)
        lx.whitespace = " \t\r"
        lx.whitespace_split = True
        toks = list(lx)
    except ValueError:
        return True
    segs, cur, i = [], [], 0
    while i < len(toks):
        t = toks[i]
        if t and all(ch in SHELL_PUNCT for ch in t):
            if ">" in t:
                nxt = toks[i + 1] if i + 1 < len(toks) else ""
                if not (nxt == "/dev/null" or (t.endswith("&") and (nxt.isdigit() or nxt == "-"))):
                    return True
                i += 2
                continue
            if any(ch in t for ch in ";|&()\n"):
                segs.append(cur)
                cur = []
            # A lone "<" or "<<" reads input. The next word is a file or a tag.
            i += 1
            continue
        if "`" in t or "$(" in t:
            return True
        cur.append(t)
        i += 1
    segs.append(cur)
    for seg in segs:
        j = 0
        while j < len(seg) and (seg[j] in SHELL_PREFIX or ASSIGN_WORD.match(seg[j])):
            if ASSIGN_WORD.match(seg[j]) and seg[j].split("=", 1)[0] not in SAFE_ASSIGN:
                return True
            j += 1
        if j >= len(seg) or seg[j] == "for":
            # "for x in a b" names no command. The loop body is its own segment.
            continue
        word, args = os.path.basename(seg[j]), seg[j + 1:]
        if word in TAMPER_RUNNERS:
            if not args or args[0].startswith("-"):
                return True
            continue
        if word not in TAMPER_READ_CMDS:
            return True
        if word == "find" and any(a in FIND_WRITE_OPTS for a in args):
            return True
        if runs_program(word, args):
            return True
        if word == "uniq" and len([a for a in args if not a.startswith("-")]) > 1:
            return True
    return False


def split_open_words(cmd):
    """Split a command into words. Return None if it uses any shell feature.

    Outside quotes, only letters, digits and ._/:@%+,- are permitted.
    The characters $ ` \\ ! and control characters are not permitted anywhere.
    """
    words, cur, quote, have = [], [], None, False
    for ch in cmd:
        if ch in "$`\\!" or (ord(ch) < 32 and ch != "\t") or ord(ch) == 127:
            return None
        if quote:
            if ch == quote:
                quote = None
            else:
                cur.append(ch)
            continue
        if ch in "'\"":
            quote, have = ch, True
            continue
        if ch in " \t":
            if have:
                words.append("".join(cur))
                cur, have = [], False
            continue
        if not (ch.isalnum() or ch in OPEN_WORD_CHARS):
            return None
        cur.append(ch)
        have = True
    if quote:
        return None
    if have:
        words.append("".join(cur))
    return words


def bad_open_path(path):
    """True if a part of the path ends with a denied suffix (for example x.app/)."""
    for part in path.rstrip("/").split("/"):
        if part.casefold().endswith(OPEN_DENY_SUFFIXES):
            return True
    return False


def open_app_ok(app):
    """The -a value must be an exact name on the app allow list."""
    return app in OPEN_APPS


def open_file_type_ok(path):
    """True if the file name ends with a viewer type on the allow list."""
    base = os.path.basename(path.rstrip("/"))
    if "." not in base:
        return False
    return base.rsplit(".", 1)[1].casefold() in OPEN_FILE_TYPES


def is_alias_file(path):
    """True if the file is a Finder alias (bookmark data), or if it cannot be read."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
        return head[:4] == b"book" and head[8:12] == b"mark"
    except OSError:
        return True


def plain_folder(path):
    """True if the folder is not a bundle (an app, a package, a plugin)."""
    if os.path.basename(path.rstrip("/")).casefold().endswith(OPEN_BUNDLE_SUFFIXES):
        return False
    for rel in ("Contents/Info.plist", "Contents/MacOS", "Info.plist"):
        if os.path.lexists(os.path.join(path, rel)):
            return False
    return True


def open_local_ok(raw, cwd):
    """A local target: a viewer file type, or a plain folder. No symlink, no alias."""
    import stat
    p = os.path.normpath(raw if os.path.isabs(raw) else os.path.join(cwd, raw))
    try:
        st = os.lstat(p)
    except (FileNotFoundError, NotADirectoryError):
        # open fails on a missing file. The name must still be a viewer type.
        return open_file_type_ok(p)
    if stat.S_ISLNK(st.st_mode):
        return False
    real = os.path.realpath(p)
    if stat.S_ISDIR(st.st_mode):
        return plain_folder(p) and plain_folder(real)
    if not stat.S_ISREG(st.st_mode):
        return False
    if not (open_file_type_ok(p) and open_file_type_ok(real)):
        return False
    # open runs an executable file in Terminal.
    if os.access(real, os.X_OK):
        return False
    return not is_alias_file(real)


def open_target_ok(target, cwd):
    """http and https links, or a local target. Every other scheme (also file:) is denied."""
    m = URL_SCHEME.match(target)
    if m:
        if m.group(1).casefold() not in ("http", "https"):
            return False
        from urllib.parse import urlsplit, unquote
        parts = urlsplit(target)
        return bool(parts.netloc) and not bad_open_path(unquote(parts.path))
    if ":" in target:
        return False
    return open_local_ok(target, cwd)


def open_command_ok(cmd, cwd):
    """True if cmd is exactly: open [-R] [-e] [-t] [-a <App>] <target> [<target>...]."""
    try:
        words = split_open_words(cmd)
        if not words or words[0] != "open":
            return False
        import shlex
        if shlex.split(cmd) != words:
            return False
        targets = []
        i = 1
        while i < len(words):
            w = words[i]
            if w == "-a":
                if i + 1 >= len(words) or not open_app_ok(words[i + 1]):
                    return False
                i += 2
                continue
            if w.startswith("-"):
                if w not in OPEN_FLAGS:
                    return False
                i += 1
                continue
            if not w:
                return False
            targets.append(w)
            i += 1
        return bool(targets) and all(open_target_ok(t, cwd) for t in targets)
    except Exception:
        return False


def path_forms(path):
    """The text forms of a path in a shell command, casefolded.

    The absolute path, and under the home folder also ~/x, $HOME/x and ${HOME}/x.
    Under a custom Claude folder also $CLAUDE_CONFIG_DIR/x.
    """
    path = path.casefold().rstrip("/")
    out = [path]
    homes = {HOME.casefold().rstrip("/"), os.path.realpath(HOME).casefold().rstrip("/")}
    for h in homes:
        if h and h != "/" and path.startswith(h + "/"):
            rel = path[len(h) + 1:]
            out += ["~/" + rel, "$home/" + rel, "${home}/" + rel]
    cd = CLAUDE_DIR.casefold().rstrip("/")
    if os.environ.get("CLAUDE_CONFIG_DIR") and path.startswith(cd + "/"):
        rel = path[len(cd) + 1:]
        out += ["$claude_config_dir/" + rel, "${claude_config_dir}/" + rel]
    return out


def plugin_in_cache():
    """True if the plugin root is under the Claude plugins folder (an installed copy)."""
    bases = (CLAUDE_DIR + "/plugins", os.path.realpath(CLAUDE_DIR) + "/plugins")
    return any(under(PLUGIN_ROOT, b) for b in bases)


# A word-bounded folder name. "_", "-" and "." count as word characters, so
# gate_status.py and a gate-release folder do not match.
def token_re(names):
    return r"(?<![\w.-])(?:%s)(?![\w.-])" % "|".join(names)


def plugin_tamper_text(root):
    """The tamper_text patterns for the gate's own folders (Round 7 fix F4).

    - The gate folder and the root folder, in every text form (path_forms).
    - For an installed copy (under ~/.claude/plugins): the hooks folder too, and a
      command that names the plugin root and then the word gate or hooks (for
      example: cd <root> && rm -rf gate).
    """
    dirs = [GATE_DIR] + [r + "/gate" for r in PLUGIN_ROOTS[1:]]
    if root and root != "/":
        dirs.append(root)
    names = ["gate"]
    if plugin_in_cache():
        dirs += [r + "/hooks" for r in PLUGIN_ROOTS]
        names.append("hooks")
    out = []
    for d in dirs:
        out += [re.escape(f) for f in path_forms(d)]
    if plugin_in_cache():
        forms = sorted({f for r in PLUGIN_ROOTS for f in path_forms(r)}, key=len, reverse=True)
        out.append("(?s)(?:%s)(?:[\\s'\"/]).*%s" % ("|".join(re.escape(f) for f in forms), token_re(names)))
    return out


def build_rules(cfg, cwd):
    """Merge the global lists with the entry of the best matching project."""
    best, best_len = None, -1
    for path, entry in (cfg.get("projects") or {}).items():
        p = os.path.normpath(expand(path))
        if under(cwd, p) and len(p) > best_len and isinstance(entry, dict):
            best, best_len = entry, len(p)
    best = best or {}

    def lst(key):
        out = list(cfg.get(key, [])) + list(best.get(key, []))
        if key in ("writable_dirs", "deny_write_dirs", "read_dirs"):
            # {cwd} is the folder of the window (for example {cwd}/.orchestrate).
            out = [expand(d, cwd) if isinstance(d, str) else d for d in out]
        if key == "deny_write_dirs":
            # The gate's own folder and its config folder stay closed in every mode.
            out = out + [GATE_DIR, STATE.get("root") or ROOT]
            if plugin_in_cache():
                # An installed copy: hooks/hooks.json starts the gate. Close it too.
                out.append(PLUGIN_ROOT + "/hooks")
        if key == "tamper_text":
            out = out + plugin_tamper_text(STATE.get("root") or ROOT)
        return out

    rules = {
        "allow_tools": set(lst("allow_tools")),
        "conductor_prefixes": tuple(lst("conductor_prefixes")),
        "suborch_prefixes": tuple(lst("conductor_prefixes")) + tuple(lst("suborch_prefixes")),
        "conductor_deny_prefixes": tuple(lst("conductor_deny_prefixes")),
        "suborch_deny_prefixes": tuple(lst("suborch_deny_prefixes")),
        "deny_tools": set(lst("deny_tools")),
        "file_tools": set(cfg.get("file_tools", ["Read", "Edit", "Write", "NotebookEdit"])),
        "writable_dirs": [norm(d) or d for d in lst("writable_dirs")],
        "deny_write_dirs": [norm(d) or d for d in lst("deny_write_dirs")],
        "deny_write_suffixes": tuple(x.casefold() for x in lst("deny_write_suffixes")),
        "deny_write_parts": set(x.casefold() for x in lst("deny_write_parts")),
        "conductor_bash": compile_patterns(lst("conductor_bash")),
        "suborch_bash": compile_patterns(lst("suborch_bash")),
        # Round 6: Read and read-only Bash folders ("*" = one folder name), note
        # suffixes for writes, and file names that stay closed to writes.
        "read_dirs": [expand(d) for d in lst("read_dirs") if isinstance(d, str)],
        "note_suffixes": tuple(x.casefold() for x in lst("note_suffixes")),
        "deny_write_names": [x.casefold() for x in lst("deny_write_names") if isinstance(x, str)],
        # Round 6 fix F1: tamper names (path parts under ~/.claude) and the text
        # patterns that mark a tamper path in a Bash command.
        "tamper_names": [x.casefold() for x in lst("tamper_names") if isinstance(x, str)],
        "tamper_text": [re.compile(x) for x in lst("tamper_text") if isinstance(x, str)],
        "mode": best.get("mode"),
        "label": best.get("label"),
    }
    template = cfg.get("memory_dir_template")
    rules["own_memory_dir"] = None
    if isinstance(template, str):
        rules["own_memory_dir"] = norm(expand(template.replace("{slug}", slug_of(cwd))))
        rules["writable_dirs"].append(rules["own_memory_dir"])
    return rules


def off_switch():
    """The off switch line for the deny message (plugin mode or locked mode)."""
    if STATE.get("locked"):
        return ("Off switch (owner only, in a plain Terminal window): sudo \"%s/gatectl\" off"
                % (STATE.get("root") or ROOT))
    # Do not print the plugin off command here. A worker sub-agent is not checked,
    # so the exact command would tell the model how to go around the gate.
    return ("Off switch: owner only, in a plain Terminal window. "
            "See \"Turn it off\" in the ladder plugin README.")


def message(level, label):
    where = " in the %s project" % label if label else ""
    if level == "suborch":
        return (
            "Conductor gate: the sub-orchestrator does not do hands-on work%s. "
            "Dispatch this to a worker sub-agent with the Agent tool and set model "
            "(haiku for bulk, sonnet for builds, opus for judging). "
            "Read the block message: the allowed list depends on the project. "
            "Also allowed: wait loops (sleep, until [ -f x ]; do sleep N; done) and one plain "
            "ls, cat or grep in a ledger folder or a memory folder (no chains). %s"
            % (where, off_switch())
        )
    cfg = STATE.get("cfg") or {}
    opener = ", and open for a file or a web link" if cfg.get("allow_open_command", True) is True else ""
    direct = ("To let the main window act this turn, the user starts the message with /direct. "
              if cfg.get("allow_direct_skill", True) is True else "")
    conduct = ("To let this window work hands-on until further notice, the user types /conduct off. "
               if cfg.get("allow_conduct_skill", True) is True else "")
    return (
        "Conductor gate: the conductor does not do hands-on work%s. "
        "Dispatch this goal to the sub-orchestrator with the Agent tool "
        "(subagent_type %s) and set model. "
        "Allowed for you: Agent, SendMessage, TaskStop, the ledger notes (.md, .txt, .ready) in "
        "~/.claude/plans, the .orchestrate folder of this project and the project memory folder, "
        "Read and one plain ls, cat or grep there, "
        "wait loops (sleep, until), the date command%s. "
        "%s%s%s"
        % (where, SUBORCH_TYPE, opener, direct, conduct, off_switch())
    )


TAMPER_TEXT = (
    "Conductor gate: this folder is closed to the main window in every mode "
    "(the gate folder, conductor-gate files, the gate log, config and state folder, the managed settings folder). "
    "Reads pass. Ask the user to make this change in a Terminal."
)
# Round 6 fix R2-5: tools that only read a path. Round 6 fix R4-1 removed the MCP read-name
# skip (get_, read_, ...): chrome-devtools get_network_request can save a file.
PATH_READ_TOOLS = frozenset(("Read", "Grep", "Glob", "LS", "NotebookRead"))
# Round 6 fix R3-1 and R4-2: the path walk. A string value is a path candidate if its key
# has a path word, or if the value starts with "/" or "~" and its key is not free text.
PATH_KEY_PARTS = ("path", "file", "dir", "dest", "output", "target")
# Round 6 fix R4-2: the key parts count as whole words. The gate splits a key at camelCase
# and at "_", "-" or ".". A word counts if it is HEAD + part + TAIL with HEAD and TAIL from
# these lists (for example filename, outdir, destination, files). So "direction" and
# "profile" do not count.
KEY_WORD_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
KEY_PART_HEADS = frozenset(("", "out", "in", "save", "src", "source", "base", "work", "tmp",
                            "temp", "sub", "root", "log", "data", "cache", "home", "download",
                            "upload", "export", "local") + PATH_KEY_PARTS)
KEY_PART_TAILS = frozenset(("", "s", "name", "names", "ectory", "ectories", "ination",
                            "inations") + PATH_KEY_PARTS + tuple(x + "s" for x in PATH_KEY_PARTS))
# Keys that hold free text. A value under one of these keys that starts with "/" is not a
# path candidate (an Agent prompt or a question can start with a folder name).
FREE_TEXT_KEYS = frozenset(("prompt", "command", "description", "content", "message", "text",
                            "old_string", "new_string", "query", "pattern", "body",
                            "instructions", "args", "subject", "title", "note", "comment",
                            "question", "label", "header", "summary", "reason", "payload",
                            "markdown", "searchtext", "caption", "answer", "explanation"))
# Bash and Monitor have their own command check.
WALK_SKIP_TOOLS = frozenset(("Bash", "Monitor"))
# The soft cap: past it, the gate writes a PATH-WALK-CAP row and also runs the text match.
WALK_MAX_VALUES = 200
WALK_MAX_DEPTH = 6
# Round 6 fix R4-3: the hard cap. The walk stops here.
WALK_HARD_VALUES = 2000
WALK_HARD_DEPTH = 32


def path_key(key):
    """True if a key of a tool input names a file place (Round 6 fix R4-2)."""
    for w in KEY_WORD_RE.findall(key):
        w = w.casefold()
        for part in PATH_KEY_PARTS:
            i = w.find(part)
            if i >= 0 and w[:i] in KEY_PART_HEADS and w[i + len(part):] in KEY_PART_TAILS:
                return True
    return False
# Write and Edit tools that may write a conductor-gate* note in the own memory folder.
NOTE_EDIT_TOOLS = ("Write", "Edit", "MultiEdit")


def emit(payload):
    sys.stdout.write(json.dumps({"hookSpecificOutput": dict(payload, hookEventName="PreToolUse")}))
    sys.stdout.flush()


def block(text, detail, name=None):
    log(name or "DENY", detail)
    sys.stderr.write(text + "\n")
    sys.exit(2)


def direct_turn_allowed(mode, level):
    """True if the conductor is in a /direct turn and the config permits it."""
    if level != "conductor" or mode not in ("enforce", "warn"):
        return False
    if (STATE.get("cfg") or {}).get("allow_direct_skill", True) is not True:
        return False
    # Round 5b: only an interactive session (a window or the desktop app) can use /direct.
    if first_entrypoint(STATE.get("transcript_path")) not in DIRECT_ENTRYPOINTS:
        return False
    return is_direct_turn(STATE.get("transcript_path"))


def violation(mode, level, label, detail, text=None, name=None, direct=True):
    """A rule failed. In enforce: block. In warn: allow with context.

    In a /direct turn of the conductor, the call passes (decision ALLOW-DIRECT).
    direct=False keeps the rule in force also in a direct turn and in guards mode
    (the tamper folders).
    In guards mode (Round 6) the dispatch rule does not apply: the call passes with no log.
    Round 6 fix F1: direct=False (a tamper path) blocks in enforce, warn and guards.
    Log mode (headless) only logs WOULD-DENY, so the weekly jobs keep running.
    """
    if not direct:
        detail = "tamper: " + detail
        if mode in ("guards", "warn", "enforce"):
            block(TAMPER_TEXT, detail, name)
    if mode == "guards":
        sys.exit(0)
    if direct and direct_turn_allowed(mode, level):
        log("ALLOW-DIRECT", detail)
        sys.exit(0)
    text = text or message(level, label)
    if mode == "log":
        # Round 6: some timer and judge folders give no WOULD-DENY rows (log noise only).
        cfg = STATE.get("cfg") or {}
        cwd = os.path.normpath(STATE.get("cwd") or "/")
        if not any(glob_under(cwd, q) for q in cfg.get("quiet_log_cwds", []) if isinstance(q, str)):
            log("WOULD-DENY", detail)
        sys.exit(0)
    if mode == "warn":
        log(name or "WARN", detail)
        emit({"additionalContext": text})
        sys.exit(0)
    block(text, detail, name)


def resolve_mode(cfg, level, rules, root, conduct=None, headless=False):
    """Return (mode, source). The first match wins.

    1. The OFF file: "off".
    2. Conductor only: the last genuine /conduct line ("on" = enforce, "off" = guards).
    3. The project entry for the cwd (for example a project in enforce_projects).
    4. Headless (claude -p): headless_mode ("log").
    5. Sub-orchestrator: suborchestrator_default_mode ("enforce").
    6. Conductor: default_mode ("guards"). The session model no longer picks the mode.
    """
    if off_file_present(root):
        return "off", "off-file"
    if level == "conductor" and conduct == "on":
        return "enforce", "/conduct on"
    if level == "conductor" and conduct == "off":
        return "guards", "/conduct off"
    if rules["mode"] in MODES and not (level == "suborch" and rules["mode"] == "guards"):
        # A sub-orchestrator never runs in guards: it stays on its own default.
        return rules["mode"], "project %s" % (rules.get("label") or "entry")
    if headless:
        return cfg.get("headless_mode", "log"), "headless"
    if level == "suborch":
        return cfg.get("suborchestrator_default_mode", "enforce"), "suborchestrator default"
    mode = cfg.get("default_mode", "guards")
    return (mode if mode in MODES else "guards"), "default"


FABLE_TEXT = "Fable workers need the user's approval in the user's own words. A headless run has no one to ask, so the gate denies it."
# Every fable call asks the user, in every mode.
FABLE_ASK_TEXT = "Fable agent: the user approves each one"


def fable_call(mode, detail):
    """A call that would start a fable worker.

    The rule: ask the user in every mode (enforce, warn, guards).
    A headless session has no person to ask, so the deny stays there.
    """
    if mode == "log" and not STATE.get("headless"):
        log("WOULD-ASK", detail)
        sys.exit(0)
    if STATE.get("headless"):
        # A headless session has no person to ask: keep the deny.
        block(FABLE_TEXT, detail)
    log("ASK", detail)
    emit({"permissionDecision": "ask", "permissionDecisionReason": FABLE_ASK_TEXT})
    sys.exit(0)


def check_agent(mode, tool_input, tier):
    """Rules for the Agent tool. Returns normally if the call may go on."""
    if tool_input.get("subagent_type") == "fork":
        # A fork ignores model and runs on the session model.
        # An unknown tier counts as fable.
        if tier == "fable" or tier is None:
            fable_call(mode, "fork on a fable session")
        return
    model = tool_input.get("model")
    if not isinstance(model, str) or not model.strip():
        if mode == "log":
            log("WOULD-FILL", "Agent without model (enforce would deny)")
            sys.exit(0)
        if mode == "enforce":
            block("Set model on every Agent call: haiku for bulk, sonnet for builds, opus for judging.", "Agent without model")
        fixed = dict(tool_input)
        fixed["model"] = "sonnet"
        log("FILL", "Agent model set to sonnet")
        emit({
            "permissionDecision": "allow",
            "permissionDecisionReason": "Gate filled in model: sonnet.",
            "updatedInput": fixed,
            "additionalContext": "Gate filled in model: sonnet.",
        })
        sys.exit(0)
    if "fable" in model.lower():
        fable_call(mode, "Agent model " + model)


def workflow_has_fable(tool_input):
    script = tool_input.get("script")
    if isinstance(script, str) and "fable" in script.casefold():
        return True
    sp = tool_input.get("scriptPath")
    if isinstance(sp, str) and sp:
        try:
            with open(expand(sp), "rb") as fh:
                text = fh.read(2 * 1024 * 1024).decode("utf-8", "replace")
            if "fable" in text.casefold():
                return True
        except Exception:
            pass
    return False


def local_paths(tool_input):
    """Every local path an Artifact-like call names, resolved against root."""
    out = []
    fp = tool_input.get("file_path")
    if isinstance(fp, str) and fp:
        out.append(fp)
    fps = tool_input.get("file_paths")
    if isinstance(fps, list):
        out.extend(x for x in fps if isinstance(x, str) and x)
    files = tool_input.get("files")
    if isinstance(files, dict):
        for v in files.values():
            if isinstance(v, str) and v:
                out.append(v)
            elif isinstance(v, dict) and isinstance(v.get("from"), str) and v["from"]:
                out.append(v["from"])
    elif isinstance(files, list):
        for v in files:
            if isinstance(v, dict) and isinstance(v.get("path"), str) and v["path"]:
                out.append(v["path"])
    return out


def off_file_present(root):
    """True if the OFF file exists. Locked mode reads only the locked folder.

    Plugin mode also reads ~/.claude/conductor-gate/OFF (the user's off switch).
    """
    if os.path.exists(os.path.join(root, "OFF")):
        return True
    return not STATE.get("locked") and os.path.exists(os.path.join(USER_DIR, "OFF"))


def main():
    args = parse_args(sys.argv[1:])
    root = args["root"]
    STATE.update(log_file=args["log_file"], root=root, locked=args["locked"])
    if not args["locked"] and managed_gate_present(args["managed"], args["managed_owner"]):
        # A locked copy of the gate runs from the managed settings. Only that copy decides.
        sys.exit(0)
    if off_file_present(root):
        sys.exit(0)
    try:
        data = json.loads(sys.stdin.read())
        if not isinstance(data, dict):
            raise ValueError("not an object")
        tool = data["tool_name"]
        cwd = data["cwd"]
        if not isinstance(tool, str) or not isinstance(cwd, str):
            raise ValueError("bad field type")
    except Exception as exc:
        log("FAIL-OPEN", "input error: %r" % (exc,))
        sys.exit(0)
    STATE.update(session_id=data.get("session_id"), cwd=cwd, tool=tool)

    try:
        cfg = load_config(root, args["user_config"], args["locked"])
    except Exception as exc:
        log("FAIL-OPEN", "config error: %r" % (exc,))
        sys.exit(0)
    if STATE.get("user_config_error"):
        log("USER-CONFIG-ERR", STATE["user_config_error"])
    STATE["cfg"] = cfg
    STATE["transcript_path"] = data.get("transcript_path")

    # Level.
    agent_type = data.get("agent_type")
    if agent_type in cfg.get("suborchestrator_types", ["suborchestrator", SUBORCH_TYPE]):
        level = "suborch"
    elif data.get("agent_id"):
        sys.exit(0)
    else:
        level = "conductor"
    STATE["level"] = level

    c = os.path.normpath(cwd)
    try:
        rules = build_rules(cfg, c)
    except Exception as exc:
        # A user value that passed the checks still broke the rules. Use the shipped
        # config, so the money guards stay on.
        log("USER-CONFIG-ERR", "rules error, shipped config used: %r" % (exc,))
        cfg = STATE["cfg"] = STATE.get("shipped_cfg") or cfg
        rules = build_rules(cfg, c)
    tp = data.get("transcript_path")
    entry = first_entrypoint(tp)
    headless = entry == "sdk-cli"
    STATE["headless"] = headless

    # Round 6: the session state file (bookmark of the last /conduct line, last logged mode).
    sdir = sname = serr = None
    state = {}
    if level == "conductor":
        sname = state_name(data.get("session_id"))
        if sname:
            sdir, serr = state_dir()
        if sdir:
            state = load_state(sdir, sname)
    conduct = None
    if (level == "conductor" and cfg.get("allow_conduct_skill", True) is True
            and entry in DIRECT_ENTRYPOINTS and not off_file_present(root)):
        conduct = conduct_switch(tp, state)
    mode, source = resolve_mode(cfg, level, rules, root, conduct, headless)
    STATE["mode"] = mode
    if level == "conductor" and sname and not headless and source != "off-file":
        # No MODE row and no state write when the OFF file exists: the gate is fully off.
        # A MODE row each time the mode of the session changes (for the status line).
        if sdir:
            want_mode = state.get("last_mode") != mode
            if want_mode:
                state["last_mode"] = mode
                state["_dirty"] = True
            if state.get("_dirty"):
                serr = save_state(sdir, sname, state)
            if not serr and want_mode:
                log("MODE", "mode set by: " + source)
        if serr:
            # Round 6 fix F5: a bad state folder or a failed save gives one STATE-ERR row.
            state_fallback(data.get("session_id"), mode, source, serr)
    if mode == "off":
        sys.exit(0)

    label = rules["label"]
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}

    if tool == "Agent":
        check_agent(mode, tool_input, get_model())

    prefixes = rules["suborch_prefixes"] if level == "suborch" else rules["conductor_prefixes"]
    patterns = rules["suborch_bash"] if level == "suborch" else rules["conductor_bash"]
    read_dirs = rules["read_dirs"] + [d for d in rules["writable_dirs"] if d]

    def own_memory_note(p):
        """True if p is a .md file inside the window's own project memory folder.

        Round 6 fix F1b: such a note may have a conductor-gate* name (for example
        conductor-gate-global-decisions.md). p must be a realpath, so "..", a symlink and
        the memory folder of another project do not count.
        """
        own = rules.get("own_memory_dir")
        return bool(p and own and p != own and under(p, own) and p.casefold().endswith(".md"))

    def tamper_path(p, note_ok=False):
        """True if a write to this path stays closed in every mode (Round 6 fix F1).

        A deny folder, or a path part under ~/.claude that matches a tamper name
        (conductor-gate*). Round 6 fix R2-1: settings files and bin folders are not tamper
        paths. Enforce closes them with its normal write rules. Guards and warn leave them open.
        note_ok=True (Write and Edit only): a note in the own memory folder passes the name check.
        """
        if not p:
            return False
        if any(d and under(p, d) for d in rules["deny_write_dirs"]):
            return True
        if note_ok and own_memory_note(p):
            return False
        # Round 6 fix R2-1: the tamper names count only under ~/.claude (the gate's own files).
        if not under(p, CLAUDE_DIR):
            return False
        parts = [x.casefold() for x in p.split("/") if x]
        return any(fnmatch.fnmatchcase(x, n) for x in parts for n in rules["tamper_names"])

    def bash_check(cmd, what):
        if not isinstance(cmd, str):
            if what == "Monitor":
                # Round 6: a Monitor call with no command (for example a ws source)
                # gets a clear decision. It is not a wait and not a read.
                violation(mode, level, label, "Monitor without a command (ws or other source)")
            log("FAIL-OPEN", "no command for " + what)
            sys.exit(0)
        cmd = cmd.lstrip(" ").rstrip()
        if FABLE_SELECT.search(cmd):
            # Round 5b: a command that selects a fable model (for example claude -p
            # --model claude-fable-5) follows the Fable rule, also in a /direct turn.
            fable_call(mode, what + " command selects fable: " + cmd[:120])
        if any(p.fullmatch(cmd) for p in patterns):
            sys.exit(0)
        # Round 6: waits (sleep, until loops) for both levels, Bash and Monitor.
        if wait_command_ok(cmd):
            sys.exit(0)
        # Round 6: one plain ls, cat or grep inside the read folders (Bash only).
        if what == "Bash" and read_command_ok(cmd, c, read_dirs):
            sys.exit(0)
        if (what == "Bash" and level == "conductor" and cfg.get("allow_open_command", True) is True
                and open_command_ok(cmd, c)):
            log("ALLOW-OPEN", cmd[:150])
            sys.exit(0)
        # Round 6 fix F1: a command that names a tamper path and may write is denied
        # in every mode, also in guards and in a /direct turn.
        if tamper_command(cmd) and command_may_write(cmd):
            violation(mode, level, label, what + " " + cmd[:150], direct=False)
        violation(mode, level, label, what + " " + cmd[:150])

    def tamper_command(cmd):
        """True if the command text (or the cwd) names a tamper path.

        Round 6 fix R2-2: the match also runs on the text with quotes and backslashes
        removed, and with "//", "/./" and "x/../" made simple. This is best effort.
        """
        low = cmd.casefold()
        bare = low.replace('"', "").replace("'", "").replace("\\", "")
        texts = (low, norm_text(low), bare, norm_text(bare))
        if any(p.search(t) for p in rules["tamper_text"] for t in texts):
            return True
        here = norm(c)
        if here and plugin_in_cache() and here.casefold() == PLUGIN_ROOT.casefold():
            # Round 7 fix F4: the window is in the plugin root, so "rm -rf gate" or
            # "rm -rf hooks" needs no path text.
            words = re.compile(token_re(["gate", "hooks"]))
            if any(words.search(t) for t in texts):
                return True
        return tamper_path(here)

    if tool == "Workflow":
        if workflow_has_fable(tool_input):
            fable_call(mode, "Workflow script names a fable model")
        sys.exit(0)

    def write_ok(p):
        """The write rules for enforce: an allowed folder, no deny folder, no bin part,
        no code suffix, and no denied file name."""
        if not (p and any(d and under(p, d) for d in rules["writable_dirs"])):
            return False
        parts = set(x.casefold() for x in p.split("/"))
        base = os.path.basename(p).casefold()
        if (any(d and under(p, d) for d in rules["deny_write_dirs"])
                or (parts & rules["deny_write_parts"])
                or p.casefold().endswith(rules["deny_write_suffixes"])
                or any(fnmatch.fnmatchcase(base, n) for n in rules["deny_write_names"])):
            return False
        return True

    if tool in ("Artifact", "SendUserFile", "ArtifactData"):
        od = tool_input.get("out_dir")
        if isinstance(od, str) and od:
            odp = norm(od, c)
            if tamper_path(odp):
                violation(mode, level, label, "%s out_dir %s" % (tool, od[:150]), direct=False)
            # Round 6 fix F3: the tool saves each published path under out_dir.
            # Check the joined path the same way as a Write.
            names = []
            if isinstance(tool_input.get("path"), str) and tool_input["path"]:
                names.append(tool_input["path"])
            if isinstance(tool_input.get("paths"), list):
                names.extend(x for x in tool_input["paths"] if isinstance(x, str) and x)
            for n in names:
                p = norm(n, odp) if odp else None
                if tamper_path(p):
                    violation(mode, level, label, "%s out_dir file %s" % (tool, n[:150]), direct=False)
            if not write_ok(odp):
                violation(mode, level, label, "%s out_dir %s" % (tool, od[:150]))
            for n in names:
                if not write_ok(norm(n, odp) if odp else None):
                    violation(mode, level, label, "%s out_dir file %s" % (tool, n[:150]))
        root_dir = tool_input.get("root") if isinstance(tool_input.get("root"), str) else None
        for raw in local_paths(tool_input):
            p = norm(raw, norm(root_dir, c) if root_dir and norm(root_dir, c) else c)
            if not (p and any(d and under(p, d) for d in rules["writable_dirs"])):
                violation(mode, level, label, "%s path %s" % (tool, raw[:150]))
        sys.exit(0)

    if tool == "Monitor":
        bash_check(tool_input.get("command"), "Monitor")

    def walk_paths(obj):
        """Round 6 fix R3-1 and R4-3: find the path candidates in a tool input.

        The walk goes into nested objects and lists. A list item keeps the key of its list.
        It returns (candidates, soft, hard). Each candidate is (value, out_dir or None), where
        out_dir is a string out_dir key in the same object or in an outer object.
        soft is True past WALK_MAX_VALUES strings or WALK_MAX_DEPTH levels.
        hard is True if the walk stopped at WALK_HARD_VALUES or WALK_HARD_DEPTH.
        """
        found = []
        state = {"n": 0, "soft": False, "hard": False}
        keys = {}

        def visit(v, key, od, depth):
            if state["hard"]:
                return
            if depth > WALK_MAX_DEPTH:
                state["soft"] = True
            if depth > WALK_HARD_DEPTH:
                state["hard"] = True
                return
            if isinstance(v, dict):
                sub = v.get("out_dir")
                if isinstance(sub, str) and sub:
                    od = sub
                for k, x in v.items():
                    visit(x, str(k), od, depth + 1)
            elif isinstance(v, list):
                for x in v:
                    visit(x, key, od, depth + 1)
            elif isinstance(v, str) and v:
                state["n"] += 1
                if state["n"] > WALK_MAX_VALUES:
                    state["soft"] = True
                if state["n"] > WALK_HARD_VALUES:
                    state["hard"] = True
                    return
                # A path has no line break and is not longer than 1024 characters.
                if "\n" in v or len(v) > 1024:
                    return
                if key not in keys:
                    keys[key] = (path_key(key), key.casefold() in FREE_TEXT_KEYS)
                keyed, free = keys[key]
                if keyed or (v.startswith(("/", "~")) and not free):
                    found.append((v, od))

        visit(obj, "", None, 0)
        return found, state["soft"], state["hard"]

    # Round 6 fix R2-5, R3-1 and R4-1/R4-2: the path walk runs for every tool that is not a
    # file tool, not a pure read tool (Read, Grep, Glob, LS, NotebookRead), not Bash or
    # Monitor, and not in allow_tools (coordination tools that write no file). MCP tools with
    # a read name (get_, read_, ...) are walked too: some of them save a file.
    # Only a gate path denies. Other values pass.
    if (tool not in rules["file_tools"] and tool not in PATH_READ_TOOLS
            and tool not in WALK_SKIP_TOOLS and tool not in rules["allow_tools"]):
        found, soft, hard = walk_paths(tool_input)
        cache = {}
        dir_real = {}

        def walk_norm(v, base):
            """The same result as norm(v, base), with the realpath of each folder cached.
            A big input with many names in one folder stays fast."""
            p = expand(v)
            if not os.path.isabs(p):
                p = os.path.join(base, p)
            p = os.path.normpath(p)
            d, b = os.path.split(p)
            rd = dir_real.get(d)
            if rd is None:
                rd = dir_real[d] = os.path.realpath(d)
            full = os.path.join(rd, b) if b else rd
            if b and os.path.islink(full):
                return os.path.realpath(full)
            return full

        for v, od in found:
            bases = [c]
            odp = norm(od, c) if od else None
            if odp:
                bases.append(odp)
            for base in bases:
                if (v, base) in cache:
                    continue
                cache[(v, base)] = True
                if tamper_path(walk_norm(v, base)):
                    violation(mode, level, label, "%s path %s" % (tool, v[:150]), direct=False)
        if soft:
            # Past the soft cap the gate also matches the whole input as text (conductor-gate,
            # support/claudecode). A hit denies.
            log("PATH-WALK-CAP", "%s: more than %d values or depth %d" % (tool, WALK_MAX_VALUES, WALK_MAX_DEPTH))
            dump = json.dumps(tool_input, ensure_ascii=False).casefold()
            if any(p.search(dump) for p in rules["tamper_text"]):
                violation(mode, level, label, "%s input names a gate path (walk cap)" % tool, direct=False)
        if hard and tamper_path(norm(c)):
            # Round 6 fix R4-3: the walk did not see the whole input, and the cwd is a gate
            # folder, so a relative name can be a gate file. Fail closed.
            violation(mode, level, label, "%s input too big to check, cwd is a gate folder" % tool, direct=False)

    if tool in rules["allow_tools"] or tool.startswith(prefixes):
        sys.exit(0)

    if tool in rules["file_tools"]:
        raw = tool_input.get("file_path") or tool_input.get("notebook_path")
        if not isinstance(raw, str) or not raw:
            log("FAIL-OPEN", "no path for " + tool)
            sys.exit(0)
        p = norm(raw, c)
        memory_note = tool in NOTE_EDIT_TOOLS and own_memory_note(p)
        if tool != "Read" and tamper_path(p, note_ok=tool in NOTE_EDIT_TOOLS):
            violation(mode, level, label, "%s write blocked: %s" % (tool, raw), direct=False)
        if tool == "Read":
            # Round 6: Read is also allowed in every project memory folder.
            if not (p and any(d and glob_under(p, d) for d in read_dirs)):
                violation(mode, level, label, "%s %s" % (tool, raw))
            sys.exit(0)
        if not (p and any(d and under(p, d) for d in rules["writable_dirs"])):
            violation(mode, level, label, "%s %s" % (tool, raw))
        parts = set(x.casefold() for x in p.split("/"))
        base = os.path.basename(p).casefold()
        if (any(d and under(p, d) for d in rules["deny_write_dirs"])
                or (parts & rules["deny_write_parts"])
                or p.casefold().endswith(rules["deny_write_suffixes"])
                or (not memory_note and any(fnmatch.fnmatchcase(base, n) for n in rules["deny_write_names"]))):
            violation(mode, level, label, "%s write blocked: %s" % (tool, raw))
        # Round 6: only note files (.md, .txt, .ready, ...) in the allowed folders.
        if rules["note_suffixes"] and not base.endswith(rules["note_suffixes"]):
            violation(mode, level, label, "%s write blocked (not a note file): %s" % (tool, raw))
        sys.exit(0)

    if tool == "Bash":
        bash_check(tool_input.get("command"), "Bash")

    if tool in rules["deny_tools"]:
        violation(mode, level, label, tool)

    deny_prefixes = rules["suborch_deny_prefixes"] if level == "suborch" else rules["conductor_deny_prefixes"]
    if tool.startswith(deny_prefixes):
        violation(mode, level, label, tool)

    # An unknown tool is a violation for the conductor and the sub-orchestrator.
    violation(mode, level, label, "unknown tool: " + tool, name="UNKNOWN-TOOL")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        log("FAIL-OPEN", "crash: %r" % (exc,))
        sys.exit(0)

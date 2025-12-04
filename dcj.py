#!/usr/bin/env python3
"""
Docker Compose Jinja Templates 'Plugin'

A cross‑platform single‑file wrapper for docker compose / docker‑compose that:
- Detects a Jinja template (docker-compose.jinja or docker-compose.j2) in the current directory.
- Loads environment variables from a .env file (or an overridden file) into the process environment.
- Renders the template to a docker-compose YAML file (default: docker-compose.yml, can be overridden).
- Recognizes custom parameters, replaces them with equivalent docker compose parameters while preserving order.
- Forwards all other parameters unmodified to docker compose.

Custom parameters implemented:
- --yml-file=<filename> or "--yml-file <filename>"
  → Render template output to <filename> and pass "-f <filename>" to docker compose
- --env-file=<filename> or "--env-file <filename>"
  → Load env vars from <filename> and pass "--env-file <filename>" to docker compose

Notes:
- Prefers the Docker CLI v2 subcommand ("docker compose"). Falls back to the legacy binary ("docker-compose") if needed.
- Requires the Jinja2 package: pip install jinja2
- Packaging to a single executable can be done with PyInstaller: pyinstaller --onefile dcj.py
"""
from __future__ import annotations

import os
import sys
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Tuple, Optional

# --- Configuration ---
DEFAULT_TEMPLATE_NAMES = [
    "docker-compose.jinja.yml",
    "docker-compose.jinja",
    "docker-compose.j2.yml",
    "docker-compose.j2",
]
DEFAULT_OUTPUT_YML = "docker-compose.yml"
DEFAULT_ENV_FILE = ".env"

# --- Utilities ---

# Version and author metadata (override via env: DCJ_VERSION, DCJ_AUTHOR)
__version__ = "1.1.5"
__author__ = "Matt Lowe <marl.scot.1@googlemail.com>"

def which_compose() -> Tuple[List[str], str]:
    """Return the command argv list to run docker compose, and a human label.

    Prefer `docker compose` (Docker CLI v2). Fallback to `docker-compose`.
    """
    docker_bin = shutil.which("docker")
    legacy_bin = shutil.which("docker-compose")
    if docker_bin is not None:
        return [docker_bin, "compose"], "docker compose"
    if legacy_bin is not None:
        return [legacy_bin], "docker-compose"
    raise RuntimeError(
        "Neither 'docker' (with 'compose' subcommand) nor 'docker-compose' was found in PATH."
    )


def parse_equals_or_next(argv: List[str], flag: str) -> Tuple[Optional[str], List[int]]:
    """Parse a flag that may appear as "--flag=value" or "--flag value".

    Returns (value, consumed_indices). If not found, (None, []).
    If found in --flag=value, consumed_indices has [i]. If --flag value, [i, i+1].
    """
    consumed: List[int] = []
    for i, tok in enumerate(argv):
        if tok == flag:
            if i + 1 < len(argv):
                consumed = [i, i + 1]
                return argv[i + 1], consumed
            else:
                raise SystemExit(f"Expected a value after {flag}")
        if tok.startswith(flag + "="):
            consumed = [i]
            return tok.split("=", 1)[1], consumed
    return None, []


@dataclass
class CustomArgs:
    yml_file: Optional[str] = None
    env_file: Optional[str] = None
    dump: bool = False


class ArgRewriter:
    """Recognize custom args and rewrite to docker compose equivalents while preserving order."""

    def __init__(self, argv: List[str]):
        self.original_argv = argv[:]  # without the executable
        self.custom = CustomArgs()
        self.rewritten: List[str] = argv[:]

    def process(self) -> CustomArgs:
        # We must preserve the order of non-custom args. For each custom arg occurrence,
        # replace it in-place with its docker equivalent tokens.
        i = 0
        out: List[str] = []
        while i < len(self.rewritten):
            tok = self.rewritten[i]
            if tok == "--yml-file" or tok.startswith("--yml-file="):
                # Extract value
                if tok == "--yml-file":
                    if i + 1 >= len(self.rewritten):
                        raise SystemExit("--yml-file requires a value")
                    value = self.rewritten[i + 1]
                    i += 2
                else:
                    value = tok.split("=", 1)[1]
                    i += 1
                self.custom.yml_file = value
                # Replace with docker compose equivalent: -f value
                out.extend(["-f", value])
                continue
            if tok == "--env-file" or tok.startswith("--env-file="):
                if tok == "--env-file":
                    if i + 1 >= len(self.rewritten):
                        raise SystemExit("--env-file requires a value")
                    value = self.rewritten[i + 1]
                    i += 2
                else:
                    value = tok.split("=", 1)[1]
                    i += 1
                self.custom.env_file = value
                # docker compose equivalent: --env-file value
                out.extend(["--env-file", value])
                continue
            if tok == "--dump":
                # dcj-only flag: enable dump mode and remove it from forwarded args
                self.custom.dump = True
                i += 1
                continue
            # Not a custom token; keep as-is
            out.append(tok)
            i += 1
        self.rewritten = out
        return self.custom


# --- .env loader ---

def load_dotenv(path: str, debug: bool = False) -> List[str]:
    """Robust .env loader (no external dependency).

    Features:
    - Supports lines like "KEY=VALUE" and with spaces around '='.
    - Supports quoted values (single/double) with '#' and '=' inside quotes.
    - Supports inline comments after unquoted values: KEY=value # comment
    - Supports optional 'export ' prefix: export KEY=value
    - Preserves existing os.environ (does not override existing keys).
    - Does NOT expand variable references (e.g., ${VAR}).

    Returns a list of keys that were actually added from the file.
    """
    loaded_keys: List[str] = []
    if not os.path.isfile(path):
        return loaded_keys

    def strip_inline_comment_unquoted(text: str) -> str:
        in_single = False
        in_double = False
        esc = False
        for i, ch in enumerate(text):
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == "'" and not in_double:
                in_single = not in_single
                continue
            if ch == '"' and not in_single:
                in_double = not in_double
                continue
            if ch == "#" and not in_single and not in_double:
                # comment starts here
                return text[:i].rstrip()
        return text.rstrip()

    try:
        with open(path, "r", encoding="utf-8-sig") as f:  # handle BOM via -sig
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                # Handle optional export prefix
                if line.lower().startswith("export "):
                    line = line[7:].lstrip()
                if "=" not in line:
                    if debug:
                        print(f"[dcj][debug] Skipping .env line without '=': {raw_line.rstrip()}", file=sys.stderr)
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                # Remove inline comment when value is unquoted
                if not ((val.startswith("'") and val.endswith("'")) or (val.startswith('"') and val.endswith('"'))):
                    val = strip_inline_comment_unquoted(val).strip()
                # Unquote if quoted
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                    # Support common escape sequences in double-quoted strings
                    # (single quotes are treated literally as in many dotenv parsers)
                    if raw_line.strip().startswith('"') or (val and val[0] != "'"):
                        val = val.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\'", "'")
                if key and all(c == '_' or c.isalnum() for c in key):
                    if key not in os.environ:
                        os.environ[key] = val
                        loaded_keys.append(key)
                else:
                    if debug:
                        print(f"[dcj][debug] Skipping invalid env key: {key}", file=sys.stderr)
    except Exception as e:
        print(f"Warning: Failed to load env file '{path}': {e}", file=sys.stderr)
    return loaded_keys


def dump_environment_vars(prefix: str = "[dcj][debug][env] ") -> None:
    """Dump current environment variables in a stable order to stderr.

    Values are printed as-is; use with care. Intended for debug output only.
    """
    try:
        items = sorted(os.environ.items(), key=lambda kv: kv[0])
    except Exception:
        # Fallback without sorting if something odd happens
        items = list(os.environ.items())
    print(f"[dcj][debug] Dumping {len(items)} environment variables:", file=sys.stderr)
    for k, v in items:
        # Print unescaped to make it easy to copy; users can redirect stderr if needed
        print(f"{prefix}{k}={v}", file=sys.stderr)


# --- Jinja rendering ---

def render_template_string(template_path: str, env: dict, debug: bool = False) -> str:
    try:
        from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError
    except ImportError:
        raise SystemExit(
            "Jinja2 is required. Please install it with: pip install jinja2"
        )

    tpl_dir = os.path.dirname(os.path.abspath(template_path)) or "."
    tpl_name = os.path.basename(template_path)

    if debug:
        print(f"[dcj][debug] Jinja loader dir: {tpl_dir}", file=sys.stderr)
        print(f"[dcj][debug] Jinja template name: {tpl_name}", file=sys.stderr)

    jenv = Environment(
        loader=FileSystemLoader(tpl_dir),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    try:
        tpl = jenv.get_template(tpl_name)
        rendered = tpl.render(**env)
        return rendered
    except TemplateError as e:
        # Provide additional context in debug mode
        if debug:
            name = getattr(e, 'name', tpl_name)
            lineno = getattr(e, 'lineno', '?')
            print(f"[dcj][debug] Jinja TemplateError in {name} at line {lineno}: {e}", file=sys.stderr)
        raise


def render_template_to_yaml(template_path: str, output_path: str, env: dict, debug: bool = False) -> None:
    rendered = render_template_string(template_path, env, debug=debug)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(rendered)


# --- Main orchestration ---

def run_single_debug(program_name: str, argv_wo_dbg: List[str]) -> int:
    """Print dcj and Jinja diagnostics, then exit without invoking compose.

    - Honors --env-file if provided in argv_wo_dbg for loading env before rendering.
    - Shows planned output file if --yml-file is present; does not write it.
    - Attempts to render the template in-memory to expose Jinja errors and prints loader info.
    """
    print(f"[dcj][debug] Program: {program_name}", file=sys.stderr)
    print(f"[dcj][debug] CWD: {os.getcwd()}", file=sys.stderr)
    print(f"[dcj][debug] Original args (without --jdebug): {argv_wo_dbg}", file=sys.stderr)

    # Detect env file from args
    env_file_val, _ = parse_equals_or_next(argv_wo_dbg, "--env-file")
    env_file = env_file_val or DEFAULT_ENV_FILE
    print(f"[dcj][debug] Env file: {env_file} (exists={os.path.isfile(env_file)})", file=sys.stderr)
    if env_file_val and not os.path.isfile(env_file):
        print(f"[dcj] Warning: specified --env-file '{env_file}' does not exist; continuing without loading it.", file=sys.stderr)
    loaded_keys = load_dotenv(env_file, debug=True)
    print(f"[dcj][debug] Loaded environment size: {len(os.environ)} vars", file=sys.stderr)
    if loaded_keys:
        print(f"[dcj][debug] .env added {len(loaded_keys)} vars: {', '.join(sorted(loaded_keys))}", file=sys.stderr)
    else:
        print(f"[dcj][debug] .env added 0 vars (existing env may have overridden or file empty)", file=sys.stderr)
    # Dump all environment variables for inspection
    dump_environment_vars()

    # Template detection
    print(f"[dcj][debug] Template candidates: {DEFAULT_TEMPLATE_NAMES}", file=sys.stderr)
    template_path = find_first_template()
    print(f"[dcj][debug] Selected template: {template_path}", file=sys.stderr)

    # Planned output file
    yml_val, _ = parse_equals_or_next(argv_wo_dbg, "--yml-file")
    output_yml = yml_val or DEFAULT_OUTPUT_YML
    print(f"[dcj][debug] Planned output YAML: {output_yml}", file=sys.stderr)

    # Compose runner detection
    try:
        compose_argv, compose_label = which_compose()
        print(f"[dcj][debug] Compose runner: {compose_label} -> {compose_argv}", file=sys.stderr)
    except Exception as e:
        print(f"[dcj][debug] Compose runner detection failed: {e}", file=sys.stderr)

    # Arg rewriting preview to see if -f injection would happen
    rewriter = ArgRewriter(argv_wo_dbg)
    custom_preview = rewriter.process()
    needs_default_f = False
    if template_path and "-f" not in rewriter.rewritten and "--file" not in rewriter.rewritten and not custom_preview.yml_file:
        needs_default_f = True
    print(f"[dcj][debug] Would inject '-f {output_yml}': {bool(needs_default_f)}", file=sys.stderr)

    # Try rendering to catch Jinja errors and emit Jinja debug info
    if template_path:
        try:
            rendered = render_template_string(template_path, dict(os.environ), debug=True)
            print(f"[dcj][debug] Template render OK. Size: {len(rendered)} bytes", file=sys.stderr)
            print(f"[dcj][debug] Use --dump to print the rendered YAML", file=sys.stderr)
        except Exception as e:
            print(f"[dcj] Template render failed: {e}", file=sys.stderr)
            return 1
    else:
        print("[dcj][debug] No template found. Nothing to render.", file=sys.stderr)

    print("[dcj][debug] Debug run complete. Exiting without invoking docker compose.", file=sys.stderr)
    return 0

def find_first_template() -> Optional[str]:
    for name in DEFAULT_TEMPLATE_NAMES:
        if os.path.isfile(name):
            return name
    return None


def print_help(program_name: str) -> None:
    msg = f"""
Usage: {program_name} [dcj-options] [compose-args]

Docker Compose Jinja Templates 'Plugin' (dcj)
Version: {__version__}
Author: {__author__}

Behavior:
  - If a template file exists (docker-compose.jinja(.yml) or docker-compose.j2(.yml)), it is rendered to a YAML file
    (default: docker-compose.yml, override with --yml-file).
  - Loads environment variables from a .env file by default (override with --env-file). Existing env vars win.
  - Forwards all other arguments to docker compose (prefers 'docker compose', falls back to 'docker-compose').
  - Parameter order is preserved.

dcj options:
  --yml-file <file>      Render the template to <file>; the equivalent "-f <file>" is passed to docker compose.
  --env-file <file>      Load env vars from <file>; the equivalent "--env-file <file>" is passed to docker compose.
  --dump                 Print the rendered YAML to stdout instead of writing a file or invoking compose.
  --jdebug               Show dcj debug info (including an environment variable dump) and exit. No compose is invoked.
  --jhelp                Show this dcj help and exit. (To ask Docker Compose for help, run with only -h or --help.)

Notes:
  - If you run {program_name} with only "-h" or "--help" and no other arguments, dcj will forward that to Docker Compose
    and then print a brief note reminding you that "--jhelp" is available for dcj/Jinja help.
  - Any other occurrence of "-h"/"--help" among additional arguments is forwarded to Docker Compose unchanged.

Debugging examples:
  {program_name} --jdebug         # print dcj/Jinja diagnostics and exit (no compose)
  {program_name} --dump           # print rendered YAML to stdout (no compose)

Packaging:
  pip install jinja2 pyinstaller
  pyinstaller --onefile dcj.py
"""
    print(msg.strip())


def main(argv: List[str]) -> int:
    # argv excludes program name
    prog = os.path.basename(sys.argv[0]) or "dcj"

    # 0) Special case: plain Docker Compose help pass-through
    if len(argv) == 1 and argv[0] in ("-h", "--help"):
        compose_argv, compose_label = which_compose()
        cmd = compose_argv + [argv[0]]
        try:
            escaped = " ".join(shlex.quote(p) for p in cmd)
        except Exception:
            escaped = " ".join(cmd)
        print(f"[dcj] Forwarding help to {compose_label}: {escaped}")
        proc = subprocess.run(cmd)
        # After compose returns, print hint about --jhelp
        print("[dcj] Tip: For dcj/Jinja help, use --jhelp.")
        return proc.returncode

    # 0a) dcj help handling (--jhelp)
    if any(t == "--jhelp" for t in argv):
        print_help(prog)
        return 0

    # 0b) dcj debug handling (--jdebug)
    if any(t == "--jdebug" for t in argv):
        # Remove all occurrences of --jdebug for clarity when printing planned command
        argv_wo_dbg = [t for t in argv if t != "--jdebug"]
        return run_single_debug(prog, argv_wo_dbg)

    # From here on, no special dcj-only modes are active
    debug_enabled = False  # legacy variable retained for conditional debug prints

    # 1) Rewrite args and collect custom values
    rewriter = ArgRewriter(argv)
    custom = rewriter.process()

    # 2) Determine env file to load
    needs_default_f = None
    env_file = custom.env_file or DEFAULT_ENV_FILE
    # Warn if a user-specified env file is missing; proceed regardless.
    if custom.env_file and not os.path.isfile(env_file):
        print(f"[dcj] Warning: specified --env-file '{env_file}' does not exist; continuing without loading it.", file=sys.stderr)
    load_dotenv(env_file, debug=debug_enabled)

    # 3) If a template is present, render it
    template_path = find_first_template()
    if template_path:
        output_yml = custom.yml_file or DEFAULT_OUTPUT_YML
        # Ensure parent directory exists (in case a nested path is provided)
        out_dir = os.path.dirname(output_yml)
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir, exist_ok=True)
        # If dump mode, render to stdout and exit
        if custom.dump:
            try:
                rendered = render_template_string(template_path, dict(os.environ), debug=debug_enabled)
            except Exception as e:
                print(f"[dcj] Template render failed: {e}", file=sys.stderr)
                return 1
            print(rendered, end="")
            return 0
        # Render with the current environment variables
        render_template_to_yaml(template_path, output_yml, dict(os.environ), debug=debug_enabled)
        # If no -f was present and user didn't pass --yml-file, ensure compose uses the output file
        # Note: The rewriter already inserted "-f <file>" when --yml-file was provided.
        if "-f" not in rewriter.rewritten and "--file" not in rewriter.rewritten:
            needs_default_f = output_yml
    else:
        if custom.dump:
            print("[dcj] --dump requested but no template was found.", file=sys.stderr)
            return 2

    # 4) Build the compose command
    compose_argv, compose_label = which_compose()

    final_args: List[str] = []
    if needs_default_f:
        final_args.extend(["-f", needs_default_f])
    final_args.extend(rewriter.rewritten)

    # 5) Execute
    cmd = compose_argv + final_args
    # Print the command in a shell-escaped way for transparency
    try:
        escaped = " ".join(shlex.quote(p) for p in cmd)
    except Exception:
        escaped = " ".join(cmd)
    print(f"[dcj] Using {compose_label}: {escaped}")

    proc = subprocess.run(cmd)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

Docker Compose Jinja Templates 'Plugin' (dcj)

A small cross‑platform wrapper that lets you use Jinja templates for your Docker Compose file. If a template exists in the current directory, it is rendered before invoking Docker Compose.

Key features
- Detects and renders `docker-compose.jinja.yml`, `docker-compose.jinja`, `docker-compose.j2.yml`, or `docker-compose.j2` to YAML (default: `docker-compose.yml`).
- Loads environment variables from `.env` by default (can be overridden). Existing environment variables take precedence over `.env`.
- Recognizes custom options and forwards their Docker Compose equivalents while preserving the order of the other arguments.
- Prefers `docker compose` (Docker CLI v2), falls back to legacy `docker-compose`.
- Works regardless of the file name (you can rename `dcj.py` and it will still function the same way).

Installation
1. Ensure Python 3.8+ and Jinja2 are available:
   pip install jinja2
2. Place `dcj.py` somewhere on your PATH (or call it directly with Python):
   chmod +x dcj.py
   # Optionally move to a directory in PATH, e.g. /usr/local/bin/ (Unix)

You could also rename the file to 'docker-compose' or create an alias, this would allow integration without having to remember a new command.

```bash
alais docker-compose='dcj'
```

Packaging as a single binary
- Install PyInstaller and build a one‑file binary:
  pip install pyinstaller
  pyinstaller --onefile dcj.py

Usage
- Run `dcj.py` instead of `docker compose`/`docker-compose`:
  ./dcj.py up -d

- If a template exists in the working directory, it will be rendered first to `docker-compose.yml` (unless overridden by `--yml-file`).
- If no `-f` is present in your arguments and a template was rendered, `dcj.py` injects `-f <rendered.yml>` so Compose uses the freshly rendered YAML.

Custom options (dcj)
- --yml-file <file>
  Render the template to `<file>` and pass the equivalent `-f <file>` to Docker Compose.
- --env-file <file>
  Load env vars from `<file>` and pass the equivalent `--env-file <file>` to Docker Compose.
- --dump
  Print the rendered YAML to stdout instead of writing a file or invoking Docker Compose. Exits after printing.
- --jdebug
  Debug mode. dcj prints detailed diagnostics (including an environment variable dump, Jinja loader info, and an in-memory render attempt) and exits without invoking Docker Compose.
- --jhelp
  Show dcj’s own help.

Help behavior
- To ask Docker Compose for its own help, run dcj with only `-h` or `--help` and no other arguments. dcj will forward the flag to Docker Compose, then print a short note that `--jhelp` is available for dcj/Jinja help.

Environment loading
- By default, `.env` in the current directory is read if present. Values already set in the process environment are NOT overridden by `.env`.
- Supported .env syntax: `KEY=VALUE` with optional spaces; quoted values (`"value"` or `'value'`) may contain `#` or `=`; inline comments after unquoted values (`KEY=value # comment`); optional `export KEY=value`; UTF‑8 with or without BOM. Variable expansion (`${VAR}`) is not performed.
- If you specify `--env-file` and the file doesn’t exist, dcj prints a warning and continues with your current environment.
- In debug modes, dcj prints how many keys were added from the .env file and dumps the full environment (be careful with secrets).

Template precedence
- If both files exist, the first match is used in this order:
  1. `docker-compose.jinja`
  2. `docker-compose.j2`

Examples
- Render default template and bring services up:
  ./dcj.py up -d

- Use different output YAML and env file:
  ./dcj.py --env-file .env.local --yml-file docker-compose.gen.yml up

- Ask Docker Compose for its help (compose help pass-through):
  ./dcj.py -h

Notes
- All arguments other than the custom options above are forwarded unchanged and in the same order to Docker Compose.
- Jinja2 is required at runtime. If it’s missing, dcj will ask you to install it.

CI: GitHub Actions builds and releases
- This repository includes a workflow at `.github/workflows/build-release.yml`.
- It builds single-file binaries for Linux, macOS, and Windows using PyInstaller.
- Triggers:
  - On tag push matching `v*` (e.g., `v1.0.0`), it builds and publishes a GitHub Release attaching the binaries.
  - Manual trigger via the Actions tab (`workflow_dispatch`). For manual runs, artifacts are uploaded to the workflow run but no release is created.
- Artifact naming: `dcj-<version>-<os>-<arch><.exe?>` (e.g., `dcj-v1.0.0-linux-x86_64`, `dcj-v1.0.0-windows-x86_64.exe`).
- How to release:
  1. Ensure `requirements.txt` contains `Jinja2` (runtime dep). The workflow installs `pyinstaller` automatically.
  2. Create and push a version tag, for example:
     git tag v1.0.0
     git push origin v1.0.0
  3. Wait for the workflow to finish. The release will appear automatically with the built assets attached.

#!/usr/bin/env python3
"""
cred-cli — Blitz Credential Manager CLI for AI Agents & Developers
Zero third-party dependencies (Python 3 stdlib only).
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_SERVER_URL = "__DEFAULT_SERVER_URL__"


def resolve_server_url(explicit_server: str, token: str) -> str:
    if explicit_server:
        return explicit_server.rstrip("/")
    env_url = os.environ.get("CRED_SERVER_URL", "")
    if env_url:
        return env_url.rstrip("/")
    if token and token.startswith("ctk_"):
        rest = token[4:]
        parts = rest.split("_", 1)
        if len(parts) == 2 and parts[0]:
            try:
                pad = "=" * (-len(parts[0]) % 4)
                decoded = base64.urlsafe_b64decode(parts[0] + pad).decode("utf-8")
                if decoded.startswith("http://") or decoded.startswith("https://"):
                    return decoded.rstrip("/")
            except Exception:
                pass
    if DEFAULT_SERVER_URL and not DEFAULT_SERVER_URL.startswith("__"):
        return DEFAULT_SERVER_URL.rstrip("/")
    return ""


def fetch_bundle(server_url: str, token: str) -> dict:
    url = f"{server_url}/api/cli/pull"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "X-Cred-Token": token,
            "User-Agent": "cred-cli/1.0.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        sys.stderr.write(f"Error (HTTP {e.code}): {body}\n")
        sys.exit(1)
    except Exception as e:
        sys.stderr.write(f"Error connecting to {server_url}: {e}\n")
        sys.exit(1)


def download_file(server_url: str, token: str, filename: str) -> bytes:
    encoded = urllib.parse.quote(filename, safe="")
    url = f"{server_url}/api/cli/file/{encoded}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "X-Cred-Token": token,
            "User-Agent": "cred-cli/1.0.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        sys.stderr.write(f"Error downloading '{filename}' (HTTP {e.code}): {body}\n")
        sys.exit(1)
    except Exception as e:
        sys.stderr.write(f"Error downloading '{filename}': {e}\n")
        sys.exit(1)


def format_env(env: dict, fmt: str) -> str:
    fmt = (fmt or "dotenv").lower()
    if fmt == "json":
        return json.dumps(env, indent=2, ensure_ascii=False) + "\n"
    if fmt in ("export", "sh", "bash"):
        lines = []
        for k, v in env.items():
            escaped = str(v).replace("'", "'\"'\"'")
            lines.append(f"export {k}='{escaped}'")
        return "\n".join(lines) + ("\n" if lines else "")
    if fmt in ("ps1", "powershell"):
        lines = []
        for k, v in env.items():
            escaped = str(v).replace("'", "''")
            lines.append(f"$env:{k} = '{escaped}'")
        return "\n".join(lines) + ("\n" if lines else "")
    # default: dotenv
    lines = []
    for k, v in env.items():
        v_str = str(v)
        if any(c in v_str for c in (" ", "\t", "\n", '"', "'", "#")):
            escaped = v_str.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
            lines.append(f'{k}="{escaped}"')
        else:
            lines.append(f"{k}={v_str}")
    return "\n".join(lines) + ("\n" if lines else "")


def main():
    raw_argv = sys.argv[1:]
    if not raw_argv or raw_argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        print(
            """
Usage:
  cred-cli list --token <TOKEN>
  cred-cli env --token <TOKEN> [--key <KEY>] [--format dotenv|json|export|ps1] [-o <FILE>]
  cred-cli file <FILENAME> --token <TOKEN> [-o <OUTPUT_PATH>] [--stdout]
  cred-cli pull-all --token <TOKEN> [--dir <DIR>]
  cred-cli run --token <TOKEN> -- <COMMAND> [ARGS...]
"""
        )
        return

    if raw_argv[0] in ("version", "--version", "-v"):
        print("cred-cli v1.0.0")
        return

    exec_cmd = []
    if raw_argv[0] in ("run", "exec") and "--" in raw_argv:
        idx = raw_argv.index("--")
        exec_cmd = raw_argv[idx + 1 :]
        raw_argv = raw_argv[:idx]

    parser = argparse.ArgumentParser(prog="cred-cli", add_help=False)
    parser.add_argument("command", help="Subcommand: list, env, file, pull-all, run")
    parser.add_argument("target", nargs="?", default="", help="Filename for 'file' command")
    parser.add_argument("--token", "-t", default=os.environ.get("CRED_TOKEN", ""), help="Temporary MCP token")
    parser.add_argument("--server", "-s", default="", help="Server URL override")
    parser.add_argument("--key", "-k", default="", help="Specific env key")
    parser.add_argument("--format", "-f", default="dotenv", help="Env output format: dotenv|json|export|ps1")
    parser.add_argument("-o", "--output", default="", help="Output file path")
    parser.add_argument("--name", default="", help="Filename to pull")
    parser.add_argument("--dir", default=".", help="Directory for pull-all")
    parser.add_argument("--stdout", action="store_true", help="Write file content to stdout")

    args, unknown = parser.parse_known_args(raw_argv)
    if args.command in ("run", "exec") and not exec_cmd:
        exec_cmd = ([args.target] if args.target else []) + unknown

    token = args.token
    if not token:
        sys.stderr.write("Error: --token <TEMP_TOKEN> is required. Obtain one via the Remote MCP 'get_temp_token' tool first.\n")
        sys.exit(1)

    server_url = resolve_server_url(args.server, token)
    if not server_url:
        sys.stderr.write("Error: Could not resolve server URL. Pass --server <URL> or use a valid ctk_ token.\n")
        sys.exit(1)

    cmd = args.command.lower()
    if cmd in ("list", "ls"):
        data = fetch_bundle(server_url, token)
        print(f"User: {data.get('username')} (Token expires: {data.get('expires_at')})\n")
        env_map = data.get("env", {})
        print(f"Environment Variables ({len(env_map)}):")
        for k in env_map:
            print(f"  - {k}")
        files = data.get("files", [])
        print(f"\nCredential Files ({len(files)}):")
        for f in files:
            print(f"  - {f.get('filename')} ({f.get('size')} bytes) {f.get('description', '')}")

    elif cmd in ("env", "pull-env"):
        data = fetch_bundle(server_url, token)
        env_map = data.get("env", {})
        if args.key:
            if args.key not in env_map:
                sys.stderr.write(f"Error: environment variable '{args.key}' not found.\n")
                sys.exit(1)
            val = str(env_map[args.key])
            if args.output:
                with open(args.output, "w", encoding="utf-8") as fp:
                    fp.write(val)
                sys.stderr.write(f"Saved {args.key} -> {args.output}\n")
            else:
                sys.stdout.write(val)
            return
        rendered = format_env(env_map, args.format)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as fp:
                fp.write(rendered)
            sys.stderr.write(f"Saved {len(env_map)} environment variables -> {args.output}\n")
        else:
            sys.stdout.write(rendered)

    elif cmd in ("file", "pull-file"):
        filename = args.target or args.name or (unknown[0] if unknown else "")
        if not filename:
            sys.stderr.write("Error: filename required. Usage: cred-cli file <FILENAME> --token <TOKEN> [-o <PATH>]\n")
            sys.exit(1)
        raw = download_file(server_url, token, filename)
        if args.stdout:
            sys.stdout.buffer.write(raw)
            return
        out_path = args.output or os.path.basename(filename)
        out_dir = os.path.dirname(out_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(out_path, "wb") as fp:
            fp.write(raw)
        print(f"Saved credential file '{filename}' ({len(raw)} bytes) -> {out_path}")

    elif cmd in ("pull-all", "pull"):
        data = fetch_bundle(server_url, token)
        os.makedirs(args.dir, exist_ok=True)
        env_map = data.get("env", {})
        if env_map:
            env_path = os.path.join(args.dir, ".env")
            with open(env_path, "w", encoding="utf-8") as fp:
                fp.write(format_env(env_map, "dotenv"))
            print(f"Saved {len(env_map)} env vars -> {env_path}")
        for f in data.get("files", []):
            fname = f["filename"]
            raw = download_file(server_url, token, fname)
            target_path = os.path.join(args.dir, os.path.basename(fname))
            with open(target_path, "wb") as fp:
                fp.write(raw)
            print(f"Saved file '{fname}' ({len(raw)} bytes) -> {target_path}")

    elif cmd in ("run", "exec"):
        if not exec_cmd:
            sys.stderr.write("Error: no command specified. Usage: cred-cli run --token <TOKEN> -- <COMMAND> [ARGS...]\n")
            sys.exit(1)
        data = fetch_bundle(server_url, token)
        child_env = os.environ.copy()
        for k, v in data.get("env", {}).items():
            child_env[str(k)] = str(v)
        res = subprocess.run(exec_cmd, env=child_env)
        sys.exit(res.returncode)

    else:
        sys.stderr.write(f"Unknown command: {args.command}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()

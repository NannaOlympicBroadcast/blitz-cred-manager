#!/usr/bin/env python3
"""
Blitz Credential Manager — Keep-Alive Script (防止 15 分钟无访问休眠)
默认每 10 分钟 (600 秒) 请求一次 /api/health，刷新 Blitz 网关的 lastTrafficAt。
"""
import argparse
import datetime
import time
import urllib.error
import urllib.request

DEFAULT_URL = "https://cred-manager.blitz48.blitz.cloud/api/health"
DEFAULT_INTERVAL = 600  # 10 minutes (Blitz sleeps after 15 minutes of inactivity)


def ping_once(url: str, timeout: int = 30) -> tuple[bool, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Blitz-KeepAlive/1.0",
            "Cache-Control": "no-cache",
        },
    )
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace").strip()
            elapsed = (time.time() - start) * 1000
            return True, f"HTTP {resp.status} ({elapsed:.0f}ms) - {body}"
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        return False, f"FAILED ({elapsed:.0f}ms) - {e}"


def main():
    parser = argparse.ArgumentParser(description="Keep Blitz Credential Manager awake.")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Health check URL (default: {DEFAULT_URL})")
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL,
        help=f"Ping interval in seconds (default: {DEFAULT_INTERVAL}s = 10min)",
    )
    parser.add_argument("--once", action="store_true", help="Send a single keep-alive ping and exit")
    parser.add_argument("--startup-delay", type=int, default=15, help="Initial delay in seconds before first ping (default: 15s)")
    args = parser.parse_args()

    if args.once:
        ok, detail = ping_once(args.url)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] KeepAlive -> {args.url} | {detail}")
        raise SystemExit(0 if ok else 1)

    print(f"[KeepAlive] Container daemon started: target={args.url} interval={args.interval}s (initial delay {args.startup_delay}s)...", flush=True)
    if args.startup_delay > 0:
        time.sleep(args.startup_delay)
    while True:
        ok, detail = ping_once(args.url)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] {'OK' if ok else 'ERR'} | {detail}", flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

"""Daemon management for the token-optimizer collector.

Provides start/stop/status commands with PID file management.
The daemon runs the collector in the background, logging to
~/.openclaw/logs/token-optimizer.log.
"""

import logging
import os
import signal
import sys
import time
from datetime import datetime

DAEMON_LOG = os.path.expanduser("~/.openclaw/logs/token-optimizer.log")
PID_FILE = os.path.expanduser("~/.openclaw/token-optimizer/collector.pid")
POLL_INTERVAL = 5.0  # seconds between log polls

logger = logging.getLogger("token-optimizer.daemon")


def _setup_logging():
    """Configure file + console logging for the daemon."""
    os.makedirs(os.path.dirname(DAEMON_LOG), exist_ok=True)
    root = logging.getLogger("token-optimizer")
    root.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    fh = logging.FileHandler(DAEMON_LOG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)


def _write_pid():
    """Write current PID to the pid file."""
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))


def _read_pid() -> int:
    """Read PID from pid file. Returns 0 if not found or invalid."""
    try:
        with open(PID_FILE, "r") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _remove_pid():
    """Remove the pid file."""
    try:
        os.remove(PID_FILE)
    except FileNotFoundError:
        pass


def _is_running(pid: int) -> bool:
    """Check if a process with the given PID is alive."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def status() -> dict:
    """Return daemon status as a dict."""
    pid = _read_pid()
    running = _is_running(pid)

    if not running and pid > 0:
        # Stale pid file
        _remove_pid()
        pid = 0

    result = {
        "running": running,
        "pid": pid if running else None,
        "pid_file": PID_FILE,
        "log_file": DAEMON_LOG,
    }

    # Add uptime info from log file
    if running and os.path.exists(DAEMON_LOG):
        try:
            with open(DAEMON_LOG, "r") as f:
                for line in f:
                    if "Collector started" in line:
                        result["started"] = line[:19]
        except (IOError, OSError):
            pass

    return result


def start(foreground: bool = False) -> str:
    """Start the collector daemon.

    Args:
        foreground: If True, run in the foreground (blocking).
                    If False, fork to background.

    Returns:
        Status message string.
    """
    pid = _read_pid()
    if _is_running(pid):
        return f"Collector already running (PID {pid})"

    # Clean up stale pid
    _remove_pid()

    if foreground:
        return _run_collector()

    # Fork to background
    try:
        child_pid = os.fork()
    except AttributeError:
        # os.fork() not available (Windows) — run in foreground
        return _run_collector()

    if child_pid > 0:
        # Parent process
        time.sleep(0.5)
        if _is_running(child_pid):
            return f"Collector started (PID {child_pid})"
        return "Collector failed to start — check " + DAEMON_LOG

    # Child process — daemonize
    os.setsid()
    # Redirect stdin/stdout/stderr
    sys.stdin = open(os.devnull, "r")
    sys.stdout = open(DAEMON_LOG, "a")
    sys.stderr = sys.stdout

    _run_collector()
    sys.exit(0)


def _run_collector() -> str:
    """Run the collector (blocking). Called by start()."""
    _setup_logging()
    _write_pid()

    # Handle graceful shutdown
    def _shutdown(signum, frame):
        logger.info("Received signal %d, shutting down", signum)
        _remove_pid()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        try:
            from .collector import Collector
        except ImportError:
            from collector import Collector

        collector = Collector()
        collector.run(interval=POLL_INTERVAL)
    except Exception as e:
        logger.error("Collector crashed: %s", e, exc_info=True)
    finally:
        _remove_pid()

    return "Collector stopped"


def stop() -> str:
    """Stop the collector daemon."""
    pid = _read_pid()
    if not _is_running(pid):
        _remove_pid()
        return "Collector is not running"

    try:
        os.kill(pid, signal.SIGTERM)
        # Wait up to 5 seconds for clean shutdown
        for _ in range(50):
            if not _is_running(pid):
                break
            time.sleep(0.1)
        else:
            os.kill(pid, signal.SIGKILL)
            time.sleep(0.2)

        _remove_pid()
        return f"Collector stopped (was PID {pid})"
    except (ProcessLookupError, PermissionError) as e:
        _remove_pid()
        return f"Error stopping collector: {e}"


def restart() -> str:
    """Restart the collector daemon."""
    stop_msg = stop()
    time.sleep(0.5)
    start_msg = start()
    return f"{stop_msg}\n{start_msg}"


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    """CLI interface: python -m token_optimizer.daemon {start|stop|status|restart}"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Token Optimizer collector daemon",
        prog="token-optimizer",
    )
    parser.add_argument(
        "command",
        choices=["start", "stop", "status", "restart"],
        help="Daemon command",
    )
    parser.add_argument(
        "--foreground", "-f",
        action="store_true",
        help="Run in foreground (don't daemonize)",
    )
    args = parser.parse_args()

    if args.command == "start":
        print(start(foreground=args.foreground))
    elif args.command == "stop":
        print(stop())
    elif args.command == "status":
        s = status()
        if s["running"]:
            print(f"Running (PID {s['pid']})")
            if s.get("started"):
                print(f"  Started: {s['started']}")
        else:
            print("Not running")
        print(f"  PID file: {s['pid_file']}")
        print(f"  Log file: {s['log_file']}")
    elif args.command == "restart":
        print(restart())


if __name__ == "__main__":
    main()

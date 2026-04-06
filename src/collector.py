"""Log collector — tails OpenClaw gateway logs and extracts API call metrics.

Supports two log formats:
1. Plain-text gateway.log lines with ClawRouter routing info:
   2026-04-02T15:16:22.977-04:00 [gateway] [MEDIUM] google/gemini-3.1-flash-lite $0.0097 ...
2. NDJSON structured logs (openclaw-YYYY-MM-DD.log) with _meta fields.

The collector watches log files, parses new lines, and inserts records into SQLite.
"""

import glob
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Dict, List, Optional

try:
    from . import database
except ImportError:
    import database

logger = logging.getLogger("token-optimizer.collector")

# Default log locations
GATEWAY_LOG = os.path.expanduser("~/.openclaw/logs/gateway.log")
NDJSON_LOG_DIR = None  # Discovered at runtime from gateway.log

# Regex for plain-text ClawRouter routing lines:
# 2026-04-02T15:16:22.977-04:00 [gateway] [MEDIUM] google/gemini-3.1-flash-lite $0.0097 (saved 94%) | score=0.24 | long (14367 tokens), ...
ROUTING_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+-\d{2}:\d{2})\s+"
    r"\[gateway\]\s+"
    r"\[(?P<complexity>[A-Z]+)\]\s+"
    r"(?P<model>\S+)\s+"
    r"\$(?P<cost>[\d.]+)\s+"
    r"(?:\(saved\s+(?P<saved_pct>\d+)%\))?\s*"
    r"(?:\|\s*score=(?P<score>[\d.]+))?\s*"
    r"(?:\|\s*(?:long|short|medium)?\s*\((?P<tokens>\d+)\s+tokens?\))?"
    r"(?P<rest>.*)"
)

# Regex for usage.cost / sessions.usage ws lines (informational, no direct cost data)
WS_RE = re.compile(
    r"^(?P<timestamp>\S+)\s+\[ws\]\s+.*\s(?P<method>usage\.cost|sessions\.usage)\s"
)


class LogTailer:
    """Tail a single log file, tracking position across reads."""

    def __init__(self, path: str):
        self.path = path
        self._inode = None
        self._offset = 0

    def read_new_lines(self) -> List[str]:
        """Read any new lines appended since last read. Handles rotation."""
        lines = []
        try:
            stat = os.stat(self.path)
        except FileNotFoundError:
            return lines

        current_inode = stat.st_ino

        # Detect log rotation (new inode) or truncation (smaller size)
        if self._inode is not None and (current_inode != self._inode or stat.st_size < self._offset):
            logger.info("Log rotated or truncated: %s (inode %s -> %s)",
                        self.path, self._inode, current_inode)
            self._offset = 0

        self._inode = current_inode

        if stat.st_size <= self._offset:
            return lines

        try:
            with open(self.path, "r", errors="replace") as f:
                f.seek(self._offset)
                for line in f:
                    stripped = line.rstrip("\n\r")
                    if stripped:
                        lines.append(stripped)
                self._offset = f.tell()
        except (IOError, OSError) as e:
            logger.warning("Error reading %s: %s", self.path, e)

        return lines


def parse_gateway_line(line: str) -> Optional[Dict]:
    """Parse a plain-text gateway routing line into a call record."""
    m = ROUTING_RE.match(line)
    if not m:
        return None

    tokens = int(m.group("tokens")) if m.group("tokens") else 0
    saved_pct = float(m.group("saved_pct")) if m.group("saved_pct") else None
    score = float(m.group("score")) if m.group("score") else None

    rest = m.group("rest") or ""
    # Extract task type hints from the rest of the line
    task_type = None
    for hint in ("code", "simple", "format", "imperative", "constraints"):
        if hint in rest.lower():
            task_type = hint
            break

    # Extract routing info (e.g., "eco", "fallback to free/gpt-oss-120b")
    routing = None
    routing_parts = []
    if "eco" in rest:
        routing_parts.append("eco")
    if "fallback" in rest:
        routing_parts.append("fallback")
    if routing_parts:
        routing = ", ".join(routing_parts)

    return {
        "timestamp": m.group("timestamp"),
        "model": m.group("model"),
        "tokens_in": tokens,
        "tokens_out": 0,  # gateway.log doesn't split in/out; tokens is total
        "cost": float(m.group("cost")),
        "saved_pct": saved_pct,
        "complexity": m.group("complexity"),
        "score": score,
        "task_type": task_type,
        "routing": routing,
        "raw_line": line[:500],
    }


def parse_ndjson_line(line: str) -> Optional[Dict]:
    """Parse an NDJSON log line. Extracts LLM-related entries only."""
    try:
        data = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None

    meta = data.get("_meta", {})
    timestamp = data.get("time") or meta.get("date", "")
    subsystem = ""
    name = meta.get("name", "")
    if isinstance(name, str) and "subsystem" in name:
        try:
            subsystem = json.loads(name).get("subsystem", "")
        except (json.JSONDecodeError, ValueError):
            subsystem = name

    # Look for gateway routing messages in field "1"
    msg = data.get("1", "")
    if not isinstance(msg, str):
        return None

    # Try to match routing pattern in the message field
    # e.g. "[MEDIUM] google/gemini-3.1-flash-lite $0.0097 (saved 94%) | ..."
    routing_in_msg = re.match(
        r"\[(?P<complexity>[A-Z]+)\]\s+"
        r"(?P<model>\S+)\s+"
        r"\$(?P<cost>[\d.]+)\s+"
        r"(?:\(saved\s+(?P<saved_pct>\d+)%\))?\s*"
        r"(?:\|\s*score=(?P<score>[\d.]+))?\s*"
        r"(?:\|\s*(?:long|short|medium)?\s*\((?P<tokens>\d+)\s+tokens?\))?",
        msg,
    )
    if routing_in_msg:
        tokens = int(routing_in_msg.group("tokens")) if routing_in_msg.group("tokens") else 0
        saved_pct = float(routing_in_msg.group("saved_pct")) if routing_in_msg.group("saved_pct") else None
        score = float(routing_in_msg.group("score")) if routing_in_msg.group("score") else None
        return {
            "timestamp": timestamp,
            "model": routing_in_msg.group("model"),
            "tokens_in": tokens,
            "tokens_out": 0,
            "cost": float(routing_in_msg.group("cost")),
            "saved_pct": saved_pct,
            "complexity": routing_in_msg.group("complexity"),
            "score": score,
            "task_type": None,
            "routing": None,
            "raw_line": line[:500],
        }

    return None


def discover_ndjson_dir() -> Optional[str]:
    """Try to discover the NDJSON log directory from gateway.log."""
    try:
        with open(GATEWAY_LOG, "r", errors="replace") as f:
            for line in f:
                if "log file:" in line:
                    # Extract path like /var/folders/.../openclaw-2026-04-05.log
                    match = re.search(r"log file:\s*(\S+)", line)
                    if match:
                        return os.path.dirname(match.group(1))
    except (IOError, OSError):
        pass
    return None


class Collector:
    """Monitors OpenClaw logs and feeds parsed records into SQLite."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or database.DB_PATH
        self.conn = database.get_connection(self.db_path)
        self._tailers: Dict[str, LogTailer] = {}
        self._ndjson_dir: Optional[str] = None
        self._setup_tailers()

    def _setup_tailers(self):
        """Initialize tailers for known log files."""
        # Always tail the main gateway log
        if os.path.exists(GATEWAY_LOG):
            self._tailers["gateway"] = LogTailer(GATEWAY_LOG)
            logger.info("Tailing gateway log: %s", GATEWAY_LOG)

        # Discover and tail NDJSON logs
        self._ndjson_dir = discover_ndjson_dir()
        if self._ndjson_dir:
            self._refresh_ndjson_tailers()

    def _refresh_ndjson_tailers(self):
        """Add tailers for any new NDJSON log files."""
        if not self._ndjson_dir:
            return
        pattern = os.path.join(self._ndjson_dir, "openclaw-*.log")
        for path in glob.glob(pattern):
            key = os.path.basename(path)
            if key not in self._tailers:
                self._tailers[key] = LogTailer(path)
                logger.info("Tailing NDJSON log: %s", path)

    def collect_once(self) -> int:
        """Read new lines from all tailed logs, parse, and store. Returns record count."""
        self._refresh_ndjson_tailers()
        batch = []

        for key, tailer in self._tailers.items():
            lines = tailer.read_new_lines()
            for line in lines:
                record = None
                if key == "gateway":
                    record = parse_gateway_line(line)
                else:
                    record = parse_ndjson_line(line)
                if record:
                    batch.append(record)

        if batch:
            count = database.insert_api_calls_batch(self.conn, batch)
            logger.debug("Inserted %d records", count)
            return count
        return 0

    def run(self, interval: float = 5.0):
        """Run the collector loop. Polls every `interval` seconds."""
        logger.info("Collector started (interval=%.1fs, db=%s)", interval, self.db_path)
        try:
            # On first run, seek to end of existing files to avoid re-processing
            # (unless the DB is empty — then process everything)
            latest = database.latest_timestamp(self.conn)
            if latest:
                logger.info("DB has data up to %s — tailing from current position", latest)
                # Advance all tailers to end-of-file
                for tailer in self._tailers.values():
                    tailer.read_new_lines()
            else:
                logger.info("Empty DB — processing all existing log data")

            while True:
                try:
                    count = self.collect_once()
                    if count > 0:
                        logger.info("Collected %d new API call records", count)
                except Exception as e:
                    logger.error("Collection error: %s", e)
                time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Collector stopped by user")
        finally:
            self.conn.close()

    def close(self):
        """Close database connection."""
        self.conn.close()

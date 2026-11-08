import json
import logging
import sys
from datetime import datetime
from pathlib import Path

_STD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys())
_STD_ATTRS.update({"message", "asctime"})


class JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.utcfromtimestamp(record.created).isoformat(timespec="seconds") + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k not in _STD_ATTRS and not k.startswith("_"):
                payload[k] = v
        return json.dumps(payload, default=str)


class ReadableFormatter(logging.Formatter):
    """One line a person can read in Render's log viewer, reasons included.

    Every event in the pipeline is logged as a short name with its reason in
    `extra={...}`: pipeline.challenge_abandoned carries how many questions
    survived and what the floor was, diagram.render_failed carries the type
    and the error. The web app never configured logging, so Python's fallback
    handler printed the bare name and threw the reason away. A customer's
    booklet shipped without a Final Challenge and the log said only
    "pipeline.challenge_abandoned", which is a fact and not a diagnosis.

    The extras are appended as key=value, after the message, so the event name
    still reads first and a search for it still finds the line.
    """

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        extras = {k: v for k, v in record.__dict__.items()
                  if k not in _STD_ATTRS and not k.startswith("_")}
        if extras:
            line += " " + " ".join(
                f"{k}={json.dumps(v, default=str)}" for k, v in extras.items())
        return line


_READABLE_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_stderr_logging(level: str | int = "INFO") -> None:
    """Send every log line to stderr with its reasons. For servers.

    Idempotent, so the web app factory and the worker can both call it, and a
    second call from a test or a reload does not double every line.
    """
    root = logging.getLogger()
    root.setLevel(level)
    for h in root.handlers:
        if getattr(h, "_folio_readable", False):
            return
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(ReadableFormatter(_READABLE_FORMAT))
    sh._folio_readable = True
    root.addHandler(sh)


def configure_logging(log_dir: Path | str = "logs") -> Path:
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_file = log_dir / f"booklet-{ts}.jsonl"

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Clear pre-existing handlers (e.g. from repeat runs in the web app).
    root.handlers.clear()

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(JsonLineFormatter())
    root.addHandler(fh)

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(ReadableFormatter(_READABLE_FORMAT))
    root.addHandler(sh)
    return log_file

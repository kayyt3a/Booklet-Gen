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
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root.addHandler(sh)
    return log_file

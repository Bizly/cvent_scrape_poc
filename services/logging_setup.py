import logging
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")
dest_var: ContextVar[str] = ContextVar("dest", default="-")


class ContextFilter(logging.Filter):
    """
    Injects trace_id and dest from contextvars into every LogRecord so the
    format string can reference them without per-call extra={...} boilerplate.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = trace_id_var.get()
        record.dest = dest_var.get()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    """
    One-time root logger config. Streams to stdout (line-buffered with
    PYTHONUNBUFFERED=1 in the Docker image) so Dozzle gets logs live.
    """
    root = logging.getLogger()
    root.setLevel(level)

    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] [trace=%(trace_id)s] [dest=%(dest)s] %(name)s: %(message)s"
        )
    )
    handler.addFilter(ContextFilter())
    root.addHandler(handler)


@contextmanager
def log_context(trace_id: Optional[str] = None, dest: Optional[str] = None):
    """
    Context manager that scopes trace_id and dest for all log lines emitted
    inside the block. Restores previous values on exit so nested scopes work.
    """
    trace_token = trace_id_var.set(trace_id) if trace_id is not None else None
    dest_token = dest_var.set(dest) if dest is not None else None
    try:
        yield
    finally:
        if dest_token is not None:
            dest_var.reset(dest_token)
        if trace_token is not None:
            trace_id_var.reset(trace_token)

import logging
from contextlib import contextmanager

import pytest
import structlog.stdlib

from app.main import _configure_logging


@contextmanager
def _isolated_root_logger():
    """Snapshot/restore root handlers + level so tests don't leak global state."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        root.handlers = []
        yield root
    finally:
        root.handlers = saved_handlers
        root.level = saved_level


def test_configure_logging_installs_single_processor_formatter_handler():
    with _isolated_root_logger() as root:
        _configure_logging()

        assert len(root.handlers) == 1, (
            f"expected exactly one root handler, got {root.handlers!r}"
        )
        handler = root.handlers[0]
        assert isinstance(handler.formatter, structlog.stdlib.ProcessorFormatter), (
            f"expected ProcessorFormatter, got {type(handler.formatter)!r}"
        )


def test_configure_logging_mutes_noisy_loggers():
    with _isolated_root_logger():
        _configure_logging()

        for name in (
            "uvicorn.access",
            "httpx",
            "httpcore",
            "telegram",
            "telegram.ext",
        ):
            assert logging.getLogger(name).level == logging.WARNING, (
                f"logger {name!r} not pinned to WARNING"
            )


def test_configure_logging_is_idempotent():
    with _isolated_root_logger() as root:
        _configure_logging()
        _configure_logging()
        _configure_logging()

        assert len(root.handlers) == 1, (
            f"configure_logging is not idempotent: {root.handlers!r}"
        )


def test_foreign_stdlib_log_renders_as_console_text(capfd: pytest.CaptureFixture[str]):
    with _isolated_root_logger():
        _configure_logging()
        # apscheduler.scheduler is a stdlib logger that propagates to root.
        logging.getLogger("apscheduler.scheduler").info("Scheduler started")

    captured = capfd.readouterr()
    # The formatted record goes to stderr by default (StreamHandler() default stream).
    line = (captured.err or captured.out).strip().splitlines()[-1]
    assert "[info     ]" in line
    assert "Scheduler started" in line
    assert "[apscheduler.scheduler]" in line

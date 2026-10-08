"""Root logger configuration, shared by the app and background workers."""

import logging

_HANDLER_ATTR = "_bulk_certgen_handler"


def configure_logging(level: str) -> None:
    """Attach a single formatted handler to the root logger and set its level.

    Idempotent: `create_app()` may be called more than once per process (e.g. once per test),
    so we tag the handler we install and skip re-adding it on subsequent calls - otherwise each
    call would duplicate every log line.
    """
    root = logging.getLogger()
    root.setLevel(level)

    already_installed = any(getattr(h, _HANDLER_ATTR, False) for h in root.handlers)
    if already_installed:
        return

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    setattr(handler, _HANDLER_ATTR, True)
    root.addHandler(handler)

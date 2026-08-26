"""Small HTTP routing seam for main-model verification."""

from api.helpers import j


def handle_main_model_post(handler, parsed) -> bool | None:
    if parsed.path != "/api/model-config/main/check":
        return False

    from api.model_config import check_main_model_connection
    from api.routes import (
        _check_csrf,
        _configuration_io_error_response,
        _csrf_rejection_error,
    )

    if not _check_csrf(handler):
        return j(handler, {"error": _csrf_rejection_error(handler)}, status=403)

    try:
        return j(handler, check_main_model_connection())
    except (RuntimeError, OSError) as exc:
        return _configuration_io_error_response(handler, exc)

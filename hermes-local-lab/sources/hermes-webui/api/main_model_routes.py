"""Small HTTP routing seam for main-model verification."""

from api.helpers import j, read_body


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
        # The unread body must not become the next HTTP/1.1 request line.
        handler.close_connection = True
        return j(handler, {"error": _csrf_rejection_error(handler)}, status=403)

    try:
        # This action has no parameters, but still owns its request body.
        read_body(handler)
    except ValueError:
        handler.close_connection = True
        return j(handler, {"error": "Invalid request body length"}, status=400)
    except OSError as exc:
        handler.close_connection = True
        return _configuration_io_error_response(handler, exc)

    try:
        return j(handler, check_main_model_connection())
    except (RuntimeError, OSError) as exc:
        return _configuration_io_error_response(handler, exc)

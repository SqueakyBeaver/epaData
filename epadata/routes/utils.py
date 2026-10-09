import json
from typing import Literal, TypeAlias

from flask import make_response
from flask.wrappers import Response


AlertType: TypeAlias = Literal[
    "primary", "secondary", "success", "danger", "warning", "info", "light", "dark"
]


def flash_message(
    message: str,
    alert_type: AlertType = "danger",
    resp: Response | None = None,
) -> Response:
    """
    Shows an auto-dismissing status message at the top of the page.
    :param message: The message to show in the status alert
    :param alert_type: Corresponds directly to the Bootstrap 5 alert variants (see: https://getbootstrap.com/docs/5.3/components/alerts)
    """
    if not resp:
        resp = make_response()

    resp.headers["HX-Trigger"] = json.dumps(
        {
            "flash": {
                "target": "#flash",
                "message": message,
                "alert_type": alert_type,
            }
        }
    )
    return resp

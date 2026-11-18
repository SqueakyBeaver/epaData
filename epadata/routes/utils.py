import io
import json
import re
from typing import Literal, TypeAlias

import pandas as pd
from flask import make_response, request
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


def table_download(df, base_name: str, metadata: dict | None = None) -> Response:
    """
     send a DataFrame as a download. The format comes from the ?format= part of the link:
    format=xlsx (or excel) gives an Excel file, anything else gives a CSV. Every download button on the
    site goes through this one function, so they all offer the same two choices.

    NEW: `metadata` (optional dict of name -> value) is how analysis exports record the filters, the
    definitions and so on, so a result can be reproduced. In Excel it becomes a second sheet called
    "Metadata"; in a CSV it becomes "# name: value" lines at the top (read it back with
    pandas.read_csv(..., comment="#")).
    """
    # keep the file name safe for the Content-Disposition header (no quotes, slashes or odd symbols)
    base = re.sub(r"[^\w.\- ()=]+", "-", base_name).strip(" .-") or "epadata"
    if request.args.get("format", "csv").lower() in ("xlsx", "excel"):
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Data")
            if metadata:  # NEW
                pd.DataFrame(
                    {"Item": list(metadata), "Value": [str(v) for v in metadata.values()]}
                ).to_excel(writer, index=False, sheet_name="Metadata")
            # Text that starts with "=" would be run as a formula by Excel (uploaded names are not trusted),
            # so every such cell is marked as plain text.
            for sheet in writer.sheets.values():  # CHANGED: every sheet, not only "Data"
                for row in sheet.iter_rows():
                    for cell in row:
                        if isinstance(cell.value, str) and cell.value[:1] in ("=", "+", "-", "@"):
                            cell.data_type = "s"
        return Response(
            buf.getvalue(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{base}.xlsx"'},
        )
    header = ""
    if metadata:  # NEW: one "# name: value" line each (line breaks inside a value become spaces)
        header = "".join(
            f"# {k}: {' '.join(str(v).split())}\n" for k, v in metadata.items()
        )
    return Response(
        header + df.to_csv(index=False),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{base}.csv"'},
    )
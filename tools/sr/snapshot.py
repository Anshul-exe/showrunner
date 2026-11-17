"""Render the dashboard's metric widgets to PNG for a fixed time window
(CloudWatch GetMetricWidgetImage). Recorded demo fallbacks (docs/demo/) come from
real runs this way: if the live demo can't run, the pictures are of the real thing.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from common import client, shared_params


def snapshot(env: str, start: datetime, end: datetime, out_dir: Path, only: str | None = None) -> list[Path]:
    cw = client("cloudwatch")
    body = json.loads(cw.get_dashboard(DashboardName=shared_params(env)["dashboard_name"])["DashboardBody"])
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for w in body["widgets"]:
        if w["type"] != "metric":
            continue
        props = dict(w["properties"])
        title = props.get("title", "widget")
        if only and only.lower() not in title.lower():
            continue
        props.update(
            start=start.astimezone(timezone.utc).isoformat(),
            end=end.astimezone(timezone.utc).isoformat(),
            width=900,
            height=360,
            timezone="+0000",
        )
        props.pop("region", None)
        props["region"] = "us-east-1"
        png = cw.get_metric_widget_image(MetricWidget=json.dumps(props), OutputFormat="png")["MetricWidgetImage"]
        name = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] + ".png"
        path = out_dir / name
        path.write_bytes(png)
        written.append(path)
    return written

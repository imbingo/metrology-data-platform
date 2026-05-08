"""Background collector worker placeholder.

In the formal version, scheduled collection should run here instead of inside
the web server process. Celery, RQ, APScheduler, or a Windows service can all
fit this boundary.
"""

from app.services.collector import collect_measurement_item


def run_once(item_id: int) -> dict:
    return collect_measurement_item(item_id)

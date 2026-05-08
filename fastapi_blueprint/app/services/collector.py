"""Collector service placeholder.

Move the proven V1.7 CSV/Excel reading logic here when upgrading from the
single-file MVP to a FastAPI + worker architecture.
"""


def collect_measurement_item(item_id: int) -> dict:
    return {
        "item_id": item_id,
        "status": "NOT_IMPLEMENTED",
        "message": "Port V1.7 collect_item logic into this service.",
    }

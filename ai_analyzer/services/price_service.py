"""Model prices for cost estimates: ai_analyzer/model_prices.json -> the model_prices table."""

import json
import logging
from pathlib import Path

from ai_analyzer.models.session import session_scope
from ai_analyzer.repositories import price_repo

log = logging.getLogger(__name__)

PRICES_FILE = Path(__file__).resolve().parent.parent / "model_prices.json"


def sync_prices() -> int:
    """Write every model's prices from the JSON file to the database. Returns how many models."""
    prices = {model: values for model, values in json.loads(PRICES_FILE.read_text()).items()
              if not model.startswith("_")}  # "_note" is documentation
    with session_scope() as session:
        for model, values in prices.items():
            price_repo.upsert(session, model, values)
    placeholders = [model for model, values in prices.items() if values.get("source") == "placeholder"]
    if placeholders:
        log.warning("model prices for %s are placeholders; set real ones in %s", placeholders, PRICES_FILE)
    return len(prices)

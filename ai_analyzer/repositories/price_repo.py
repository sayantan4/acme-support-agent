"""Queries on model_prices. Every function takes the caller's session."""

from sqlalchemy.orm import Session

from ai_analyzer.models import ModelPrice


def upsert(session: Session, model: str, prices: dict) -> None:
    """Insert or update one model's prices (keys: input_per_1m, cached_input_per_1m, output_per_1m, source)."""
    row = session.get(ModelPrice, model)
    if row is None:
        session.add(ModelPrice(model=model, **prices))
        return
    for key, value in prices.items():
        setattr(row, key, value)

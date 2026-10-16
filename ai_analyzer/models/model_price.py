"""model_prices: USD per 1M tokens for each model, so the dashboard can estimate cost in SQL.

Filled from ai_analyzer/model_prices.json (services/price_service.py). Cost is computed when the
dashboard is viewed, so correcting a price re-prices all history.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from ai_analyzer.models.base import Base, utcnow


class ModelPrice(Base):
    __tablename__ = "model_prices"

    model: Mapped[str] = mapped_column(String(64), primary_key=True)
    input_per_1m: Mapped[float] = mapped_column(Float)         # uncached input tokens
    cached_input_per_1m: Mapped[float] = mapped_column(Float)  # input tokens served from the prompt cache
    output_per_1m: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(200))           # "placeholder" until set from the pricing page
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

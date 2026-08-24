"""Shared contracts for the Equity Compass weekly opportunity pipeline."""

from finance_news.weekly.config import OpportunityConfig, load_opportunity_config
from finance_news.weekly.models import WeeklySnapshot

__all__ = ["OpportunityConfig", "WeeklySnapshot", "load_opportunity_config"]

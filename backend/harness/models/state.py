"""
Harness generic state helpers — reducers and base TypedDict patterns.
"""
import operator
from typing import Annotated


def keep_latest(_, new):
    """Reducer for scalar state keys updated by parallel branches."""
    return new

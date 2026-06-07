"""Внешние интеграции (канбан-доска и т.п.)."""

from .yougile import BoardCard, BoardClient, build_board

__all__ = ["BoardCard", "BoardClient", "build_board"]

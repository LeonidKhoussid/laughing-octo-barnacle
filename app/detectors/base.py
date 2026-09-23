"""Detector interface."""
from __future__ import annotations

import abc

from app.core.models import Detection


class Detector(abc.ABC):
    """A detector produces Detection candidates from original text."""

    detector_id: str = "base"
    detector_version: str = "0.0.0"

    @abc.abstractmethod
    def detect(self, text: str) -> list[Detection]:
        """Return detection candidates. Must not mutate the input text."""

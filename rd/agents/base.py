"""Dream Lab agent base class."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class DreamAgent(ABC):
    name: str = "agent"

    @abstractmethod
    def run(self, task: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute one agent cycle and return structured result."""

    def proposals_from_result(self, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        return list(result.get("proposals") or [])

    def envelope(self, result: Dict[str, Any], *, ok: bool = True) -> Dict[str, Any]:
        return {
            "agent": self.name,
            "ok": ok,
            "ts": time.time(),
            **result,
        }

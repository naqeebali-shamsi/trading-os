#!/usr/bin/env python3
"""Thin ADK/A2A runner wrapping AgentBrain with exported JSON schemas."""
from __future__ import annotations

from typing import Any, Dict, Optional

from cortex.agent import AgentBrain, BrainRunResult, run_brain
from cortex.agent_schemas import export_json_schemas


def agent_card() -> Dict[str, Any]:
    return export_json_schemas()["agent_card"]


class ADKBrainRunner:
    """External orchestrator entry point with stable schema exports."""

    def __init__(self, *, brain: Optional[AgentBrain] = None):
        self.brain = brain or AgentBrain()

    def schemas(self) -> Dict[str, Any]:
        return export_json_schemas()

    def decide(self, **kwargs: Any) -> BrainRunResult:
        return self.brain.run(**kwargs)


def run_decision(**kwargs: Any) -> BrainRunResult:
    return run_brain(**kwargs)

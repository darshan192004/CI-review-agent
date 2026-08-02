from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AlertPayload:
    platform: str
    incident_title: str
    root_cause: str
    resolution_steps: str

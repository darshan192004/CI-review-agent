from __future__ import annotations

FAILED_VARIANTS = frozenset({"failure", "failed", "FAILED"})
PASSED_VARIANTS = frozenset({"PASSED", "success"})


def canonical_status(status: str) -> str:
    """Map a raw stored status to its canonical UI value.

    Collapses the historical aliases so ``failure``/``failed``/``FAILED``
    unify to ``failed`` and ``PASSED``/``success`` unify to ``passed``.
    Every other status keeps its identity (lowercased), so Error and Exhausted
    stay distinct. Used by the runs filter so dropdown selections match rows
    regardless of which alias the DB stored.
    """
    if status in FAILED_VARIANTS:
        return "failed"
    if status in PASSED_VARIANTS:
        return "passed"
    return status.lower()


def is_failure(status: str) -> bool:
    """True when a raw status is a canonical CI failure (not error/exhausted)."""
    return status in FAILED_VARIANTS

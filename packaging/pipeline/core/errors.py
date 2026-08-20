"""Stable errors shared by candidate pipeline layers."""


class PipelineError(RuntimeError):
    """A stable, operator-actionable candidate pipeline failure."""

    def __init__(self, message, *, category="PIPELINE_BLOCKED"):
        super().__init__(message)
        self.category = category

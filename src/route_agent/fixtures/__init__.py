"""FYP route planning. Importing this package never performs network requests."""

__version__ = "0.1.0"


def plan_route(text, clarifications=None, context_mode="replay", snapshot_id="demo-clear", **kwargs):
    from .service import plan_route as implementation
    return implementation(text, clarifications, context_mode, snapshot_id, **kwargs)

"""RouteAgent online route-planning service."""
__version__ = "0.2.0"
def plan_route(text, clarifications=None, **kwargs):
    from .service import plan_route as implementation
    return implementation(text, clarifications, **kwargs)

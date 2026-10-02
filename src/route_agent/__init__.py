"""Real online planning; historical synthetic experiments are isolated in fixtures."""
__version__ = "0.2.0"
def plan_route(text, clarifications=None, **kwargs):
    from .service import plan_route as implementation
    return implementation(text, clarifications, **kwargs)

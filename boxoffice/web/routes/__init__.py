from . import auth, bookings, events, pages

BLUEPRINTS = (pages.blueprint, auth.blueprint, events.blueprint, bookings.blueprint)

__all__ = ["BLUEPRINTS"]

"""Plain data objects passed between the repository, service and web layers.

Repositories return these instead of raw ``sqlite3.Row`` objects so that the
column names of the schema stop at the data layer, and so the JSON shape the
frontend depends on is defined in exactly one place.
"""

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    name: str
    category: str
    venue: str
    city: str
    event_date: str
    event_time: str
    total_seats: int
    base_price: float

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Event":
        return cls(
            event_id=row["event_id"],
            name=row["name"],
            category=row["category"],
            venue=row["venue"],
            city=row["city"],
            event_date=row["event_date"],
            event_time=row["event_time"],
            total_seats=row["total_seats"],
            base_price=row["base_price"],
        )

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "name": self.name,
            "category": self.category,
            "venue": self.venue,
            "city": self.city,
            "event_date": self.event_date,
            "event_time": self.event_time,
            "total_seats": self.total_seats,
            "base_price": round(self.base_price, 2),
        }


@dataclass(frozen=True, slots=True)
class Seat:
    seat_id: str
    event_id: str
    section: str
    row_label: str
    seat_number: int
    price: float
    status: str
    booked_by_user_id: str | None
    booked_at: str | None

    @property
    def is_booked(self) -> bool:
        return self.status == "booked"

    @property
    def display_label(self) -> str:
        """What a ticket would print: section, row, seat -- e.g. "VIP 3-12"."""
        return f"{self.section} {self.row_label}-{self.seat_number}"

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Seat":
        return cls(
            seat_id=row["seat_id"],
            event_id=row["event_id"],
            section=row["section"],
            row_label=row["row_label"],
            seat_number=row["seat_number"],
            price=row["price"],
            status=row["status"],
            booked_by_user_id=row["booked_by_user_id"],
            booked_at=row["booked_at"],
        )

    def to_dict(self) -> dict:
        # "row" rather than "row_label" -- the schema name avoids a reserved
        # word, but the API speaks the language of the seating chart.
        return {
            "seat_id": self.seat_id,
            "event_id": self.event_id,
            "section": self.section,
            "row": self.row_label,
            "seat_number": self.seat_number,
            "price": round(self.price, 2),
            "status": self.status,
            "booked_by_user_id": self.booked_by_user_id,
            "label": self.display_label,
        }


@dataclass(frozen=True, slots=True)
class User:
    """An account, without any of the material used to authenticate it."""

    user_id: str
    full_name: str
    email: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "User":
        return cls(
            user_id=row["user_id"],
            full_name=row["full_name"],
            email=row["email"],
        )

    def to_dict(self) -> dict:
        # Phone and signup date stay server-side; the UI has no use for them
        # and they are the kind of field that leaks by accident.
        return {"user_id": self.user_id, "full_name": self.full_name, "email": self.email}


@dataclass(frozen=True, slots=True)
class EventStats:
    """Live occupancy figures for one event, computed in SQL."""

    total_seats: int
    booked_seats: int
    revenue: float

    @property
    def available_seats(self) -> int:
        return self.total_seats - self.booked_seats

    @property
    def occupancy_percent(self) -> float:
        if not self.total_seats:
            return 0.0
        return round(self.booked_seats / self.total_seats * 100, 1)

    def to_dict(self) -> dict:
        return {
            "total": self.total_seats,
            "booked": self.booked_seats,
            "available": self.available_seats,
            "occupancy_percent": self.occupancy_percent,
            "revenue": round(self.revenue, 2),
        }

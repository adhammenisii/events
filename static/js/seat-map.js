/**
 * The seating chart.
 *
 * Built once per event, then patched one seat at a time. A full re-render on
 * every click would be simpler, but it throws away scroll position and
 * restarts every animation on screen -- and with 200 seats it is visible.
 * So the view keeps an index from seat id to its button and touches only
 * what changed.
 */

import { formatPrice } from './format.js';

const SEAT_CLASS = {
  available: 'seat seat--available',
  taken: 'seat seat--taken',
  mine: 'seat seat--mine',
};

export class SeatMapView {
  constructor(container, { onSeatActivate }) {
    this.container = container;
    this.onSeatActivate = onSeatActivate;
    this.seatsById = new Map();
    this.buttonsById = new Map();
    this.sectionCounters = new Map();

    // One listener on the container instead of one per seat: with 200 seats
    // per event and an event switcher, per-button listeners add up.
    this.container.addEventListener('click', (event) => {
      const button = event.target.closest('.seat');
      if (button && !button.disabled) {
        this.onSeatActivate(button.dataset.seatId);
      }
    });
  }

  render(seats) {
    this.seatsById = new Map(seats.map((seat) => [seat.seat_id, seat]));
    this.buttonsById.clear();
    this.sectionCounters.clear();
    this.container.replaceChildren();

    if (seats.length === 0) {
      this.showMessage('This event has no seats configured.');
      return;
    }
    for (const [section, sectionSeats] of groupBy(seats, (seat) => seat.section)) {
      this.container.append(this.buildSectionCard(section, sectionSeats));
    }
  }

  /** Replace one seat with a newer version of itself. */
  applySeat(seat, { highlight = false } = {}) {
    const button = this.buttonsById.get(seat.seat_id);
    if (!button) return;

    this.seatsById.set(seat.seat_id, seat);
    this.dressSeatButton(button, seat);
    this.refreshSectionCount(seat.section);

    if (highlight) {
      button.classList.remove('seat--changed');
      void button.offsetWidth;
      button.classList.add('seat--changed');
    }
  }

  /**
   * Apply a freshly fetched seat list, returning how many seats moved.
   *
   * Used by the background refresh: only seats whose status or owner differs
   * from what is on screen are touched, so a poll that finds nothing new
   * costs nothing visually.
   */
  applyChanges(seats) {
    let changed = 0;
    for (const seat of seats) {
      const current = this.seatsById.get(seat.seat_id);
      if (!current) continue;
      if (current.status !== seat.status || current.mine !== seat.mine) {
        this.applySeat(seat);
        changed += 1;
      }
    }
    return changed;
  }

  setPending(seatId, isPending) {
    const button = this.buttonsById.get(seatId);
    button?.classList.toggle('seat--pending', isPending);
  }

  showMessage(text, { isError = false, actionLabel, onAction } = {}) {
    const paragraph = document.createElement('p');
    paragraph.className = isError ? 'placeholder placeholder--error' : 'placeholder';
    paragraph.textContent = text;

    if (actionLabel && onAction) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'button button--ghost';
      button.textContent = actionLabel;
      button.addEventListener('click', onAction);
      paragraph.append(document.createElement('br'), button);
    }
    this.container.replaceChildren(paragraph);
  }

  buildSectionCard(section, sectionSeats) {
    const card = document.createElement('div');
    card.className = 'section-card';

    const head = document.createElement('div');
    head.className = 'section-head';

    const name = document.createElement('span');
    name.className = 'section-name';
    name.textContent = `Section ${section}`;

    const counter = document.createElement('span');
    counter.className = 'section-count';
    head.append(name, counter);
    card.append(head);

    this.sectionCounters.set(section, { element: counter, seats: sectionSeats });

    for (const [rowLabel, rowSeats] of groupBy(sectionSeats, (seat) => seat.row)) {
      card.append(this.buildRow(rowLabel, rowSeats));
    }
    this.refreshSectionCount(section);
    return card;
  }

  buildRow(rowLabel, rowSeats) {
    const row = document.createElement('div');
    row.className = 'seat-row';

    const label = document.createElement('span');
    label.className = 'seat-row-label';
    label.textContent = rowLabel;

    const seatsWrapper = document.createElement('div');
    seatsWrapper.className = 'seat-row-seats';

    for (const seat of rowSeats) {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.seatId = seat.seat_id;
      button.textContent = seat.seat_number;
      this.dressSeatButton(button, seat);
      this.buttonsById.set(seat.seat_id, button);
      seatsWrapper.append(button);
    }

    row.append(label, seatsWrapper);
    return row;
  }

  /** Apply the styling and description that match a seat's current state. */
  dressSeatButton(button, seat) {
    const state = seat.mine ? 'mine' : seat.status === 'booked' ? 'taken' : 'available';
    button.className = SEAT_CLASS[state];
    button.disabled = state === 'taken';

    const price = formatPrice(seat.price);
    const description = {
      mine: `${seat.label} — booked by you, ${price}. Activate to cancel.`,
      taken: `${seat.label} — already booked.`,
      available: `${seat.label} — ${price}. Activate to book.`,
    }[state];

    button.title = description;
    button.setAttribute('aria-label', description);
  }

  refreshSectionCount(section) {
    const counter = this.sectionCounters.get(section);
    if (!counter) return;
    const booked = counter.seats.filter(
      (seat) => this.seatsById.get(seat.seat_id)?.status === 'booked',
    ).length;
    counter.element.textContent = `${booked}/${counter.seats.length} booked`;
  }
}

/** Group items into a Map, preserving the order they first appear in. */
function groupBy(items, keyOf) {
  const groups = new Map();
  for (const item of items) {
    const key = keyOf(item);
    const bucket = groups.get(key);
    if (bucket) {
      bucket.push(item);
    } else {
      groups.set(key, [item]);
    }
  }
  return groups;
}

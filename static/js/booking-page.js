/**
 * Booking floor controller.
 *
 * Holds the small amount of state a page like this needs -- who is signed in,
 * which event is showing, which seat is mid-request -- and wires the seat map,
 * statistics panel and toast together.
 *
 * Booking flow: mark the seat pending, send the request, then apply the seat
 * and statistics the server returned. Both come back in the booking response,
 * so a confirmed booking never needs a second round trip to be reflected on
 * screen, and the figures shown are the ones the database committed.
 */

import { api, ApiError } from './api.js';
import { formatEventDate, initialsOf } from './format.js';
import { SeatMapView } from './seat-map.js';
import { StatsPanel } from './stats-panel.js';
import { Toast } from './toast.js';

/** How often to pick up bookings made by other people, while the tab is visible. */
const REFRESH_INTERVAL_MS = 15000;

const dom = {
  eventSelect: document.getElementById('event-select'),
  accountName: document.getElementById('account-name'),
  accountInitials: document.getElementById('account-initials'),
  signOut: document.getElementById('sign-out'),
  eventMeta: document.getElementById('event-meta'),
  seatMap: document.getElementById('seat-map'),
};

const stats = new StatsPanel({
  available: document.getElementById('stat-available'),
  booked: document.getElementById('stat-booked'),
  occupancy: document.getElementById('stat-occupancy'),
  revenue: document.getElementById('stat-revenue'),
  occupancyFill: document.getElementById('occupancy-fill'),
});

const toast = new Toast(
  document.getElementById('toast'),
  document.getElementById('toast-icon'),
  document.getElementById('toast-message'),
);

const state = {
  user: null,
  events: [],
  currentEventId: null,
  pendingSeatId: null,
  refreshTimer: null,
};

const seatMap = new SeatMapView(dom.seatMap, { onSeatActivate: handleSeatActivate });

/** Any 401 means the session ended elsewhere; the page cannot recover in place. */
function redirectToLogin() {
  window.location.replace('/login');
}

function reportError(error, fallback) {
  if (error instanceof ApiError && error.isAuthFailure) {
    redirectToLogin();
    return;
  }
  toast.error(error instanceof ApiError ? error.message : fallback);
}

async function start() {
  try {
    const { user } = await api.currentSession();
    state.user = user;
    dom.accountName.textContent = user.full_name;
    dom.accountInitials.textContent = initialsOf(user.full_name);
  } catch (error) {
    redirectToLogin();
    return;
  }

  try {
    const { events } = await api.listEvents();
    state.events = events;
    populateEventPicker(events);
  } catch (error) {
    seatMap.showMessage('Could not load the event list.', {
      isError: true,
      actionLabel: 'Try again',
      onAction: () => window.location.reload(),
    });
    reportError(error, 'Could not load events.');
    return;
  }

  if (state.events.length === 0) {
    seatMap.showMessage('No events are on sale.');
    return;
  }

  await showEvent(state.events[0].event_id);
  startBackgroundRefresh();
}

function populateEventPicker(events) {
  dom.eventSelect.replaceChildren(
    ...events.map((event) => {
      const option = document.createElement('option');
      option.value = event.event_id;
      option.textContent = `${event.name} — ${event.city} · ${event.stats.available} left`;
      return option;
    }),
  );
}

async function showEvent(eventId) {
  state.currentEventId = eventId;
  state.pendingSeatId = null;
  dom.eventSelect.value = eventId;
  seatMap.showMessage('Loading seats…');
  stats.clear();

  try {
    const { event, seats, stats: eventStats } = await api.seatMap(eventId);
    // A slow response for an event the user has already navigated away from
    // must not overwrite the chart they are now looking at.
    if (state.currentEventId !== eventId) return;

    seatMap.render(seats);
    stats.update(eventStats);
    dom.eventMeta.textContent =
      `${event.venue}, ${event.city} · ${formatEventDate(event.event_date, event.event_time)}`;
  } catch (error) {
    seatMap.showMessage('Could not load this seating chart.', {
      isError: true,
      actionLabel: 'Try again',
      onAction: () => showEvent(eventId),
    });
    reportError(error, 'Could not load seats.');
  }
}

async function handleSeatActivate(seatId) {
  // One request at a time. Without this, double-clicking sends a booking and
  // a cancellation for the same seat and the second one wins at random.
  if (state.pendingSeatId) return;

  const seat = seatMap.seatsById.get(seatId);
  if (!seat) return;

  state.pendingSeatId = seatId;
  seatMap.setPending(seatId, true);

  try {
    const result = seat.mine
      ? await api.cancelBooking(seatId)
      : await api.bookSeat(state.currentEventId, seatId);

    seatMap.applySeat({ ...result.seat, mine: result.status === 'booking_successful' }, {
      highlight: true,
    });
    stats.update(result.stats);
    refreshEventPickerLabel(state.currentEventId, result.stats);
    toast.success(result.message);
  } catch (error) {
    await handleSeatFailure(error, seatId);
  } finally {
    seatMap.setPending(seatId, false);
    state.pendingSeatId = null;
  }
}

/**
 * Explain a refused booking, and correct the chart while doing it.
 *
 * Losing a race is the interesting case: the seat on screen says available
 * because it was, a moment ago. Re-reading the event puts the map back in
 * step with reality rather than leaving a seat the user can keep clicking.
 */
async function handleSeatFailure(error, seatId) {
  if (error instanceof ApiError && error.isAuthFailure) {
    redirectToLogin();
    return;
  }

  toast.error(error instanceof ApiError ? error.message : 'That did not go through.');

  const staleView = error instanceof ApiError
    && ['seat_already_booked', 'cancel_failed', 'seat_unavailable'].includes(error.code);
  if (staleView) {
    await refreshSeats({ highlightSeatId: seatId });
  }
}

/** Pull current seat state and apply only what differs from the screen. */
async function refreshSeats({ highlightSeatId = null } = {}) {
  const eventId = state.currentEventId;
  if (!eventId) return;

  try {
    const { seats, stats: eventStats } = await api.seatMap(eventId);
    if (state.currentEventId !== eventId) return;

    seatMap.applyChanges(seats);
    stats.update(eventStats);
    refreshEventPickerLabel(eventId, eventStats);
    if (highlightSeatId) {
      const seat = seatMap.seatsById.get(highlightSeatId);
      if (seat) seatMap.applySeat(seat, { highlight: true });
    }
  } catch (error) {
    // A failed background refresh is not worth interrupting anyone over; the
    // next tick will try again. An expired session still needs acting on.
    if (error instanceof ApiError && error.isAuthFailure) redirectToLogin();
  }
}

function refreshEventPickerLabel(eventId, eventStats) {
  const event = state.events.find((candidate) => candidate.event_id === eventId);
  const option = dom.eventSelect.querySelector(`option[value="${CSS.escape(eventId)}"]`);
  if (!event || !option) return;

  event.stats = eventStats;
  option.textContent = `${event.name} — ${event.city} · ${eventStats.available} left`;
}

/**
 * Poll for other people's bookings, but only while the tab is on screen.
 *
 * A backgrounded tab has nobody watching it, and browsers throttle its timers
 * anyway; stopping outright and refreshing once on return is both cheaper and
 * more accurate than a stream of delayed requests.
 */
function startBackgroundRefresh() {
  const tick = () => {
    if (document.visibilityState === 'visible' && !state.pendingSeatId) {
      refreshSeats();
    }
  };
  state.refreshTimer = setInterval(tick, REFRESH_INTERVAL_MS);

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') refreshSeats();
  });
}

dom.eventSelect.addEventListener('change', (event) => showEvent(event.target.value));

dom.signOut.addEventListener('click', async () => {
  dom.signOut.disabled = true;
  clearInterval(state.refreshTimer);
  try {
    await api.signOut();
  } catch {
    // The session may already be gone. Either way the destination is the
    // login page, so there is nothing to tell the user.
  }
  redirectToLogin();
});

start();

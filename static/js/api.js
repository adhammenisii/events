/**
 * The only place in the frontend that talks to the network.
 *
 * Every failure arrives as an ApiError with the code the server chose, so the
 * UI can branch on `seat_already_booked` rather than on a status number or a
 * message string. A lost connection is given the same shape, so callers never
 * need a separate path for "the request never left the building".
 */

export class ApiError extends Error {
  constructor(code, message, { status = 0, details = {} } = {}) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
    this.details = details;
  }

  get isAuthFailure() {
    return this.status === 401;
  }
}

const JSON_HEADERS = { 'Content-Type': 'application/json' };

async function request(path, { method = 'GET', body } = {}) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: body === undefined ? undefined : JSON_HEADERS,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: 'same-origin',
    });
  } catch (cause) {
    throw new ApiError('network_unreachable', 'Cannot reach the booking service.', {
      status: 0,
    });
  }

  // A 204, or an error page from a proxy, will not parse as JSON. Falling
  // back to null keeps the error path below in charge of the message.
  const payload = await response.json().catch(() => null);

  if (!response.ok) {
    const error = payload?.error ?? {};
    throw new ApiError(
      error.code ?? 'request_failed',
      error.message ?? `Request failed (${response.status}).`,
      { status: response.status, details: error.details ?? {} },
    );
  }
  return payload ?? {};
}

export const api = {
  currentSession: () => request('/api/session'),
  signIn: (email, password) =>
    request('/api/session', { method: 'POST', body: { email, password } }),
  register: (fullName, email, password) =>
    request('/api/accounts', { method: 'POST', body: { full_name: fullName, email, password } }),
  signOut: () => request('/api/session', { method: 'DELETE' }),
  demoAccounts: () => request('/api/demo-accounts'),

  listEvents: () => request('/api/events'),
  seatMap: (eventId) => request(`/api/events/${encodeURIComponent(eventId)}/seats`),
  eventStats: (eventId) => request(`/api/events/${encodeURIComponent(eventId)}/stats`),

  bookSeat: (eventId, seatId) =>
    request('/api/bookings', { method: 'POST', body: { event_id: eventId, seat_id: seatId } }),
  cancelBooking: (seatId) =>
    request(`/api/bookings/${encodeURIComponent(seatId)}`, { method: 'DELETE' }),
};

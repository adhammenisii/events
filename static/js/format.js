/** Presentation helpers shared by the booking page views. */

const currency = new Intl.NumberFormat(undefined, {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
});

const compactCurrency = new Intl.NumberFormat(undefined, {
  style: 'currency',
  currency: 'USD',
  notation: 'compact',
  maximumFractionDigits: 1,
});

/** Revenue can reach six figures, which will not fit in a stat tile. */
export function formatRevenue(amount) {
  return amount >= 100000 ? compactCurrency.format(amount) : currency.format(amount);
}

export function formatPrice(amount) {
  return currency.format(amount);
}

export function formatPercent(value) {
  return `${Math.round(value)}%`;
}

export function formatEventDate(isoDate, time) {
  const parsed = new Date(`${isoDate}T${time || '00:00'}`);
  if (Number.isNaN(parsed.getTime())) {
    return [isoDate, time].filter(Boolean).join(' ');
  }
  return parsed.toLocaleDateString(undefined, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }) + (time ? `, ${time}` : '');
}

/** Two-letter monogram for the account avatar. */
export function initialsOf(fullName) {
  const parts = String(fullName || '').trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  const letters = parts.length === 1 ? parts[0].slice(0, 2) : parts[0][0] + parts.at(-1)[0];
  return letters.toUpperCase();
}

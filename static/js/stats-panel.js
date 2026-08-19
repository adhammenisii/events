/**
 * The live availability figures.
 *
 * Values arrive with every booking response, so the panel never recomputes
 * anything from the seat list -- it displays what the database reported at
 * the moment the change committed. Tiles whose value actually changed are
 * flashed, which is what makes a booking made in another tab noticeable.
 */

import { formatPercent, formatRevenue } from './format.js';

export class StatsPanel {
  constructor(elements) {
    this.elements = elements;
    this.lastRendered = {};
  }

  update(stats) {
    this.applyValue('available', this.elements.available, String(stats.available));
    this.applyValue('booked', this.elements.booked, String(stats.booked));
    this.applyValue('occupancy', this.elements.occupancy, formatPercent(stats.occupancy_percent));
    this.applyValue('revenue', this.elements.revenue, formatRevenue(stats.revenue));
    this.elements.occupancyFill.style.width = `${stats.occupancy_percent}%`;
  }

  clear() {
    this.lastRendered = {};
    for (const key of ['available', 'booked', 'occupancy', 'revenue']) {
      this.elements[key].textContent = '–';
    }
    this.elements.occupancyFill.style.width = '0%';
  }

  applyValue(key, element, text) {
    if (element.textContent === text) return;

    const isFirstPaint = this.lastRendered[key] === undefined;
    element.textContent = text;
    this.lastRendered[key] = text;
    if (isFirstPaint) return;

    // Retrigger the animation: removing and re-adding the class in the same
    // frame is a no-op, so force a reflow between the two.
    element.classList.remove('stat-value--updated');
    void element.offsetWidth;
    element.classList.add('stat-value--updated');
  }
}

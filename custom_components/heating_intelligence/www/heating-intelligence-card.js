class HeatingIntelligenceCard extends HTMLElement {
  setConfig(config) {
    this._config = {
      entity: "sensor.heating_intelligence_overview",
      title: "Heating Intelligence",
      ...config,
    };
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() { return 5; }

  _fmt(value, unit = "°C") {
    return value === null || value === undefined || value === ""
      ? "—"
      : `${Number(value).toFixed(1)} ${unit}`;
  }

  _render() {
    if (!this.shadowRoot || !this._hass || !this._config) return;
    const state = this._hass.states[this._config.entity];
    const attrs = state?.attributes || {};
    const rooms = attrs.rooms || {};
    const entries = Object.values(rooms).sort((a, b) => a.name.localeCompare(b.name));
    const selected = entries.find((room) => room.id === this._selectedRoom) || entries[0];
    if (selected) this._selectedRoom = selected.id;

    const demandCount = attrs.heating_rooms ?? entries.filter((r) => r.demand).length;
    const avg = attrs.average_room_temperature;
    const water = attrs.water_temperature;
    const roomOptions = entries.map((r) =>
      `<option value="${this._esc(r.id)}" ${selected?.id === r.id ? "selected" : ""}>${this._esc(r.name)}</option>`
    ).join("");

    const roomDetail = selected ? `
      <div class="detail-grid">
        <div class="metric"><span>Room temperature</span><strong>${this._fmt(selected.temperature)}</strong></div>
        <div class="metric"><span>Heating demand</span><strong class="${selected.demand ? "active" : ""}">${selected.demand ? "Requested" : "Not requested"}</strong></div>
        <div class="metric"><span>Window</span><strong class="${selected.window_open ? "warning" : ""}">${selected.window_entity ? (selected.window_open ? "Open" : "Closed") : "Not configured"}</strong></div>
      </div>
      <div class="subtle">Demand source: ${this._esc(selected.demand_state ?? "Unavailable")}</div>
    ` : `<p class="subtle">No rooms configured yet. Add rooms in Settings → Devices & services → Heating Intelligence → Configure.</p>`;

    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; color:var(--primary-text-color); }
        ha-card { padding:16px; }
        h2 { margin:0 0 14px; font-size:1.25rem; font-weight:600; }
        .overview { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; }
        .metric { min-width:0; background:var(--secondary-background-color); border-radius:12px; padding:12px; }
        .metric span { display:block; color:var(--secondary-text-color); font-size:.78rem; margin-bottom:7px; }
        .metric strong { font-size:1.15rem; font-weight:600; overflow-wrap:anywhere; }
        .active { color:var(--success-color,#2e9d62); }
        .warning { color:var(--warning-color,#d97706); }
        .section { margin-top:18px; }
        .section-title { font-size:.9rem; font-weight:600; margin:0 0 9px; }
        select { width:100%; box-sizing:border-box; padding:11px 12px; border-radius:10px; border:1px solid var(--divider-color); color:var(--primary-text-color); background:var(--card-background-color); font:inherit; }
        .detail-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; }
        .subtle { color:var(--secondary-text-color); font-size:.78rem; margin:10px 2px 0; }
        @media (max-width:430px) { .overview,.detail-grid { grid-template-columns:1fr 1fr; } .overview .metric:last-child { grid-column:span 2; } }
      </style>
      <ha-card>
        <h2>${this._esc(this._config.title)}</h2>
        <div class="overview">
          <div class="metric"><span>Rooms requesting heat</span><strong>${demandCount} / ${entries.length}</strong></div>
          <div class="metric"><span>Average room temperature</span><strong>${this._fmt(avg)}</strong></div>
          <div class="metric"><span>Heating water inlet</span><strong>${this._fmt(water)}</strong></div>
        </div>
        <div class="section">
          <div class="section-title">Room details</div>
          ${entries.length ? `<select aria-label="Select room">${roomOptions}</select>${roomDetail}` : roomDetail}
        </div>
      </ha-card>
    `;

    const select = this.shadowRoot.querySelector("select");
    if (select) select.addEventListener("change", (event) => {
      this._selectedRoom = event.target.value;
      this._render();
    });
  }

  _esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[char]);
  }

  static getStubConfig() {
    return { entity: "sensor.heating_intelligence_overview", title: "Heating Intelligence" };
  }
}

if (!customElements.get("heating-intelligence-card")) {
  customElements.define("heating-intelligence-card", HeatingIntelligenceCard);
}
window.customCards = window.customCards || [];
window.customCards.push({
  type: "heating-intelligence-card",
  name: "Heating Intelligence",
  description: "Heating overview with selectable per-room details.",
  preview: true
});

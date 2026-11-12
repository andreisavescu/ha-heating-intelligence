class HeatingIntelligenceCard extends HTMLElement {
  setConfig(config) {
    this._config = { entity: "sensor.heating_intelligence_overview", title: "Heating Intelligence", ...config };
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._render();
  }
  set hass(hass) { this._hass = hass; this._render(); }
  getCardSize() { return 7; }
  _fmt(value, unit = "°C", digits = 1) {
    return value === null || value === undefined || value === "" ? "—" : `${Number(value).toFixed(digits)} ${unit}`;
  }
  _duration(seconds) {
    if (seconds === null || seconds === undefined) return "—";
    const mins = Math.round(Number(seconds) / 60);
    const hours = Math.floor(mins / 60);
    return hours ? `${hours}h ${mins % 60}m` : `${mins}m`;
  }
  _esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;" })[c]);
  }
  _render() {
    if (!this.shadowRoot || !this._hass || !this._config) return;
    const state = this._hass.states[this._config.entity];
    const attrs = state?.attributes || {};
    const rooms = attrs.rooms || {};
    const pumps = Object.values(attrs.pumps || {}).sort((a,b) => a.name.localeCompare(b.name));
    const entries = Object.values(rooms).sort((a,b) => a.name.localeCompare(b.name));
    const selected = entries.find((r) => r.id === this._selectedRoom) || entries[0];
    if (selected) this._selectedRoom = selected.id;
    const pumpCards = pumps.map((p) => `
      <div class="pump">
        <div class="pump-head"><strong>${this._esc(p.name)}</strong><span class="${p.running ? "active" : ""}">${p.running ? "Running" : "Stopped"}</span></div>
        <div class="pump-values">
          <div><span>Power</span><strong>${this._fmt(p.power, "W")}</strong></div>
          <div><span>Energy total</span><strong>${this._fmt(p.energy, "kWh", 2)}</strong></div>
          <div><span>Cycles</span><strong>${p.cycle_count ?? 0}</strong></div>
          <div><span>Total runtime</span><strong>${this._fmt((p.total_runtime_seconds ?? 0) / 3600, "h", 2)}</strong></div>
          <div><span>Current cycle</span><strong>${p.running ? this._duration(p.current_cycle_seconds) : "—"}</strong></div>
          <div><span>Last cycle</span><strong>${this._duration(p.last_cycle_duration_seconds)}</strong></div>
          <div><span>Energy today</span><strong>${this._fmt(p.daily_energy_kwh, "kWh", 3)}</strong></div>
          <div><span>Runtime today</span><strong>${this._fmt((p.daily_runtime_seconds ?? 0) / 3600, "h", 2)}</strong></div>
          <div><span>Cycles today</span><strong>${p.daily_cycle_count ?? 0}</strong></div>
        </div>
      </div>`).join("");
    const sync = attrs.pump_synchronization || {};
    const syncLabel = ({ synchronised:"Synchronised", out_of_sync:"Out of sync", single_pump_cycle:"Single-pump cycle (OK)", unknown:"No comparison yet" })[sync.status] || sync.status || "—";
    const syncClass = sync.status === "out_of_sync" ? "warning" : sync.status === "synchronised" || sync.status === "single_pump_cycle" ? "active" : "";
    const roomOptions = entries.map((r) => `<option value="${this._esc(r.id)}" ${selected?.id === r.id ? "selected" : ""}>${this._esc(r.name)}</option>`).join("");
    const roomDetail = selected ? `
      <div class="detail-grid">
        <div class="metric"><span>Floor</span><strong>${this._esc(selected.floor_label || "Unassigned")}</strong></div>
        <div class="metric"><span>Room temperature</span><strong>${this._fmt(selected.temperature)}</strong></div>
        <div class="metric"><span>Heating demand</span><strong class="${selected.demand ? "active" : ""}">${selected.demand ? "Requested" : "Not requested"}</strong></div>
        <div class="metric"><span>Window</span><strong class="${selected.window_open ? "warning" : ""}">${selected.window_entity ? (selected.window_open ? "Open" : "Closed") : "Not configured"}</strong></div>
        <div class="metric"><span>Demand today</span><strong>${this._fmt((selected.demand_seconds_today ?? 0) / 3600, "h", 2)}</strong></div>
        <div class="metric"><span>Demand this week</span><strong>${this._fmt((selected.demand_seconds_week ?? 0) / 3600, "h", 2)}</strong></div>
        <div class="metric"><span>Demand this month</span><strong>${this._fmt((selected.demand_seconds_month ?? 0) / 3600, "h", 2)}</strong></div>
        <div class="metric"><span>Cycles today / week / month</span><strong>${selected.cycles_today ?? 0} / ${selected.cycles_week ?? 0} / ${selected.cycles_month ?? 0}</strong></div>
        <div class="metric"><span>Average cycle this week</span><strong>${selected.average_cycle_seconds_week == null ? "—" : this._duration(selected.average_cycle_seconds_week)}</strong></div>
        <div class="metric"><span>Current cycle</span><strong>${selected.demand ? this._duration(selected.active_cycle_seconds) : "—"}</strong></div>
      </div><div class="subtle">Demand source: ${this._esc(selected.demand_state ?? "Unavailable")}. Any available state counts as demand; unavailable means no demand.</div>` :
      `<p class="subtle">No rooms configured yet.</p>`;
    this.shadowRoot.innerHTML = `
      <style>
        :host{display:block;color:var(--primary-text-color)} ha-card{padding:16px}
        h2{margin:0 0 14px;font-size:1.25rem;font-weight:600}
        .overview,.detail-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
        .metric,.pump{min-width:0;background:var(--secondary-background-color);border-radius:12px;padding:12px}
        .metric span,.pump-values span{display:block;color:var(--secondary-text-color);font-size:.78rem;margin-bottom:7px}
        .metric strong,.pump strong{font-size:1.08rem;font-weight:600;overflow-wrap:anywhere}
        .active{color:var(--success-color,#2e9d62)} .warning{color:var(--warning-color,#d97706)}
        .section{margin-top:18px}.section-title{font-size:.9rem;font-weight:600;margin:0 0 9px}
        .pumps{display:grid;grid-template-columns:1fr;gap:8px}.pump-head{display:flex;justify-content:space-between;gap:8px;margin-bottom:12px}
        .pump-values{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
        select{width:100%;box-sizing:border-box;padding:11px 12px;border-radius:10px;border:1px solid var(--divider-color);color:var(--primary-text-color);background:var(--card-background-color);font:inherit}
        .subtle{color:var(--secondary-text-color);font-size:.78rem;margin:10px 2px 0}
      </style>
      <ha-card>
        <h2>${this._esc(this._config.title)}</h2>
        <div class="overview">
          <div class="metric"><span>Rooms requesting heat</span><strong>${attrs.heating_rooms ?? entries.filter(r=>r.demand).length} / ${entries.length}</strong></div>
          <div class="metric"><span>Average room temperature</span><strong>${this._fmt(attrs.average_room_temperature)}</strong></div>
          <div class="metric"><span>Heating water inlet</span><strong>${this._fmt(attrs.water_temperature)}</strong></div>
          <div class="metric"><span>Pumps running</span><strong>${attrs.running_pumps ?? pumps.filter(p=>p.running).length} / ${pumps.length}</strong></div>
          <div class="metric"><span>Total pump power</span><strong>${this._fmt(attrs.total_pump_power, "W")}</strong></div>
          <div class="metric"><span>Combined energy total</span><strong>${this._fmt(attrs.total_pump_energy, "kWh", 2)}</strong></div>
        </div>
        <div class="section"><div class="section-title">Today's heating activity</div>
          <div class="overview">
            <div class="metric"><span>Pump energy today</span><strong>${this._fmt(attrs.daily_heating?.pump_energy_kwh, "kWh", 3)}</strong></div>
            <div class="metric"><span>Pump runtime today</span><strong>${this._fmt((attrs.daily_heating?.pump_runtime_seconds ?? 0) / 3600, "h", 2)}</strong></div>
            <div class="metric"><span>Pump cycles today</span><strong>${attrs.daily_heating?.pump_cycles ?? 0}</strong></div>
          </div>
          <div class="subtle">Daily totals reset at local midnight. Runtime is accumulated from valid running-state observations.</div>
        </div>
        <div class="section"><div class="section-title">Pump synchronisation</div>
          <div class="metric"><span>Latest result</span><strong class="${syncClass}">${this._esc(syncLabel)}</strong>
            <div class="subtle">Mismatches: ${sync.mismatch_count ?? 0} · Allowed start difference: ${sync.tolerance_seconds ?? 60}s</div>
          </div>
        </div>
        <div class="section"><div class="section-title">Manifold pumps</div><div class="pumps">${pumpCards || '<p class="subtle">Configure pump sensors in integration settings.</p>'}</div></div>
        <div class="section"><div class="section-title">Demand ↔ pump correlation</div>
          <div class="pumps">
            ${["ground_floor","upstairs"].map((id) => {
              const f = attrs.floor_analysis?.[id] || {};
              const label = id === "ground_floor" ? "Downstairs" : "Upstairs";
              const status = ({demand_without_pump:"Demand, pump stopped",pump_without_demand:"Pump running, no demand",demand_and_pump:"Demand and pump running",idle:"Idle"})[f.demand_pump_state] || "No data";
              const cls = f.demand_pump_state === "demand_without_pump" ? "warning" : f.demand_pump_state === "demand_and_pump" ? "active" : "";
              return `<div class="metric"><span>${label} · ${f.active_rooms ?? 0} rooms requesting</span><strong class="${cls}">${status}</strong><div class="subtle">Demand time today: ${this._fmt(f.demand_hours_today, "h", 2)} · Pump: ${f.pump_running ? "Running" : "Stopped"} · ${this._fmt(f.pump_power_w, "W")}</div></div>`;
            }).join("")}
          </div>
        </div>
        <div class="section"><div class="section-title">Room details</div>
          ${entries.length ? `<select aria-label="Select room">${roomOptions}</select>${roomDetail}` : roomDetail}
        </div>
      </ha-card>`;
    const select = this.shadowRoot.querySelector("select");
    if (select) select.addEventListener("change", (event) => { this._selectedRoom = event.target.value; this._render(); });
  }
  static getStubConfig() { return { entity:"sensor.heating_intelligence_overview", title:"Heating Intelligence" }; }
}
if (!customElements.get("heating-intelligence-card")) customElements.define("heating-intelligence-card", HeatingIntelligenceCard);
window.customCards = window.customCards || [];
window.customCards.push({ type:"heating-intelligence-card", name:"Heating Intelligence", description:"Heating, pump cycles and synchronisation with room details.", preview:true });

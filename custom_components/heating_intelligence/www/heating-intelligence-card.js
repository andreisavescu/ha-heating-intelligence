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
    const thermalRanking = entries.filter((r) => r.heat_retention_hours_per_degree != null)
      .sort((a,b) => b.heat_retention_hours_per_degree - a.heat_retention_hours_per_degree);
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
    const insights = attrs.insights || [];
    const insightCards = insights.length ? insights.map((item) => '<article class="insight ' + (item.severity === "warning" ? "insight-warning" : "insight-info") + '"><div class="insight-top"><span class="insight-dot"></span><strong>' + this._esc(item.title) + '</strong><span class="severity">' + (item.severity === "warning" ? "Review" : "Insight") + '</span></div><p>' + this._esc(item.detail) + '</p><small>' + this._esc(item.recommendation) + '</small></article>').join("") : '<div class="empty-insights">No operational issues detected from the available observations.</div>';
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
        <div class="metric"><span>Shortest / longest cycle</span><strong>${selected.shortest_cycle_seconds_week == null ? "—" : this._duration(selected.shortest_cycle_seconds_week)} / ${selected.longest_cycle_seconds_week == null ? "—" : this._duration(selected.longest_cycle_seconds_week)}</strong></div>
        <div class="metric"><span>Current cycle</span><strong>${selected.demand ? this._duration(selected.active_cycle_seconds) : "—"}</strong></div>
        <div class="metric"><span>Total cycles observed</span><strong>${selected.total_cycles ?? 0}</strong></div>
        <div class="metric"><span>Temperature change · 1h / 6h / 24h</span><strong>${this._fmt(selected.temperature_change_1h)} / ${this._fmt(selected.temperature_change_6h)} / ${this._fmt(selected.temperature_change_24h)}</strong></div>
        <div class="metric"><span>Heating response · +0.5°C</span><strong>${selected.heating_response_minutes == null ? "—" : this._fmt(selected.heating_response_minutes, "min")}</strong></div>
        <div class="metric"><span>Cooling rate · windows closed</span><strong>${this._fmt(selected.cooling_rate_c_per_hour, "°C/h", 2)}</strong></div>
        <div class="metric"><span>Heat retention · per 1°C</span><strong>${this._fmt(selected.heat_retention_hours_per_degree, "h/°C", 1)}</strong></div>
        <div class="metric"><span>Room floor area</span><strong>${this._fmt(selected.area_m2, "m²", 1)}</strong></div>
        <div class="metric"><span>Room efficiency score</span><strong class="${selected.score == null ? "" : selected.score >= 70 ? "active" : selected.score < 45 ? "warning" : ""}">${selected.score == null ? "Collecting data" : selected.score + " / 100"}</strong></div>
        <div class="metric"><span>Observed heat-loss indicator</span><strong>${this._fmt(selected.observed_heat_loss_c_per_hour, "°C/h", 2)}</strong></div>
        <div class="metric"><span>Temperature samples</span><strong>${selected.temperature_samples ?? selected.temperature_sample_count ?? 0}</strong></div>
        <div class="metric"><span>Window open during demand today</span><strong>${this._fmt((selected.window_open_demand_seconds_today ?? 0) / 3600, "h", 2)}</strong></div>
      </div><div class="subtle">Demand source: ${this._esc(selected.demand_state ?? "Unavailable")}. Any available state counts as demand; unavailable means no demand.</div>` :
      `<p class="subtle">No rooms configured yet.</p>`;
    this.shadowRoot.innerHTML = `
      <style>
        :host{display:block;color:var(--primary-text-color);--hi-blue:#3989d6;--hi-green:#249c70;--hi-amber:#d89127;--hi-red:#d45c52}
        ha-card{padding:14px;overflow:hidden}
        .hero{position:relative;margin:-18px -18px 16px;padding:22px 20px 19px;background:linear-gradient(120deg,rgba(41,112,184,.22),rgba(36,156,112,.12));border-bottom:1px solid var(--divider-color)}
        .hero h2{font-size:1.28rem;font-weight:750;letter-spacing:-.02em;margin:0 0 5px}
        .hero p{margin:0;color:var(--secondary-text-color);font-size:.82rem}
        .hero-line{display:flex;align-items:center;justify-content:space-between;gap:12px}
        .live-pill{white-space:nowrap;border-radius:999px;padding:5px 10px;background:rgba(36,156,112,.15);color:var(--hi-green);font-size:.72rem;font-weight:700}
        .overview,.detail-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}
        .metric,.pump{min-width:0;background:var(--secondary-background-color);border:1px solid var(--divider-color);border-radius:11px;padding:9px}
        .metric{position:relative;overflow:hidden}
        .metric:before{content:"";position:absolute;left:0;top:10px;bottom:10px;width:3px;border-radius:0 4px 4px 0;background:var(--hi-blue);opacity:.8}
        .metric span,.pump-values span{display:block;color:var(--secondary-text-color);font-size:.72rem;margin-bottom:4px}
        .metric strong,.pump strong{font-size:1.03rem;font-weight:700;overflow-wrap:anywhere;line-height:1.3}
        .active{color:var(--hi-green)} .warning{color:var(--hi-amber)} .danger{color:var(--hi-red)}
        .section{margin-top:8px;border:1px solid var(--divider-color);border-radius:12px;overflow:hidden}.section-title{display:flex;align-items:center;gap:8px;font-size:.88rem;font-weight:750;margin:0;padding:11px 12px;cursor:pointer;list-style:none}.section-title::-webkit-details-marker{display:none}.section-title:after{content:"＋";margin-left:auto;color:var(--secondary-text-color);font-weight:500}details[open]>.section-title:after{content:"−"}.section-content{padding:0 10px 10px}
        .section-title:before{content:"";width:4px;height:16px;border-radius:4px;background:var(--hi-blue)}
        .pumps{display:grid;grid-template-columns:1fr;gap:7px}.pump{background:linear-gradient(135deg,var(--secondary-background-color),var(--card-background-color))}
        .pump-head{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:12px}
        .pump-head strong{font-size:1rem}.pump-head span{font-size:.72rem;font-weight:750;padding:5px 9px;border-radius:999px;background:var(--secondary-background-color);color:var(--secondary-text-color)}
        .pump-head span.active{background:rgba(36,156,112,.14);color:var(--hi-green)}
        .pump-values{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}
        select{width:100%;box-sizing:border-box;padding:11px 12px;border-radius:11px;border:1px solid var(--divider-color);color:var(--primary-text-color);background:var(--card-background-color);font:inherit;margin-bottom:10px}
        .subtle{color:var(--secondary-text-color);font-size:.76rem;margin:10px 2px 0;line-height:1.45}
        .insights{display:grid;gap:8px}
        .insight{border-radius:13px;padding:12px 13px;border:1px solid var(--divider-color);background:var(--secondary-background-color)}
        .insight-warning{border-left:4px solid var(--hi-amber);background:color-mix(in srgb,var(--hi-amber) 9%,var(--card-background-color))}
        .insight-info{border-left:4px solid var(--hi-blue)}
        .insight-top{display:flex;align-items:center;gap:8px}
        .insight-top strong{font-size:.86rem;flex:1;line-height:1.3}
        .insight-dot{width:8px;height:8px;border-radius:50%;background:var(--hi-blue);flex:none}
        .insight-warning .insight-dot{background:var(--hi-amber)}
        .severity{font-size:.65rem;text-transform:uppercase;letter-spacing:.06em;font-weight:800;color:var(--secondary-text-color)}
        .insight p{font-size:.78rem;line-height:1.45;margin:8px 0 5px}
        .insight small{display:block;color:var(--secondary-text-color);font-size:.73rem;line-height:1.4}
        .empty-insights{padding:13px;border-radius:12px;background:rgba(36,156,112,.1);color:var(--hi-green);font-size:.8rem}
        .subhead{color:var(--secondary-text-color);font-size:.75rem;margin:-4px 0 9px}
        @media(min-width:560px){.overview,.detail-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.pumps{grid-template-columns:repeat(2,minmax(0,1fr))}}
      </style>
      <ha-card>
        <div class="hero"><div class="hero-line"><div><h2>${this._esc(this._config.title)}</h2><p>Whole-home heating performance at a glance</p></div><span class="live-pill">● LIVE MONITORING</span></div></div>
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
        <div class="section"><div class="section-title">Whole-house efficiency</div>
          <div class="overview">
            <div class="metric"><span>Outdoor temperature</span><strong>${this._fmt(attrs.efficiency?.outdoor_temperature)}</strong></div>
            <div class="metric"><span>Indoor–outdoor ΔT</span><strong>${this._fmt(attrs.efficiency?.delta_t_average)}</strong></div>
            <div class="metric"><span>Solar radiation average</span><strong>${this._fmt(attrs.efficiency?.solar_radiation_average, "W/m²", 0)}</strong></div>
            <div class="metric"><span>Heating degree days</span><strong>${this._fmt(attrs.efficiency?.heating_degree_days, "°C·d", 2)}</strong></div>
            <div class="metric"><span>Gas energy today</span><strong>${this._fmt(attrs.efficiency?.gas_energy_today_kwh, "kWh", 2)}</strong></div>
            <div class="metric"><span>Electricity today</span><strong>${this._fmt(attrs.efficiency?.electricity_energy_today_kwh, "kWh", 2)}</strong></div>
            <div class="metric"><span>Downstairs pump electricity</span><strong>${this._fmt(attrs.efficiency?.electricity_ground_floor_today_kwh, "kWh", 3)} · ${this._fmt(attrs.efficiency?.electricity_ground_floor_share_percent, "%", 1)}</strong></div>
            <div class="metric"><span>Upstairs pump electricity</span><strong>${this._fmt(attrs.efficiency?.electricity_upstairs_today_kwh, "kWh", 3)} · ${this._fmt(attrs.efficiency?.electricity_upstairs_share_percent, "%", 1)}</strong></div>
            <div class="metric"><span>Boiler pump electricity</span><strong>${this._fmt(attrs.efficiency?.electricity_boiler_today_kwh, "kWh", 3)} · ${this._fmt(attrs.efficiency?.electricity_boiler_share_percent, "%", 1)}</strong></div>
            <div class="metric"><span>Gas energy per degree day</span><strong>${this._fmt(attrs.efficiency?.gas_kwh_per_degree_day, "kWh/°C·d", 2)}</strong></div>
          </div>
          <div class="subtle">Gas correlation · HDD: ${attrs.efficiency?.gas_hdd_correlation ?? "—"} · ΔT: ${attrs.efficiency?.gas_delta_t_correlation ?? "—"} · solar: ${attrs.efficiency?.gas_solar_correlation ?? "—"}</div>
          <div class="subtle">Electricity correlation · HDD: ${attrs.efficiency?.electricity_hdd_correlation ?? "—"} · ΔT: ${attrs.efficiency?.electricity_delta_t_correlation ?? "—"} · solar: ${attrs.efficiency?.electricity_solar_correlation ?? "—"} · days sampled: ${attrs.efficiency?.days_with_data ?? 0}</div>
        </div>
        <div class="section"><div class="section-title">Operational insights</div><div class="subhead">Rule-based observations · diagnostic guidance, not automatic fault detection</div><div class="insights">${insightCards}</div></div>
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
        <div class="section"><div class="section-title">Forecasts</div>
          <div class="subtle">Explainable short-term projections · confidence reflects sample coverage and trend consistency.</div>
          <div class="overview">
            <div class="metric"><span>Rooms with forecasts</span><strong>${attrs.predictions?.rooms_with_forecasts ?? 0} / ${entries.length}</strong></div>
            <div class="metric"><span>Average confidence</span><strong>${this._fmt(attrs.predictions?.average_confidence, "%", 0)}</strong></div>
            <div class="metric"><span>Projected gas today</span><strong>${this._fmt(attrs.predictions?.energy?.gas_kwh, "kWh", 1)}</strong></div>
            <div class="metric"><span>Projected electricity today</span><strong>${this._fmt(attrs.predictions?.energy?.electricity_kwh, "kWh", 1)}</strong></div>
          </div>
          <div class="pumps">
            ${entries.map((r) => {
              const p = attrs.predictions?.rooms?.[r.id] || {};
              return `<div class="metric"><span>${this._esc(r.name)} · ${p.status === "available" ? `confidence ${p.confidence}%` : "collecting data"}</span><strong>${p.status === "available" ? `1h ${this._fmt(p.temperature_1h)} · 3h ${this._fmt(p.temperature_3h)} · 6h ${this._fmt(p.temperature_6h)}` : "Forecast unavailable"}</strong><div class="subtle">${p.time_to_comfort_minutes != null ? `Time to comfort: ${this._duration(p.time_to_comfort_minutes * 60)} · ` : ""}${p.time_below_comfort_minutes != null ? `Below comfort in: ${this._duration(p.time_below_comfort_minutes * 60)} · ` : ""}${this._esc(p.note || "")}</div></div>`;
            }).join("")}
          </div>
          <div class="subtle">${this._esc(attrs.predictions?.energy?.note || "Energy forecast unavailable.")}</div>
        </div>
        <div class="section"><div class="section-title">Room details</div>
          ${entries.length ? `<select aria-label="Select room">${roomOptions}</select>${roomDetail}` : roomDetail}
        </div>
        <div class="section"><div class="section-title">Room heat-retention comparison</div>
          <div class="pumps">
            ${thermalRanking.length ? thermalRanking.map((r) => `<div class="metric"><span>${this._esc(r.name)}</span><strong>${this._fmt(r.heat_retention_hours_per_degree, "h/°C", 1)} · cooling ${this._fmt(r.cooling_rate_c_per_hour, "°C/h", 2)}</strong></div>`).join("") : '<p class="subtle">Waiting for enough closed-window cooling observations.</p>'}
          </div>
        </div>
        <div class="section"><div class="section-title">Advanced diagnostics</div>
          <div class="overview">
            ${entries.map((r) => `<div class="metric"><span>${this._esc(r.name)} · efficiency</span><strong>${r.score == null ? "Collecting data" : r.score + " / 100"}</strong><div class="subtle">${r.score_status === "available" ? Object.entries(r.score_components || {}).map(([k,v]) => `${this._esc(k.replaceAll("_"," "))}: ${v}`).join(" · ") : "At least two valid metrics and 12 temperature samples are required."}</div></div>`).join("")}
          </div>
          <div class="overview" style="margin-top:9px">
            ${Object.entries(attrs.advanced_diagnostics?.floor_area_analysis || {}).map(([floor, v]) => `<div class="metric"><span>${this._esc(floor.replaceAll("_"," "))} · configured area</span><strong>${this._fmt(v.configured_area_m2, "m²", 1)}</strong><div class="subtle">Area-weighted observed cooling: ${this._fmt(v.area_weighted_observed_cooling_c_per_hour, "°C/h", 2)} · ${v.rooms_with_area_and_cooling_data} rooms</div></div>`).join("")}
            <div class="metric"><span>Gas per degree day</span><strong>${this._fmt(attrs.advanced_diagnostics?.boiler_analysis?.gas_kwh_per_degree_day, "kWh/°C·d", 2)}</strong></div>
            <div class="metric"><span>Gas / electricity ratio</span><strong>${this._fmt(attrs.advanced_diagnostics?.boiler_analysis?.gas_to_electricity_ratio, "ratio", 2)}</strong></div>
            <div class="metric"><span>Historical days sampled</span><strong>${attrs.advanced_diagnostics?.diagnostic_data_days ?? 0}</strong></div>
          </div>
          <div class="subtle">${this._esc(attrs.advanced_diagnostics?.boiler_analysis?.note || "Boiler analysis unavailable.")}</div>
          <div class="insights" style="margin-top:9px">
            ${(attrs.advanced_diagnostics?.anomalies || []).length ? attrs.advanced_diagnostics.anomalies.map((item) => `<article class="insight ${item.severity === "warning" ? "insight-warning" : "insight-info"}"><div class="insight-top"><strong>${this._esc(item.title)}</strong><span class="severity">${this._esc(item.severity)}</span></div><p>${this._esc(item.detail)}</p></article>`).join("") : '<div class="empty-insights">No historical anomalies detected with the available baseline.</div>'}
          </div>
          <div class="subtle">Scores are comparative heuristics, not building energy ratings. Cooling rate is an observed °C/h indicator, not a physical heat-loss coefficient. Boiler combustion efficiency requires measured heat output.</div>
        </div>
      </ha-card>`;
    // Convert long analytical sections into compact, accessible disclosure panels.
    this.shadowRoot.querySelectorAll(".section").forEach((section) => {
      const heading = section.querySelector(".section-title");
      if (!heading) return;
      const disclosure = document.createElement("details");
      disclosure.className = "section";
      const summary = document.createElement("summary");
      summary.className = "section-title";
      summary.textContent = heading.textContent;
      const content = document.createElement("div");
      content.className = "section-content";
      [...section.children].forEach((child) => {
        if (child !== heading) content.appendChild(child);
      });
      disclosure.append(summary, content);
      section.replaceWith(disclosure);
      if (summary.textContent === "Forecasts" || summary.textContent === "Room details") disclosure.open = true;
    });
    const select = this.shadowRoot.querySelector("select");
    if (select) select.addEventListener("change", (event) => { this._selectedRoom = event.target.value; this._render(); });
  }
  static getStubConfig() { return { entity:"sensor.heating_intelligence_overview", title:"Heating Intelligence" }; }
}
if (!customElements.get("heating-intelligence-card")) customElements.define("heating-intelligence-card", HeatingIntelligenceCard);
window.customCards = window.customCards || [];
window.customCards.push({ type:"heating-intelligence-card", name:"Heating Intelligence", description:"Heating, pump cycles and synchronisation with room details.", preview:true });

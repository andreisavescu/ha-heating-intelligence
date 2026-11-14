class HeatingIntelligenceCard extends HTMLElement {
  setConfig(config) {
    this._config = { entity: "sensor.heating_intelligence_overview", title: "Heating Intelligence", ...config };
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    if (!this._focusHandlerInstalled) {
      this.shadowRoot.addEventListener("focusout", () => queueMicrotask(() => {
        if (this._pendingRender && !this.shadowRoot.activeElement?.matches("select, .info-button")) { this._pendingRender = false; this._render(); }
      }));
      this._focusHandlerInstalled = true;
    }
    this._render();
  }
  set hass(hass) { this._hass = hass; if (this.shadowRoot?.activeElement?.matches("select, .info-button")) { this._pendingRender = true; return; } this._render(); }
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
  _info(label, description) {
    return `<button type="button" class="info-button" aria-label="About ${this._esc(label)}" aria-expanded="false" data-info-key="${this._esc(label)}" data-info="${this._esc(description)}">i</button>`;
  }
  _metricHelp(label) {
    const help = {
      "Gas energy per degree day":"Daily gas energy divided by heating degree days. HDD = max(0, 18°C − daily mean outdoor temperature). Lower values can indicate less gas for comparable outdoor heating demand, but solar gains, occupancy, indoor setpoint and thermal storage also affect it. This is a daily comparison indicator, not boiler efficiency.",
      "Whole-house efficiency":"A group of measured energy and thermal indicators, not one certified efficiency rating. It combines gas use, pump electricity, weather and temperature observations. It does not measure useful heat delivered or boiler combustion efficiency.",
      "Heating degree days":"A weather-demand indicator calculated as max(0, 18°C − average outdoor temperature) for the day. It estimates how far outdoor conditions are below the chosen 18°C base; it is not a measurement of heat delivered.",
      "Indoor–outdoor ΔT":"Difference between the average indoor room temperature and the configured indoor–outdoor temperature-difference sensor. Larger differences generally mean a greater temperature gradient through the building envelope.",
      "Solar radiation average":"Average of the configured solar-radiation sensor observations collected by this integration. Solar gains can reduce heating demand, but sensor orientation, shading and units affect interpretation.",
      "Gas correlation":"Pearson correlation between daily gas energy and the named variable. Values range from −1 to +1; magnitude describes association, not causation. At least three paired days are required and small samples are unstable.",
      "Electricity correlation":"Pearson correlation between daily pump electricity and the named variable. It describes association only, not causation, and requires at least three paired days.",
      "Room efficiency score":"A heuristic 0–100 diagnostic score assembled from available room metrics. It is not a measured heating efficiency, energy rating or physical heat-loss coefficient. Inspect the component contributions before comparing rooms.",
      "Observed heat-loss indicator":"Observed room temperature cooling rate during no-demand periods with closed windows, in °C per hour. It is a temperature-decay indicator, not heat loss in watts or W/K.",
      "Heat retention · per 1°C":"An estimate derived from the observed closed-window cooling rate: hours for the room temperature to fall by 1°C if the observed rate continued. It is not a fixed material property.",
      "Cooling rate · windows closed":"Temperature decrease per hour during periods without heating demand and with the window reported closed. Solar gains, internal gains, ventilation and sensor noise can influence it.",
      "Heating response · +0.5°C":"Average observed time for the room temperature to rise by 0.5°C during a heating-demand period. It depends on starting conditions and concurrent heat gains/losses.",
      "Pump synchronisation":"Compares observed pump-cycle start times against the configured tolerance. A mismatch is a timing observation, not proof of a pump or control fault.",
      "Demand ↔ pump correlation":"Compares room demand on a floor with that floor's manifold-pump running state. Brief delays and the coordinator sampling interval can create apparent mismatches.",
      "Average confidence":"A heuristic indicator based on recent sample coverage and consistency. It is not a statistical probability that the forecast will be correct.",
      "Weather-normalised gas consumption":"Regression estimate comparing actual gas with gas expected under reference weather conditions. It adjusts for HDD, indoor–outdoor ΔT and solar radiation; it is a comparative index, not a physical heat-loss or boiler-efficiency measurement.",
      "Floor thermal performance":"Area-weighted room observations. Only rooms with configured floor area and valid readings contribute; the coverage percentage is the share of rooms with area configured, not the share of total floor area."
    };
    return help[label] || "This value is calculated from the observations available to Heating Intelligence. It is descriptive and may be unavailable until sufficient valid history has been collected.";
  }
  _demandHeatmap(rooms) {
    const now=Date.now(), dayMs=86400000, start=now-7*dayMs;
    const days=Array.from({length:7},(_,i)=>new Date(start+i*dayMs));
    const cells=rooms.map(room=>{
      const hours=Array(7).fill(0).map(()=>Array(24).fill(0));
      for(const cycle of room.demand_history||[]){
        const a=Date.parse(cycle.start),b=Date.parse(cycle.end);
        if(!Number.isFinite(a)||!Number.isFinite(b)||b<=a)continue;
        const from=Math.max(a,start),to=Math.min(b,now);
        for(let t=from;t<to;){
          const d=new Date(t), day=Math.floor((Date.UTC(d.getFullYear(),d.getMonth(),d.getDate())-Date.UTC(days[0].getFullYear(),days[0].getMonth(),days[0].getDate()))/dayMs);
          const nextHour=new Date(d.getFullYear(),d.getMonth(),d.getDate(),d.getHours()+1).getTime();
          const stop=Math.min(to,nextHour);
          if(day>=0&&day<7)hours[day][d.getHours()]+=Math.max(0,stop-t)/60000;
          t=stop;
        }
      }
      const total=hours.flat().reduce((a,b)=>a+b,0);
      return `<div class="heatmap-room"><strong>${this._esc(room.name)}</strong><div class="heatmap-grid">${hours.map((row,d)=>row.map((minutes,h)=>{const intensity=Math.min(1,minutes/60);return `<span class="heat-cell" style="--heat:${intensity.toFixed(2)}" title="${days[d].toLocaleDateString()} ${String(h).padStart(2,"0")}:00 · ${Math.round(minutes)} min"></span>`;}).join("")).join("")}</div><small>${this._duration(total*60)} demand</small></div>`;
    }).join("");
    return `<div class="heatmap-labels"><span>Day / hour</span><span>00 → 23</span></div>${cells||'<div class="chart-empty">No room history available.</div>'}`;
  }
  _historyComparisons(history) {
    const rows=(history||[]).filter(d=>d.date&&d.date<new Date().toLocaleDateString("en-CA"));
    const sum=(items,key)=>items.reduce((n,d)=>n+(Number(d[key])||0),0);
    const last=rows.slice(-14), current=last.slice(-7), previous=last.slice(0,7);
    const compare=(key)=>{const a=sum(current,key),b=sum(previous,key);return {a,b,delta:b?((a-b)/b*100):null};};
    const gas=compare("gas_kwh"), electric=compare("electricity_kwh"), normal=compare("weather_normalised_gas_kwh");
    const card=(name,v,unit)=>`<div class="metric"><span>${name}</span><strong>${v.delta==null?"Need 14 days":`${v.a.toFixed(1)} vs ${v.b.toFixed(1)} ${unit} · ${v.delta>0?"+":""}${v.delta.toFixed(1)}%`}</strong><small class="subtle">Latest 7 days vs preceding 7 days</small></div>`;
    return `<div class="overview">${card("Gas consumption",gas,"kWh")}${card("Pump electricity",electric,"kWh")}${normal.a&&normal.b?card("Weather-normalised gas",normal,"kWh"):'<div class="metric"><span>Weather-normalised gas</span><strong>Collecting model data</strong><small class="subtle">Available after the weather model has enough complete days.</small></div>'}</div>`;
  }
  _temperatureChart(room, prediction, comfortTarget) {
    const samples = (room.temperature_history || []).filter((item) =>
      item && Number.isFinite(Number(item.temperature)) && Number.isFinite(Date.parse(item.time))
    ).slice(-145);
    if (samples.length < 2) return '<div class="chart-empty">Temperature history is building up. At least two readings are needed.</div>';
    const now = Date.now();
    const start = now - 24 * 3600000;
    const end = now + 12 * 3600000;
    const actual = samples.filter((p) => Date.parse(p.time) >= start).map((p) => ({x: Date.parse(p.time), y: Number(p.temperature)}));
    if (actual.length < 2) return '<div class="chart-empty">Not enough readings from the last 24 hours yet.</div>';
    const future = [];
    if (prediction?.status === "available") {
      const latest = actual[actual.length - 1];
      future.push(latest);
      for (const hours of [1, 3, 6, 12]) {
        const value = prediction[`temperature_${hours}h`];
        if (Number.isFinite(Number(value))) future.push({x: now + hours * 3600000, y: Number(value)});
      }
    }
    const all = [...actual, ...future];
    const minY = Math.min(...all.map(p => p.y), Number(comfortTarget) - 0.8) - 0.35;
    const maxY = Math.max(...all.map(p => p.y), Number(comfortTarget) + 0.8) + 0.35;
    const x = t => 36 + (t - start) / (end - start) * 344;
    const y = v => 116 - (v - minY) / Math.max(0.1, maxY - minY) * 94;
    const line = points => points.map(p => `${x(p.x).toFixed(1)},${y(p.y).toFixed(1)}`).join(" ");
    const targetY = y(Number(comfortTarget));
    const demandBands = actual.map((point, index) => { const sample = samples.find(item => Date.parse(item.time) === point.x); if (!sample?.demand) return ""; const next = actual[index + 1]?.x ?? Math.min(now, point.x + 600000); const left = Math.max(start, point.x), right = Math.min(now, next); return right > left ? `<rect x="${x(left).toFixed(1)}" y="22" width="${Math.max(1, x(right)-x(left)).toFixed(1)}" height="94" class="demand-band"/>` : ""; }).join("");
    const actualLine = line(actual);
    const forecastLine = future.length > 1 ? line(future) : "";
    return `<div class="chart-wrap"><div class="chart-heading"><strong>Room temperature · 24h history + 12h forecast</strong><span>°C</span></div>
      <svg viewBox="0 0 400 146" role="img" aria-label="Room temperature history and forecast">
        <line x1="36" y1="22" x2="380" y2="22" class="chart-grid"/><line x1="36" y1="69" x2="380" y2="69" class="chart-grid"/><line x1="36" y1="116" x2="380" y2="116" class="chart-grid"/>
        ${demandBands}<line x1="36" y1="${targetY.toFixed(1)}" x2="380" y2="${targetY.toFixed(1)}" class="chart-target"/>
        <polyline points="${actualLine}" class="chart-actual"/>
        ${forecastLine ? `<polyline points="${forecastLine}" class="chart-forecast"/>` : ""}
        <text x="3" y="25" class="chart-label">${maxY.toFixed(1)}</text><text x="3" y="72" class="chart-label">${((maxY+minY)/2).toFixed(1)}</text><text x="3" y="119" class="chart-label">${minY.toFixed(1)}</text>
        <text x="36" y="138" class="chart-label">−24h</text><text x="208" y="138" text-anchor="middle" class="chart-label">Now</text><text x="380" y="138" text-anchor="end" class="chart-label">+12h</text>
      </svg><div class="chart-legend"><span><i class="legend-actual"></i>Measured</span><span><i class="legend-forecast"></i>Forecast</span><span><i class="legend-target"></i>Target ${Number(comfortTarget).toFixed(1)}°C</span><span><i class="legend-demand"></i>Heat demand</span></div></div>`;
  }
  _demandTimeline(rooms, hours=24) {
    const end=Date.now(), start=end-hours*3600000, width=720;
    const rows=rooms.map(room=>{
      const bars=(room.demand_history||[]).map(item=>{
        const a=Date.parse(item.start), b=Date.parse(item.end);
        if(!Number.isFinite(a)||!Number.isFinite(b)||b<=start||a>=end)return "";
        const x=Math.max(0,(Math.max(a,start)-start)/(end-start)*width), right=Math.min(width,(Math.min(b,end)-start)/(end-start)*width);
        return `<rect x="${x.toFixed(1)}" y="3" width="${Math.max(1,right-x).toFixed(1)}" height="20" rx="5" class="${item.active?"demand-active":"demand-complete"}"><title>${this._esc(new Date(a).toLocaleString())} – ${this._esc(new Date(b).toLocaleString())}</title></rect>`;
      }).join("");
      return `<div class="demand-row"><div class="demand-room"><strong>${this._esc(room.name)}</strong><span class="demand-status ${room.demand?"active":""}">${room.demand?"Requesting":"Idle"}</span></div><svg viewBox="0 0 ${width} 26" preserveAspectRatio="none" role="img" aria-label="Demand timeline for ${this._esc(room.name)}"><line x1="0" y1="13" x2="${width}" y2="13" class="demand-track"/>${bars}</svg></div>`;
    }).join("");
    const ticks=hours===24?["−24h","−18h","−12h","−6h","Now"]:["−7d","−5d","−3d","−1d","Now"];
    return `<div class="demand-axis">${ticks.map(t=>`<span>${t}</span>`).join("")}</div>${rows||'<div class="chart-empty">No rooms configured.</div>'}`;
  }
  _energyChart(days) {
    const rows = (days || []).filter(d => Number.isFinite(Number(d.gas_kwh)) || Number.isFinite(Number(d.electricity_kwh)));
    if (rows.length < 2) return '<div class="chart-empty">Daily meter history is building up. At least two days are needed.</div>';
    const max = Math.max(1, ...rows.flatMap(d => [Number(d.gas_kwh) || 0, Number(d.electricity_kwh) || 0]));
    const width = 360 / rows.length;
    const bars = rows.map((d, i) => {
      const gx = 36 + i * width + width * .12, ex = gx + width * .34;
      const gh = (Number(d.gas_kwh) || 0) / max * 78, eh = (Number(d.electricity_kwh) || 0) / max * 78;
      const label = String(d.date || "").slice(5);
      return `<rect x="${gx.toFixed(1)}" y="${(98-gh).toFixed(1)}" width="${Math.max(2,width*.27).toFixed(1)}" height="${gh.toFixed(1)}" rx="2" class="bar-gas"><title>${this._esc(label)} gas: ${(Number(d.gas_kwh)||0).toFixed(2)} kWh</title></rect><rect x="${ex.toFixed(1)}" y="${(98-eh).toFixed(1)}" width="${Math.max(2,width*.27).toFixed(1)}" height="${eh.toFixed(1)}" rx="2" class="bar-electric"><title>${this._esc(label)} electricity: ${(Number(d.electricity_kwh)||0).toFixed(2)} kWh</title></rect>${i % Math.max(1,Math.ceil(rows.length/7)) === 0 ? `<text x="${(gx+width*.25).toFixed(1)}" y="116" text-anchor="middle" class="chart-label">${this._esc(label)}</text>` : ""}`;
    }).join("");
    return `<div class="chart-wrap"><div class="chart-heading"><strong>Daily energy · last ${rows.length} days</strong><span>kWh</span></div><svg viewBox="0 0 400 128" role="img" aria-label="Daily gas and electricity consumption"><line x1="34" y1="20" x2="394" y2="20" class="chart-grid"/><line x1="34" y1="59" x2="394" y2="59" class="chart-grid"/><line x1="34" y1="98" x2="394" y2="98" class="chart-grid"/>${bars}</svg><div class="chart-legend"><span><i class="legend-gas"></i>Gas</span><span><i class="legend-electric"></i>Electricity</span></div></div>`;
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
        <div class="metric"><span>Relative humidity</span><strong>${selected.humidity == null ? "Not configured" : this._fmt(selected.humidity, "%", 0) + " · " + (selected.humidity_status || "—")}</strong></div>
        <div class="metric"><span>CO₂ concentration</span><strong>${selected.co2_entity ? (selected.co2_ppm == null ? "Unavailable" : this._fmt(selected.co2_ppm, "ppm", 0) + " · " + (selected.co2_status || "—")) : "Not configured"}</strong></div>
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
        <div class="metric"><span>Heating response · +0.5°C ${this._info("Heating response · +0.5°C", this._metricHelp("Heating response · +0.5°C"))}</span><strong>${selected.heating_response_minutes == null ? "—" : this._fmt(selected.heating_response_minutes, "min")}</strong></div>
        <div class="metric"><span>Cooling rate · windows closed ${this._info("Cooling rate · windows closed", this._metricHelp("Cooling rate · windows closed"))}</span><strong>${this._fmt(selected.cooling_rate_c_per_hour, "°C/h", 2)}</strong></div>
        <div class="metric"><span>Heat retention · per 1°C ${this._info("Heat retention · per 1°C", this._metricHelp("Heat retention · per 1°C"))}</span><strong>${this._fmt(selected.heat_retention_hours_per_degree, "h/°C", 1)}</strong></div>
        <div class="metric"><span>Room floor area</span><strong>${this._fmt(selected.area_m2, "m²", 1)}</strong></div>
        <div class="metric"><span>Room efficiency score ${this._info("Room efficiency score", this._metricHelp("Room efficiency score"))}</span><strong class="${selected.score == null ? "" : selected.score >= 70 ? "active" : selected.score < 45 ? "warning" : ""}">${selected.score == null ? "Collecting data" : selected.score + " / 100"}</strong></div>
        <div class="metric"><span>Observed heat-loss indicator ${this._info("Observed heat-loss indicator", this._metricHelp("Observed heat-loss indicator"))}</span><strong>${this._fmt(selected.observed_heat_loss_c_per_hour, "°C/h", 2)}</strong></div>
        <div class="metric"><span>Temperature samples</span><strong>${selected.temperature_samples ?? selected.temperature_sample_count ?? 0}</strong></div>
        <div class="metric"><span>Window open during demand today</span><strong>${this._fmt((selected.window_open_demand_seconds_today ?? 0) / 3600, "h", 2)}</strong></div>
      </div>${this._temperatureChart(selected, attrs.predictions?.rooms?.[selected.id], attrs.predictions?.comfort_target_c ?? 21)}<div class="subtle">Demand source: ${this._esc(selected.demand_state ?? "Unavailable")}. Any available state counts as demand; unavailable means no demand.</div>` :
      `<p class="subtle">No rooms configured yet.</p>`;
    const openSections = new Set(
      [...this.shadowRoot.querySelectorAll("details[open] > summary")]
        .map((summary) => summary.textContent)
    );
    this.shadowRoot.innerHTML = `
      <style>
        :host{display:block;color:var(--primary-text-color);--hi-blue:#3989d6;--hi-green:#249c70;--hi-amber:#d89127;--hi-red:#d45c52}
        ha-card{padding:14px;overflow:hidden}
        .hero{position:relative;margin:-18px -18px 9px;padding:14px 16px 12px;background:linear-gradient(120deg,rgba(41,112,184,.22),rgba(36,156,112,.12));border-bottom:1px solid var(--divider-color)}
        .hero h2{font-size:1.28rem;font-weight:750;letter-spacing:-.02em;margin:0 0 5px}
        .hero p{margin:0;color:var(--secondary-text-color);font-size:.82rem}
        .hero-line{display:flex;align-items:center;justify-content:space-between;gap:12px}
        .live-pill{white-space:nowrap;border-radius:999px;padding:5px 10px;background:rgba(36,156,112,.15);color:var(--hi-green);font-size:.72rem;font-weight:700}
        .overview,.detail-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px}
        .metric,.pump{min-width:0;background:var(--secondary-background-color);border:1px solid var(--divider-color);border-radius:8px;padding:6px 7px}
        .metric{position:relative;overflow:hidden}
        .metric:before{content:"";position:absolute;left:0;top:10px;bottom:10px;width:3px;border-radius:0 4px 4px 0;background:var(--hi-blue);opacity:.8}
        .metric span,.pump-values span{display:block;color:var(--secondary-text-color);font-size:.72rem;margin-bottom:4px}
        .metric strong,.pump strong{font-size:1.03rem;font-weight:700;overflow-wrap:anywhere;line-height:1.3}
        .active{color:var(--hi-green)} .warning{color:var(--hi-amber)} .danger{color:var(--hi-red)}
        .info-button{display:inline-grid;place-items:center;flex:0 0 17px;width:17px;height:17px;padding:0;border:1px solid var(--secondary-text-color);border-radius:50%;background:transparent;color:var(--secondary-text-color);font:700 11px/1 sans-serif;cursor:pointer;vertical-align:middle}.info-button:focus-visible{outline:2px solid var(--primary-color);outline-offset:2px}.info-popover{display:none;margin-top:5px;padding:8px 10px;border-left:3px solid var(--hi-blue);border-radius:5px;background:var(--card-background-color);color:var(--secondary-text-color);font-size:.75rem;line-height:1.45}.info-popover.open{display:block}.heatmap-room{display:grid;grid-template-columns:78px minmax(0,1fr) 65px;align-items:center;gap:6px;padding:5px 0;border-bottom:1px solid var(--divider-color)}.heatmap-room strong{font-size:.72rem;overflow-wrap:anywhere}.heatmap-room small{font-size:.65rem;color:var(--secondary-text-color);text-align:right}.heatmap-grid{display:grid;grid-template-columns:repeat(24,minmax(2px,1fr));gap:2px}.heat-cell{height:12px;border-radius:2px;background:color-mix(in srgb,var(--hi-green) calc(var(--heat)*100%),var(--divider-color));opacity:calc(.35 + var(--heat)*.65)}.heatmap-labels{display:flex;justify-content:space-between;color:var(--secondary-text-color);font-size:.65rem;margin:6px 0}.comparison-heading{font-size:.75rem;color:var(--secondary-text-color);margin:4px 0 7px}.section{margin-top:4px;border:1px solid var(--divider-color);border-radius:12px;overflow:hidden}.section-title{display:flex;align-items:center;gap:6px;font-size:.82rem;font-weight:750;margin:0;padding:7px 9px;cursor:pointer;list-style:none}.section-title::-webkit-details-marker{display:none}.section-title:after{content:"＋";margin-left:auto;color:var(--secondary-text-color);font-weight:500}details[open]>.section-title:after{content:"−"}.section-content{padding:0 7px 7px}
        .section-title:before{content:"";width:4px;height:16px;border-radius:4px;background:var(--hi-blue)}
        .pumps{display:grid;grid-template-columns:1fr;gap:4px}.pump{background:linear-gradient(135deg,var(--secondary-background-color),var(--card-background-color))}
        .pump-head{display:flex;justify-content:space-between;align-items:center;gap:6px;margin-bottom:6px}
        .pump-head strong{font-size:1rem}.pump-head span{font-size:.72rem;font-weight:750;padding:5px 9px;border-radius:999px;background:var(--secondary-background-color);color:var(--secondary-text-color)}
        .pump-head span.active{background:rgba(36,156,112,.14);color:var(--hi-green)}
        .pump-values{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px}
        select{width:100%;box-sizing:border-box;padding:7px 9px;border-radius:11px;border:1px solid var(--divider-color);color:var(--primary-text-color);background:var(--card-background-color);font:inherit;margin-bottom:10px}

        .chart-wrap{margin:5px 0 2px;padding:6px;border:1px solid var(--divider-color);border-radius:12px;background:var(--secondary-background-color)}
        .chart-heading{display:flex;justify-content:space-between;gap:8px;font-size:.78rem;margin-bottom:4px}.chart-heading span{color:var(--secondary-text-color)}
        .chart-wrap svg{display:block;width:100%;height:auto;overflow:visible}
        .chart-grid{stroke:var(--divider-color);stroke-width:1;stroke-dasharray:3 4}
        .chart-target{stroke:var(--hi-amber);stroke-width:1.5;stroke-dasharray:5 4}
        .chart-actual{fill:none;stroke:var(--hi-blue);stroke-width:2.6;stroke-linecap:round;stroke-linejoin:round}
        .chart-forecast{fill:none;stroke:var(--hi-green);stroke-width:2.6;stroke-dasharray:5 4;stroke-linecap:round;stroke-linejoin:round}
        .chart-label{font-size:9px;fill:var(--secondary-text-color)}
        .chart-legend{display:flex;flex-wrap:wrap;gap:7px;font-size:.68rem;color:var(--secondary-text-color);margin-top:3px}
        .chart-legend span{display:flex;align-items:center;gap:5px}.chart-legend i{display:inline-block;width:13px;height:3px;border-radius:2px}
        .legend-actual{background:var(--hi-blue)}.legend-forecast{background:var(--hi-green)}.legend-target{background:var(--hi-amber)}.legend-gas{background:#d89127}.legend-electric{background:#3989d6}
        .bar-gas{fill:#d89127}.bar-electric{fill:#3989d6}.demand-band{fill:var(--hi-green);opacity:.09}.legend-demand{height:8px!important;background:var(--hi-green);opacity:.25}
        .tabs{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin:5px 0 10px}.tab{border:1px solid var(--divider-color);border-radius:9px;padding:9px 5px;background:var(--secondary-background-color);color:var(--secondary-text-color);font:inherit;font-size:.8rem;font-weight:700}.tab[aria-selected="true"]{background:rgba(57,137,214,.16);border-color:var(--hi-blue);color:var(--primary-text-color)}.demand-row{display:grid;grid-template-columns:100px minmax(0,1fr);align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--divider-color)}.demand-room{display:grid;gap:3px;min-width:0}.demand-room strong{font-size:.76rem;overflow-wrap:anywhere}.demand-status{font-size:.63rem;color:var(--secondary-text-color)}.demand-status.active{color:var(--hi-green);font-weight:700}.demand-row svg{width:100%;height:26px;background:var(--card-background-color);border-radius:6px}.demand-track{stroke:var(--divider-color);stroke-width:3}.demand-complete{fill:var(--hi-blue)}.demand-active{fill:var(--hi-green)}.demand-axis{display:flex;justify-content:space-between;color:var(--secondary-text-color);font-size:.64rem;margin:4px 0 7px}.demand-controls{display:flex;gap:5px;margin:7px 0}.demand-controls button{border:1px solid var(--divider-color);border-radius:8px;padding:6px 10px;background:var(--secondary-background-color);color:var(--primary-text-color)}.demand-controls button[aria-pressed="true"]{border-color:var(--hi-blue);background:rgba(57,137,214,.16)}.chart-empty{padding:8px 7px;color:var(--secondary-text-color);font-size:.76rem;border:1px dashed var(--divider-color);border-radius:10px;margin:10px 0}
        .subtle{color:var(--secondary-text-color);font-size:.72rem;margin:5px 1px 0;line-height:1.45}
        .insights{display:grid;gap:5px}
        .insight{border-radius:9px;padding:7px 9px;border:1px solid var(--divider-color);background:var(--secondary-background-color)}
        .insight-warning{border-left:4px solid var(--hi-amber);background:color-mix(in srgb,var(--hi-amber) 9%,var(--card-background-color))}
        .insight-info{border-left:4px solid var(--hi-blue)}
        .insight-top{display:flex;align-items:center;gap:8px}
        .insight-top strong{font-size:.86rem;flex:1;line-height:1.3}
        .insight-dot{width:8px;height:8px;border-radius:50%;background:var(--hi-blue);flex:none}
        .insight-warning .insight-dot{background:var(--hi-amber)}
        .severity{font-size:.65rem;text-transform:uppercase;letter-spacing:.06em;font-weight:800;color:var(--secondary-text-color)}
        .insight p{font-size:.75rem;line-height:1.35;margin:4px 0 3px}
        .insight small{display:block;color:var(--secondary-text-color);font-size:.73rem;line-height:1.4}
        .empty-insights{padding:8px;border-radius:12px;background:rgba(36,156,112,.1);color:var(--hi-green);font-size:.8rem}
        .subhead{color:var(--secondary-text-color);font-size:.75rem;margin:-4px 0 9px}
        @media(min-width:560px){.overview,.detail-grid{grid-template-columns:repeat(3,minmax(0,1fr))}.pumps{grid-template-columns:repeat(2,minmax(0,1fr))}}
      </style>
      <ha-card>
        <div class="hero"><div class="hero-line"><div><h2>${this._esc(this._config.title)}</h2><p>Whole-home heating performance at a glance</p></div><span class="live-pill">● LIVE MONITORING</span></div></div>
        <nav class="tabs" role="tablist" aria-label="Heating Intelligence views">
          <button type="button" class="tab" role="tab" data-view="overview" aria-selected="true">Overview</button>
          <button type="button" class="tab" role="tab" data-view="demand" aria-selected="false">Heat demand</button>
          <button type="button" class="tab" role="tab" data-view="predict" aria-selected="false">Predictions</button>
        </nav>
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
        <div class="section"><div class="section-title">Whole-house efficiency ${this._info("Whole-house efficiency",this._metricHelp("Whole-house efficiency"))}</div>
          <div class="overview">
            <div class="metric"><span>Outdoor temperature</span><strong>${this._fmt(attrs.efficiency?.outdoor_temperature)}</strong></div>
            <div class="metric"><span>Indoor–outdoor ΔT ${this._info("Indoor–outdoor ΔT", this._metricHelp("Indoor–outdoor ΔT"))}</span><strong>${this._fmt(attrs.efficiency?.delta_t_average)}</strong></div>
            <div class="metric"><span>Solar radiation average ${this._info("Solar radiation average", this._metricHelp("Solar radiation average"))}</span><strong>${this._fmt(attrs.efficiency?.solar_radiation_average, "W/m²", 0)}</strong></div>
            <div class="metric"><span>Heating degree days ${this._info("Heating degree days", this._metricHelp("Heating degree days"))}</span><strong>${this._fmt(attrs.efficiency?.heating_degree_days, "°C·d", 2)}</strong></div>
            <div class="metric"><span>Gas energy today</span><strong>${this._fmt(attrs.efficiency?.gas_energy_today_kwh, "kWh", 2)}</strong></div>
            <div class="metric"><span>Electricity today</span><strong>${this._fmt(attrs.efficiency?.electricity_energy_today_kwh, "kWh", 2)}</strong></div>
            <div class="metric"><span>Downstairs pump electricity</span><strong>${this._fmt(attrs.efficiency?.electricity_ground_floor_today_kwh, "kWh", 3)} · ${this._fmt(attrs.efficiency?.electricity_ground_floor_share_percent, "%", 1)}</strong></div>
            <div class="metric"><span>Upstairs pump electricity</span><strong>${this._fmt(attrs.efficiency?.electricity_upstairs_today_kwh, "kWh", 3)} · ${this._fmt(attrs.efficiency?.electricity_upstairs_share_percent, "%", 1)}</strong></div>
            <div class="metric"><span>Boiler pump electricity</span><strong>${this._fmt(attrs.efficiency?.electricity_boiler_today_kwh, "kWh", 3)} · ${this._fmt(attrs.efficiency?.electricity_boiler_share_percent, "%", 1)}</strong></div>
            <div class="metric"><span>Gas energy per degree day ${this._info("Gas energy per degree day", this._metricHelp("Gas energy per degree day"))}</span><strong>${this._fmt(attrs.efficiency?.gas_kwh_per_degree_day, "kWh/°C·d", 2)}</strong></div>
          </div>
          <div class="subtle">Gas correlation · HDD: ${attrs.efficiency?.gas_hdd_correlation ?? "—"} · ΔT: ${attrs.efficiency?.gas_delta_t_correlation ?? "—"} · solar: ${attrs.efficiency?.gas_solar_correlation ?? "—"}</div>
          <div class="subtle">Electricity correlation · HDD: ${attrs.efficiency?.electricity_hdd_correlation ?? "—"} · ΔT: ${attrs.efficiency?.electricity_delta_t_correlation ?? "—"} · solar: ${attrs.efficiency?.electricity_solar_correlation ?? "—"} · days sampled: ${attrs.efficiency?.days_with_data ?? 0}</div>
        </div>
        <div class="section"><div class="section-title">Heating-water analysis</div>
          <div class="overview">
            <div class="metric"><span>Flow / supply</span><strong>${this._fmt(attrs.advanced_heating?.water?.flow_temperature_c)}</strong></div>
            <div class="metric"><span>Return</span><strong>${this._fmt(attrs.advanced_heating?.water?.return_temperature_c)}</strong></div>
            <div class="metric"><span>Water-side ΔT</span><strong>${this._fmt(attrs.advanced_heating?.water?.water_delta_t_c)}</strong></div>
            <div class="metric"><span>24h average ΔT</span><strong>${this._fmt(attrs.advanced_heating?.water?.average_delta_t_24h_c)}</strong></div>
            <div class="metric"><span>24h min / max ΔT</span><strong>${this._fmt(attrs.advanced_heating?.water?.minimum_delta_t_24h_c)} / ${this._fmt(attrs.advanced_heating?.water?.maximum_delta_t_24h_c)}</strong></div>
            <div class="metric"><span>Samples (24h)</span><strong>${attrs.advanced_heating?.water?.samples_24h ?? 0}</strong></div>
          </div>
          <div class="subtle">Flow temperature uses the existing heating-water inlet sensor. Configure a return-water sensor in Global settings to calculate water-side ΔT. Values are observational; no target range is assumed.</div>
        </div>
        <div class="section"><div class="section-title">Solar contribution analysis</div>
          <div class="overview">
            <div class="metric"><span>Model status</span><strong>${this._esc(attrs.advanced_heating?.solar?.status || "Collecting data")}</strong></div>
            <div class="metric"><span>Days analysed</span><strong>${attrs.advanced_heating?.solar?.days_used ?? 0}</strong></div>
            <div class="metric"><span>Solar / gas-per-HDD correlation</span><strong>${attrs.advanced_heating?.solar?.solar_vs_weather_normalised_gas_correlation ?? "—"}</strong></div>
            <div class="metric"><span>Low-solar gas / HDD</span><strong>${this._fmt(attrs.advanced_heating?.solar?.low_solar_gas_per_hdd, "kWh/°C·d", 2)}</strong></div>
            <div class="metric"><span>High-solar gas / HDD</span><strong>${this._fmt(attrs.advanced_heating?.solar?.high_solar_gas_per_hdd, "kWh/°C·d", 2)}</strong></div>
            <div class="metric"><span>High-solar difference</span><strong>${this._fmt(attrs.advanced_heating?.solar?.high_solar_reduction_percent, "%", 1)}</strong></div>
          </div>
          <div class="subtle">${this._esc(attrs.advanced_heating?.solar?.note || "At least 10 valid daily observations are required.")}</div>
        </div>
        <div class="section"><div class="section-title">Forecast validation</div>
          <div class="overview">
            ${[1,3,6,12].map(h => { const v=attrs.advanced_heating?.forecast_validation?.[String(h)]||{}; return `<div class="metric"><span>${h}h forecast · samples ${v.sample_count ?? 0}</span><strong>${this._fmt(v.mae_c, "°C", 2)} MAE</strong></div>`; }).join("")}
          </div>
          <div class="subtle">Mean absolute error compares stored room-temperature forecasts with later measured temperatures near each forecast horizon. Accuracy becomes meaningful only after enough forecasts have matured.</div>
        </div>
        <div class="section"><div class="section-title">Seasonal and monthly trends</div>
          <div class="pumps">
            ${Object.entries(attrs.advanced_heating?.seasonal?.months || {}).slice(-12).map(([month,v]) => `<div class="metric"><span>${this._esc(month)} · ${v.days} days</span><strong>Gas ${this._fmt(v.gas_kwh,"kWh",1)} · electricity ${this._fmt(v.electricity_kwh,"kWh",1)}</strong><div class="subtle">Heating degree days: ${this._fmt(v.hdd,"°C·d",1)}</div></div>`).join("") || '<p class="subtle">Monthly history is building up.</p>'}
          </div>
        </div>
        <div class="section"><div class="section-title">Advanced heating anomalies</div>
          <div class="insights">
            ${(attrs.advanced_heating?.anomalies || []).map(item => `<article class="insight ${item.severity === "warning" ? "insight-warning" : "insight-info"}"><div class="insight-top"><strong>${this._esc(item.title)}</strong><span class="severity">${this._esc(item.severity)}</span></div><p>${this._esc(item.detail)}</p></article>`).join("") || '<div class="empty-insights">No additional water or robust-baseline anomalies detected.</div>'}
          </div>
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
        <div class="section"><div class="section-title">Floor thermal performance</div>
          <div class="subtle">Area-weighted room observations. Only rooms with configured surface area and valid readings contribute to each weighted metric.</div>
          <div class="overview">
            ${["ground_floor","upstairs"].map((id) => {
              const f = attrs.floor_performance?.[id] || {};
              const label = id === "ground_floor" ? "Downstairs" : "Upstairs";
              return '<div class="metric"><span>' + label + ' · ' + (f.room_count ?? 0) + ' rooms</span><strong>' + this._fmt(f.configured_area_m2, "m²", 1) + ' · ' + (f.rooms_with_area_percent ?? 0) + '% rooms with area</strong><div class="subtle">Area-weighted temperature: ' + this._fmt(f.area_weighted_temperature_c) + ' · cooling: ' + this._fmt(f.area_weighted_cooling_c_per_hour, "°C/h", 2) + ' · heating response: ' + this._fmt(f.area_weighted_heating_response_minutes, "min", 0) + ' · demand: ' + this._fmt(f.area_weighted_demand_hours_today, "h", 1) + '</div></div>';
            }).join("")}
          </div>
        </div>
        <div class="section"><div class="section-title">Historical comparisons ${this._info("Historical comparisons","Compares the latest seven completed days with the preceding seven completed days. Percentage change is descriptive and is not weather-adjusted unless the weather-normalised series is available. Only the last 30 daily records are currently retained in the overview payload.")}</div><div class="section-content"><div class="comparison-heading">Latest 7 days vs preceding 7 days</div>${this._historyComparisons(attrs.energy_history)}</div></div>
        <div class="section"><div class="section-title">Energy history</div>${this._energyChart(attrs.energy_history)}</div>
        <div class="section"><div class="section-title">Weather-normalised efficiency</div>
          <div class="overview">
            <div class="metric"><span>Model status</span><strong>${this._esc(attrs.efficiency?.weather_normalised?.status || "collecting data")}</strong></div>
            <div class="metric"><span>Completed days used</span><strong>${attrs.efficiency?.weather_normalised?.days_used ?? 0} / ${attrs.efficiency?.weather_normalised?.minimum_days ?? 21}</strong></div>
            <div class="metric"><span>Reference gas use</span><strong>${this._fmt(attrs.efficiency?.weather_normalised?.reference_expected_gas_kwh, "kWh", 1)}</strong></div>
          </div>
          <div class="subtle">${this._esc(attrs.efficiency?.weather_normalised?.note || "Collecting weather and energy history.")}</div>
          <div class="pumps">
            ${(attrs.efficiency?.weather_normalised?.recent_days || []).slice(-7).map((d) => '<div class="metric"><span>' + this._esc(d.date) + '</span><strong>Actual ' + this._fmt(d.actual_gas_kwh, "kWh", 1) + ' · normalised ' + this._fmt(d.weather_normalised_gas_kwh, "kWh", 1) + '</strong><div class="subtle">Weather model expected ' + this._fmt(d.weather_expected_gas_kwh, "kWh", 1) + ' · deviation ' + this._fmt(d.weather_effect_percent, "%", 1) + '</div></div>').join("") || '<p class="subtle">At least 21 complete days with gas, outdoor temperature, ΔT and solar data are required.</p>'}
          </div>
        </div>
        <div class="section"><div class="section-title">Weekly room-demand heatmap ${this._info("Weekly room-demand heatmap","Each cell represents one hour of the day. Darker green means more minutes with a recorded heating-demand cycle during that hour. It uses stored demand-cycle start/end times; missing history is not interpreted as zero demand.")}</div><div class="section-content"><div class="subtle">Demand minutes by room, day and hour. Select a cell to inspect its time and duration.</div>${this._demandHeatmap(entries)}</div></div>
        <div class="section"><div class="section-title">Demand timeline</div><div class="subtle">Recorded room demand cycles. Blue shows completed cycles; green shows an ongoing request.</div><div class="demand-controls"><button data-hours="24" aria-pressed="true">24 hours</button><button data-hours="168" aria-pressed="false">7 days</button></div><div class="demand-timeline">${this._demandTimeline(entries,24)}</div><div class="subtle">Demand means the configured source is available, regardless of its on/off value.</div></div>
        <div class="section"><div class="section-title">Forecasts</div>
          <div class="subtle">Explainable short-term projections · confidence reflects sample coverage and trend consistency.</div>
          <div class="overview">
            <div class="metric"><span>Rooms with forecasts</span><strong>${attrs.predictions?.rooms_with_forecasts ?? 0} / ${entries.length}</strong></div>
            <div class="metric"><span>Average confidence ${this._info("Average confidence", this._metricHelp("Average confidence"))}</span><strong>${this._fmt(attrs.predictions?.average_confidence, "%", 0)}</strong></div>
            <div class="metric"><span>Projected gas today</span><strong>${this._fmt(attrs.predictions?.energy?.gas_kwh, "kWh", 1)}</strong></div>
            <div class="metric"><span>Projected electricity today</span><strong>${this._fmt(attrs.predictions?.energy?.electricity_kwh, "kWh", 1)}</strong></div>
          </div>
          <div class="pumps">
            ${entries.map((r) => {
              const p = attrs.predictions?.rooms?.[r.id] || {};
              return `<div class="metric"><span>${this._esc(r.name)} · ${p.status === "available" ? `confidence ${p.confidence}%` : "collecting data"}</span><strong>${p.status === "available" ? `1h ${this._fmt(p.temperature_1h)} · 3h ${this._fmt(p.temperature_3h)} · 6h ${this._fmt(p.temperature_6h)} · 12h ${this._fmt(p.temperature_12h)}` : "Forecast unavailable"}</strong><div class="subtle">${p.time_to_comfort_minutes != null ? `Time to comfort: ${this._duration(p.time_to_comfort_minutes * 60)} · ` : ""}${p.time_below_comfort_minutes != null ? `Below comfort in: ${this._duration(p.time_below_comfort_minutes * 60)} · ` : ""}${this._esc(p.note || "")}</div></div>`;
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
      if (openSections.has(summary.textContent) || summary.textContent === "Forecasts" || summary.textContent === "Room details") disclosure.open = true;
    });
    this.shadowRoot.querySelectorAll(".info-button").forEach(button=>button.addEventListener("click",()=>{const existing=button.nextElementSibling;if(existing?.classList.contains("info-popover")){existing.remove();button.setAttribute("aria-expanded","false");this._openInfoKey=null;return;}this.shadowRoot.querySelectorAll(".info-popover").forEach(p=>p.remove());this.shadowRoot.querySelectorAll(".info-button").forEach(b=>b.setAttribute("aria-expanded","false"));const pop=document.createElement("div");pop.className="info-popover open";pop.textContent=button.dataset.info||"";button.insertAdjacentElement("afterend",pop);button.setAttribute("aria-expanded","true");this._openInfoKey=button.dataset.infoKey;}));
    if(this._openInfoKey){const button=[...this.shadowRoot.querySelectorAll(".info-button")].find(b=>b.dataset.infoKey===this._openInfoKey);if(button){const pop=document.createElement("div");pop.className="info-popover open";pop.textContent=button.dataset.info||"";button.insertAdjacentElement("afterend",pop);button.setAttribute("aria-expanded","true");}}
    const applyView=(view)=>{this._activeView=view;this.shadowRoot.querySelectorAll(".tab").forEach(b=>{const active=b.dataset.view===view;b.setAttribute("aria-selected",String(active));b.setAttribute("tabindex",active?"0":"-1");});this.shadowRoot.querySelectorAll("details.section").forEach(s=>{const h=s.querySelector("summary")?.textContent||"";const tab=h==="Demand timeline"?"demand":["Forecasts","Room details","Room heat-retention comparison","Advanced diagnostics"].includes(h)?"predict":"overview";s.hidden=tab!==view;if(tab===view)s.open=true;});};
    this.shadowRoot.querySelectorAll(".tab").forEach(b=>b.addEventListener("click",()=>applyView(b.dataset.view)));applyView(this._activeView||"overview");
    this.shadowRoot.querySelectorAll("[data-hours]").forEach(b=>b.addEventListener("click",()=>{this._demandHours=Number(b.dataset.hours);this.shadowRoot.querySelectorAll("[data-hours]").forEach(x=>x.setAttribute("aria-pressed",String(x===b)));const target=this.shadowRoot.querySelector(".demand-timeline");if(target)target.innerHTML=this._demandTimeline(entries,this._demandHours);}));
    const select = this.shadowRoot.querySelector("select");
    if (select) select.addEventListener("change", (event) => { this._selectedRoom = event.target.value; this._render(); });
  }
  static getStubConfig() { return { entity:"sensor.heating_intelligence_overview", title:"Heating Intelligence" }; }
}
if (!customElements.get("heating-intelligence-card")) customElements.define("heating-intelligence-card", HeatingIntelligenceCard);
window.customCards = window.customCards || [];
window.customCards.push({ type:"heating-intelligence-card", name:"Heating Intelligence", description:"Heating, pump cycles and synchronisation with room details.", preview:true });

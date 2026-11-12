"""Adaptive, explainable room and daily energy forecasts."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

COMFORT_TARGET_C = 21.0
HORIZONS = (1, 3, 6, 12)

def _mode_model(samples: list[dict[str, Any]], now: datetime, demand: bool) -> dict[str, Any] | None:
    cutoff = now.astimezone(timezone.utc) - timedelta(hours=24)
    points = []
    for item in samples:
        try:
            stamp = datetime.fromisoformat(item["time"]).astimezone(timezone.utc)
            temp = float(item["temperature"])
        except (KeyError, TypeError, ValueError):
            continue
        if cutoff <= stamp <= now.astimezone(timezone.utc) and not item.get("window_open") and bool(item.get("demand")) == demand:
            points.append((stamp, temp))
    points.sort()
    if len(points) < 4 or (now.astimezone(timezone.utc)-points[-1][0]).total_seconds() > 1200:
        return None
    rates = [(b[1]-a[1])/((b[0]-a[0]).total_seconds()/3600)
             for a,b in zip(points,points[1:])
             if 5 <= (b[0]-a[0]).total_seconds()/60 <= 45]
    span = (points[-1][0]-points[0][0]).total_seconds()/3600
    if len(rates) < 3 or span < .5:
        return None
    center = median(rates)
    noise = median([abs(v-center) for v in rates])
    confidence = round(100*min(1,len(rates)/12)*min(1,span/4)*max(.2,1-noise/.35))
    return {"rate":max(-1,min(1,center)),"noise":noise,"confidence":max(5,min(95,confidence)),
            "samples":len(points),"intervals":len(rates),"span":round(span*60)}

def room_forecast(room: dict[str, Any], now: datetime, comfort_target_c: float = COMFORT_TARGET_C) -> dict[str, Any]:
    current, demand = room.get("temperature"), bool(room.get("demand"))
    model = _mode_model(room.get("temperature_samples",[]),now,demand)
    result = {"status":"insufficient_data","model":"adaptive_mode_response","mode":"heating" if demand else "coasting",
              "confidence":0,"comfort_target_c":comfort_target_c,"current_temperature":current,
              "trend_c_per_hour":None,"sample_count":0,"model_sample_count":0,
              "time_to_comfort_minutes":None,"time_below_comfort_minutes":None,
              "note":"Learning separate heating and coast rates; need 4 same-mode samples spanning 30 minutes."}
    for h in HORIZONS:
        result.update({f"temperature_{h}h":None,f"temperature_{h}h_low":None,f"temperature_{h}h_high":None})
    if model is None or current is None:
        return result
    rate=model["rate"]
    result.update({"status":"available","confidence":model["confidence"],"trend_c_per_hour":round(rate,3),
                   "sample_count":model["samples"],"model_sample_count":model["intervals"],
                   "model_span_minutes":model["span"],"model_noise_c_per_hour":round(model["noise"],3),
                   "note":"Learned from recent closed-window observations in the current demand mode; assumes that mode continues."})
    for h in HORIZONS:
        estimate=float(current)+rate*h
        margin=.12+model["noise"]*h*.65+h*.025
        result.update({f"temperature_{h}h":round(estimate,2),f"temperature_{h}h_low":round(estimate-margin,2),
                       f"temperature_{h}h_high":round(estimate+margin,2)})
    if demand and current<comfort_target_c and rate>.02:
        result["time_to_comfort_minutes"]=round((comfort_target_c-current)/rate*60)
    if not demand and current>comfort_target_c and rate<-.02:
        result["time_below_comfort_minutes"]=round((current-comfort_target_c)/abs(rate)*60)
    return result

def energy_forecast(efficiency: dict[str, Any], now: datetime) -> dict[str, Any]:
    elapsed=(now.hour*3600+now.minute*60+now.second)/3600
    result={"status":"insufficient_data","confidence":0,"gas_kwh":None,"electricity_kwh":None,
            "elapsed_hours":round(elapsed,2),"note":"At least 2 hours of today's meter history are required."}
    if elapsed<2: return result
    gas,electricity=efficiency.get("gas_energy_today_kwh"),efficiency.get("electricity_energy_today_kwh")
    if gas is None and electricity is None: return result
    factor=24/elapsed
    result.update({"status":"available","confidence":min(55,round(20+elapsed/24*35)),
                   "gas_kwh":round(gas*factor,2) if gas is not None else None,
                   "electricity_kwh":round(electricity*factor,2) if electricity is not None else None,
                   "note":"Elapsed-day run-rate projection; weather, solar gains and occupancy are not modelled."})
    return result

def build_predictions(rooms: dict[str,dict[str,Any]],efficiency:dict[str,Any],now:datetime,comfort_target_c:float=COMFORT_TARGET_C)->dict[str,Any]:
    room_results={key:room_forecast(room,now,comfort_target_c) for key,room in rooms.items()}
    confidence=[v["confidence"] for v in room_results.values() if v["status"]=="available"]
    return {"comfort_target_c":comfort_target_c,"rooms":room_results,"energy":energy_forecast(efficiency,now),
            "rooms_with_forecasts":len(confidence),"average_confidence":round(sum(confidence)/len(confidence)) if confidence else None,
            "generated_at":now.isoformat()}

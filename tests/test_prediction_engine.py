"""Tests for adaptive room-response forecasts."""
import importlib.util
from datetime import datetime,timedelta,timezone
from pathlib import Path
PATH=Path(__file__).parents[1]/"custom_components"/"heating_intelligence"/"prediction_engine.py"
SPEC=importlib.util.spec_from_file_location("prediction_engine",PATH)
MODULE=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MODULE)
def samples(start,slope=.2,count=13,minutes=10,demand=True,window=False):
 return [{"time":(start+timedelta(minutes=i*minutes)).isoformat(),"temperature":20+slope*i*minutes/60,
          "demand":demand,"window_open":window} for i in range(count)]
def test_adaptive_heating_forecast_and_uncertainty():
 now=datetime(2026,11,12,12,tzinfo=timezone.utc)
 r=MODULE.room_forecast({"temperature":20.4,"demand":True,"temperature_samples":samples(now-timedelta(hours=2))},now)
 assert r["status"]=="available" and r["temperature_1h"]==20.6
 assert r["temperature_12h"] is not None and r["temperature_6h_low"]<r["temperature_6h"]<r["temperature_6h_high"]
 assert r["time_to_comfort_minutes"] is not None
def test_heating_and_coasting_are_modelled_separately():
 now=datetime(2026,11,12,12,tzinfo=timezone.utc)
 history=samples(now-timedelta(hours=2),.3, demand=True)+samples(now-timedelta(hours=1),-.15,demand=False)
 r=MODULE.room_forecast({"temperature":20,"demand":False,"temperature_samples":history},now)
 assert r["status"]=="available" and r["trend_c_per_hour"]<0
def test_insufficient_and_open_window_samples_are_rejected():
 now=datetime(2026,11,12,12,tzinfo=timezone.utc)
 assert MODULE.room_forecast({"temperature":19,"demand":True,"temperature_samples":samples(now-timedelta(minutes=40),count=4)},now)["status"]=="insufficient_data"
 assert MODULE.room_forecast({"temperature":19,"demand":True,"temperature_samples":samples(now-timedelta(hours=2),window=True)},now)["status"]=="insufficient_data"
def test_coasting_time_below_comfort():
 now=datetime(2026,11,12,12,tzinfo=timezone.utc)
 r=MODULE.room_forecast({"temperature":22,"demand":False,"temperature_samples":samples(now-timedelta(hours=2),-.2,demand=False)},now)
 assert r["time_below_comfort_minutes"]==300
def test_energy_projection():
 now=datetime(2026,11,12,12,tzinfo=timezone.utc)
 r=MODULE.energy_forecast({"gas_energy_today_kwh":6,"electricity_energy_today_kwh":1.2},now)
 assert r["gas_kwh"]==12 and r["electricity_kwh"]==2.4
 assert MODULE.energy_forecast({"gas_energy_today_kwh":1},now.replace(hour=1))["status"]=="insufficient_data"

#!/usr/bin/env python
"""Build map-ready infrastructure layers (pipelines and facilities) as GeoJSON.

Outputs (repo root):
    infra_pipelines.geojson   LineString / MultiLineString features
    infra_facilities.geojson  Point features

Sources:
    eia      US EIA layer shapefiles (https://www.eia.gov/maps/map_data/*.zip, the
             files behind the EIA Energy Atlas; the Atlas FeatureServer itself now
             answers "Token Required", so the zips are the key-free route)
    usgs     USGS "Mineral operations outside the United States" (minfac), a global
             compilation with commodity, capacity and country
    curated  hand-placed literals in this file (see the CURATED blocks)

Raw downloads are cached under data_store/raw/infra/ (gitignored). Re-running
reproduces the output files byte for byte from the cache; pass --refresh to
re-download. Geometry work is plain Python (own shapefile reader and
Douglas-Peucker), no geopandas.

Usage:  .venv/bin/python scripts/build_infra.py [--refresh]
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import struct
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data_store" / "raw" / "infra"
OUT_PIPES = ROOT / "infra_pipelines.geojson"
OUT_FACS = ROOT / "infra_facilities.geojson"

TARGET_BYTES = 4 * 1024 * 1024
HARD_CAP_BYTES = 6 * 1024 * 1024

EIA_BASE = "https://www.eia.gov/maps/map_data/"
EIA_LAYERS = {
    "crude": "CrudeOil_Pipelines_US_EIA.zip",
    "products": "PetroleumProduct_Pipelines_US_EIA.zip",
    "hgl": "HGL_Pipelines_US_EIA.zip",
    "gas": "NaturalGas_InterIntrastate_Pipelines_US_EIA.zip",
    "refineries": "Petroleum_Refineries_US_EIA.zip",
    "lng": "Lng_ImportExportTerminals_US_EIA.zip",
    "storage": "NaturalGas_UndergroundStorage_US_EIA.zip",
    "coal": "CoalMines_US_EIA.zip",
}
USGS_MINFAC = "https://mrdata.usgs.gov/mineral-operations/minfac-csv.zip"

# Simplification and filtering knobs
TOL_LIQUIDS = 0.01        # degrees, crude / products / HGL
TOL_GAS = 0.01            # degrees, gas trunklines
GAS_INCLUDE_INTRASTATE = True   # size allows it (combined output stays well under target)
GAS_MIN_CHAIN_DEG = 0.1   # drop chained gas lines shorter than this (laterals, gathering)
GAS_MIN_OPERATOR_DEG = 0.5  # drop operators whose whole kept system is shorter
STORAGE_MIN_BCF = 20.0    # working gas capacity threshold
COAL_TOP_N = 40
USGS_MINES_PER_COMMODITY = 30
USGS_PLANTS_PER_COMMODITY = 15

PIPE_KEYS = ["name", "commodity", "status", "capacity", "capacity_unit",
             "operator", "countries", "origin", "note"]
FAC_KEYS = ["name", "kind", "commodity", "also", "capacity", "capacity_unit",
            "country", "operator", "status", "origin", "note"]
PIPE_COMMODITIES = {"crude", "products", "gas", "hgl"}
PIPE_STATUS = {"operating", "idle", "damaged", "construction"}
PIPE_UNITS = {"kb/d", "bcm/yr", "Bcf/d"}
FAC_KINDS = {"refinery", "lng_export", "lng_import", "gas_storage", "gas_processing",
             "oil_terminal", "mine", "smelter", "metal_refinery", "enrichment",
             "conversion", "coal_mine"}

# =============================================================================
# CURATED PIPELINES (hand-placed, good to roughly 50 km)
# Waypoints are (lat, lon) for readability and flipped to GeoJSON order on write.
# Capacities are widely published nameplate figures; omitted where unsure.
# Fields: name, commodity, status, capacity, unit, operator, countries, note, pts
# =============================================================================
CURATED_PIPELINES = [
    ("Druzhba (main line, Almetyevsk to Mozyr)", "crude", "operating", 1300, "kb/d", "Transneft",
     ["RUS", "BLR"], "Trunk from Tatarstan to the Mozyr split; flows cut sharply since 2022",
     [(54.90, 52.30), (53.20, 50.15), (53.15, 48.47), (52.90, 40.50), (53.25, 34.37),
      (52.85, 32.68), (52.05, 29.25)]),
    ("Druzhba North (Mozyr to Plock and Schwedt)", "crude", "operating", None, None, "Gomeltransneft / PERN",
     ["BLR", "POL", "DEU"], "Russian crude halted 2023; carries Kazakh KEBCO to Schwedt",
     [(52.05, 29.25), (52.10, 23.70), (52.35, 23.05), (52.55, 19.70), (53.06, 14.28)]),
    ("Druzhba South (Mozyr to Hungary)", "crude", "operating", None, None, "Ukrtransnafta / MOL",
     ["BLR", "UKR", "HUN"], "Sanctions-exempt Russian crude to MOL; repeatedly hit in 2025",
     [(52.05, 29.25), (50.08, 25.14), (48.62, 22.30), (48.07, 18.95), (47.30, 18.91)]),
    ("Druzhba South (Slovakia and Czechia spur)", "crude", "operating", None, None, "Transpetrol / MERO",
     ["UKR", "SVK", "CZE"], "Czech deliveries of Russian crude ended 2025; Slovakia still supplied",
     [(48.62, 22.30), (48.63, 21.93), (48.15, 17.11), (49.20, 16.60), (50.60, 13.60)]),
    ("Nord Stream 1", "gas", "damaged", 55, "bcm/yr", "Nord Stream AG",
     ["RUS", "DEU"], "Both strings ruptured by sabotage Sept 2022",
     [(60.53, 28.07), (59.85, 25.00), (59.30, 22.00), (58.00, 20.40), (56.40, 18.70),
      (55.20, 15.90), (54.75, 14.80), (54.14, 13.65)]),
    ("Nord Stream 2", "gas", "damaged", 55, "bcm/yr", "Nord Stream 2 AG",
     ["RUS", "DEU"], "One of two strings ruptured Sept 2022; never certified",
     [(59.66, 28.05), (59.75, 25.00), (59.20, 22.00), (57.90, 20.25), (56.30, 18.55),
      (55.05, 15.75), (54.65, 14.70), (54.14, 13.65)]),
    ("Yamal-Europe", "gas", "idle", 33, "bcm/yr", "Gazprom / EuRoPol Gaz",
     ["RUS", "BLR", "POL", "DEU"], "Westbound Russian flow stopped 2022; Polish section runs in reverse",
     [(57.04, 34.96), (54.78, 32.05), (53.90, 27.57), (53.13, 23.90), (52.65, 19.07),
      (52.46, 14.49)]),
    ("Brotherhood (Urengoy-Pomary-Uzhgorod, Ukraine transit)", "gas", "idle", None, None, "Gazprom / GTSOU",
     ["RUS", "UKR", "SVK"], "Russian transit via Sudzha ended 1 Jan 2025",
     [(51.19, 35.27), (50.75, 33.48), (50.00, 30.60), (49.08, 27.68), (48.97, 24.00),
      (48.62, 22.30), (48.55, 22.08)]),
    ("Soyuz (Orenburg to western Ukraine)", "gas", "idle", None, None, "Gazprom / GTSOU",
     ["RUS", "KAZ", "UKR"], "Sokhranovka entry shut May 2022; transit ended 2025",
     [(51.77, 55.10), (51.20, 51.40), (50.15, 48.57), (49.60, 44.00), (49.54, 39.10),
      (49.07, 33.42), (49.08, 27.68), (48.62, 22.30)]),
    ("TurkStream", "gas", "operating", 31.5, "bcm/yr", "Gazprom",
     ["RUS", "TUR"], "Last Russian pipeline route into Europe (via Turkey and Bulgaria)",
     [(44.89, 37.32), (43.60, 35.00), (42.50, 32.00), (41.95, 29.50), (41.63, 28.10),
      (41.40, 27.35)]),
    ("Blue Stream", "gas", "operating", 16, "bcm/yr", "Gazprom / BOTAS",
     ["RUS", "TUR"], "Black Sea crossing to Samsun, on to Ankara",
     [(45.37, 41.70), (44.35, 38.53), (42.80, 37.50), (41.33, 36.50), (40.55, 34.95),
      (39.93, 32.85)]),
    ("Power of Siberia 1", "gas", "operating", 38, "bcm/yr", "Gazprom",
     ["RUS", "CHN"], "Chayanda and Kovykta gas to China at Blagoveshchensk/Heihe",
     [(60.36, 111.71), (60.72, 114.90), (60.37, 120.40), (58.61, 125.39), (56.66, 124.70),
      (53.98, 123.94), (51.38, 128.13), (50.27, 127.53), (50.24, 127.49)]),
    ("ESPO (Taishet to Kozmino)", "crude", "operating", 1600, "kb/d", "Transneft",
     ["RUS"], "1.6 mb/d to Skovorodino, about 1.0 mb/d onward to Kozmino",
     [(55.95, 98.02), (56.80, 105.70), (57.78, 108.12), (60.72, 114.90), (60.37, 120.40),
      (58.61, 125.39), (55.15, 124.70), (53.98, 123.94), (50.92, 128.47), (48.48, 135.07),
      (45.93, 133.74), (42.72, 133.05)]),
    ("ESPO Daqing spur (Skovorodino-Mohe-Daqing)", "crude", "operating", 600, "kb/d", "Transneft / CNPC",
     ["RUS", "CHN"], "Two parallel lines into northeast China",
     [(53.98, 123.94), (53.47, 122.37), (52.97, 122.54), (50.41, 124.13), (48.50, 124.90),
      (46.59, 125.10)]),
    ("CPC (Tengiz to Novorossiysk)", "crude", "operating", 1500, "kb/d", "Caspian Pipeline Consortium",
     ["KAZ", "RUS"], "Carries about 80% of Kazakh exports; SPMs and pump stations hit in 2025",
     [(46.15, 53.38), (47.10, 51.90), (46.35, 48.05), (45.33, 46.03), (45.43, 40.58),
      (44.68, 37.63)]),
    ("BTC (Baku-Tbilisi-Ceyhan)", "crude", "operating", 1200, "kb/d", "BP",
     ["AZE", "GEO", "TUR"], "Azeri Light plus Kazakh and Turkmen volumes to the Mediterranean",
     [(40.20, 49.48), (40.60, 46.40), (41.45, 44.90), (41.60, 43.00), (39.90, 41.27),
      (39.75, 39.50), (39.75, 37.02), (38.00, 36.50), (36.88, 35.93)]),
    ("Baku-Supsa (Western Route)", "crude", "operating", 145, "kb/d", "BP",
     ["AZE", "GEO"], "Used intermittently since 2022",
     [(40.20, 49.48), (40.65, 46.40), (41.60, 44.90), (41.99, 43.60), (42.04, 41.81)]),
    ("South Caucasus Pipeline (SCP)", "gas", "operating", 24, "bcm/yr", "BP",
     ["AZE", "GEO", "TUR"], "Shah Deniz gas; first leg of the Southern Gas Corridor",
     [(40.20, 49.48), (40.55, 46.40), (41.40, 44.90), (41.55, 43.40), (41.58, 42.82)]),
    ("TANAP", "gas", "operating", 16, "bcm/yr", "TANAP (SOCAR-led)",
     ["TUR"], "Georgian border to Greece; expandable to 31 bcm/yr",
     [(41.58, 42.82), (39.95, 41.27), (39.80, 37.02), (39.60, 33.50), (39.77, 30.52),
      (40.10, 28.00), (40.35, 26.65), (40.95, 26.31)]),
    ("TAP (Trans Adriatic Pipeline)", "gas", "operating", 10, "bcm/yr", "TAP AG",
     ["GRC", "ALB", "ITA"], "Final leg of the Southern Gas Corridor into Italy",
     [(40.95, 26.31), (41.00, 24.40), (40.75, 22.90), (40.60, 20.95), (40.73, 19.56),
      (40.50, 19.00), (40.27, 18.40)]),
    ("Central Asia-China gas (Lines A/B/C)", "gas", "operating", 55, "bcm/yr", "CNPC / Asia Trans Gas",
     ["TKM", "UZB", "KAZ", "CHN"], "Turkmen, Uzbek and Kazakh gas to Khorgos",
     [(38.99, 64.01), (39.40, 63.80), (40.13, 63.45), (40.10, 65.37), (41.00, 68.00),
      (42.30, 69.60), (42.90, 71.37), (43.25, 76.90), (44.21, 80.41)]),
    ("Kazakhstan-China oil (Atyrau-Alashankou)", "crude", "operating", 400, "kb/d", "KazTransOil / CNPC",
     ["KAZ", "CHN"], "Atasu-Alashankou leg also carries Russian transit crude",
     [(47.10, 51.90), (48.61, 57.11), (46.45, 65.60), (48.60, 71.40), (46.85, 75.00),
      (45.17, 82.56)]),
    ("Myanmar-China crude", "crude", "operating", 440, "kb/d", "CNPC (SEAOP)",
     ["MMR", "CHN"], "Kyaukpyu deepwater port to the Kunming refinery; bypasses Malacca",
     [(19.43, 93.53), (20.40, 94.90), (21.97, 96.08), (22.93, 97.75), (24.00, 97.85),
      (25.10, 99.17), (25.60, 100.27), (25.04, 102.70)]),
    ("Myanmar-China gas", "gas", "operating", 12, "bcm/yr", "CNPC (SEAGP)",
     ["MMR", "CHN"], "Shwe field gas via Kunming to Guizhou and Guangxi",
     [(19.43, 93.53), (20.45, 94.95), (22.02, 96.10), (22.98, 97.75), (24.05, 97.88),
      (25.15, 99.17), (25.65, 100.27), (25.09, 102.70), (26.60, 106.70), (23.10, 109.60)]),
    ("East-West Pipeline (Petroline)", "crude", "operating", 5000, "kb/d", "Saudi Aramco",
     ["SAU"], "Abqaiq to Yanbu, the Hormuz bypass; can surge to about 7 mb/d",
     [(25.94, 49.67), (25.10, 48.10), (24.40, 46.30), (24.00, 43.50), (24.20, 40.50),
      (23.95, 38.25)]),
    ("Habshan-Fujairah (ADCOP)", "crude", "operating", 1500, "kb/d", "ADNOC",
     ["ARE"], "Hormuz bypass to the Gulf of Oman",
     [(23.83, 53.62), (24.10, 54.50), (24.46, 55.34), (25.15, 56.35)]),
    ("Kirkuk-Ceyhan (Iraq-Turkey Pipeline)", "crude", "operating", None, None, "North Oil Co / BOTAS",
     ["IRQ", "TUR"], "Shut March 2023 to Sept 2025; flows well below nameplate",
     [(35.47, 44.40), (36.10, 43.20), (37.07, 42.38), (37.25, 42.20), (37.20, 40.70),
      (37.10, 37.40), (36.88, 35.93)]),
    ("SUMED", "crude", "operating", 2500, "kb/d", "Arab Petroleum Pipelines Co",
     ["EGY"], "Ain Sukhna to Sidi Kerir, the Suez Canal bypass",
     [(29.60, 32.32), (29.80, 31.20), (30.40, 30.30), (31.02, 29.63)]),
    ("IPSA (Iraq Pipeline through Saudi Arabia)", "crude", "idle", 1650, "kb/d", "Saudi Aramco",
     ["IRQ", "SAU"], "Closed since 1990; Saudi section converted, Iraq link unused",
     [(30.39, 47.70), (28.43, 45.96), (26.30, 46.60), (25.10, 48.00), (24.45, 46.20),
      (24.05, 43.40), (24.25, 40.40), (23.82, 38.40)]),
    ("Goreh-Jask", "crude", "operating", 1000, "kb/d", "NIOC",
     ["IRN"], "Iran's Hormuz bypass; design 1 mb/d, used far below that",
     [(29.50, 50.65), (28.40, 52.50), (27.70, 54.40), (27.45, 56.20), (26.60, 57.10),
      (25.70, 57.75)]),
    ("Trans-Mediterranean (Enrico Mattei)", "gas", "operating", 32, "bcm/yr", "Sonatrach / Eni",
     ["DZA", "TUN", "ITA"], "Hassi R'Mel via Tunisia and Sicily to northern Italy",
     [(32.93, 3.27), (34.80, 6.50), (35.30, 8.30), (36.40, 10.20), (37.05, 11.01),
      (37.65, 12.58), (37.60, 14.00), (38.20, 15.55), (40.90, 14.80), (44.62, 11.48)]),
    ("Medgaz", "gas", "operating", 10.5, "bcm/yr", "Medgaz (Sonatrach / Naturgy)",
     ["DZA", "ESP"], "Direct Algeria to Spain subsea link",
     [(32.93, 3.27), (34.20, 0.80), (35.30, -1.38), (36.83, -2.40)]),
    ("Maghreb-Europe (GME)", "gas", "idle", 12, "bcm/yr", "Sonatrach / ONHYM / Enagas",
     ["DZA", "MAR", "ESP"], "Algerian flow ended Nov 2021; Spain to Morocco reverse flow only",
     [(32.93, 3.27), (33.30, 0.50), (34.00, -2.03), (34.20, -4.00), (35.60, -5.70),
      (36.10, -5.80), (37.88, -4.78)]),
    ("Greenstream", "gas", "operating", 8, "bcm/yr", "Eni / NOC",
     ["LBY", "ITA"], "Mellitah to Gela; running well below capacity",
     [(32.86, 12.24), (35.00, 13.20), (37.07, 14.25)]),
    ("West African Gas Pipeline", "gas", "operating", 0.47, "Bcf/d", "WAPCo",
     ["NGA", "BEN", "TGO", "GHA"], "Offshore line from Lagos to Takoradi",
     [(6.60, 3.30), (6.30, 3.00), (6.20, 2.40), (6.00, 1.25), (5.55, 0.05), (5.00, -0.90),
      (4.90, -1.75)]),
    ("Chad-Cameroon", "crude", "operating", 225, "kb/d", "COTCO / TOTCO",
     ["TCD", "CMR"], "Doba basin crude to the Kribi offshore terminal",
     [(8.47, 16.71), (7.60, 15.30), (6.50, 14.30), (4.93, 13.30), (4.30, 12.00),
      (3.50, 10.80), (2.94, 9.91)]),
    ("EACOP (East African Crude Oil Pipeline)", "crude", "construction", 246, "kb/d", "EACOP Ltd (TotalEnergies)",
     ["UGA", "TZA"], "Heated line from Lake Albert to Tanga; status as of 2025",
     [(1.47, 31.05), (0.30, 31.60), (-1.00, 31.42), (-2.60, 31.80), (-3.83, 32.60),
      (-4.82, 34.75), (-5.00, 36.20), (-5.43, 38.02), (-4.99, 39.09)]),
    ("Tazama", "products", "operating", None, None, "Tazama Pipelines",
     ["TZA", "ZMB"], "Dar es Salaam to Ndola; converted from crude feedstock to diesel in 2023",
     [(-6.82, 39.28), (-6.82, 37.66), (-7.77, 35.70), (-8.90, 33.45), (-9.30, 32.76),
      (-11.83, 31.45), (-13.97, 28.68), (-12.97, 28.63)]),
    ("Langeled", "gas", "operating", 25.5, "bcm/yr", "Gassco",
     ["NOR", "GBR"], "Nyhamna via Sleipner to Easington",
     [(62.85, 6.95), (61.50, 3.60), (58.37, 1.91), (56.00, 1.00), (53.65, 0.12)]),
    ("Europipe II", "gas", "operating", 24, "bcm/yr", "Gassco",
     ["NOR", "DEU"], "Karsto to Dornum",
     [(59.28, 5.53), (58.40, 5.00), (57.00, 5.60), (55.50, 6.40), (54.20, 7.10),
      (53.65, 7.43)]),
    ("Norpipe (gas)", "gas", "operating", None, None, "Gassco",
     ["NOR", "DEU"], "Ekofisk to Emden",
     [(56.55, 3.21), (55.30, 4.80), (54.20, 6.20), (53.37, 7.20)]),
    ("Baltic Pipe", "gas", "operating", 10, "bcm/yr", "Energinet / Gaz-System",
     ["NOR", "DNK", "POL"], "Norwegian gas via Denmark to Poland",
     [(55.75, 5.90), (55.75, 8.20), (55.60, 9.30), (55.35, 10.70), (55.20, 12.10),
      (54.85, 13.60), (54.70, 14.70), (54.09, 15.08)]),
    ("Trans Mountain (TMX)", "crude", "operating", 890, "kb/d", "Trans Mountain Corp",
     ["CAN"], "Only Canadian crude line to the Pacific; expansion in service May 2024",
     [(53.55, -113.35), (53.58, -116.43), (52.88, -118.08), (52.83, -119.27),
      (50.67, -120.33), (49.38, -121.44), (49.27, -122.93)]),
    ("Enbridge Mainline", "crude", "operating", 3200, "kb/d", "Enbridge",
     ["CAN", "USA"], "Largest Canadian export system; continues as Lakehead to Chicago and Sarnia",
     [(53.55, -113.35), (52.67, -111.31), (50.45, -104.60), (49.00, -97.56),
      (47.69, -95.43), (46.72, -92.10), (44.50, -89.60), (41.52, -87.42),
      (42.27, -85.00), (42.97, -82.40)]),
    ("Keystone", "crude", "operating", 622, "kb/d", "South Bow",
     ["CAN", "USA"], "Hardisty to Steele City, Cushing and the Gulf Coast",
     [(52.67, -111.31), (50.66, -109.91), (50.45, -104.60), (49.00, -97.95),
      (46.00, -97.60), (42.90, -97.40), (40.04, -97.02), (35.98, -96.77),
      (32.50, -95.50), (29.90, -93.93)]),
    ("Coastal GasLink", "gas", "operating", 2.1, "Bcf/d", "TC Energy",
     ["CAN"], "Montney gas to LNG Canada at Kitimat",
     [(55.78, -120.92), (55.30, -122.00), (54.60, -122.90), (53.95, -124.00),
      (54.05, -125.80), (54.20, -126.80), (54.05, -128.65)]),
    ("GASBOL (Bolivia-Brazil)", "gas", "operating", 11, "bcm/yr", "GTB / TBG",
     ["BOL", "BRA"], "Bolivian volumes in decline; increasingly reverse-supplied",
     [(-18.10, -63.00), (-18.60, -60.50), (-18.97, -57.80), (-20.45, -54.62),
      (-20.75, -51.70), (-22.76, -47.15), (-23.50, -46.80), (-25.43, -49.27),
      (-27.60, -48.80), (-29.92, -51.18)]),
    ("SOTE (Trans-Ecuadorian)", "crude", "operating", 360, "kb/d", "Petroecuador",
     ["ECU"], "Oriente crude to Balao; frequent erosion and landslide outages",
     [(0.09, -76.88), (-0.05, -77.50), (-0.46, -77.89), (-0.38, -78.50),
      (-0.25, -79.17), (0.33, -79.47), (0.95, -79.68)]),
    ("OCP (Heavy Crude Pipeline)", "crude", "operating", 450, "kb/d", "OCP Ecuador",
     ["ECU"], "Napo heavy crude to Balao; shares the SOTE corridor risk",
     [(0.09, -76.88), (0.00, -77.50), (-0.30, -77.80), (-0.05, -78.30),
      (0.07, -78.68), (0.35, -79.40), (0.97, -79.66)]),
    ("Camisea (TGP)", "gas", "operating", None, None, "TGP",
     ["PER"], "Malvinas to Pisco and Lima; a parallel line carries NGLs",
     [(-11.84, -72.95), (-12.80, -73.40), (-13.20, -74.20), (-13.50, -75.30),
      (-13.75, -76.20), (-13.00, -76.50), (-12.27, -76.87)]),
    ("Oleoducto Norperuano", "crude", "operating", None, None, "Petroperu",
     ["PER"], "Amazon crude to Bayovar; chronic spills and outages",
     [(-4.71, -74.93), (-4.56, -77.42), (-5.20, -78.20), (-5.64, -78.53),
      (-5.98, -79.75), (-5.83, -81.03)]),
    ("Gasoducto Perito Moreno (Vaca Muerta trunk)", "gas", "operating", 7.7, "bcm/yr", "Enarsa",
     ["ARG"], "Formerly Gasoducto Nestor Kirchner; Tratayen to Salliquelo",
     [(-38.41, -68.59), (-38.60, -66.50), (-37.90, -64.80), (-36.75, -62.92)]),
    ("West-East Gas Pipeline (Line 1)", "gas", "operating", 17, "bcm/yr", "PipeChina",
     ["CHN"], "Tarim basin to Shanghai; representative of the West-East system",
     [(41.48, 84.25), (42.87, 90.20), (42.82, 93.50), (38.93, 100.45), (37.50, 105.20),
      (37.60, 108.80), (36.08, 111.52), (34.75, 113.65), (32.92, 117.38),
      (32.06, 118.80), (31.23, 121.47)]),
    ("Moomba-Sydney Pipeline", "gas", "operating", None, None, "APA Group",
     ["AUS"], "Cooper basin gas to New South Wales",
     [(-28.11, 140.20), (-30.00, 142.80), (-31.80, 145.20), (-33.77, 147.52),
      (-34.30, 148.30), (-34.23, 150.70)]),
    ("Dampier-Bunbury Natural Gas Pipeline", "gas", "operating", None, None, "AGIG",
     ["AUS"], "North West Shelf gas to Perth and the southwest",
     [(-20.66, 116.71), (-22.70, 114.95), (-25.00, 114.60), (-27.50, 114.90),
      (-29.20, 115.30), (-31.30, 115.80), (-32.23, 115.80), (-33.33, 115.64)]),
]
# ============================ END CURATED PIPELINES ==========================

# =============================================================================
# CURATED FACILITIES (hand-placed, good to roughly 10 km; coordinates checked
# against Wikipedia / OpenStreetMap lookups in Nov 2026)
# Fields: name, kind, commodity, also, capacity, unit, country, operator,
#         status, note, (lat, lon)
# =============================================================================
CURATED_FACILITIES = [
    # largest non-US refineries
    ("Jamnagar Refinery", "refinery", "crude", None, 1240, "kb/d", "IND", "Reliance Industries", "operating",
     "World's largest refining complex", (22.348, 69.869)),
    ("Ulsan Refinery", "refinery", "crude", None, 840, "kb/d", "KOR", "SK Energy", "operating", None, (35.50, 129.36)),
    ("Ruwais Refinery", "refinery", "crude", None, 817, "kb/d", "ARE", "ADNOC", "operating", None, (24.106, 52.728)),
    ("Ras Tanura Refinery", "refinery", "crude", None, 550, "kb/d", "SAU", "Saudi Aramco", "operating", None, (26.693, 50.096)),
    ("Yeosu Refinery", "refinery", "crude", None, 800, "kb/d", "KOR", "GS Caltex", "operating", None, (34.833, 127.74)),
    ("Dangote Refinery", "refinery", "crude", None, 650, "kb/d", "NGA", "Dangote Group", "operating",
     "Largest single-train refinery; ramping since 2024", (6.453, 4.001)),
    ("Jurong Island Refinery", "refinery", "crude", None, 592, "kb/d", "SGP", "ExxonMobil", "operating", None, (1.27, 103.696)),
    ("Al-Zour Refinery", "refinery", "crude", None, 615, "kb/d", "KWT", "KIPIC", "operating", None, (28.713, 48.336)),
    ("Paraguana Refinery Complex (Amuay-Cardon)", "refinery", "crude", None, 955, "kb/d", "VEN", "PDVSA", "operating",
     "Nameplate; runs far below capacity", (11.747, -70.195)),
    # major LNG export plants
    ("Ras Laffan LNG", "lng_export", "gas", None, 77, "mtpa", "QAT", "QatarEnergy LNG", "operating",
     "North Field expansion adds capacity from 2026", (25.90, 51.55)),
    ("Gorgon LNG (Barrow Island)", "lng_export", "gas", None, 15.6, "mtpa", "AUS", "Chevron", "operating", None, (-20.79, 115.44)),
    ("Yamal LNG (Sabetta)", "lng_export", "gas", None, 17.4, "mtpa", "RUS", "Novatek", "operating", None, (71.273, 72.073)),
    ("Nigeria LNG (Bonny Island)", "lng_export", "gas", None, 22, "mtpa", "NGA", "NLNG", "operating",
     "Train 7 under construction", (4.426, 7.153)),
    ("MLNG Bintulu", "lng_export", "gas", None, 29.3, "mtpa", "MYS", "Petronas", "operating", None, (3.28, 113.08)),
    ("Sakhalin-2 LNG (Prigorodnoye)", "lng_export", "gas", None, 9.6, "mtpa", "RUS", "Sakhalin Energy", "operating", None, (46.63, 142.90)),
    ("Hammerfest LNG (Melkoya)", "lng_export", "gas", None, 4.3, "mtpa", "NOR", "Equinor", "operating", None, (70.69, 23.599)),
    ("Arzew LNG", "lng_export", "gas", None, None, None, "DZA", "Sonatrach", "operating",
     "Arzew and Bethioua trains (GL1Z, GL2Z, GL3Z)", (35.82, -0.27)),
    ("Coral Sul FLNG", "lng_export", "gas", None, 3.4, "mtpa", "MOZ", "Eni", "operating",
     "Floating plant offshore Cabo Delgado; position approximate", (-10.55, 41.15)),
    ("Calcasieu Pass LNG", "lng_export", "gas", None, None, None, "USA", "Venture Global", "operating",
     "Added because the EIA layer is 2020 vintage", (29.774, -93.333)),
    ("Plaquemines LNG", "lng_export", "gas", None, None, None, "USA", "Venture Global", "operating",
     "Added because the EIA layer is 2020 vintage", (29.60, -89.90)),
    # key oil export terminals
    ("Ras Tanura Terminal", "oil_terminal", "crude", None, None, None, "SAU", "Saudi Aramco", "operating",
     "Largest offshore oil loading facility", (26.64, 50.16)),
    ("Kharg Island Terminal", "oil_terminal", "crude", None, None, None, "IRN", "NIOC", "operating",
     "Handles about 90% of Iranian crude exports", (29.245, 50.31)),
    ("Al Basrah Oil Terminal (ABOT)", "oil_terminal", "crude", None, None, None, "IRQ", "Basra Oil Co", "operating",
     "Main outlet for southern Iraqi crude", (29.783, 48.807)),
    ("Novorossiysk (Sheskharis)", "oil_terminal", "crude", None, None, None, "RUS", "Transneft", "operating",
     "Black Sea outlet for Urals; CPC loads nearby at Yuzhnaya Ozereyevka", (44.72, 37.83)),
    ("Primorsk", "oil_terminal", "crude", None, None, None, "RUS", "Transneft", "operating",
     "Largest Baltic outlet for Urals", (60.34, 28.70)),
    ("Kozmino", "oil_terminal", "crude", None, None, None, "RUS", "Transneft", "operating",
     "Pacific outlet for ESPO blend", (42.72, 133.05)),
    ("Ceyhan Terminal", "oil_terminal", "crude", None, None, None, "TUR", "BOTAS", "operating",
     "Loads BTC and Kirkuk crude", (36.88, 35.93)),
    ("LOOP (Louisiana Offshore Oil Port)", "oil_terminal", "crude", None, None, None, "USA", "LOOP LLC", "operating",
     "Only US port that fully loads VLCCs", (28.885, -90.025)),
    # enrichment and conversion
    ("Urenco Almelo", "enrichment", "uranium", None, None, None, "NLD", "Urenco", "operating", None, (52.34, 6.68)),
    ("Urenco Gronau", "enrichment", "uranium", None, None, None, "DEU", "Urenco", "operating", None, (52.212, 7.06)),
    ("Urenco Capenhurst", "enrichment", "uranium", None, None, None, "GBR", "Urenco", "operating", None, (53.258, -2.95)),
    ("Urenco USA (Eunice)", "enrichment", "uranium", None, None, None, "USA", "Urenco", "operating",
     "Only commercial enrichment plant in the US", (32.436, -103.08)),
    ("Orano Tricastin (Georges Besse II)", "enrichment", "uranium", None, 7.5, "MSWU/yr", "FRA", "Orano", "operating",
     "Site also hosts the Philippe Coste conversion plant", (44.336, 4.723)),
    ("Novouralsk (UEKhK)", "enrichment", "uranium", None, None, None, "RUS", "Rosatom (TVEL)", "operating",
     "Largest enrichment plant in the world", (57.25, 60.083)),
    ("Port Hope Conversion Facility", "conversion", "uranium", None, None, None, "CAN", "Cameco", "operating",
     "UF6 and UO2 conversion", (43.95, -78.30)),
    ("Blind River Refinery", "conversion", "uranium", None, None, None, "CAN", "Cameco", "operating",
     "Refines concentrate to UO3 for Port Hope", (46.183, -82.95)),
    ("Metropolis Works", "conversion", "uranium", None, None, None, "USA", "Honeywell / ConverDyn", "operating",
     "Only US UF6 conversion plant; restarted 2023", (37.172, -88.757)),
    # flagship mines
    ("Kamoa-Kakula", "mine", "copper", None, None, None, "COD", "Ivanhoe Mines / Zijin", "operating", None, (-10.87, 25.20)),
    ("Tenke Fungurume", "mine", "copper", ["cobalt"], None, None, "COD", "CMOC", "operating", None, (-10.568, 26.196)),
    ("Mutanda", "mine", "copper", ["cobalt"], None, None, "COD", "Glencore", "operating", None, (-10.786, 25.808)),
    ("Escondida", "mine", "copper", None, None, None, "CHL", "BHP", "operating", "World's largest copper mine", (-24.27, -69.07)),
    ("Grasberg", "mine", "copper", ["gold"], None, None, "IDN", "Freeport Indonesia", "operating",
     "Block cave mud-rush incident Sept 2025 cut output", (-4.053, 137.116)),
    ("Olympic Dam", "mine", "copper", ["uranium", "gold"], None, None, "AUS", "BHP", "operating", None, (-30.44, 136.873)),
    ("Cigar Lake", "mine", "uranium", None, None, None, "CAN", "Cameco", "operating", None, (58.069, -104.541)),
    ("McArthur River", "mine", "uranium", None, None, None, "CAN", "Cameco", "operating", None, (57.761, -105.052)),
    ("Husab", "mine", "uranium", None, None, None, "NAM", "Swakop Uranium (CGN)", "operating", None, (-22.588, 15.025)),
    ("Greenbushes", "mine", "lithium", None, None, None, "AUS", "Talison Lithium", "operating",
     "Largest hard-rock lithium mine", (-33.866, 116.065)),
    ("Salar de Atacama", "mine", "lithium", None, None, None, "CHL", "SQM / Albemarle", "operating",
     "Brine operations at the southern end of the salar", (-23.55, -68.37)),
    ("Weda Bay", "mine", "nickel", None, None, None, "IDN", "Tsingshan / Eramet", "operating",
     "Mine plus the IWIP smelter park", (0.478, 127.984)),
    ("Norilsk", "mine", "nickel", ["palladium", "copper", "platinum", "cobalt"], None, None, "RUS", "Nornickel", "operating",
     "Mines and smelters of the Polar Division", (69.35, 88.20)),
    ("Simandou", "mine", "iron_ore", None, None, None, "GIN", "Rio Tinto Simfer / WCS", "operating",
     "First shipments late 2025; ramping", (8.517, -8.90)),
    ("Carajas", "mine", "iron_ore", None, None, None, "BRA", "Vale", "operating",
     "Northern System incl. S11D about 40 km south", (-6.059, -50.177)),
    ("Bayan Obo", "mine", "rare_earths", ["iron_ore"], None, None, "CHN", "Baogang Group", "operating",
     "Largest rare earth deposit in production", (41.783, 109.974)),
    ("Mountain Pass", "mine", "rare_earths", None, None, None, "USA", "MP Materials", "operating",
     "Only US rare earth mine", (35.479, -115.532)),
]
# ============================ END CURATED FACILITIES =========================

# USGS minfac country names -> ISO3
ISO3 = {
    "Albania": "ALB", "Algeria": "DZA", "Angola": "AGO", "Argentina": "ARG", "Armenia": "ARM",
    "Aruba": "ABW", "Australia": "AUS", "Austria": "AUT", "Azerbaijan": "AZE", "Bahamas": "BHS",
    "Bahrain": "BHR", "Bangladesh": "BGD", "Barbados": "BRB", "Belarus": "BLR", "Belgium": "BEL",
    "Belize": "BLZ", "Benin": "BEN", "Bhutan": "BTN", "Bolivia": "BOL",
    "Bosnia and Herzegovina": "BIH", "Botswana": "BWA", "Brazil": "BRA", "Brunei": "BRN",
    "Bulgaria": "BGR", "Burkina Faso": "BFA", "Burma": "MMR", "Burundi": "BDI", "Cambodia": "KHM",
    "Cameroon": "CMR", "Canada": "CAN", "Cape Verde": "CPV", "Central African Republic": "CAF",
    "Chad": "TCD", "Chile": "CHL", "China": "CHN", "Colombia": "COL", "Congo - Brazzaville": "COG",
    "Congo - Kinshasa": "COD", "Costa Rica": "CRI", "Cote D'Ivoire": "CIV", "Croatia": "HRV",
    "Cuba": "CUB", "Cyprus": "CYP", "Czech Republic": "CZE", "Denmark": "DNK", "Djibouti": "DJI",
    "Domincan Republic": "DOM", "Ecuador": "ECU", "Egypt": "EGY", "El Salvador": "SLV",
    "Equatorial Guinea": "GNQ", "Eritrea": "ERI", "Estonia": "EST", "Ethiopia": "ETH", "Fiji": "FJI",
    "Finland": "FIN", "France": "FRA", "French Guiana": "GUF", "Gabon": "GAB", "Georgia": "GEO",
    "Germany": "DEU", "Ghana": "GHA", "Greece": "GRC", "Guatemala": "GTM", "Guinea": "GIN",
    "Guyana": "GUY", "Honduras": "HND", "Hungary": "HUN", "Iceland": "ISL", "India": "IND",
    "Indonesia": "IDN", "Iran": "IRN", "Iraq": "IRQ", "Ireland": "IRL", "Israel": "ISR",
    "Italy": "ITA", "Jamaica": "JAM", "Japan": "JPN", "Jordan": "JOR", "Kazakhstan": "KAZ",
    "Kenya": "KEN", "Kuwait": "KWT", "Kyrgyzstan": "KGZ", "Laos": "LAO", "Latvia": "LVA",
    "Lebanon": "LBN", "Lesotho": "LSO", "Liberia": "LBR", "Libya": "LBY", "Lithuania": "LTU",
    "Luxembourg": "LUX", "Macedonia": "MKD", "Madagascar": "MDG", "Malawi": "MWI",
    "Malaysia": "MYS", "Mali": "MLI", "Mauritania": "MRT", "Mauritius": "MUS", "Mexico": "MEX",
    "Moldova": "MDA", "Mongolia": "MNG", "Montenegro": "MNE", "Morocco": "MAR",
    "Mozambique": "MOZ", "Namibia": "NAM", "Nepal": "NPL", "Netherlands": "NLD",
    "New Caledonia": "NCL", "New Zealand": "NZL", "Nicaragua": "NIC", "Niger": "NER",
    "Nigeria": "NGA", "North Korea": "PRK", "Norway": "NOR", "Oman": "OMN", "Pakistan": "PAK",
    "Panama": "PAN", "Papua New Guinea": "PNG", "Paraguay": "PRY", "Peru": "PER",
    "Philippines": "PHL", "Poland": "POL", "Portugal": "PRT", "Qatar": "QAT",
    "Republic of Korea": "KOR", "Reunion": "REU", "Romania": "ROU", "Russia": "RUS",
    "Rwanda": "RWA", "Saudi Arabia": "SAU", "Senegal": "SEN", "Serbia": "SRB",
    "Sierra Leone": "SLE", "Singapore": "SGP", "Slovakia": "SVK", "Slovenia": "SVN",
    "Somalia": "SOM", "South Africa": "ZAF", "Spain": "ESP", "Sri Lanka": "LKA", "Sudan": "SDN",
    "Suriname": "SUR", "Swaziland": "SWZ", "Sweden": "SWE", "Switzerland": "CHE", "Syria": "SYR",
    "Taiwan": "TWN", "Tajikistan": "TJK", "Tanzania": "TZA", "Thailand": "THA", "Togo": "TGO",
    "Trinidad & Tobago": "TTO", "Tunisia": "TUN", "Turkey": "TUR", "Turkmenistan": "TKM",
    "Uganda": "UGA", "Ukraine": "UKR", "United Arab Emirates": "ARE", "United Kingdom": "GBR",
    "Uruguay": "URY", "Uzbekistan": "UZB", "Venezuela": "VEN", "Vietnam": "VNM",
    "Western Sahara": "ESH", "Yemen": "YEM", "Zambia": "ZMB", "Zimbabwe": "ZWE",
}

# ordered: first match in the commodity string is the primary commodity
USGS_COMMODITIES = [
    ("copper", r"copper"), ("cobalt", r"cobalt"), ("lithium", r"lithium"),
    ("nickel", r"nickel"), ("bauxite", r"bauxite"), ("alumina", r"alumina"),
    ("aluminum", r"alumin(?:i)?um"), ("iron_ore", r"iron ore"), ("gold", r"gold"),
    ("pgm", r"platinum|palladium"), ("tin", r"\btin\b"), ("zinc", r"zinc|\bzn\b"),
    ("manganese", r"manganese"), ("rare_earths", r"rare earth"), ("uranium", r"uranium"),
    ("lead", r"\blead\b"), ("silver", r"silver"),
]
USGS_TARGETS = ["copper", "cobalt", "lithium", "nickel", "bauxite", "alumina", "aluminum",
                "iron_ore", "gold", "pgm", "tin", "zinc", "manganese", "rare_earths", "uranium"]


# ----------------------------------------------------------------------------
# download + readers
# ----------------------------------------------------------------------------
def fetch(url: str, name: str, refresh: bool = False) -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / name
    if path.exists() and path.stat().st_size > 0 and not refresh:
        return path
    print(f"  downloading {url}")
    r = requests.get(url, timeout=180, headers={"User-Agent": "Mozilla/5.0 (infra-layer-builder)"})
    r.raise_for_status()
    if r.content[:2] != b"PK":
        raise RuntimeError(f"{url} did not return a zip archive")
    path.write_bytes(r.content)
    return path


def read_dbf(b: bytes) -> list[dict]:
    n, hlen, rlen = struct.unpack("<IHH", b[4:12])
    fields, p = [], 32
    while b[p] != 0x0D:
        fields.append((b[p:p + 11].split(b"\0")[0].decode(), chr(b[p + 11]), b[p + 16]))
        p += 32
    rows = []
    for i in range(n):
        rec = b[hlen + i * rlen: hlen + (i + 1) * rlen]
        o, d = 1, {}
        for name, typ, ln in fields:
            v = rec[o:o + ln].decode("utf-8", "replace").strip()
            o += ln
            if typ in "NF":
                try:
                    v = float(v)
                except ValueError:
                    v = None
            d[name] = v if v != "" else None
        rows.append(d)
    return rows


def read_shp(b: bytes) -> list:
    """Return per record: None, ('pt', (x, y)) or ('line', [[(x, y), ...], ...])."""
    out, p = [], 100
    while p < len(b):
        _, clen = struct.unpack(">ii", b[p:p + 8])
        rec = b[p + 8: p + 8 + clen * 2]
        p += 8 + clen * 2
        st = struct.unpack("<i", rec[:4])[0]
        if st == 0:
            out.append(None)
        elif st in (1, 11, 21):
            out.append(("pt", struct.unpack("<2d", rec[4:20])))
        elif st in (3, 13, 23):
            nparts, npts = struct.unpack("<2i", rec[36:44])
            parts = list(struct.unpack(f"<{nparts}i", rec[44:44 + 4 * nparts])) + [npts]
            o = 44 + 4 * nparts
            flat = struct.unpack(f"<{2 * npts}d", rec[o:o + 16 * npts])
            pts = [(flat[2 * i], flat[2 * i + 1]) for i in range(npts)]
            out.append(("line", [pts[parts[i]:parts[i + 1]] for i in range(nparts)]))
        else:
            raise ValueError(f"unsupported shape type {st}")
    return out


def load_shapefile(zpath: Path):
    z = zipfile.ZipFile(zpath)
    shp = [n for n in z.namelist() if n.lower().endswith(".shp")][0]
    prj = z.read(shp[:-4] + ".prj").decode("utf-8", "replace")
    if "GCS_WGS_1984" not in prj or "PROJCS" in prj:
        raise RuntimeError(f"{zpath.name}: expected geographic WGS84, got {prj[:60]}")
    geoms = read_shp(z.read(shp))
    rows = read_dbf(z.read(shp[:-4] + ".dbf"))
    if len(geoms) != len(rows):
        raise RuntimeError(f"{zpath.name}: shp/dbf record mismatch")
    return geoms, rows


# ----------------------------------------------------------------------------
# geometry helpers
# ----------------------------------------------------------------------------
def douglas_peucker(pts: list, tol: float) -> list:
    if len(pts) < 3:
        return list(pts)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        best, idx = 0.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            if seg2 == 0:
                d2 = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
                d2 = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d2 > best:
                best, idx = d2, i
        if idx >= 0 and best > tol * tol:
            keep[idx] = True
            stack.append((a, idx))
            stack.append((idx, b))
    return [p for p, k in zip(pts, keep) if k]


def round_line(pts: list) -> list:
    out = []
    for x, y in pts:
        q = [round(x, 3), round(y, 3)]
        if not out or out[-1] != q:
            out.append(q)
    return out


def line_len(pts: list) -> float:
    return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
               for i in range(len(pts) - 1))


def chain_segments(segs: list) -> list:
    """Greedily join segments that share endpoints into longer polylines."""
    def key(p):
        return (round(p[0], 3), round(p[1], 3))

    ends = defaultdict(list)
    for i, s in enumerate(segs):
        ends[key(s[0])].append(i)
        ends[key(s[-1])].append(i)
    used = [False] * len(segs)
    order = sorted(range(len(segs)), key=lambda i: (-len(segs[i]), i))
    chains = []
    for i in order:
        if used[i]:
            continue
        used[i] = True
        line = list(segs[i])
        for at_end in (True, False):
            while True:
                k = key(line[-1] if at_end else line[0])
                nxt = next((j for j in ends[k] if not used[j]), None)
                if nxt is None:
                    break
                used[nxt] = True
                s = segs[nxt]
                if at_end:
                    line.extend(s[1:] if key(s[0]) == k else s[-2::-1])
                else:
                    line[:0] = s[:-1] if key(s[-1]) == k else s[:0:-1]
        chains.append(line)
    return chains


def simplify_parts(parts: list, tol: float, min_len: float = 0.0) -> list:
    out = []
    for line in chain_segments([p for p in parts if len(p) >= 2]):
        if line_len(line) < min_len:
            continue
        r = round_line(douglas_peucker(line, tol))
        if len(r) >= 2:
            out.append(r)
    out.sort(key=lambda r: (-len(r), r[0]))
    return out


def line_feature(parts: list, props: dict) -> dict:
    geom = ({"type": "LineString", "coordinates": parts[0]} if len(parts) == 1
            else {"type": "MultiLineString", "coordinates": parts})
    return {"type": "Feature", "properties": props, "geometry": geom}


def point_feature(lon: float, lat: float, props: dict) -> dict:
    return {"type": "Feature", "properties": props,
            "geometry": {"type": "Point", "coordinates": [round(lon, 3), round(lat, 3)]}}


def us_line_countries(parts: list) -> list:
    """Rough border test for EIA lines that run into Canada or Mexico."""
    out = ["USA"]
    pts = [p for part in parts for p in part]
    if any((y > 49.05 and -123.3 < x < -95.2) or (42.3 < y < 49 and -82.4 < x < -79.1 and y > 42.9)
           or (y > 45.05 and -74.6 < x < -71.6) for x, y in pts):
        out.append("CAN")
    if any(y < 25.8 and x < -97.3 for x, y in pts):
        out.append("MEX")
    return out


# ----------------------------------------------------------------------------
# text helpers
# ----------------------------------------------------------------------------
KEEP_UPPER = {"USA", "US", "LLC", "LP", "LLP", "INC", "CO", "BP", "PBF", "HF", "CVR", "WRB",
              "NGL", "LNG", "ONEOK", "DCP", "MPLX", "TC", "EPL", "II", "III", "IV", "SA", "TX",
              "NV", "ANR", "KO", "WBI", "EQT", "UGI", "DTE", "CNG", "NGPL", "PG&E", "EQM"}


def clean(s) -> str | None:
    if s is None:
        return None
    s = str(s).replace(chr(0x2014), "-").replace(chr(0x2013), "-").replace("\x97", "-").replace("\x96", "-")
    s = re.sub(r"[\x00-\x1f\x7f-\x9f\xad]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def smart_title(s) -> str | None:
    s = clean(s)
    if not s:
        return None
    if s != s.upper():
        return s
    words = []
    for w in s.split(" "):
        core = w.strip(".,()")
        words.append(w if core in KEEP_UPPER or (len(core) <= 2 and "&" in core) else w.capitalize())
    return " ".join(words)


def props(keys: list, **kw) -> dict:
    """Ordered property dict, dropping unknowns (never null-filled)."""
    out = {}
    for k in keys:
        v = kw.get(k)
        if v is None or v == "" or v == []:
            continue
        if isinstance(v, float):
            v = int(v) if v == int(v) else round(v, 2)
        out[k] = v
    return out


def num(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if not math.isnan(v) else None
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


# ----------------------------------------------------------------------------
# EIA layers
# ----------------------------------------------------------------------------
def eia_liquid_pipelines(zpath: Path, commodity: str) -> list:
    geoms, rows = load_shapefile(zpath)
    groups = defaultdict(list)
    for g, r in zip(geoms, rows):
        if not g or g[0] != "line":
            continue
        name = clean(r.get("Pipename")) or smart_title(r.get("Opername")) or "Unnamed"
        groups[(name, smart_title(r.get("Opername")))].extend(g[1])
    feats = []
    for (name, oper), parts in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        simp = simplify_parts(parts, TOL_LIQUIDS)
        if not simp:
            continue
        feats.append(line_feature(simp, props(
            PIPE_KEYS, name=name, commodity=commodity, status="operating", operator=oper,
            countries=us_line_countries(simp), origin="eia")))
    return feats


def eia_gas_pipelines(zpath: Path) -> list:
    geoms, rows = load_shapefile(zpath)
    groups = defaultdict(list)
    for g, r in zip(geoms, rows):
        if not g or g[0] != "line":
            continue
        typ = clean(r.get("TYPEPIPE")) or ""
        if typ != "Interstate" and not (GAS_INCLUDE_INTRASTATE and typ == "Intrastate"):
            continue
        if (clean(r.get("Status")) or "Operating") != "Operating":
            continue
        oper = clean(r.get("Operator")) or "Unknown operator"
        groups[(oper, typ)].extend(g[1])
    feats = []
    for (oper, typ), parts in sorted(groups.items()):
        simp = simplify_parts(parts, TOL_GAS, GAS_MIN_CHAIN_DEG)
        if not simp or sum(line_len(s) for s in simp) < GAS_MIN_OPERATOR_DEG:
            continue
        feats.append(line_feature(simp, props(
            PIPE_KEYS, name=oper, commodity="gas", status="operating", operator=oper,
            countries=["USA"], origin="eia", note=f"{typ} system")))
    return feats


def eia_refineries(zpath: Path) -> list:
    geoms, rows = load_shapefile(zpath)
    feats = []
    for g, r in zip(geoms, rows):
        cap = num(r.get("AD_Mbpd"))
        if not g or not cap:
            continue
        feats.append(point_feature(g[1][0], g[1][1], props(
            FAC_KEYS, name=f"{clean(r.get('Site'))} Refinery", kind="refinery", commodity="crude",
            capacity=cap, capacity_unit="kb/d", country="USA",
            operator=smart_title(r.get("Company")), status="operating", origin="eia",
            note=f"{clean(r.get('State'))}; atmospheric distillation, EIA-820 as of Jan 2021")))
    return feats


def eia_lng(zpath: Path) -> list:
    geoms, rows = load_shapefile(zpath)
    feats = []
    for g, r in zip(geoms, rows):
        if not g:
            continue
        fn = clean(r.get("Functions")) or ""
        export = "Export" in fn
        cap = num(r.get("Liq_Bcfd")) if export else num(r.get("Regas_Bcfd"))
        feats.append(point_feature(g[1][0], g[1][1], props(
            FAC_KEYS, name=f"{clean(r.get('Facility'))} LNG", kind="lng_export" if export else "lng_import",
            commodity="gas", capacity=cap, capacity_unit="Bcf/d" if cap else None, country="USA",
            operator=clean(r.get("Operator")), status="operating", origin="eia",
            note=f"{fn}; EIA as of Apr 2020")))
    return feats


def eia_storage(zpath: Path) -> list:
    geoms, rows = load_shapefile(zpath)
    feats = []
    for g, r in zip(geoms, rows):
        work = (num(r.get("work_cap")) or 0) / 1e6  # Mcf -> Bcf
        if not g or work < STORAGE_MIN_BCF or (clean(r.get("Status")) or "") != "Active":
            continue
        feats.append(point_feature(g[1][0], g[1][1], props(
            FAC_KEYS, name=f"{smart_title(r.get('Field'))} Storage", kind="gas_storage", commodity="gas",
            capacity=round(work, 1), capacity_unit="Bcf", country="USA",
            operator=smart_title(r.get("Company")), status="operating", origin="eia",
            note=f"{clean(r.get('Field_Type'))}, {clean(r.get('State'))}; working gas capacity, EIA-191 Dec 2020")))
    return feats


def eia_coal(zpath: Path) -> list:
    geoms, rows = load_shapefile(zpath)
    recs = []
    for g, r in zip(geoms, rows):
        prod = num(r.get("tot_prod"))
        if g and prod:
            recs.append((prod, g[1], r))
    recs.sort(key=lambda t: (-t[0], clean(t[2].get("name")) or ""))
    feats = []
    for prod, (x, y), r in recs[:COAL_TOP_N]:
        feats.append(point_feature(x, y, props(
            FAC_KEYS, name=clean(r.get("name")), kind="coal_mine", commodity="coal",
            capacity=round(prod / 1e6, 2), capacity_unit="Mst/yr", country="USA",
            status="operating", origin="eia",
            note=f"{clean(r.get('type_'))}, {clean(r.get('state'))}; 2020 production (million short tons)")))
    return feats


# ----------------------------------------------------------------------------
# USGS minfac
# ----------------------------------------------------------------------------
def usgs_operator(v) -> str | None:
    v = clean(v)
    if not v:
        return None
    v = re.sub(r"\s*\(.*$", "", v).strip(" ,;")
    return v[:70] or None


def usgs_capacity(raw_cap, raw_units):
    """Normalise minfac capacity to kt/yr (tonnage) or t/yr (precious metals in kg)."""
    cap = num(raw_cap)
    u = (clean(raw_units) or "").lower()
    if cap is None or cap <= 0 or not u or "barrel" in u or "cubic" in u or "carat" in u:
        return None, None
    if u.startswith(("kilograms", "kg")):
        return cap / 1000.0, "t/yr"
    if u.startswith("thousand"):
        return cap, "kt/yr"
    if u.startswith(("t /", "t/yr", "metric tons")):
        return cap / 1000.0, "kt/yr"
    return None, None


def usgs_kind(fac_type: str, com: str, primary: str) -> str:
    f = fac_type.lower()
    if "smelter" in f or "smelter" in com:
        return "smelter"
    if "refin" in f or "refin" in com:
        return "metal_refinery"
    if f.startswith(("mine", "quarry", "mill")):
        return "mine"
    # plain "Plant"
    if re.search(r"ore|concentrate|mine", com) or primary in ("bauxite", "iron_ore", "uranium"):
        return "mine"
    if primary == "alumina" or re.search(r"metal|cathode|electrowinning|carbonate|oxide|compound", com):
        return "metal_refinery"
    if primary in ("gold", "pgm", "cobalt", "lithium", "rare_earths"):
        return "metal_refinery"
    return "smelter"


def usgs_facilities(zpath: Path) -> list:
    z = zipfile.ZipFile(zpath)
    text = z.read("minfac.csv").decode("latin-1")
    rows = list(csv.DictReader(io.StringIO(text)))
    mines, plants = defaultdict(list), defaultdict(list)
    for r in rows:
        status = (r.get("status") or "").strip().lower()
        if status != "active":
            continue
        lat, lon = num(r.get("latitude")), num(r.get("longitude"))
        iso = ISO3.get((r.get("country") or "").strip())
        if lat is None or lon is None or iso is None or (lat == 0 and lon == 0):
            continue
        com = (clean(r.get("commodity")) or "").lower()
        name = clean(r.get("fac_name")) or clean(r.get("location"))
        if not name:
            continue
        fac_type = clean(r.get("fac_type")) or ""
        base = dict(name=name[:80], country=iso, operator=usgs_operator(r.get("op_comp")),
                    lat=lat, lon=lon, year=(r.get("year") or "").strip())
        if "petrol" in com or "petroel" in com:
            # minfac petroleum refinery capacities mix b/d and kb/d under one unit label,
            # so they are not used; the big non-US refineries come from the curated block
            continue
        hits = sorted((m.start(), c) for c, pat in USGS_COMMODITIES for m in [re.search(pat, com)] if m)
        hits = [c for _, c in hits]
        if "ferro" in com or "steel" in com and "iron ore" not in com or "secondary" in com \
                or "rolled" in com or "semimanuf" in com or "fluoride" in com or "fused" in com:
            continue
        if not hits or hits[0] not in USGS_TARGETS:
            continue
        primary = hits[0]
        kind = usgs_kind(fac_type, com, primary)
        if primary == "aluminum" and kind == "mine":
            primary = "bauxite"
        cap, unit = usgs_capacity(r.get("capacity"), r.get("units"))
        if cap is None:
            continue
        rec = dict(base, kind=kind, commodity=primary, also=[c for c in hits[1:] if c != primary],
                   cap=cap, unit=unit,
                   note=f"USGS minfac {base['year']}; {fac_type.lower()}, {com}"[:110])
        (mines if kind == "mine" else plants)[primary].append(rec)

    def top(recs, n):
        """Largest n sites. Capacities are reported on mixed bases (ore tonnage in
        kt/yr, contained metal in kt/yr or t/yr), so each unit is ranked on its own
        and gets slots in proportion to how many sites report in it."""
        best = {}
        for rec in recs:
            k = (round(rec["lat"], 1), round(rec["lon"], 1), rec["kind"], rec["commodity"])
            if k not in best or rec["cap"] > best[k]["cap"]:
                best[k] = rec
        buckets = defaultdict(list)
        for rec in best.values():
            buckets[rec["unit"]].append(rec)
        total = sum(len(b) for b in buckets.values())
        out = []
        for unit in sorted(buckets):
            b = sorted(buckets[unit], key=lambda d: (-d["cap"], d["name"]))
            out += b[:max(1, round(n * len(b) / total))] if total > n else b
        return sorted(out, key=lambda d: (d["unit"], -d["cap"], d["name"]))[:n]

    # a capacity repeated across 3+ sites of one operator is a group total, not a site figure
    shared = Counter((r["operator"], r["commodity"], r["kind"], r["cap"], r["unit"])
                     for d in (mines, plants) for recs in d.values() for r in recs)
    picked = []
    for c in USGS_TARGETS:
        picked += top(mines[c], USGS_MINES_PER_COMMODITY)
        picked += top(plants[c], USGS_PLANTS_PER_COMMODITY)

    # merge rows that are the same site listed under several commodities
    merged, order = {}, []
    for rec in picked:
        k = (rec["name"], round(rec["lat"], 2), round(rec["lon"], 2), rec["kind"])
        if k in merged:
            m = merged[k]
            for c in [rec["commodity"]] + rec.get("also", []):
                if c != m["commodity"] and c not in m["also"]:
                    m["also"].append(c)
        else:
            merged[k] = dict(rec, also=list(rec.get("also", [])))
            order.append(k)
    feats = []
    for k in order:
        m = merged[k]
        group = shared[(m["operator"], m["commodity"], m["kind"], m["cap"], m["unit"])] >= 3
        if group:
            m["note"] = (m["note"] + "; capacity reported for operator group only")[:140]
        feats.append(point_feature(m["lon"], m["lat"], props(
            FAC_KEYS, name=m["name"], kind=m["kind"], commodity=m["commodity"], also=m["also"],
            capacity=None if group else round(m["cap"], 2),
            capacity_unit=None if group else m["unit"], country=m["country"],
            operator=m["operator"], status="operating", origin="usgs", note=m["note"])))
    return feats


# ----------------------------------------------------------------------------
# curated
# ----------------------------------------------------------------------------
def curated_pipelines() -> list:
    feats = []
    for name, com, status, cap, unit, oper, countries, note, pts in CURATED_PIPELINES:
        coords = [[round(lon, 3), round(lat, 3)] for lat, lon in pts]
        feats.append(line_feature([coords], props(
            PIPE_KEYS, name=name, commodity=com, status=status, capacity=cap, capacity_unit=unit,
            operator=oper, countries=countries, origin="curated", note=note)))
    return feats


def curated_facilities() -> list:
    feats = []
    for name, kind, com, also, cap, unit, country, oper, status, note, (lat, lon) in CURATED_FACILITIES:
        feats.append(point_feature(lon, lat, props(
            FAC_KEYS, name=name, kind=kind, commodity=com, also=also, capacity=cap, capacity_unit=unit,
            country=country, operator=oper, status=status, origin="curated", note=note)))
    return feats


def drop_near_curated(feats: list, curated: list, deg: float = 0.2) -> list:
    """Drop fetched points that duplicate a curated facility of the same kind."""
    out = []
    for f in feats:
        x, y = f["geometry"]["coordinates"]
        dup = any(c["properties"]["kind"] == f["properties"]["kind"]
                  and math.hypot(c["geometry"]["coordinates"][0] - x,
                                 c["geometry"]["coordinates"][1] - y) < deg
                  for c in curated)
        if not dup:
            out.append(f)
    return out


# ----------------------------------------------------------------------------
# write + validate
# ----------------------------------------------------------------------------
def write_fc(path: Path, feats: list) -> None:
    lines = [json.dumps(f, ensure_ascii=False, separators=(",", ":")) for f in feats]
    path.write_text('{"type":"FeatureCollection","features":[\n' + ",\n".join(lines) + "\n]}\n",
                    encoding="utf-8")


def iter_coords(geom: dict):
    t, c = geom.get("type"), geom.get("coordinates")
    if t == "Point":
        yield c
    elif t == "LineString":
        yield from c
    elif t == "MultiLineString":
        for part in c:
            yield from part
    else:
        raise AssertionError(f"unexpected geometry type {t}")


def validate(path: Path, layer: str) -> list:
    text = path.read_text(encoding="utf-8")
    assert chr(0x2014) not in text, f"{path.name}: contains an em dash"
    fc = json.loads(text)
    assert fc.get("type") == "FeatureCollection" and isinstance(fc.get("features"), list), path.name
    allowed = set(PIPE_KEYS if layer == "pipelines" else FAC_KEYS)
    required = ["name", "commodity", "origin"] + (["kind"] if layer == "facilities" else [])
    for i, f in enumerate(fc["features"]):
        p, g = f.get("properties") or {}, f.get("geometry") or {}
        tag = f"{path.name}[{i}] {p.get('name')!r}"
        assert f.get("type") == "Feature", tag
        for k in required:
            assert p.get(k) not in (None, ""), f"{tag}: missing {k}"
        assert set(p) <= allowed, f"{tag}: unexpected keys {set(p) - allowed}"
        assert all(v is not None for v in p.values()), f"{tag}: null property"
        pts = list(iter_coords(g))
        if layer == "pipelines":
            assert g["type"] in ("LineString", "MultiLineString"), tag
            parts = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
            assert parts and all(len(part) >= 2 for part in parts), f"{tag}: degenerate line"
            assert p["commodity"] in PIPE_COMMODITIES, f"{tag}: commodity {p['commodity']}"
            assert p.get("status", "operating") in PIPE_STATUS, tag
            assert p["origin"] in ("eia", "curated"), tag
            assert p.get("capacity_unit") is None or p["capacity_unit"] in PIPE_UNITS, tag
            assert isinstance(p.get("countries"), list) and all(len(c) == 3 for c in p["countries"]), tag
        else:
            assert g["type"] == "Point", tag
            assert p["kind"] in FAC_KINDS, f"{tag}: kind {p['kind']}"
            assert p["origin"] in ("eia", "usgs", "curated"), tag
            assert len(p.get("country", "XXX")) == 3, tag
        assert ("capacity" in p) == ("capacity_unit" in p), f"{tag}: capacity without unit"
        for c in pts:
            assert len(c) == 2 and all(isinstance(v, (int, float)) for v in c), f"{tag}: bad coord"
            assert -180 <= c[0] <= 180 and -90 <= c[1] <= 90, f"{tag}: out of range {c}"
    return fc["features"]


def report(feats: list, layer: str) -> None:
    print(f"\n{layer}: {len(feats)} features")
    print("  by origin:   ", dict(sorted(Counter(f["properties"]["origin"] for f in feats).items())))
    if layer == "facilities":
        print("  by kind:     ", dict(sorted(Counter(f["properties"]["kind"] for f in feats).items())))
    print("  by commodity:", dict(sorted(Counter(f["properties"]["commodity"] for f in feats).items())))
    if layer == "pipelines":
        print("  by status:   ", dict(sorted(Counter(f["properties"].get("status") for f in feats).items())))
        print("  vertices:    ", sum(len(list(iter_coords(f["geometry"]))) for f in feats))


def main() -> int:
    refresh = "--refresh" in sys.argv
    print("fetching sources (cache: data_store/raw/infra/)")
    z = {k: fetch(EIA_BASE + v, "eia_" + v, refresh) for k, v in EIA_LAYERS.items()}
    minfac = fetch(USGS_MINFAC, "usgs_minfac-csv.zip", refresh)

    cur_p, cur_f = curated_pipelines(), curated_facilities()
    pipes = (cur_p
             + eia_liquid_pipelines(z["crude"], "crude")
             + eia_liquid_pipelines(z["products"], "products")
             + eia_liquid_pipelines(z["hgl"], "hgl")
             + eia_gas_pipelines(z["gas"]))
    fetched = (eia_refineries(z["refineries"]) + eia_lng(z["lng"]) + eia_storage(z["storage"])
               + eia_coal(z["coal"]) + usgs_facilities(minfac))
    facs = cur_f + drop_near_curated(fetched, cur_f)

    write_fc(OUT_PIPES, pipes)
    write_fc(OUT_FACS, facs)

    report(validate(OUT_PIPES, "pipelines"), "pipelines")
    report(validate(OUT_FACS, "facilities"), "facilities")
    sp, sf = OUT_PIPES.stat().st_size, OUT_FACS.stat().st_size
    total = sp + sf
    print(f"\nsizes: {OUT_PIPES.name} {sp / 1e6:.2f} MB, {OUT_FACS.name} {sf / 1e6:.2f} MB, "
          f"combined {total / 1e6:.2f} MB (target {TARGET_BYTES / 2**20:.0f} MiB, hard cap {HARD_CAP_BYTES / 2**20:.0f} MiB)")
    if total > HARD_CAP_BYTES:
        print("FAIL: combined size exceeds the hard cap")
        return 1
    if total > TARGET_BYTES:
        print("WARNING: combined size is over the target (still under the hard cap)")
    print("validation OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

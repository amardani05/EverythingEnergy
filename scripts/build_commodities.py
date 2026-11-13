#!/usr/bin/env python
"""Build commodities.json: world commodity supply chains (metals + energy).

Run from the repo root:

    .venv/bin/python scripts/build_commodities.py            # uses a 7 day download cache
    .venv/bin/python scripts/build_commodities.py --refresh  # force re-download of everything

What is FETCHED on every run (machine sources):
  - USGS Mineral Commodity Summaries 2026 data release (ScienceBase CSV):
    country mine/smelter/refinery production (2025 estimate, 2024 prior), reserves,
    world totals, US net import reliance.
  - EIA international bulk file (INTL.zip): annual production and consumption by country
    for crude oil, dry natural gas, coal, plus refined petroleum products output.
  - World Nuclear Association "World Uranium Mining Production" HTML table.
  - UN Comtrade public preview API (key-free): partner-reported imports (mirror data)
    used to derive export destination shares for the leading exporters.

What is CURATED (hand-entered, lives in the dicts directly below):
  - COUNTRIES (names, ISO numeric codes, label points), USGS_NAMES.
  - CURATED[*]: name, group, basis text, end_uses, end_use_basis, key_inputs,
    price_tickers, notes, refining rows sourced from IEA charts, trade rows sourced
    from USGS chapter prose, and the Comtrade query definitions (HS codes, exporters).

The script is the single source of truth: re-running it reproduces commodities.json.
Downloads are cached outside the repo (system temp dir) so the repo only gains the JSON.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import html
import io
import json
import math
import os
import re
import sys
import tempfile
import time
import zipfile

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(ROOT, "commodities.json")
CACHE_DIR = os.path.join(tempfile.gettempdir(), "everythingenergy_commodities_cache")
CACHE_MAX_AGE_S = 7 * 24 * 3600

USGS_ITEM_ID = "696a75d5d4be0228872d3bf8"  # ScienceBase: Mineral Commodity Summaries 2026 Data Release
USGS_ITEM_URL = "https://www.sciencebase.gov/catalog/item/%s?format=json" % USGS_ITEM_ID
USGS_CSV_NAME = "MCS2026_Commodities_Data.csv"
USGS_YEAR = 2025  # MCS 2026 reports 2025 (estimated) and 2024
EIA_URL = "https://www.eia.gov/opendata/bulk/INTL.zip"
WNA_URL = ("https://world-nuclear.org/information-library/nuclear-fuel-cycle/"
           "mining-of-uranium/world-uranium-mining-production")
COMTRADE_URL = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
COMTRADE_REPORTERS_URL = "https://comtradeapi.un.org/files/v1/app/reference/Reporters.json"
TRADE_YEAR = 2024
# Mirror data undercount exports of sanctioned or opaque exporters (many of their buyers do not
# report, or report late), so destination shares for these are marked confidence "low".
LOW_CONF_EXPORTERS = {"RUS", "BLR", "PRK", "IRN", "MMR"}
ENERGY_TOP_N = 40  # EIA lists about 230 geographies; keep the top N and fold the rest into OTH

# =====================================================================================
# ============================  HAND-CURATED DATA STARTS HERE  ========================
# =====================================================================================

SOURCES = [
    {"id": "usgs_mcs",
     "title": "USGS Mineral Commodity Summaries 2026 Data Release (ScienceBase): "
              "commodity salient U.S. and world statistics, data year 2025 (estimated) and 2024",
     "url": "https://www.sciencebase.gov/catalog/item/696a75d5d4be0228872d3bf8"},
    {"id": "usgs_mcs_chapters",
     "title": "USGS Mineral Commodity Summaries 2026 (ver. 1.3), commodity chapter text: "
              "end uses, events, trade prose",
     "url": "https://doi.org/10.3133/mcs2026"},
    {"id": "eia_intl",
     "title": "U.S. EIA International Energy Statistics, bulk file INTL.zip",
     "url": "https://www.eia.gov/opendata/bulk/INTL.zip"},
    {"id": "wna_uranium",
     "title": "World Nuclear Association: World Uranium Mining Production (table, tonnes U)",
     "url": WNA_URL},
    {"id": "iea_gcmo_2026",
     "title": "IEA Global Critical Minerals Outlook 2026: executive summary and chart "
              "'Market size and top refining country share for select minerals, 2025'",
     "url": "https://www.iea.org/data-and-statistics/charts/"
            "market-size-and-top-refining-country-share-for-select-minerals-2025"},
    {"id": "iea_gcmo_2025",
     "title": "IEA Global Critical Minerals Outlook 2025: executive summary and chart "
              "'Share of top refining country for 20 energy-related minerals'",
     "url": "https://www.iea.org/data-and-statistics/charts/"
            "share-of-top-refining-country-for-20-energy-related-minerals"},
    {"id": "un_comtrade",
     "title": "UN Comtrade public preview API: partner-reported imports by HS code "
              "(mirror data), %d" % TRADE_YEAR,
     "url": "https://comtradeplus.un.org/"},
]

# USGS country names -> ISO3. "Other countries" becomes OTH (not in COUNTRIES).
USGS_NAMES = {
    "United States": "USA", "Argentina": "ARG", "Australia": "AUS", "Austria": "AUT",
    "Bahrain": "BHR", "Belarus": "BLR", "Bolivia": "BOL", "Brazil": "BRA", "Burma": "MMR",
    "Canada": "CAN", "Chile": "CHL", "China": "CHN", "Congo (Kinshasa)": "COD", "Cuba": "CUB",
    "Côte d’Ivoire": "CIV", "Cote d'Ivoire": "CIV", "Gabon": "GAB", "Germany": "DEU",
    "Ghana": "GHA", "Greece": "GRC", "Greenland": "GRL", "Guatemala": "GTM", "Guinea": "GIN",
    "Iceland": "ISL", "India": "IND", "Indonesia": "IDN", "Iran": "IRN", "Ireland": "IRL",
    "Israel": "ISR", "Jamaica": "JAM", "Japan": "JPN", "Jordan": "JOR", "Kazakhstan": "KAZ",
    "Korea, North": "PRK", "Korea, Republic of": "KOR", "Kyrgyzstan": "KGZ", "Laos": "LAO",
    "Madagascar": "MDG", "Malaysia": "MYS", "Mali": "MLI", "Mauritania": "MRT", "Mexico": "MEX",
    "Mozambique": "MOZ", "New Caledonia": "NCL", "Nigeria": "NGA", "Norway": "NOR",
    "Pakistan": "PAK", "Papua New Guinea": "PNG", "Peru": "PER", "Philippines": "PHL",
    "Poland": "POL", "Portugal": "PRT", "Russia": "RUS", "Rwanda": "RWA", "Saudi Arabia": "SAU",
    "South Africa": "ZAF", "Spain": "ESP", "Sri Lanka": "LKA", "Sweden": "SWE",
    "Tajikistan": "TJK", "Tanzania": "TZA", "Thailand": "THA", "Turkey": "TUR", "Ukraine": "UKR",
    "United Arab Emirates": "ARE", "Uzbekistan": "UZB", "Vietnam": "VNM", "Zambia": "ZMB",
    "Zimbabwe": "ZWE", "Other countries": "OTH",
}

# WNA table country labels -> ISO3
WNA_NAMES = {
    "Kazakhstan": "KAZ", "Canada": "CAN", "Namibia": "NAM", "Australia": "AUS",
    "Uzbekistan": "UZB", "Russia": "RUS", "China": "CHN", "Niger": "NER", "India": "IND",
    "South Africa": "ZAF", "Ukraine": "UKR", "USA": "USA", "Others": "OTH",
}

# Every country referenced anywhere in the output. iso_n3 = ISO 3166-1 numeric (zero-padded
# string, matches world-atlas topojson ids). lon/lat = label point. "name" follows USGS
# naming where USGS has one; "short" is the everyday short name.
COUNTRIES = {
    "AGO": {"name": "Angola", "short": "Angola", "iso_n3": "024", "lon": 18.5, "lat": -12.5},
    "ARE": {"name": "United Arab Emirates", "short": "UAE", "iso_n3": "784", "lon": 54.3, "lat": 24.0},
    "ARG": {"name": "Argentina", "short": "Argentina", "iso_n3": "032", "lon": -64.0, "lat": -34.0},
    "ARM": {"name": "Armenia", "short": "Armenia", "iso_n3": "051", "lon": 45.0, "lat": 40.0},
    "AUS": {"name": "Australia", "short": "Australia", "iso_n3": "036", "lon": 134.0, "lat": -25.0},
    "AUT": {"name": "Austria", "short": "Austria", "iso_n3": "040", "lon": 13.3, "lat": 47.3},
    "AZE": {"name": "Azerbaijan", "short": "Azerbaijan", "iso_n3": "031", "lon": 47.5, "lat": 40.5},
    "BEL": {"name": "Belgium", "short": "Belgium", "iso_n3": "056", "lon": 4.0, "lat": 50.8},
    "BGD": {"name": "Bangladesh", "short": "Bangladesh", "iso_n3": "050", "lon": 90.0, "lat": 24.0},
    "BGR": {"name": "Bulgaria", "short": "Bulgaria", "iso_n3": "100", "lon": 25.0, "lat": 43.0},
    "BHR": {"name": "Bahrain", "short": "Bahrain", "iso_n3": "048", "lon": 50.5, "lat": 26.0},
    "BIH": {"name": "Bosnia and Herzegovina", "short": "Bosnia and Herzegovina", "iso_n3": "070", "lon": 18.0, "lat": 44.0},
    "BLR": {"name": "Belarus", "short": "Belarus", "iso_n3": "112", "lon": 28.0, "lat": 53.0},
    "BOL": {"name": "Bolivia", "short": "Bolivia", "iso_n3": "068", "lon": -65.0, "lat": -17.0},
    "BRA": {"name": "Brazil", "short": "Brazil", "iso_n3": "076", "lon": -52.0, "lat": -10.5},
    "BWA": {"name": "Botswana", "short": "Botswana", "iso_n3": "072", "lon": 24.0, "lat": -22.0},
    "CAN": {"name": "Canada", "short": "Canada", "iso_n3": "124", "lon": -106.0, "lat": 57.0},
    "CHL": {"name": "Chile", "short": "Chile", "iso_n3": "152", "lon": -70.9, "lat": -30.0},
    "CHN": {"name": "China", "short": "China", "iso_n3": "156", "lon": 104.0, "lat": 35.5},
    "CIV": {"name": "Cote d'Ivoire", "short": "Ivory Coast", "iso_n3": "384", "lon": -5.0, "lat": 8.0},
    "COD": {"name": "Congo (Kinshasa)", "short": "DR Congo", "iso_n3": "180", "lon": 23.6, "lat": -2.9},
    "COG": {"name": "Congo (Brazzaville)", "short": "Congo Rep.", "iso_n3": "178", "lon": 15.0, "lat": -1.0},
    "COL": {"name": "Colombia", "short": "Colombia", "iso_n3": "170", "lon": -72.0, "lat": 4.0},
    "CUB": {"name": "Cuba", "short": "Cuba", "iso_n3": "192", "lon": -80.0, "lat": 21.5},
    "CZE": {"name": "Czechia", "short": "Czechia", "iso_n3": "203", "lon": 15.5, "lat": 49.8},
    "DEU": {"name": "Germany", "short": "Germany", "iso_n3": "276", "lon": 9.0, "lat": 51.0},
    "DZA": {"name": "Algeria", "short": "Algeria", "iso_n3": "012", "lon": 3.0, "lat": 28.0},
    "ECU": {"name": "Ecuador", "short": "Ecuador", "iso_n3": "218", "lon": -78.3, "lat": -1.4},
    "EGY": {"name": "Egypt", "short": "Egypt", "iso_n3": "818", "lon": 30.0, "lat": 27.0},
    "ESP": {"name": "Spain", "short": "Spain", "iso_n3": "724", "lon": -3.6, "lat": 40.2},
    "FRA": {"name": "France", "short": "France", "iso_n3": "250", "lon": 2.5, "lat": 46.6},
    "GAB": {"name": "Gabon", "short": "Gabon", "iso_n3": "266", "lon": 11.8, "lat": -1.0},
    "GBR": {"name": "United Kingdom", "short": "UK", "iso_n3": "826", "lon": -1.8, "lat": 53.5},
    "GHA": {"name": "Ghana", "short": "Ghana", "iso_n3": "288", "lon": -2.0, "lat": 8.0},
    "GIN": {"name": "Guinea", "short": "Guinea", "iso_n3": "324", "lon": -10.0, "lat": 11.0},
    "GRC": {"name": "Greece", "short": "Greece", "iso_n3": "300", "lon": 22.0, "lat": 39.3},
    "GRL": {"name": "Greenland", "short": "Greenland", "iso_n3": "304", "lon": -41.0, "lat": 72.0},
    "GTM": {"name": "Guatemala", "short": "Guatemala", "iso_n3": "320", "lon": -90.2, "lat": 15.5},
    "GUY": {"name": "Guyana", "short": "Guyana", "iso_n3": "328", "lon": -59.0, "lat": 5.0},
    "HKG": {"name": "Hong Kong", "short": "Hong Kong", "iso_n3": "344", "lon": 114.2, "lat": 22.3},
    "HUN": {"name": "Hungary", "short": "Hungary", "iso_n3": "348", "lon": 20.0, "lat": 47.0},
    "IDN": {"name": "Indonesia", "short": "Indonesia", "iso_n3": "360", "lon": 113.9, "lat": -0.8},
    "IND": {"name": "India", "short": "India", "iso_n3": "356", "lon": 77.0, "lat": 20.0},
    "IRL": {"name": "Ireland", "short": "Ireland", "iso_n3": "372", "lon": -8.0, "lat": 53.0},
    "IRN": {"name": "Iran", "short": "Iran", "iso_n3": "364", "lon": 53.0, "lat": 32.0},
    "IRQ": {"name": "Iraq", "short": "Iraq", "iso_n3": "368", "lon": 44.0, "lat": 33.0},
    "ISL": {"name": "Iceland", "short": "Iceland", "iso_n3": "352", "lon": -18.0, "lat": 65.0},
    "ISR": {"name": "Israel", "short": "Israel", "iso_n3": "376", "lon": 34.9, "lat": 31.2},
    "ITA": {"name": "Italy", "short": "Italy", "iso_n3": "380", "lon": 12.5, "lat": 42.8},
    "JAM": {"name": "Jamaica", "short": "Jamaica", "iso_n3": "388", "lon": -77.5, "lat": 18.2},
    "JOR": {"name": "Jordan", "short": "Jordan", "iso_n3": "400", "lon": 36.0, "lat": 31.0},
    "JPN": {"name": "Japan", "short": "Japan", "iso_n3": "392", "lon": 138.0, "lat": 36.5},
    "KAZ": {"name": "Kazakhstan", "short": "Kazakhstan", "iso_n3": "398", "lon": 67.0, "lat": 48.0},
    "KGZ": {"name": "Kyrgyzstan", "short": "Kyrgyzstan", "iso_n3": "417", "lon": 75.0, "lat": 41.0},
    "KHM": {"name": "Cambodia", "short": "Cambodia", "iso_n3": "116", "lon": 105.0, "lat": 13.0},
    "KOR": {"name": "Korea, Republic of", "short": "South Korea", "iso_n3": "410", "lon": 127.5, "lat": 37.0},
    "KWT": {"name": "Kuwait", "short": "Kuwait", "iso_n3": "414", "lon": 45.8, "lat": 29.5},
    "LAO": {"name": "Laos", "short": "Laos", "iso_n3": "418", "lon": 105.0, "lat": 18.0},
    "LBY": {"name": "Libya", "short": "Libya", "iso_n3": "434", "lon": 17.0, "lat": 25.0},
    "LKA": {"name": "Sri Lanka", "short": "Sri Lanka", "iso_n3": "144", "lon": 81.0, "lat": 7.0},
    "MAR": {"name": "Morocco", "short": "Morocco", "iso_n3": "504", "lon": -5.0, "lat": 32.0},
    "MDG": {"name": "Madagascar", "short": "Madagascar", "iso_n3": "450", "lon": 47.0, "lat": -20.0},
    "MEX": {"name": "Mexico", "short": "Mexico", "iso_n3": "484", "lon": -102.0, "lat": 23.0},
    "MKD": {"name": "North Macedonia", "short": "North Macedonia", "iso_n3": "807", "lon": 22.0, "lat": 41.8},
    "MLI": {"name": "Mali", "short": "Mali", "iso_n3": "466", "lon": -4.0, "lat": 17.0},
    "MMR": {"name": "Burma", "short": "Myanmar", "iso_n3": "104", "lon": 98.0, "lat": 22.0},
    "MNG": {"name": "Mongolia", "short": "Mongolia", "iso_n3": "496", "lon": 105.0, "lat": 46.0},
    "MOZ": {"name": "Mozambique", "short": "Mozambique", "iso_n3": "508", "lon": 35.0, "lat": -18.2},
    "MRT": {"name": "Mauritania", "short": "Mauritania", "iso_n3": "478", "lon": -12.0, "lat": 20.0},
    "MYS": {"name": "Malaysia", "short": "Malaysia", "iso_n3": "458", "lon": 102.2, "lat": 4.2},
    "NAM": {"name": "Namibia", "short": "Namibia", "iso_n3": "516", "lon": 17.0, "lat": -22.0},
    "NCL": {"name": "New Caledonia", "short": "New Caledonia", "iso_n3": "540", "lon": 165.5, "lat": -21.3},
    "NER": {"name": "Niger", "short": "Niger", "iso_n3": "562", "lon": 8.0, "lat": 16.0},
    "NGA": {"name": "Nigeria", "short": "Nigeria", "iso_n3": "566", "lon": 8.0, "lat": 10.0},
    "NLD": {"name": "Netherlands", "short": "Netherlands", "iso_n3": "528", "lon": 5.8, "lat": 52.5},
    "NOR": {"name": "Norway", "short": "Norway", "iso_n3": "578", "lon": 9.0, "lat": 61.5},
    "NZL": {"name": "New Zealand", "short": "New Zealand", "iso_n3": "554", "lon": 172.5, "lat": -42.0},
    "OMN": {"name": "Oman", "short": "Oman", "iso_n3": "512", "lon": 57.0, "lat": 21.0},
    "PAK": {"name": "Pakistan", "short": "Pakistan", "iso_n3": "586", "lon": 70.0, "lat": 30.0},
    "PER": {"name": "Peru", "short": "Peru", "iso_n3": "604", "lon": -76.0, "lat": -10.0},
    "PHL": {"name": "Philippines", "short": "Philippines", "iso_n3": "608", "lon": 122.0, "lat": 12.5},
    "PNG": {"name": "Papua New Guinea", "short": "Papua New Guinea", "iso_n3": "598", "lon": 144.5, "lat": -6.5},
    "POL": {"name": "Poland", "short": "Poland", "iso_n3": "616", "lon": 20.0, "lat": 52.0},
    "PRK": {"name": "Korea, North", "short": "North Korea", "iso_n3": "408", "lon": 127.0, "lat": 40.0},
    "PRT": {"name": "Portugal", "short": "Portugal", "iso_n3": "620", "lon": -8.0, "lat": 39.6},
    "QAT": {"name": "Qatar", "short": "Qatar", "iso_n3": "634", "lon": 51.2, "lat": 25.5},
    "ROU": {"name": "Romania", "short": "Romania", "iso_n3": "642", "lon": 25.0, "lat": 46.0},
    "RUS": {"name": "Russia", "short": "Russia", "iso_n3": "643", "lon": 95.0, "lat": 61.0},
    "RWA": {"name": "Rwanda", "short": "Rwanda", "iso_n3": "646", "lon": 30.0, "lat": -2.0},
    "SAU": {"name": "Saudi Arabia", "short": "Saudi Arabia", "iso_n3": "682", "lon": 45.0, "lat": 25.0},
    "SGP": {"name": "Singapore", "short": "Singapore", "iso_n3": "702", "lon": 103.8, "lat": 1.4},
    "SRB": {"name": "Serbia", "short": "Serbia", "iso_n3": "688", "lon": 21.0, "lat": 44.0},
    "SVK": {"name": "Slovakia", "short": "Slovakia", "iso_n3": "703", "lon": 19.5, "lat": 48.7},
    "SWE": {"name": "Sweden", "short": "Sweden", "iso_n3": "752", "lon": 15.0, "lat": 62.0},
    "TCD": {"name": "Chad", "short": "Chad", "iso_n3": "148", "lon": 19.0, "lat": 15.0},
    "THA": {"name": "Thailand", "short": "Thailand", "iso_n3": "764", "lon": 100.0, "lat": 15.0},
    "TJK": {"name": "Tajikistan", "short": "Tajikistan", "iso_n3": "762", "lon": 71.0, "lat": 39.0},
    "TKM": {"name": "Turkmenistan", "short": "Turkmenistan", "iso_n3": "795", "lon": 60.0, "lat": 40.0},
    "TTO": {"name": "Trinidad and Tobago", "short": "Trinidad", "iso_n3": "780", "lon": -61.0, "lat": 11.0},
    "TUR": {"name": "Turkey", "short": "Turkey", "iso_n3": "792", "lon": 35.0, "lat": 39.0},
    "TWN": {"name": "Taiwan", "short": "Taiwan", "iso_n3": "158", "lon": 121.0, "lat": 23.5},
    "TZA": {"name": "Tanzania", "short": "Tanzania", "iso_n3": "834", "lon": 35.0, "lat": -6.0},
    "UKR": {"name": "Ukraine", "short": "Ukraine", "iso_n3": "804", "lon": 32.0, "lat": 49.0},
    "USA": {"name": "United States", "short": "USA", "iso_n3": "840", "lon": -98.5, "lat": 39.5},
    "UZB": {"name": "Uzbekistan", "short": "Uzbekistan", "iso_n3": "860", "lon": 64.0, "lat": 41.0},
    "VEN": {"name": "Venezuela", "short": "Venezuela", "iso_n3": "862", "lon": -66.0, "lat": 8.0},
    "VNM": {"name": "Vietnam", "short": "Vietnam", "iso_n3": "704", "lon": 106.3, "lat": 16.2},
    "ZAF": {"name": "South Africa", "short": "South Africa", "iso_n3": "710", "lon": 24.0, "lat": -29.0},
    "ZMB": {"name": "Zambia", "short": "Zambia", "iso_n3": "894", "lon": 30.0, "lat": -15.0},
    "ZWE": {"name": "Zimbabwe", "short": "Zimbabwe", "iso_n3": "716", "lon": 30.0, "lat": -20.0},
}

# Yahoo Finance symbols below were checked against the Yahoo chart endpoint on 2026-11-12.
# kind: future | etf | equity. Equities and ETFs are proxies, not price benchmarks.
#
# Refining rows marked source iea_* are transcribed from the IEA chart data embedded in the
# chart pages listed in SOURCES. The 2026 chart gives the top-1 refining country share for
# 2025 but does not print the country name; the country is taken from the report text
# ("Indonesia for nickel and China for other key energy minerals") and the 2025 edition chart,
# which names it. Where neither names the country the row is marked confidence "medium".
#
# usgs: how to read the USGS CSV for this commodity (chapter, Statistics_detail strings).
# comtrade: list of mirror-trade queries (HS code, description, exporter ISO3 list).
CURATED = {
    "crude_oil": {
        "name": "Crude oil", "group": "energy", "source": "eia_intl",
        "eia": {"prod": "57-1", "cons": "5-2", "suffix": "TBPD", "refining": "54-1",
                "unit": "kb/d", "digits": 1,
                "basis": "crude oil including lease condensate production, thousand barrels per day; "
                         "consumers = petroleum and other liquids consumption (kb/d); "
                         "refining = refined petroleum products output (kb/d)",
                "refining_stage": "refined petroleum products output"},
        "end_uses": [{"use": "Road transport fuels (gasoline, diesel)"},
                     {"use": "Aviation and marine fuels"},
                     {"use": "Petrochemical feedstocks"},
                     {"use": "Industry, heating and power generation"}],
        "end_use_basis": "global, qualitative (no shares sourced)",
        "key_inputs": ["Refinery capacity matched to crude grade (light sweet vs heavy sour)",
                       "Seaborne chokepoints: Strait of Hormuz, Malacca, Suez and Bab el-Mandeb",
                       "Diluent and upgrading for heavy crude (Canada, Venezuela)",
                       "Oilfield services, rigs, frac sand and water for shale"],
        "price_tickers": [{"symbol": "CL=F", "label": "NYMEX WTI crude", "kind": "future"},
                          {"symbol": "BZ=F", "label": "Brent crude", "kind": "future"},
                          {"symbol": "USO", "label": "United States Oil Fund", "kind": "etf"}],
        "notes": "Production is concentrated in the US, Saudi Arabia and Russia, but export flows are what "
                 "move prices: Comtrade mirror data show Russian crude going almost entirely to China and "
                 "India. IEA (2026) describes a Middle East conflict with a Strait of Hormuz closure and a "
                 "record IEA stock release in March 2026; treat Gulf export numbers here (2024 trade, "
                 "pre-disruption) with care.",
        "comtrade": [{"hs": "2709", "what": "crude petroleum (HS 2709)",
                      "exporters": ["SAU", "RUS", "USA", "CAN", "IRQ"]}],
    },
    "natural_gas": {
        "name": "Natural gas", "group": "energy", "source": "eia_intl",
        "eia": {"prod": "26-1", "cons": "26-2", "suffix": "BCM", "unit": "bcm", "digits": 2,
                "basis": "dry natural gas production, billion cubic meters; "
                         "consumers = dry natural gas consumption (bcm)"},
        "end_uses": [{"use": "Power generation"},
                     {"use": "Industry (process heat and feedstock, including ammonia and methanol)"},
                     {"use": "Residential and commercial heating"},
                     {"use": "Energy sector own use and LNG liquefaction"}],
        "end_use_basis": "global, qualitative (no shares sourced)",
        "key_inputs": ["Pipeline and LNG liquefaction/regasification capacity (market is regional, not global)",
                       "LNG carrier availability and chokepoints (Hormuz for Qatar)",
                       "Associated gas output tied to oil drilling (US Permian)",
                       "Storage levels ahead of winter"],
        "price_tickers": [{"symbol": "NG=F", "label": "NYMEX Henry Hub natural gas", "kind": "future"},
                          {"symbol": "TTF=F", "label": "Dutch TTF natural gas", "kind": "future"},
                          {"symbol": "UNG", "label": "United States Natural Gas Fund", "kind": "etf"},
                          {"symbol": "LNG", "label": "Cheniere Energy (US LNG exporter)", "kind": "equity"}],
        "notes": "The US and Russia produce about 40% of world gas, but most gas is consumed where it is "
                 "produced; the traded margin is LNG (US, Qatar, Australia). Qatar LNG transits the Strait "
                 "of Hormuz, which IEA (2026) reports was closed during the 2026 Middle East conflict.",
        "comtrade": [{"hs": "271111", "what": "LNG (HS 271111)", "exporters": ["USA", "QAT", "AUS"]},
                     {"hs": "271121", "what": "pipeline natural gas (HS 271121)",
                      "exporters": ["RUS", "NOR", "CAN"]}],
    },
    "coal": {
        "name": "Coal", "group": "energy", "source": "eia_intl",
        "eia": {"prod": "7-1", "cons": "7-2", "suffix": "MT", "unit": "kt", "digits": 0,
                "basis": "coal production, all ranks (thermal + metallurgical + lignite), thousand metric "
                         "tons; consumers = coal consumption (kt)"},
        "end_uses": [{"use": "Power generation (thermal coal)"},
                     {"use": "Steelmaking (metallurgical coal and coke)"},
                     {"use": "Cement and other industry"}],
        "end_use_basis": "global, qualitative (no shares sourced)",
        "key_inputs": ["Rail and port capacity (Newcastle, Richards Bay, Kalimantan barging)",
                       "Diesel and explosives for mining",
                       "Chinese and Indian domestic output policy, which sets seaborne import demand"],
        "price_tickers": [{"symbol": "BTU", "label": "Peabody Energy (thermal + met coal)", "kind": "equity"},
                          {"symbol": "HCC", "label": "Warrior Met Coal (met coal)", "kind": "equity"},
                          {"symbol": "ARLP", "label": "Alliance Resource Partners (US thermal)", "kind": "equity"}],
        "notes": "China mines and burns roughly half of the world's coal, so the seaborne market "
                 "(Indonesia, Australia, Russia) is a small residual driven by Chinese and Indian import "
                 "swings. Metallurgical coal was added to the US critical minerals list in November 2025 (USGS).",
        "comtrade": [{"hs": "2701", "what": "coal (HS 2701)", "exporters": ["IDN", "AUS", "RUS", "USA"]}],
    },
    "uranium": {
        "name": "Uranium", "group": "nuclear", "source": "wna_uranium",
        "wna": {"unit": "tU", "basis": "mine production, tonnes of contained uranium (tU); "
                                       "1 tU = 1.179 t U3O8"},
        "end_uses": [{"use": "Nuclear power reactor fuel (after conversion, enrichment, fabrication)"},
                     {"use": "Naval propulsion, research reactors and medical isotopes"}],
        "end_use_basis": "global, qualitative (no shares sourced)",
        "key_inputs": ["Sulfuric acid for in situ leaching (over half of world output is ISL, per WNA)",
                       "Conversion and enrichment capacity, concentrated in a few countries including Russia",
                       "Transport routes out of Central Asia (via Russia or the trans-Caspian corridor)",
                       "Byproduct supply from copper mining (Olympic Dam)"],
        "price_tickers": [{"symbol": "URA", "label": "Global X Uranium ETF", "kind": "etf"},
                          {"symbol": "URNM", "label": "Sprott Uranium Miners ETF", "kind": "etf"},
                          {"symbol": "SRUUF", "label": "Sprott Physical Uranium Trust (OTC)", "kind": "etf"},
                          {"symbol": "CCJ", "label": "Cameco", "kind": "equity"}],
        "notes": "Kazakhstan (39%), Canada (24%) and Namibia (12%) supplied about three quarters of 2024 "
                 "mine output; mines covered about 90% of reactor demand, the rest came from inventories "
                 "and secondary supply (WNA). Niger output has fallen by three quarters since 2015. "
                 "Customs data on uranium are often suppressed, so trade rows are low confidence.",
        "comtrade": [{"hs": "284410", "what": "natural uranium and compounds (HS 284410)",
                      "exporters": ["KAZ", "CAN", "NAM"], "confidence": "low"}],
    },
    "copper": {
        "name": "Copper", "group": "base metals", "source": "usgs_mcs",
        "usgs": {"chapter": "COPPER", "prod": "Mine production", "reserves": "Reserves",
                 "basis": "mine production, copper content, thousand metric tons",
                 "refining": {"chapter": "COPPER", "detail": "Refinery production",
                              "stage": "refined copper (primary + secondary refinery output)"},
                 "nir": r"^Net import reliance"},
        "end_uses": [{"use": "Building construction", "share": 0.42},
                     {"use": "Electrical and electronic products", "share": 0.23},
                     {"use": "Transportation equipment", "share": 0.18},
                     {"use": "Consumer and general products", "share": 0.10},
                     {"use": "Industrial machinery and equipment", "share": 0.07}],
        "end_use_basis": "US (Copper Development Association via USGS MCS 2026)",
        "key_inputs": ["Sulfuric acid for SX-EW leaching (DRC, Zambia, Chile oxide ores)",
                       "Grid power and diesel; water in the Atacama",
                       "Smelter capacity: concentrate must find a smelter, half of which are in China",
                       "Byproduct credits (cobalt in DRC, molybdenum in Chile/Peru, gold in Indonesia)"],
        "price_tickers": [{"symbol": "HG=F", "label": "COMEX copper", "kind": "future"},
                          {"symbol": "CPER", "label": "United States Copper Index Fund", "kind": "etf"},
                          {"symbol": "COPX", "label": "Global X Copper Miners ETF", "kind": "etf"},
                          {"symbol": "FCX", "label": "Freeport-McMoRan", "kind": "equity"}],
        "notes": "DR Congo is now the second largest copper miner (about 14% of 2025 mine output) and the "
                 "second largest refined producer, ahead of Peru; Chile leads mining but China refines "
                 "about 48%. COMEX copper averaged a record in 2025 (USGS). IEA (2026) reports benchmark "
                 "smelter fees settled at USD 0/t for 2026 and still projects a 2035 supply gap near 25%.",
        "comtrade": [{"hs": "2603", "what": "copper ores and concentrates (HS 2603)",
                      "exporters": ["CHL", "PER", "IDN"]},
                     {"hs": "7403", "what": "refined copper cathode (HS 7403)",
                      "exporters": ["COD", "CHL"]},
                     {"hs": "7402", "what": "unrefined copper, blister and anodes (HS 7402)",
                      "exporters": ["COD", "ZMB"]}],
    },
    "cobalt": {
        "name": "Cobalt", "group": "battery metals", "source": "usgs_mcs",
        "usgs": {"chapter": "COBALT", "prod": "Mine production", "reserves": "Reserves",
                 "basis": "mine production, cobalt content, metric tons",
                 "nir": r"^Net import reliance"},
        "refining": [{"iso3": "CHN", "share": 0.76, "stage": "refined cobalt", "year": 2025,
                      "source": "iea_gcmo_2026", "confidence": "high"}],
        "end_uses": [{"use": "Superalloys (aircraft gas turbine engines)", "share": 0.51},
                     {"use": "Chemical applications", "share": 0.25},
                     {"use": "Other metallic applications", "share": 0.15},
                     {"use": "Cemented carbides (cutting and wear-resistant tools)", "share": 0.09}],
        "end_use_basis": "US (USGS MCS 2026). Globally the leading use is lithium-ion batteries: USGS "
                         "notes China is the leading consumer, primarily for batteries, but gives no "
                         "global share.",
        "key_inputs": ["Byproduct of copper mining (DRC, Zambia) and nickel mining (Indonesia HPAL, "
                       "Russia, Canada, Australia): cobalt supply follows host-metal economics",
                       "Sulfuric acid and sulfur for leaching (IEA 2026 flags acid cost spikes)",
                       "Chinese refineries that convert DRC hydroxide to sulfate and metal",
                       "Trucking and rail corridors from Katanga to Durban, Dar es Salaam, Lobito"],
        "price_tickers": [{"symbol": "3993.HK", "label": "CMOC Group (largest cobalt miner, DRC)", "kind": "equity"},
                          {"symbol": "603799.SS", "label": "Zhejiang Huayou Cobalt (refiner)", "kind": "equity"},
                          {"symbol": "GLEN.L", "label": "Glencore (DRC copper-cobalt)", "kind": "equity"}],
        "notes": "DR Congo mined an estimated 74% of world cobalt in 2025 and Indonesia 14% (USGS). DRC "
                 "banned exports in February 2025 and replaced the ban in October 2025 with quotas: "
                 "18,125 t for the rest of 2025 and up to 96,600 t per year in 2026 and 2027, well under "
                 "half of mine output. IEA reports cobalt prices rose about 130% between January 2025 and "
                 "April 2026 and now projects a supply gap. China refines about three quarters.",
        "comtrade": [{"hs": "810520", "what": "cobalt intermediates incl. crude hydroxide, mattes, "
                                              "unwrought metal (HS 810520)",
                      "exporters": ["COD"]}],
    },
    "lithium": {
        "name": "Lithium", "group": "battery metals", "source": "usgs_mcs",
        "usgs": {"chapter": "LITHIUM", "prod": "Mine production", "reserves": "Reserves",
                 "basis": "mine production, lithium content, metric tons (multiply by 5.323 for "
                          "lithium carbonate equivalent); world total excludes withheld US output",
                 "nir": r"^Net import reliance"},
        "refining": [{"iso3": "CHN", "share": 0.74, "stage": "refined lithium chemicals", "year": 2025,
                      "source": "iea_gcmo_2026", "confidence": "high"}],
        "end_uses": [{"use": "Batteries (EV, grid storage, portable)", "share": 0.88},
                     {"use": "Ceramics and glass", "share": 0.04},
                     {"use": "Lubricating greases", "share": 0.02},
                     {"use": "Air treatment", "share": 0.01},
                     {"use": "Continuous casting mold flux powders", "share": 0.01},
                     {"use": "Medical", "share": 0.01},
                     {"use": "Other", "share": 0.03}],
        "end_use_basis": "global (USGS MCS 2026)",
        "key_inputs": ["Hard-rock spodumene (Australia, Zimbabwe, Mali, Brazil) needs conversion, mostly in China",
                       "Brine operations (Chile, Argentina) need water rights, evaporation time, soda ash",
                       "Sulfuric acid and energy for spodumene conversion",
                       "Lepidolite output in Jiangxi, China is the high-cost swing supply"],
        "price_tickers": [{"symbol": "LIT", "label": "Global X Lithium & Battery Tech ETF", "kind": "etf"},
                          {"symbol": "ALB", "label": "Albemarle", "kind": "equity"},
                          {"symbol": "SQM", "label": "SQM", "kind": "equity"},
                          {"symbol": "PLS.AX", "label": "PLS (Pilbara spodumene)", "kind": "equity"},
                          {"symbol": "LTH=F", "label": "CME lithium hydroxide CIF CJK (thinly traded)", "kind": "future"}],
        "notes": "World output rose about 31% in 2025 to roughly 290,000 t Li while consumption was about "
                 "263,000 t (USGS). Zimbabwe is now the fourth largest miner at about 10%, ahead of "
                 "Argentina, and Mali has entered the list. China refines about 74% (IEA); IEA reports "
                 "prices more than doubled into early 2026 and that Zimbabwe has introduced lithium "
                 "trade restrictions.",
        "comtrade": [{"hs": "283691", "what": "lithium carbonate (HS 283691)", "exporters": ["CHL", "ARG"]},
                     {"hs": "253090", "what": "mineral substances n.e.s. (HS 253090: mostly spodumene "
                                              "concentrate for these exporters, but a mixed code)",
                      "exporters": ["AUS", "ZWE"], "confidence": "low"}],
    },
    "nickel": {
        "name": "Nickel", "group": "battery metals", "source": "usgs_mcs",
        "usgs": {"chapter": "NICKEL", "prod": "Mine production", "reserves": "Reserves",
                 "basis": "mine production, nickel content, metric tons",
                 "nir": r"^Net import reliance"},
        "refining": [{"iso3": "IDN", "share": 0.45, "stage": "refined nickel (all primary products)",
                      "year": 2025, "source": "iea_gcmo_2026", "confidence": "high"}],
        "end_uses": [{"use": "Stainless and alloy steels, nickel-base alloys", "share": 0.85},
                     {"use": "Electroplating, catalysts, chemicals (includes battery materials)", "share": 0.15}],
        "end_use_basis": "US (USGS MCS 2026 says steels and alloys are 'more than 85%'; the remainder is "
                         "assigned to the second line)",
        "key_inputs": ["Thermal coal power for Indonesian NPI smelters (RKEF)",
                       "Sulfuric acid and sulfur for HPAL plants (Indonesia) that make battery-grade MHP",
                       "Indonesian ore mining quotas (RKAB) and Philippine ore exports to Indonesia and China",
                       "Cobalt byproduct credits"],
        "price_tickers": [{"symbol": "NICK.L", "label": "WisdomTree Nickel ETC (London)", "kind": "etf"},
                          {"symbol": "INCO.JK", "label": "Vale Indonesia", "kind": "equity"},
                          {"symbol": "VALE", "label": "Vale (Canada, Brazil, Indonesia nickel)", "kind": "equity"}],
        "notes": "Indonesia mined about two thirds of world nickel in 2025 and its output rose 13%, while "
                 "Australian output fell an estimated 54% as mines went on care and maintenance (USGS). "
                 "The market has been in surplus since 2022 (INSG via USGS). Indonesia bans raw ore "
                 "exports, so its exports are ferronickel/NPI, matte and MHP, overwhelmingly to China.",
        "comtrade": [{"hs": "720260", "what": "ferronickel and nickel pig iron (HS 720260)", "exporters": ["IDN"]},
                     {"hs": "7501", "what": "nickel matte and intermediates incl. MHP (HS 7501)",
                      "exporters": ["IDN"]},
                     {"hs": "2604", "what": "nickel ores and concentrates (HS 2604)",
                      "exporters": ["PHL", "NCL"]}],
    },
    "aluminum": {
        "name": "Aluminum", "group": "base metals", "source": "usgs_mcs",
        "usgs": {"chapter": "ALUMINUM", "prod": "Smelter production",
                 "basis": "primary aluminum smelter production, thousand metric tons (no reserves: "
                          "see bauxite)",
                 "self_refining_stage": "primary aluminum smelting (same data as producers)",
                 "nir": r"^Net import reliance"},
        "end_uses": [{"use": "Transportation", "share": 0.36},
                     {"use": "Packaging", "share": 0.24},
                     {"use": "Building", "share": 0.13},
                     {"use": "Electrical", "share": 0.09},
                     {"use": "Consumer durables", "share": 0.08},
                     {"use": "Machinery", "share": 0.08},
                     {"use": "Other", "share": 0.02}],
        "end_use_basis": "US (USGS MCS 2026)",
        "key_inputs": ["Electricity: roughly 13 to 15 MWh per tonne, the dominant cost and the usual reason for curtailments",
                       "Alumina (about 2 t per t of metal), itself from about 4 t of bauxite (USGS rule of thumb)",
                       "Carbon anodes (petroleum coke, coal tar pitch), aluminum fluoride",
                       "China's 45 Mt per year smelting capacity cap"],
        "price_tickers": [{"symbol": "ALI=F", "label": "COMEX aluminum", "kind": "future"},
                          {"symbol": "AA", "label": "Alcoa", "kind": "equity"},
                          {"symbol": "CENX", "label": "Century Aluminum", "kind": "equity"},
                          {"symbol": "NHYDY", "label": "Norsk Hydro ADR", "kind": "equity"}],
        "notes": "China smelts about 61% of primary aluminum. The US raised Section 232 aluminum tariffs "
                 "to 50% in June 2025 (USGS). IEA (2026) says the Middle East supplies about 8% of world "
                 "aluminum and that smelter curtailments after the Strait of Hormuz closure tightened the "
                 "market.",
        "comtrade": [{"hs": "7601", "what": "unwrought aluminum (HS 7601)",
                      "exporters": ["RUS", "CAN", "IND", "ARE"]}],
    },
    "bauxite": {
        "name": "Bauxite", "group": "bulk", "source": "usgs_mcs",
        "usgs": {"chapter": "BAUXITE AND ALUMINA", "prod": "Bauxite, mine production",
                 "reserves": "Bauxite reserves",
                 "basis": "bauxite mine production, thousand metric dry tons; refining = alumina "
                          "refinery production",
                 "refining": {"chapter": "BAUXITE AND ALUMINA", "detail": "Alumina, refinery production",
                              "stage": "alumina refining (Bayer process)"},
                 "nir": r"^Net import reliance", "nir_index": 0},
        "end_uses": [{"use": "Alumina via the Bayer process (for aluminum smelting)", "share": 0.63},
                     {"use": "Abrasives, cement, chemicals, proppants, refractories, steel slag adjuster",
                      "share": 0.37}],
        "end_use_basis": "US bauxite consumption (USGS MCS 2026); the US share to alumina is low by world "
                         "standards because it has one operating refinery",
        "key_inputs": ["Caustic soda and energy (gas or coal) for the Bayer process",
                       "Bulk shipping from Guinea to China (long-haul capesize route)",
                       "Mining licences and export policy in Guinea and Indonesia"],
        "price_tickers": [{"symbol": "AA", "label": "Alcoa (bauxite, alumina, aluminum)", "kind": "equity"},
                          {"symbol": "S32.AX", "label": "South32 (alumina, bauxite, manganese)", "kind": "equity"},
                          {"symbol": "2600.HK", "label": "Chalco", "kind": "equity"}],
        "notes": "Guinea is the largest bauxite miner (about a third of world output) and ships most of it "
                 "to China, which refines about 62% of world alumina. Indonesia banned bauxite exports in "
                 "2023; Guinea revoked licences from a UAE-based producer in August 2025 after a dispute "
                 "over building a refinery (USGS).",
        "comtrade": [{"hs": "2606", "what": "aluminum ores and concentrates, bauxite (HS 2606)",
                      "exporters": ["GIN", "AUS", "BRA"]}],
    },
    "iron_ore": {
        "name": "Iron ore", "group": "bulk", "source": "usgs_mcs",
        "usgs": {"chapter": "IRON ORE", "prod": "Mine production: Usable ore",
                 "reserves": "Reserves (million metric tons): Crude ore", "reserves_scale": 1000.0,
                 "basis": "mine production, usable ore, thousand metric tons; reserves are crude ore "
                          "(source in million metric tons, converted x1000 to kt); refining = pig iron "
                          "production",
                 "refining": {"chapter": "IRON AND STEEL", "detail": "Pig iron",
                              "stage": "pig iron (blast furnace) production"},
                 "nir": r"^Net import reliance"},
        "end_uses": [{"use": "Steelmaking", "share": 0.98},
                     {"use": "Non-steel uses (cement, heavy media, pigments)", "share": 0.02}],
        "end_use_basis": "US (USGS MCS 2026)",
        "key_inputs": ["Metallurgical coal and coke for blast furnaces",
                       "Rail and port systems (Pilbara, Carajas) and capesize freight",
                       "Chinese steel output policy, which sets seaborne demand"],
        "price_tickers": [{"symbol": "VALE", "label": "Vale", "kind": "equity"},
                          {"symbol": "RIO", "label": "Rio Tinto", "kind": "equity"},
                          {"symbol": "BHP", "label": "BHP", "kind": "equity"},
                          {"symbol": "SLX", "label": "VanEck Steel ETF", "kind": "etf"}],
        "notes": "Australia and Brazil mine about 54% of usable ore and dominate seaborne trade, with "
                 "roughly 85% of Australian ore going to China, which makes about 64% of world pig iron. "
                 "62% Fe fines averaged about USD 99/t in the first nine months of 2025, down from "
                 "USD 112/t (USGS). World steel demand was flat in 2025.",
        "comtrade": [{"hs": "2601", "what": "iron ores and concentrates (HS 2601)",
                      "exporters": ["AUS", "BRA", "ZAF"]}],
    },
    "zinc": {
        "name": "Zinc", "group": "base metals", "source": "usgs_mcs",
        "usgs": {"chapter": "ZINC", "prod": "Mine production", "reserves": "Reserves",
                 "basis": "mine production, zinc content of concentrates and direct shipping ores, "
                          "thousand metric tons",
                 "nir": r"^Net import reliance.*Refined zinc"},
        "refining": [{"iso3": "CHN", "share": 0.49, "stage": "refined zinc", "year": 2025,
                      "source": "iea_gcmo_2026", "confidence": "medium"}],
        "end_uses": [{"use": "Galvanized steel (leading use)"},
                     {"use": "Brass and bronze"},
                     {"use": "Zinc-base alloys (die casting)"},
                     {"use": "Other (chemicals, rubber, agriculture)"}],
        "end_use_basis": "US, descending order, no shares published (USGS MCS 2026)",
        "key_inputs": ["Smelter electricity (electrolytic zinc is power intensive)",
                       "Concentrate treatment charges, which have turned negative per IEA (2026)",
                       "Zinc smelters are the main source of byproduct germanium, indium and cadmium"],
        "price_tickers": [{"symbol": "ZINC.L", "label": "WisdomTree Zinc ETC (London)", "kind": "etf"},
                          {"symbol": "TECK", "label": "Teck Resources (Red Dog, Trail)", "kind": "equity"},
                          {"symbol": "BOL.ST", "label": "Boliden", "kind": "equity"}],
        "notes": "Mining is diversified (China about a third, then Peru, Australia, India, Mexico) but "
                 "China holds about half of refining (IEA; country inferred from context, the chart does "
                 "not name it). ILZSG expected a small 2025 refined surplus of about 85,000 t (USGS). "
                 "US Red Dog output is declining as the mine nears end of life.",
        "comtrade": [{"hs": "2608", "what": "zinc ores and concentrates (HS 2608)",
                      "exporters": ["PER", "AUS", "BOL"]}],
    },
    "tin": {
        "name": "Tin", "group": "base metals", "source": "usgs_mcs",
        "usgs": {"chapter": "TIN", "prod": "Mine production", "reserves": "Reserves",
                 "basis": "mine production, tin content, metric tons",
                 "nir": r"^Net import reliance"},
        "end_uses": [{"use": "Chemicals", "share": 0.25},
                     {"use": "Tinplate", "share": 0.16},
                     {"use": "Alloys", "share": 0.12},
                     {"use": "Solder", "share": 0.11},
                     {"use": "Babbitt, brass and bronze, tinning", "share": 0.07},
                     {"use": "Bar tin", "share": 0.02},
                     {"use": "Other", "share": 0.27}],
        "end_use_basis": "US (USGS MCS 2026); globally solder for electronics is generally cited as the "
                         "largest use",
        "key_inputs": ["Artisanal and small-scale mining (DRC, Rwanda, Nigeria, Indonesia offshore dredging)",
                       "Burma (Wa State) concentrate feed for Chinese smelters",
                       "Indonesian export licensing through the ICDX/JFX exchanges"],
        "price_tickers": [{"symbol": "AFM.V", "label": "Alphamin Resources (Bisie mine, DR Congo)", "kind": "equity"},
                          {"symbol": "TINS.JK", "label": "PT Timah (Indonesia)", "kind": "equity"},
                          {"symbol": "MLX.AX", "label": "Metals X (Renison, Australia)", "kind": "equity"}],
        "notes": "A small market (about 290,000 t per year) with supply split across China, Indonesia, "
                 "Peru, Brazil and DR Congo, each prone to disruption. DR Congo is about 9% of mine "
                 "output, much of it understood to come from the Bisie mine in North Kivu, so conflict in "
                 "eastern DRC matters for tin even though cobalt and copper are mined far to the south. IEA says base metal prices including tin rose by a third from January 2025 to "
                 "April 2026.",
        "comtrade": [{"hs": "2609", "what": "tin ores and concentrates (HS 2609)",
                      "exporters": ["COD", "MMR", "AUS"]},
                     {"hs": "8001", "what": "unwrought tin (HS 8001)", "exporters": ["IDN", "PER"]}],
    },
    "manganese": {
        "name": "Manganese", "group": "bulk", "source": "usgs_mcs",
        "usgs": {"chapter": "MANGANESE", "prod": "Mine production", "reserves": "Reserves",
                 "basis": "mine production, manganese content, thousand metric tons",
                 "nir": r"^Net import reliance"},
        "refining": [{"iso3": "CHN", "share": 0.95, "stage": "high-purity manganese sulphate "
                                                              "(battery grade only)",
                      "year": 2024, "source": "iea_gcmo_2025", "confidence": "high"}],
        "end_uses": [{"use": "Steelmaking (ferromanganese, silicomanganese): consumption tracks steel"},
                     {"use": "Batteries (dry cell; high-purity sulphate for lithium-ion cathodes)"},
                     {"use": "Chemicals, fertilizers, animal feed, brick colorant"}],
        "end_use_basis": "US, qualitative, no shares published (USGS MCS 2026)",
        "key_inputs": ["Rail and port capacity in South Africa (Transnet) and Gabon (Transgabonais)",
                       "Electricity for ferroalloy furnaces",
                       "Weather exposure: an Australian mine was shut by a cyclone from 2024 to May 2025"],
        "price_tickers": [{"symbol": "S32.AX", "label": "South32 (GEMCO, South Africa manganese)", "kind": "equity"},
                          {"symbol": "ERA.PA", "label": "Eramet (Comilog, Gabon)", "kind": "equity"}],
        "notes": "South Africa, Gabon and Ghana mine about 73% of manganese; ore is cheap and plentiful, "
                 "so the exposure is logistics rather than geology. The 95% China figure applies only to "
                 "battery-grade manganese sulphate, a small slice of the market; most ore becomes "
                 "ferroalloys. The US has had no manganese mining since 1970 (USGS).",
        "comtrade": [{"hs": "2602", "what": "manganese ores and concentrates (HS 2602)",
                      "exporters": ["ZAF", "GAB", "GHA"]}],
    },
    "graphite": {
        "name": "Graphite (natural)", "group": "battery metals", "source": "usgs_mcs",
        "usgs": {"chapter": "GRAPHITE (NATURAL)", "prod": "Mine production", "reserves": "Reserves",
                 "basis": "natural graphite mine production, metric tons (synthetic graphite excluded)",
                 "nir": r"^Net import reliance"},
        "refining": [{"iso3": "CHN", "share": 0.95, "stage": "graphite processing (battery-grade anode "
                                                              "material)",
                      "year": 2024, "source": "iea_gcmo_2025", "confidence": "high"}],
        "trade": [
            {"from": "CHN", "to": "JPN", "share": 0.36, "what": "natural graphite exports, Jan-Sep 2025",
             "year": 2025, "source": "usgs_mcs_chapters", "confidence": "high"},
            {"from": "CHN", "to": "IDN", "share": 0.22, "what": "natural graphite exports, Jan-Sep 2025",
             "year": 2025, "source": "usgs_mcs_chapters", "confidence": "high"},
            {"from": "CHN", "to": "KOR", "share": 0.10, "what": "natural graphite exports, Jan-Sep 2025",
             "year": 2025, "source": "usgs_mcs_chapters", "confidence": "high"},
            {"from": "CHN", "to": "DEU", "share": 0.08, "what": "natural graphite exports, Jan-Sep 2025",
             "year": 2025, "source": "usgs_mcs_chapters", "confidence": "high"},
            {"from": "CHN", "to": "KOR", "share": 0.40, "what": "spherical purified graphite exports, Jan-Sep 2025",
             "year": 2025, "source": "usgs_mcs_chapters", "confidence": "high"},
            {"from": "CHN", "to": "IDN", "share": 0.33, "what": "spherical purified graphite exports, Jan-Sep 2025",
             "year": 2025, "source": "usgs_mcs_chapters", "confidence": "high"},
            {"from": "CHN", "to": "JPN", "share": 0.16, "what": "spherical purified graphite exports, Jan-Sep 2025",
             "year": 2025, "source": "usgs_mcs_chapters", "confidence": "high"},
            {"from": "CHN", "to": "USA", "share": 0.11, "what": "spherical purified graphite exports, Jan-Sep 2025",
             "year": 2025, "source": "usgs_mcs_chapters", "confidence": "high"},
        ],
        "end_uses": [{"use": "Batteries (lithium-ion anodes)"},
                     {"use": "Refractories and steelmaking"},
                     {"use": "Brake linings, lubricants, powdered metals"}],
        "end_use_basis": "US, qualitative, no shares published (USGS MCS 2026)",
        "key_inputs": ["Spheronization and purification (hydrofluoric acid or high-temperature), almost all in China",
                       "Competes with synthetic graphite made from needle coke with cheap power",
                       "Chinese export licensing on graphite and, since October 2025, anode materials"],
        "price_tickers": [{"symbol": "SYR.AX", "label": "Syrah Resources (Balama, Mozambique)", "kind": "equity"},
                          {"symbol": "NVX", "label": "Novonix ADR (synthetic anode)", "kind": "equity"}],
        "notes": "China mined roughly 80% of natural graphite in 2025 and makes about 95% of battery-grade "
                 "material (IEA). Tanzania more than doubled output to 75,000 t and Mozambique's Balama "
                 "restarted in June 2025. US preliminary antidumping duties of 93.5% on Chinese anode "
                 "material were set in 2025 (USGS); IEA reports Mozambique introduced graphite trade "
                 "restrictions and China added anode materials to export controls in October 2025.",
        "comtrade": [{"hs": "2504", "what": "natural graphite (HS 2504)", "exporters": ["MOZ", "MDG", "TZA"]}],
    },
    "rare_earths": {
        "name": "Rare earths", "group": "minor metals", "source": "usgs_mcs",
        "usgs": {"chapter": "RARE EARTHS", "prod": "Mine production", "reserves": "Reserves",
                 "basis": "mine production, rare-earth-oxide (REO) equivalent, metric tons; includes "
                          "lanthanides and yttrium",
                 "nir": r"^Net import reliance.*Compounds"},
        "refining": [{"iso3": "CHN", "share": 0.85, "stage": "refined magnet rare earths (Nd, Pr, Dy, Tb)",
                      "year": 2025, "source": "iea_gcmo_2026", "confidence": "high"}],
        "end_uses": [{"use": "Permanent magnets (leading global use: EV motors, wind turbines, defence)"},
                     {"use": "Catalysts (leading US use: petroleum refining, autocatalysts)"},
                     {"use": "Ceramics and glass, polishing"},
                     {"use": "Metallurgical alloys, batteries"}],
        "end_use_basis": "qualitative, no shares published (USGS MCS 2026)",
        "key_inputs": ["Solvent-extraction separation capacity, concentrated in China (Malaysia and the US are growing)",
                       "Heavy rare earths (Dy, Tb) from ionic clays in southern China and Burma",
                       "Chinese mining and separation quotas and export licences",
                       "Byproduct of iron ore (Bayan Obo) and heavy-mineral sands (monazite)"],
        "price_tickers": [{"symbol": "REMX", "label": "VanEck Rare Earth and Strategic Metals ETF", "kind": "etf"},
                          {"symbol": "MP", "label": "MP Materials (Mountain Pass)", "kind": "equity"},
                          {"symbol": "LYC.AX", "label": "Lynas Rare Earths", "kind": "equity"}],
        "notes": "China mines about 69% and refines about 85% of magnet rare earths; refining "
                 "concentration fell slightly in 2025 as US and Malaysian output grew (IEA). China's "
                 "April 2025 export controls on seven heavy rare earths remain in force; the wider "
                 "October 2025 controls are suspended only until November 2026 (USGS, IEA). IEA says "
                 "European Dy and Tb prices are about five times Chinese domestic prices. Burma supplies "
                 "about 6% of mine output, mostly heavy rare earths.",
        "comtrade": [{"hs": "2846", "what": "rare-earth compounds (HS 2846)", "exporters": ["CHN", "MMR", "MYS"]}],
    },
    "gold": {
        "name": "Gold", "group": "precious metals", "source": "usgs_mcs",
        "usgs": {"chapter": "GOLD", "prod": "Mine production", "reserves": "Reserves",
                 "basis": "mine production, gold content, metric tons (1 t = 32,150.7 troy oz)"},
        "end_uses": [{"use": "Jewelry", "share": 0.40},
                     {"use": "Physical bars", "share": 0.24},
                     {"use": "Central banks and other institutions", "share": 0.21},
                     {"use": "Official coins, medals and imitation coins", "share": 0.07},
                     {"use": "Electrical and electronics", "share": 0.07},
                     {"use": "Other", "share": 0.01}],
        "end_use_basis": "global, excluding ETFs and similar investment (USGS MCS 2026)",
        "key_inputs": ["Cyanide, lime, grinding media and diesel",
                       "Above-ground stocks and recycling dwarf annual mine supply, so price is set by investment flows",
                       "Byproduct output from copper mines"],
        "price_tickers": [{"symbol": "GC=F", "label": "COMEX gold", "kind": "future"},
                          {"symbol": "GLD", "label": "SPDR Gold Shares", "kind": "etf"},
                          {"symbol": "GDX", "label": "VanEck Gold Miners ETF", "kind": "etf"}],
        "notes": "The least concentrated commodity here: the top five miners (China, Russia, Australia, "
                 "Canada, US) are only 41% of output and no country exceeds 12% (USGS). Mine supply is "
                 "flat near 3,300 t, so country-level disruptions rarely move price; ETF inflows of 619 t "
                 "in the first nine months of 2025 and a record annual price did.",
    },
    "silver": {
        "name": "Silver", "group": "precious metals", "source": "usgs_mcs",
        "usgs": {"chapter": "SILVER", "prod": "Mine production", "reserves": "Reserves",
                 "basis": "mine production, silver content, metric tons (1 t = 32,150.7 troy oz)",
                 "nir": r"^Net import reliance"},
        "end_uses": [{"use": "Electrical and electronics", "share": 0.25},
                     {"use": "Other industrial uses and photography", "share": 0.19},
                     {"use": "Net physical investment (bars)", "share": 0.18},
                     {"use": "Photovoltaics", "share": 0.15},
                     {"use": "Coins and medals", "share": 0.14},
                     {"use": "Jewelry and silverware", "share": 0.06},
                     {"use": "Brazing and solder", "share": 0.03}],
        "end_use_basis": "US (USGS MCS 2026)",
        "key_inputs": ["Mostly a byproduct or coproduct of lead-zinc, copper and gold mines, so supply is price-inelastic",
                       "Lead and zinc smelter throughput",
                       "Solar cell silver loadings (falling per unit)"],
        "price_tickers": [{"symbol": "SI=F", "label": "COMEX silver", "kind": "future"},
                          {"symbol": "SLV", "label": "iShares Silver Trust", "kind": "etf"},
                          {"symbol": "SIL", "label": "Global X Silver Miners ETF", "kind": "etf"}],
        "notes": "Mexico, Peru and China mine about half of world silver. Mine supply (about 26,000 t) is "
                 "well below consumption (about 35,700 t in 2025), the gap filled by recycling and stocks; "
                 "the price reached USD 53.60/oz in November 2025. Silver was added to the US critical "
                 "minerals list in November 2025 (USGS).",
    },
    "platinum": {
        "name": "Platinum", "group": "precious metals", "source": "usgs_mcs",
        "usgs": {"chapter": "PLATINUM-GROUP METALS", "prod": "Mine production: Platinum",
                 "reserves": "PGM reserves",
                 "basis": "mine production, platinum content, kilograms; reserves are total "
                          "platinum-group metals (all six PGMs), not platinum alone",
                 "nir": r"^Net import reliance.*Platinum"},
        "end_uses": [{"use": "Catalytic converters (leading use)"},
                     {"use": "Chemical and petroleum-refining catalysts"},
                     {"use": "Jewelry and investment"},
                     {"use": "Glass manufacturing, electronics, medical and dental"}],
        "end_use_basis": "US, qualitative, no shares published (USGS MCS 2026)",
        "key_inputs": ["Eskom electricity supply for deep-level South African mines and smelters",
                       "Mined together with palladium, rhodium, nickel and copper: basket price drives output",
                       "Autocatalyst recycling as secondary supply"],
        "price_tickers": [{"symbol": "PL=F", "label": "NYMEX platinum", "kind": "future"},
                          {"symbol": "PPLT", "label": "abrdn Physical Platinum Shares", "kind": "etf"},
                          {"symbol": "ANGPY", "label": "Valterra Platinum ADR (ex Anglo American Platinum)", "kind": "equity"},
                          {"symbol": "IMPUY", "label": "Impala Platinum ADR", "kind": "equity"}],
        "notes": "South Africa mines about 70% of platinum and holds over 80% of reported PGM reserves; its output "
                 "fell an estimated 9% in 2025 on power disruptions and deep-mining costs, and the average "
                 "platinum price rose 25% (USGS). Russia and Zimbabwe each supply about 11 to 12%.",
        "comtrade": [{"hs": "711011", "what": "unwrought platinum (HS 711011)", "exporters": ["ZAF", "RUS"]}],
    },
    "palladium": {
        "name": "Palladium", "group": "precious metals", "source": "usgs_mcs",
        "usgs": {"chapter": "PLATINUM-GROUP METALS", "prod": "Mine production: Palladium",
                 "reserves": "PGM reserves",
                 "basis": "mine production, palladium content, kilograms; reserves are total "
                          "platinum-group metals (all six PGMs), not palladium alone",
                 "nir": r"^Net import reliance.*Palladium"},
        "end_uses": [{"use": "Catalytic converters for gasoline vehicles (leading use)"},
                     {"use": "Electronics (multilayer ceramic capacitors)"},
                     {"use": "Chemical catalysts, dental, investment"}],
        "end_use_basis": "US, qualitative, no shares published (USGS MCS 2026)",
        "key_inputs": ["Byproduct of Norilsk nickel-copper mining in Russia",
                       "Coproduct of South African platinum mining",
                       "Gasoline vehicle production and substitution with platinum in autocatalysts"],
        "price_tickers": [{"symbol": "PA=F", "label": "NYMEX palladium", "kind": "future"},
                          {"symbol": "PALL", "label": "abrdn Physical Palladium Shares", "kind": "etf"},
                          {"symbol": "SBSW", "label": "Sibanye-Stillwater (South Africa and US PGMs)", "kind": "equity"}],
        "notes": "Russia (about 44%) and South Africa (about 37%) mine roughly 80% of palladium, so "
                 "sanctions risk on Russian metal is the main supply question. Russian output fell an "
                 "estimated 6% in 2025; the US mine in Montana cut output about 40% (USGS).",
        "comtrade": [{"hs": "711021", "what": "unwrought palladium (HS 711021)", "exporters": ["RUS", "ZAF"]}],
    },
    "tungsten": {
        "name": "Tungsten", "group": "minor metals", "source": "usgs_mcs",
        "usgs": {"chapter": "TUNGSTEN", "prod": "Mine production", "reserves": "Reserves",
                 "basis": "mine production, tungsten content, metric tons",
                 "nir": r"^Net import reliance"},
        "refining": [{"iso3": "CHN", "share": 0.79, "stage": "refined tungsten (APT and downstream)",
                      "year": 2025, "source": "iea_gcmo_2026", "confidence": "medium"}],
        "end_uses": [{"use": "Cemented carbides (cutting and wear parts for metalworking, mining, drilling)",
                      "share": 0.60},
                     {"use": "Alloys and specialty steels, electrodes and filaments, chemicals", "share": 0.40}],
        "end_use_basis": "US (USGS MCS 2026 gives 60% for cemented carbides; remainder grouped)",
        "key_inputs": ["Chinese mining quotas and export licences (controls added February 2025)",
                       "Scrap recycling is a major secondary source",
                       "Ammonium paratungstate (APT) conversion capacity"],
        "price_tickers": [{"symbol": "600549.SS", "label": "Xiamen Tungsten", "kind": "equity"}],
        "notes": "China mines about 79% of tungsten and is also the leading importer of concentrate. "
                 "After China's February 2025 export controls, Rotterdam APT rose from USD 331 to "
                 "USD 675 per mtu during 2025 (USGS); IEA says tungsten prices rose about sixfold from "
                 "2023 to early 2026. Note the IEA top-refiner share was 44% in its 2025 edition and "
                 "79% in the 2026 edition, so the definition changed: hence medium confidence.",
        "comtrade": [{"hs": "2611", "what": "tungsten ores and concentrates (HS 2611)",
                      "exporters": ["BOL", "RWA", "PRK"], "confidence": "low"}],
    },
    "antimony": {
        "name": "Antimony", "group": "minor metals", "source": "usgs_mcs",
        "usgs": {"chapter": "ANTIMONY", "prod": "Mine production", "reserves": "Reserves",
                 "basis": "mine production, antimony content, metric tons",
                 "nir": r"^Net import reliance"},
        "refining": [{"iso3": "CHN", "share": 0.74, "stage": "refined antimony (metal and oxide)",
                      "year": 2025, "source": "iea_gcmo_2026", "confidence": "high"}],
        "end_uses": [{"use": "Metal products, including flame retardants", "share": 0.49},
                     {"use": "Antimonial lead (lead-acid batteries) and ammunition", "share": 0.40},
                     {"use": "Nonmetal products: ceramics, glass, rubber", "share": 0.11}],
        "end_use_basis": "US (USGS MCS 2026)",
        "key_inputs": ["Often a byproduct or coproduct of gold mining (Russia, Tajikistan, planned Idaho mine)",
                       "Chinese smelters depend on imported concentrate from Tajikistan and Russia",
                       "Chinese export restrictions (August 2024) and the December 2024 ban on exports to the US"],
        "price_tickers": [{"symbol": "UAMY", "label": "United States Antimony Corp", "kind": "equity"},
                          {"symbol": "PPTA", "label": "Perpetua Resources (Stibnite gold-antimony, Idaho)", "kind": "equity"}],
        "notes": "China is only about 36% of mine output, with Russia (29%) and Tajikistan (20%) close "
                 "behind, but China refines about 74% (IEA). The price averaged USD 25/lb in 2025, more "
                 "than double 2024, after China's export restrictions and its ban on shipments to the US "
                 "(USGS).",
        "comtrade": [{"hs": "261710", "what": "antimony ores and concentrates (HS 261710)",
                      "exporters": ["TJK", "RUS", "BOL"], "confidence": "low"}],
    },
    "gallium": {
        "name": "Gallium", "group": "minor metals", "source": "usgs_mcs",
        "usgs": {"chapter": "GALLIUM", "prod": "Primary production",
                 "basis": "primary low-purity gallium production, gallium content, kilograms (no "
                          "reserves reported: gallium is a byproduct)",
                 "nir": r"^Net import reliance"},
        "refining": [{"iso3": "CHN", "share": 0.99, "stage": "primary gallium recovery and refining",
                      "year": 2025, "source": "iea_gcmo_2026", "confidence": "high"}],
        "end_uses": [{"use": "Integrated circuits (GaAs and GaN: defence, telecom, high-performance computing)",
                      "share": 0.73},
                     {"use": "Optoelectronic devices (LEDs, laser diodes, photodetectors, solar cells)",
                      "share": 0.26}],
        "end_use_basis": "US (USGS MCS 2026)",
        "key_inputs": ["Byproduct of alumina refining (Bayer liquor): supply depends on Chinese alumina refineries",
                       "Minor byproduct of zinc processing",
                       "Chinese export licensing (August 2023) and the ban on exports to the US (December 2024, "
                       "lifted for one year in November 2025)"],
        "price_tickers": [{"symbol": "AXTI", "label": "AXT Inc (GaAs, InP, Ge substrates: consumer proxy)", "kind": "equity"}],
        "notes": "The most concentrated commodity in this file: China made 99% of primary gallium in 2025 "
                 "(USGS); only Japan and Russia remain as other primary producers. IEA says European "
                 "gallium prices are about five times Chinese domestic prices. China suspended its US "
                 "export ban for one year in November 2025.",
    },
    "germanium": {
        "name": "Germanium", "group": "minor metals", "source": "usgs_mcs_chapters",
        "usgs": {"chapter": "GERMANIUM", "prod": None,
                 "basis": "no country production table: USGS says global refinery and recycling data "
                          "are limited and hard to verify",
                 "nir": r"^Net import reliance"},
        "unit": "kg", "year": USGS_YEAR,
        "refining": [{"iso3": "CHN", "share": 0.74, "stage": "refined germanium", "year": 2025,
                      "source": "iea_gcmo_2026", "confidence": "high"}],
        "trade": [
            {"from": "CHN", "to": "RUS", "share": 0.28, "what": "germanium metal exports, Jan-Sep 2025",
             "year": 2025, "source": "usgs_mcs_chapters", "confidence": "high"},
            {"from": "CHN", "to": "BEL", "share": 0.26, "what": "germanium metal exports, Jan-Sep 2025",
             "year": 2025, "source": "usgs_mcs_chapters", "confidence": "high"},
            {"from": "CHN", "to": "DEU", "share": 0.26, "what": "germanium metal exports, Jan-Sep 2025",
             "year": 2025, "source": "usgs_mcs_chapters", "confidence": "high"},
            {"from": "CHN", "to": "JPN", "share": 0.18, "what": "germanium metal exports, Jan-Sep 2025",
             "year": 2025, "source": "usgs_mcs_chapters", "confidence": "high"},
        ],
        "end_uses": [{"use": "Fiber optics (germanium tetrachloride and dioxide)"},
                     {"use": "Infrared optics (lenses for thermal imaging)"},
                     {"use": "Semiconductors and multijunction solar cells for satellites"},
                     {"use": "Radiation detectors"}],
        "end_use_basis": "US, descending order, no shares published (USGS MCS 2026)",
        "key_inputs": ["Byproduct of zinc smelting (Red Dog concentrate refined at Trail, Canada) and of coal fly ash in China",
                       "Recycling of fiber-optic and infrared-optics scrap",
                       "Chinese export licensing (August 2023) and ban on exports to the US (December 2024)"],
        "price_tickers": [{"symbol": "TECK", "label": "Teck Resources (Trail smelter, germanium byproduct)", "kind": "equity"},
                          {"symbol": "AXTI", "label": "AXT Inc (germanium substrates: consumer proxy)", "kind": "equity"}],
        "notes": "No producer table: USGS does not publish country production. China is the leading "
                 "producer and exporter (IEA puts its refining share near 74%). China's reported "
                 "germanium metal exports fell to 7,520 kg in January to September 2025 from 36,656 kg in "
                 "the same period of 2023; the European metal price rose from USD 3,150 to USD 5,380 per "
                 "kg during 2025 (USGS).",
    },
    "potash": {
        "name": "Potash", "group": "bulk", "source": "usgs_mcs",
        "usgs": {"chapter": "POTASH", "prod": "Mine production", "reserves": "Reserves: K2O equivalent",
                 "basis": "mine production, potassium oxide (K2O) equivalent, thousand metric tons",
                 "nir": r"^Net import reliance"},
        "end_uses": [{"use": "Fertilizer", "share": 0.85},
                     {"use": "Chemical and industrial applications", "share": 0.15}],
        "end_use_basis": "US sales (USGS MCS 2026)",
        "key_inputs": ["Rail to port (Saskatchewan to Vancouver; Belarus lost Lithuanian port access under sanctions)",
                       "Natural gas for solution mining and drying",
                       "Sanctions policy on Belarus and Russia"],
        "price_tickers": [{"symbol": "NTR", "label": "Nutrien", "kind": "equity"},
                          {"symbol": "MOS", "label": "Mosaic", "kind": "equity"},
                          {"symbol": "IPI", "label": "Intrepid Potash", "kind": "equity"},
                          {"symbol": "ICL", "label": "ICL Group", "kind": "equity"}],
        "notes": "Canada, Russia and Belarus mine about 63% of potash, and Russia plus Belarus about a "
                 "third, which is the sanctions exposure. The US imports about 92% of its needs, mostly "
                 "from Canada. Potash was added to the US critical minerals list in November 2025; new "
                 "capacity to 2029 is mostly in Laos and Russia (USGS).",
        "comtrade": [{"hs": "310420", "what": "potassium chloride (HS 310420)",
                      "exporters": ["CAN", "RUS", "BLR"]}],
    },
}

COMMODITY_ORDER = [
    "crude_oil", "natural_gas", "coal", "uranium", "copper", "cobalt", "lithium", "nickel",
    "aluminum", "bauxite", "iron_ore", "zinc", "tin", "manganese", "graphite", "rare_earths",
    "gold", "silver", "platinum", "palladium", "tungsten", "antimony", "gallium", "germanium",
    "potash",
]

# =====================================================================================
# =============================  HAND-CURATED DATA ENDS HERE  =========================
# =====================================================================================

EM_DASH = chr(0x2014)  # written as a code point so this file contains no literal dash
EN_DASH = chr(0x2013)
WARNINGS: list[str] = []
SESSION = requests.Session()
SESSION.headers["User-Agent"] = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                                 "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
REFRESH = False


def warn(msg: str) -> None:
    WARNINGS.append(msg)
    print("  WARNING: " + msg, file=sys.stderr)


def fetch(url: str, cache_name: str, params: dict | None = None, tries: int = 5) -> bytes:
    """GET with an on-disk cache in the system temp dir (outside the repo)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, cache_name)
    if not REFRESH and os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_MAX_AGE_S:
        with open(path, "rb") as fh:
            return fh.read()
    last = ""
    for attempt in range(tries):
        try:
            resp = SESSION.get(url, params=params, timeout=300)
        except requests.RequestException as exc:  # network hiccup: retry
            last = repr(exc)
            time.sleep(2 + 2 * attempt)
            continue
        if resp.status_code == 200:
            with open(path, "wb") as fh:
                fh.write(resp.content)
            return resp.content
        last = "HTTP %s" % resp.status_code
        time.sleep(2 + 2 * attempt if resp.status_code == 429 else 1)
    raise RuntimeError("fetch failed for %s (%s)" % (url, last))


def sig_round(x: float, digits: int) -> float:
    return round(x, digits) if digits > 0 else int(round(x))


# ------------------------------------ USGS -------------------------------------------

def load_usgs_rows() -> list[dict]:
    item = json.loads(fetch(USGS_ITEM_URL, "usgs_item.json"))
    url = None
    for f in item.get("files", []):
        if f.get("name") == USGS_CSV_NAME:
            url = f["url"]
    if not url:
        raise RuntimeError("USGS data release no longer lists %s" % USGS_CSV_NAME)
    raw = fetch(url, USGS_CSV_NAME).decode("cp1252")
    return list(csv.DictReader(io.StringIO(raw)))


def parse_usgs_value(v: str):
    """Return (number or None, flag). Flags: '' | 'withheld' | 'lower_bound' | 'na'."""
    v = (v or "").strip()
    if v in (EM_DASH, EN_DASH, "-"):
        return 0.0, ""  # USGS dash means zero
    if v == "W":
        return None, "withheld"
    flag = ""
    if v.startswith(">"):
        flag, v = "lower_bound", v[1:]
    v = v.replace(",", "").strip()
    try:
        return float(v), flag
    except ValueError:
        return None, "na"  # blank, NA, "Small", "Large", "E", "s"


USGS_UNITS = {"metric tons": "t", "thousand metric tons": "kt", "thousand metric dry tons": "kt",
              "kilograms": "kg", "million metric tons": "Mt"}


def usgs_table(rows: list[dict], chapter: str, detail: str, year: int) -> tuple[dict, dict, str]:
    """country -> (value, flag, estimated) for one world-table series; plus the world total row."""
    out, world, unit = {}, {}, ""
    for r in rows:
        if r["MCS chapter"] != chapter or not r["Section"].startswith("World"):
            continue
        if str(r["Year"]).strip() != str(year):
            continue
        d = r["Statistics_detail"]
        is_world = r["Country"] == "World total"
        if not (d == detail or (is_world and d.startswith(detail) and "rounded" in d)):
            continue
        val, flag = parse_usgs_value(r["Value"])
        est = "Estimated" in (r.get("Notes") or "")
        unit = USGS_UNITS.get(r["Unit"], r["Unit"])
        if is_world:
            world = {"value": val, "flag": flag, "estimated": est}
        else:
            out[r["Country"]] = {"value": val, "flag": flag, "estimated": est}
    return out, world, unit


def iso_for_usgs(name: str) -> str:
    if name not in USGS_NAMES:
        raise KeyError("USGS country name not mapped to ISO3: %r" % name)
    return USGS_NAMES[name]


def clean_num(x: float):
    return int(x) if float(x).is_integer() else round(x, 3)


def build_usgs_commodity(cid: str, cur: dict, rows: list[dict]) -> dict:
    spec = cur["usgs"]
    out: dict = {"id": cid, "name": cur["name"], "group": cur["group"]}
    producers: list[dict] = []
    if spec.get("prod"):
        now, world_now, unit = usgs_table(rows, spec["chapter"], spec["prod"], USGS_YEAR)
        prev, world_prev, _ = usgs_table(rows, spec["chapter"], spec["prod"], USGS_YEAR - 1)
        res, world_res = {}, {}
        if spec.get("reserves"):
            res, world_res, _ = usgs_table(rows, spec["chapter"], spec["reserves"], USGS_YEAR)
        scale = spec.get("reserves_scale", 1.0)
        out.update({"unit": unit, "basis": spec["basis"], "year": USGS_YEAR})
        if world_now.get("value") is not None:
            out["world_production"] = clean_num(world_now["value"])
        if world_prev.get("value") is not None:
            out["world_production_prev"] = clean_num(world_prev["value"])
        if world_res.get("value") is not None:
            out["world_reserves"] = clean_num(world_res["value"] * scale)
            if world_res["flag"] == "lower_bound":
                out["world_reserves_note"] = "lower bound: source reports a 'greater than' value"
        wp = out.get("world_production")
        for country, cell in now.items():
            iso3 = iso_for_usgs(country)
            row: dict = {"iso3": iso3}
            pv = prev.get(country, {}).get("value")
            if cell["flag"] == "withheld":
                row["withheld"] = True
            elif cell["value"] is not None:
                row["production"] = clean_num(cell["value"])
                if wp:
                    row["share"] = round(cell["value"] / wp, 4)
            if pv is not None:
                row["prev"] = clean_num(pv)
            rc = res.get(country)
            if rc and rc["value"] is not None:
                row["reserves"] = clean_num(rc["value"] * scale)
                if rc["flag"] == "lower_bound":
                    row["reserves_lower_bound"] = True
            if cell["estimated"]:
                row["estimated"] = True
            produced = (row.get("production") or 0) > 0 or (row.get("prev") or 0) > 0
            if produced or row.get("withheld"):
                producers.append(row)
        producers.sort(key=lambda r: (-(r.get("production", -1)), r["iso3"]))
    else:
        out.update({"unit": cur.get("unit", ""), "basis": spec["basis"], "year": cur.get("year", USGS_YEAR)})
    out["producers"] = producers

    refining: list[dict] = []
    rspec = spec.get("refining")
    if rspec:
        tab, world, _ = usgs_table(rows, rspec["chapter"], rspec["detail"], USGS_YEAR)
        tot = world.get("value")
        if tot:
            for country, cell in tab.items():
                if cell["value"]:
                    refining.append({"iso3": iso_for_usgs(country), "share": round(cell["value"] / tot, 4),
                                     "stage": rspec["stage"], "year": USGS_YEAR, "source": "usgs_mcs",
                                     "confidence": "high"})
            refining.sort(key=lambda r: -r["share"])
    if spec.get("self_refining_stage"):
        for p in producers:
            if "share" in p:
                refining.append({"iso3": p["iso3"], "share": p["share"], "stage": spec["self_refining_stage"],
                                 "year": USGS_YEAR, "source": "usgs_mcs", "confidence": "high"})
    refining.extend(cur.get("refining", []))
    if refining:
        out["refining"] = refining

    # US net import reliance (percent of apparent consumption), latest year
    if spec.get("nir"):
        hits = [r for r in rows if r["MCS chapter"] == spec["chapter"]
                and str(r["Year"]).strip() == str(USGS_YEAR)
                and re.search(spec["nir"], r["Statistics_detail"])]
        if hits:
            raw = hits[min(spec.get("nir_index", 0), len(hits) - 1)]["Value"].strip()
            val, flag = parse_usgs_value(raw)
            if val is not None:
                out["us_net_import_reliance"] = round(val / 100.0, 3)
                if flag == "lower_bound":
                    out["us_net_import_reliance_note"] = "lower bound: source reports >%d%%" % val
            elif raw == "E":
                out["us_net_import_reliance_note"] = "US is a net exporter"
        else:
            warn("%s: no net import reliance row matched" % cid)
    return out


# ------------------------------------- EIA -------------------------------------------

# historic states, plus Kosovo (no ISO numeric code, so it cannot be mapped: folded into OTH)
EIA_NON_COUNTRY = {"CSK", "DDR", "SUN", "YUG", "SCG", "ANT", "WAK", "XKS"}


def load_eia(series_needed: set[str]) -> dict:
    """Return {(code, suffix): {geo: {year: value}}} for annual series like INTL.57-1-COD-TBPD.A"""
    blob = fetch(EIA_URL, "INTL.zip")
    out: dict = {k: {} for k in series_needed}
    prefixes = tuple('{"series_id":"INTL.%s-' % code for code, _ in series_needed)
    pat = re.compile(r"^INTL\.(\d+-\d+)-([A-Z0-9_]+)-([A-Z]+)\.A$")
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        with zf.open(zf.namelist()[0]) as fh:
            for raw in fh:
                line = raw.decode("utf-8", "replace")
                if not line.startswith(prefixes):
                    continue
                obj = json.loads(line)
                m = pat.match(obj.get("series_id", ""))
                if not m or (m.group(1), m.group(3)) not in out:
                    continue
                series = {}
                for year, val in obj.get("data", []):
                    if isinstance(val, (int, float)) and not (isinstance(val, float) and math.isnan(val)):
                        series[int(year)] = float(val)
                out[(m.group(1), m.group(3))][m.group(2)] = series
    return out


def eia_latest_year(table: dict) -> int:
    """Latest year with a world total and broad country coverage."""
    years = sorted(table.get("WORL", {}), reverse=True)
    for y in years:
        n = sum(1 for g, s in table.items() if len(g) == 3 and y in s)
        if n >= 150:
            return y
    raise RuntimeError("no EIA year with broad coverage")


def eia_ranked(table: dict, year: int, field: str, digits: int, top_n: int, prev_field: str | None = None):
    world = table["WORL"][year]
    rows = []
    for geo, s in table.items():
        if len(geo) != 3 or geo in EIA_NON_COUNTRY or year not in s or s[year] <= 0:
            continue
        row = {"iso3": geo, field: sig_round(s[year], digits)}
        if prev_field and (year - 1) in s:
            row[prev_field] = sig_round(s[year - 1], digits)
        row["share"] = round(s[year] / world, 4)
        row["_v"] = s[year]
        rows.append(row)
    rows.sort(key=lambda r: -r["_v"])
    top = rows[:top_n]
    rest = world - sum(r["_v"] for r in top)
    for r in top:
        del r["_v"]
    if rest > 0:
        oth = {"iso3": "OTH", field: sig_round(rest, digits), "share": round(rest / world, 4)}
        top.append(oth)
    top.sort(key=lambda r: -r[field])
    return top, sig_round(world, digits)


def build_eia_commodity(cid: str, cur: dict, eia: dict) -> dict:
    spec = cur["eia"]
    prod_t = eia[(spec["prod"], spec["suffix"])]
    cons_t = eia[(spec["cons"], spec["suffix"])]
    py, cy = eia_latest_year(prod_t), eia_latest_year(cons_t)
    year = min(py, cy)  # one consistent year for supply and demand
    producers, world = eia_ranked(prod_t, year, "production", spec["digits"], ENERGY_TOP_N, "prev")
    consumers, world_c = eia_ranked(cons_t, year, "consumption", spec["digits"], ENERGY_TOP_N)
    out = {"id": cid, "name": cur["name"], "group": cur["group"], "unit": spec["unit"],
           "basis": spec["basis"] + "; top %d countries listed, remainder in OTH" % ENERGY_TOP_N,
           "year": year, "world_production": world,
           "world_production_prev": sig_round(prod_t["WORL"][year - 1], spec["digits"]),
           "world_consumption": world_c, "producers": producers, "consumers": consumers}
    if py > year:
        out["latest_production_year_available"] = py
    if spec.get("refining"):
        ref_t = eia[(spec["refining"], spec["suffix"])]
        ry = eia_latest_year(ref_t)
        if ry >= year - 2:
            ranked, _ = eia_ranked(ref_t, ry, "output", spec["digits"], 15)
            out["refining"] = [{"iso3": r["iso3"], "share": r["share"], "stage": spec["refining_stage"],
                                "year": ry, "source": "eia_intl", "confidence": "high"} for r in ranked]
        else:
            warn("%s: EIA refining series %s only has broad coverage to %d, too stale: refining omitted"
                 % (cid, spec["refining"], ry))
    return out


# ------------------------------------- WNA -------------------------------------------

def strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def build_uranium(cur: dict) -> dict:
    page = fetch(WNA_URL, "wna_uranium.html").decode("utf-8", "replace")
    table = None
    for t in re.findall(r"(?is)<table.*?</table>", page):
        if "Kazakhstan" in t and "World total" in t:
            table = t
            break
    if table is None:
        raise RuntimeError("WNA uranium production table not found")
    grid = []
    for tr in re.findall(r"(?is)<tr.*?</tr>", table):
        cells = [strip_tags(c) for c in re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", tr)]
        if cells:
            grid.append(cells)
    header = next(r for r in grid if r[0].lower().startswith("country"))
    years = [int(y) for y in header[1:]]
    year = years[-1]

    def num(s: str) -> float:
        return float(s.replace(",", "").replace(" ", ""))

    producers, world, world_prev = [], None, None
    for r in grid:
        label = r[0]
        if label.lower().startswith("country") or len(r) < len(header):
            continue
        if re.sub(r"\s", "", label) == "WorldtotaltU":
            world, world_prev = num(r[-1]), num(r[-2])
            continue
        if label.startswith("World") or label.startswith("%"):
            continue
        est = "(est" in label
        name = re.sub(r"\s*\(est\.?\)", "", label).strip()
        if name not in WNA_NAMES:
            warn("uranium: unmapped WNA row %r skipped" % label)
            continue
        row = {"iso3": WNA_NAMES[name], "production": clean_num(num(r[-1])), "prev": clean_num(num(r[-2]))}
        if est:
            row["estimated"] = True
        producers.append(row)
    if not world:
        raise RuntimeError("WNA world total row not found")
    for p in producers:
        p["share"] = round(p["production"] / world, 4)
    producers.sort(key=lambda r: -r["production"])
    spec = cur["wna"]
    return {"id": "uranium", "name": cur["name"], "group": cur["group"], "unit": spec["unit"],
            "basis": spec["basis"], "year": year, "world_production": clean_num(world),
            "world_production_prev": clean_num(world_prev), "producers": producers}


# ----------------------------------- Comtrade ----------------------------------------

def comtrade_codes() -> tuple[dict, dict]:
    """(reporter code -> ISO3, ISO3 -> current code) from the Comtrade reporter reference."""
    ref = json.loads(fetch(COMTRADE_REPORTERS_URL, "comtrade_reporters.json"))["results"]
    code_to_iso, iso_to_code = {}, {}
    for r in ref:
        iso = r.get("reporterCodeIsoAlpha3")
        if not iso or r.get("isGroup"):
            continue
        code_to_iso[int(r["reporterCode"])] = iso
        if not r.get("entryExpiredDate"):
            iso_to_code[iso] = int(r["reporterCode"])
    code_to_iso[490] = "TWN"  # "Other Asia, nes" is Taiwan in practice
    return code_to_iso, iso_to_code


def build_trade(cid: str, queries: list[dict], code_to_iso: dict, iso_to_code: dict) -> list[dict]:
    """Destination shares for each exporter from partner-reported imports (mirror data)."""
    rows = []
    for q in queries:
        for exporter in q["exporters"]:
            code = iso_to_code.get(exporter)
            if code is None:
                warn("%s: no Comtrade code for %s" % (cid, exporter))
                continue
            name = "comtrade_%s_%s_%s.json" % (TRADE_YEAR, q["hs"], exporter)
            fresh = REFRESH or not os.path.exists(os.path.join(CACHE_DIR, name))
            try:
                data = json.loads(fetch(COMTRADE_URL, name, params={
                    "partnerCode": code, "period": TRADE_YEAR, "cmdCode": q["hs"], "flowCode": "M",
                    "partner2Code": 0, "customsCode": "C00", "motCode": 0})).get("data", [])
            except Exception as exc:  # noqa: BLE001 - trade is optional enrichment
                warn("%s: Comtrade query failed for %s HS %s (%s)" % (cid, exporter, q["hs"], exc))
                continue
            if fresh:
                time.sleep(1.6)  # preview endpoint is rate limited
            by_dest: dict = {}
            for d in data:
                val = d.get("primaryValue") or 0
                rep = int(d.get("reporterCode") or 0)
                dest = code_to_iso.get(rep)
                if rep == 0 or val <= 0 or dest == exporter:
                    continue
                key = dest or "OTH"
                by_dest[key] = by_dest.get(key, 0.0) + float(val)
            total = sum(by_dest.values())
            if total < 20e6:
                warn("%s: mirror imports from %s for HS %s under USD 20M (%.1fM): no trade rows"
                     % (cid, exporter, q["hs"], total / 1e6))
                continue
            ranked = sorted(by_dest.items(), key=lambda kv: -kv[1])
            kept = 0
            for dest, val in ranked:
                share = val / total
                if dest == "OTH" or kept >= 4 or share < 0.03:
                    continue
                kept += 1
                rows.append({"from": exporter, "to": dest, "share": round(share, 3),
                             "what": q["what"], "year": TRADE_YEAR, "source": "un_comtrade",
                             "basis": "share of partner-reported import value (mirror data)",
                             "value_usd_m": round(total / 1e6, 1),
                             "confidence": "low" if exporter in LOW_CONF_EXPORTERS
                             else q.get("confidence", "medium")})
    return rows


# ------------------------------------ assemble ---------------------------------------

def finish(out: dict, cur: dict, trade_rows: list[dict]) -> dict:
    trade = list(cur.get("trade", [])) + trade_rows
    if trade:
        out["trade"] = trade
    out["end_uses"] = cur["end_uses"]
    out["end_use_basis"] = cur["end_use_basis"]
    out["key_inputs"] = cur["key_inputs"]
    out["price_tickers"] = cur["price_tickers"]
    out["notes"] = cur["notes"]
    out["source"] = cur["source"]
    return out


def referenced_iso3(commodities: list[dict]) -> set[str]:
    used = set()
    for c in commodities:
        for key in ("producers", "consumers", "refining"):
            for r in c.get(key, []):
                used.add(r["iso3"])
        for r in c.get("trade", []):
            used.add(r["from"])
            used.add(r["to"])
    used.discard("OTH")
    return used


def has_nan(obj) -> bool:
    if isinstance(obj, float):
        return math.isnan(obj) or math.isinf(obj)
    if isinstance(obj, dict):
        return any(has_nan(v) for v in obj.values())
    if isinstance(obj, list):
        return any(has_nan(v) for v in obj)
    return False


def validate(path: str) -> list[str]:
    problems = []
    text = open(path, encoding="utf-8").read()
    data = json.loads(text)  # must load
    if "NaN" in text or "Infinity" in text or has_nan(data):
        problems.append("NaN or Infinity present")
    if EM_DASH in text or EN_DASH in text:
        problems.append("em or en dash present in output text")
    countries = data["countries"]
    for iso3, c in countries.items():
        if not re.fullmatch(r"\d{3}", str(c.get("iso_n3", ""))):
            problems.append("country %s: iso_n3 is not a 3-digit string" % iso3)
        if not (-180 <= c.get("lon", 999) <= 180 and -90 <= c.get("lat", 999) <= 90):
            problems.append("country %s: bad lon/lat" % iso3)
    for c in data["commodities"]:
        cid = c["id"]
        for key in ("producers", "consumers", "refining"):
            for r in c.get(key, []):
                if r["iso3"] != "OTH" and r["iso3"] not in countries:
                    problems.append("%s.%s: %s not in countries" % (cid, key, r["iso3"]))
        for r in c.get("trade", []):
            for end in ("from", "to"):
                if r[end] != "OTH" and r[end] not in countries:
                    problems.append("%s.trade: %s not in countries" % (cid, r[end]))
        prods = [r.get("production", -1) for r in c.get("producers", [])]
        if prods != sorted(prods, reverse=True):
            problems.append("%s: producers not sorted descending" % cid)
        cons = [r.get("consumption", -1) for r in c.get("consumers", [])]
        if cons != sorted(cons, reverse=True):
            problems.append("%s: consumers not sorted descending" % cid)
        wp = c.get("world_production")
        if wp and c.get("producers"):
            s = sum(r.get("production", 0) for r in c["producers"])
            if abs(s - wp) / wp > 0.03:
                problems.append("%s: sum of producers %.6g differs from world_production %.6g by %.1f%%"
                                % (cid, s, wp, 100 * (s - wp) / wp))
        shares = [u["share"] for u in c.get("end_uses", []) if "share" in u]
        if shares and not 0.95 <= sum(shares) <= 1.05:
            problems.append("%s: end_use shares sum to %.2f" % (cid, sum(shares)))
    return problems


def cod_report(data: dict, iso3: str = "COD") -> None:
    print("\nSanity check: %s (%s) as a top-10 producer" % (iso3, data["countries"][iso3]["name"]))
    for c in data["commodities"]:
        ranked = [p for p in c["producers"] if p["iso3"] != "OTH"]
        for rank, p in enumerate(ranked[:10], 1):
            if p["iso3"] == iso3 and p.get("production"):
                print("  %-12s rank %2d  %12s %-4s  share %5.1f%%  (%d)"
                      % (c["id"], rank, format(p["production"], ","), c["unit"],
                         100 * p.get("share", 0), c["year"]))
        for r in c.get("refining", []):
            if r["iso3"] == iso3:
                print("  %-12s refining: %.1f%% of %s (%s)" % (c["id"], 100 * r["share"], r["stage"], r["year"]))
        for r in c.get("trade", []):
            if r["from"] == iso3:
                print("  %-12s trade: %.0f%% to %s, %s (%s, %s)"
                      % (c["id"], 100 * r["share"], r["to"], r["what"], r["year"], r["confidence"]))


def main() -> int:
    global REFRESH
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refresh", action="store_true", help="ignore the download cache")
    ap.add_argument("--no-trade", action="store_true", help="skip UN Comtrade queries")
    args = ap.parse_args()
    REFRESH = args.refresh

    print("Fetching USGS MCS data release ...")
    usgs_rows = load_usgs_rows()
    print("Fetching EIA international bulk file ...")
    needed = set()
    for cur in CURATED.values():
        if "eia" in cur:
            e = cur["eia"]
            needed.add((e["prod"], e["suffix"]))
            needed.add((e["cons"], e["suffix"]))
            if e.get("refining"):
                needed.add((e["refining"], e["suffix"]))
    eia = load_eia(needed)
    code_to_iso, iso_to_code = ({}, {})
    if not args.no_trade:
        try:
            code_to_iso, iso_to_code = comtrade_codes()
        except Exception as exc:  # noqa: BLE001
            warn("Comtrade reference unavailable, trade rows limited to curated ones (%s)" % exc)

    commodities = []
    for cid in COMMODITY_ORDER:
        cur = CURATED[cid]
        print("Building %s ..." % cid)
        try:
            if "eia" in cur:
                out = build_eia_commodity(cid, cur, eia)
            elif "wna" in cur:
                out = build_uranium(cur)
            else:
                out = build_usgs_commodity(cid, cur, usgs_rows)
        except Exception as exc:  # noqa: BLE001 - skip a commodity rather than invent numbers
            warn("%s dropped: %s" % (cid, exc))
            continue
        trade_rows = []
        if cur.get("comtrade") and iso_to_code:
            trade_rows = build_trade(cid, cur["comtrade"], code_to_iso, iso_to_code)
        commodities.append(finish(out, cur, trade_rows))

    used = referenced_iso3(commodities)
    missing = sorted(used - set(COUNTRIES))
    if missing:
        warn("countries referenced but missing from COUNTRIES: %s" % ", ".join(missing))
    countries = {k: COUNTRIES[k] for k in sorted(used) if k in COUNTRIES}

    data = {
        "meta": {
            "compiled": datetime.date.today().isoformat(),
            "generator": "scripts/build_commodities.py",
            "note": ("World commodity supply chains. Metals: USGS Mineral Commodity Summaries 2026 "
                     "(data year %d, mostly estimates; prev = %d). Energy: EIA international statistics, "
                     "top %d countries per list with the remainder as iso3 OTH. Uranium: World Nuclear "
                     "Association. 'OTH' rows are 'other countries' and are not in the countries map. "
                     "Shares are fractions of the stated world total. Trade rows from un_comtrade are "
                     "destination shares of partner-reported import value in %d (mirror data: misses "
                     "non-reporting importers and transshipment). Fields are omitted when unsourced."
                     % (USGS_YEAR, USGS_YEAR - 1, ENERGY_TOP_N, TRADE_YEAR)),
            "provenance": {
                "fetched": ["producers", "consumers", "world_production", "world_production_prev",
                            "world_consumption", "world_reserves", "unit", "year",
                            "us_net_import_reliance", "refining rows with source usgs_mcs or eia_intl",
                            "trade rows with source un_comtrade"],
                "curated": ["countries (names, iso_n3, lon/lat)", "name", "group", "basis", "end_uses",
                            "end_use_basis", "key_inputs", "price_tickers", "notes",
                            "refining rows with source iea_gcmo_2025 or iea_gcmo_2026",
                            "trade rows with source usgs_mcs_chapters",
                            "Comtrade query definitions (HS codes and exporter lists)"],
            },
            "sources": SOURCES,
        },
        "countries": countries,
        "commodities": commodities,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False, allow_nan=False)
        fh.write("\n")

    problems = validate(OUT_PATH)
    n_prod = sum(len(c["producers"]) for c in commodities)
    n_cons = sum(len(c.get("consumers", [])) for c in commodities)
    n_ref = sum(len(c.get("refining", [])) for c in commodities)
    n_trade = sum(len(c.get("trade", [])) for c in commodities)
    print("\nWrote %s" % OUT_PATH)
    print("  commodities %d | countries %d | producer rows %d | consumer rows %d | refining rows %d | trade rows %d"
          % (len(commodities), len(countries), n_prod, n_cons, n_ref, n_trade))
    if WARNINGS:
        print("\nBuild warnings (%d):" % len(WARNINGS))
        for w in WARNINGS:
            print("  - " + w)
    if problems:
        print("\nVALIDATION WARNINGS (%d):" % len(problems))
        for p in problems:
            print("  - " + p)
    else:
        print("\nValidation: OK")
    if "COD" in countries:
        cod_report(data)
    hard = [p for p in problems if "differs from world_production" not in p]
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())

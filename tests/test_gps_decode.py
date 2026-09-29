"""Offline check of the QHY_GPS position decode — no camera or SDK needed.

Run from the repo root (needs astropy + loguru, e.g. inside the container):
    python tests/test_gps_decode.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from libqhyccd import QHY_GPS


def pack_lat(deg, minutes, south=False):
    return (1_000_000_000 if south else 0) + deg * 10_000_000 + round(minutes * 100_000)


def pack_lon(deg, minutes, west=False):
    return (1_000_000_000 if west else 0) + deg * 1_000_000 + round(minutes * 10_000)


def decode(lat_raw, lon_raw):
    g = QHY_GPS()
    g._Latitude = lat_raw
    g._Longitude = lon_raw
    return g.Latitude, g.Longitude


def close(a, b):
    return abs(a - b) < 1e-6


# Siding Spring, as reported by jetson008's QHY174GPS on 2026-09-29.
lat, lon = decode(pack_lat(31, 16.3078, south=True), pack_lon(149, 3.6918))
assert close(lat, -(31 + 16.3078 / 60)), lat
assert close(lon, 149 + 3.6918 / 60), lon  # was 49.06 before the fix

# Three-digit western longitude, and the sign flag must not leak into degrees.
_, lon = decode(0, pack_lon(179, 59.9999, west=True))
assert close(lon, -(179 + 59.9999 / 60)), lon

# Two-digit longitude and northern latitude are unaffected.
lat, lon = decode(pack_lat(51, 28.0), pack_lon(0, 7.5, west=True))
assert close(lat, 51 + 28.0 / 60) and close(lon, -0.125), (lat, lon)

print("GPS decode OK")

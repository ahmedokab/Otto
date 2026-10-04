"""VIN -> "Make Model Year Trim" for the dashboard's vehicle box.

Offline first: make from the manufacturer prefix (WMI, first 3 characters),
year from character 10, and the model for common VW codes. If the internet
is available, NHTSA's free vPIC decoder fills in the exact model and trim.
Set OTTO_VIN_LOOKUP=0 to never send the VIN anywhere.
"""
import json
import os
import urllib.request

LOOKUP_ONLINE = os.getenv("OTTO_VIN_LOOKUP", "1") != "0"
VPIC_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}?format=json"

WMI = {
    "1VW": "Volkswagen", "3VW": "Volkswagen", "WVW": "Volkswagen", "WVG": "Volkswagen", "9BW": "Volkswagen",
    "WAU": "Audi", "WA1": "Audi", "WBA": "BMW", "WBS": "BMW", "5UX": "BMW", "WDD": "Mercedes-Benz",
    "WDB": "Mercedes-Benz", "4JG": "Mercedes-Benz", "WP0": "Porsche", "WP1": "Porsche",
    "1HG": "Honda", "2HG": "Honda", "JHM": "Honda", "5FN": "Honda", "5J6": "Honda", "19X": "Honda",
    "JH4": "Acura", "19U": "Acura", "1FA": "Ford", "1FT": "Ford", "1FM": "Ford", "3FA": "Ford",
    "1G1": "Chevrolet", "1GC": "Chevrolet", "2G1": "Chevrolet", "3GN": "Chevrolet", "1GT": "GMC",
    "4T1": "Toyota", "4T3": "Toyota", "JTD": "Toyota", "JTE": "Toyota", "2T1": "Toyota", "5TD": "Toyota",
    "5TF": "Toyota", "JTH": "Lexus", "2T2": "Lexus", "1N4": "Nissan", "3N1": "Nissan", "JN1": "Nissan",
    "5N1": "Nissan", "JN8": "Nissan", "KMH": "Hyundai", "5NP": "Hyundai", "KNA": "Kia", "KND": "Kia",
    "5XY": "Kia", "JF1": "Subaru", "JF2": "Subaru", "4S3": "Subaru", "4S4": "Subaru", "JM1": "Mazda",
    "JM3": "Mazda", "5YJ": "Tesla", "7SA": "Tesla", "1C4": "Jeep", "1J4": "Jeep", "1C6": "Ram",
    "2C3": "Chrysler", "1C3": "Chrysler", "2C4": "Chrysler", "YV1": "Volvo", "ZFA": "Fiat",
    "SAJ": "Jaguar", "SAL": "Land Rover", "JA3": "Mitsubishi", "JA4": "Mitsubishi", "ML3": "Mitsubishi",
}

# VW (and some VW-group) model codes, VIN characters 7-8
VW_MODELS = {
    "AT": "Beetle", "1C": "New Beetle", "1Y": "New Beetle", "AJ": "Jetta", "BU": "Jetta", "16": "Jetta",
    "1K": "Jetta", "AU": "Golf", "1J": "Golf", "5K": "Golf", "A3": "Passat", "3C": "Passat",
    "5N": "Tiguan", "AX": "Tiguan", "AD": "Tiguan", "CA": "Atlas", "CR": "Atlas Cross Sport",
    "7L": "Touareg", "7P": "Touareg", "CG": "Taos", "13": "CC", "35": "CC", "7B": "Routan",
}

YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"   # 1980 (A) .. 2009 (9), then repeats from 2010


def model_year(vin):
    """Character 10 repeats every 30 years. For North-American passenger cars,
    a letter in position 7 means the 2010+ cycle."""
    i = YEAR_CODES.find(vin[9])
    if i < 0:
        return None
    return 1980 + i + (30 if vin[6].isalpha() else 0)


def decode_offline(vin):
    vin = vin.upper()
    make = WMI.get(vin[:3])
    model = VW_MODELS.get(vin[6:8]) if make == "Volkswagen" else None
    return {"vin": vin, "make": make, "model": model, "year": model_year(vin), "trim": None, "source": "offline"}


def decode_online(vin, timeout=4):
    with urllib.request.urlopen(VPIC_URL.format(vin=vin), timeout=timeout) as r:
        row = json.load(r)["Results"][0]
    year = row.get("ModelYear")
    return {"vin": vin, "make": (row.get("Make") or "").title() or None, "model": row.get("Model") or None,
            "year": int(year) if (year or "").isdigit() else None,
            "trim": row.get("Trim") or row.get("Series") or None, "source": "nhtsa"}


def decode(vin):
    """Best decode available. Never raises."""
    info = decode_offline(vin)
    if LOOKUP_ONLINE:
        try:
            online = decode_online(info["vin"])
            if info["make"]:
                online.pop("make")     # keep our spelling ("BMW", not NHTSA's title-cased "Bmw")
            info.update({k: v for k, v in online.items() if v})
        except Exception:
            pass                       # offline is fine: keep what we have
    return info


def vehicle_name(info):
    """The dashboard's "Make Model Year Trim" text."""
    parts = [info.get("make"), info.get("model"), info.get("year"), info.get("trim")]
    return " ".join(str(p) for p in parts if p)

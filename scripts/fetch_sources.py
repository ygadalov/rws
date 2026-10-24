#!/usr/bin/env python3
"""Свежие внешние источники для сверки координат (одной командой):

  data/raw/osm_esr_fresh.csv  — объекты OpenStreetMap с тегом esr:user (Overpass API): код, тип и id объекта,
                                координаты (у линий и контуров — центр), railway, name
  data/raw/wikidata_esr.csv   — элементы Викиданных с кодом ЕСР (свойство P2815) и координатами (P625)
  data/raw/sources_meta.json  — дата выгрузки и дата данных OSM

Запуск: python3 scripts/fetch_sources.py
Overpass перебирается по зеркалам: основной сервер часто перегружен.
"""
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
UA = "rws1520-coordinate-check/1.0"
OVERPASS = ["https://maps.mail.ru/osm/tools/overpass/api/interpreter",
            "https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"]
OVERPASS_Q = '[out:json][timeout:600];(node["esr:user"];way["esr:user"];relation["esr:user"];);out center tags;'
WIKIDATA = "https://query.wikidata.org/sparql"
WIKIDATA_Q = """SELECT ?item ?esr ?coord ?itemLabel WHERE {
  ?item wdt:P2815 ?esr. OPTIONAL { ?item wdt:P625 ?coord. }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "ru,en". } }"""


def get(url, params, timeout, accept):
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_osm():
    last = None
    for url in OVERPASS:
        for attempt in range(2):
            try:
                d = json.loads(get(url, {"data": OVERPASS_Q}, 900, "application/json"))
                return d, url
            except Exception as e:  # перегрузка, обрыв, таймаут — следующее зеркало
                last = e
                time.sleep(5)
    raise RuntimeError(f"Overpass недоступен: {last}")


def main():
    d, url = fetch_osm()
    n = 0
    with open(RAW / "osm_esr_fresh.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["esr", "osm_type", "osm_id", "lat", "lon", "railway", "name"])
        for e in d["elements"]:
            t = e.get("tags", {})
            la = e.get("lat", e.get("center", {}).get("lat"))
            lo = e.get("lon", e.get("center", {}).get("lon"))
            if la is None:
                continue
            for c in re.split(r"[;,]", str(t.get("esr:user", ""))):
                c = re.sub(r"\D", "", c)
                if len(c) == 6:  # редкие 5- и 7-значные значения — опечатки в OSM
                    w.writerow([c, e["type"], e["id"], la, lo, t.get("railway", ""), t.get("name", "")])
                    n += 1
    print(f"OSM ({url}): {len(d['elements'])} объектов, {n} привязок кодов")

    for attempt in range(4):  # при перегрузке Викиданные ограничивают частоту (429) — ждём и повторяем
        try:
            rows = json.loads(get(WIKIDATA, {"query": WIKIDATA_Q}, 300, "application/sparql-results+json"))["results"]["bindings"]
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 3:
                raise
            time.sleep(70)
    m = 0
    with open(RAW / "wikidata_esr.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["esr", "qid", "lat", "lon", "label"])
        for b in rows:
            p = re.match(r"Point\(([-\d.]+) ([-\d.]+)\)", b.get("coord", {}).get("value", ""))
            c = re.sub(r"\D", "", b["esr"]["value"])
            if p and len(c) == 6:
                w.writerow([c, b["item"]["value"].rsplit("/", 1)[-1], p.group(2), p.group(1), b.get("itemLabel", {}).get("value", "")])
                m += 1
    print(f"Викиданные: {m} элементов с кодом ЕСР и координатами")

    meta = {"fetched": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "osm_base": d.get("osm3s", {}).get("timestamp_osm_base", "")[:10], "osm_objects": len(d["elements"]),
            "wikidata_items": m}
    (RAW / "sources_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(meta)


if __name__ == "__main__":
    sys.exit(main())

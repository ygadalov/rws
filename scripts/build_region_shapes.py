#!/usr/bin/env python3
"""Контуры регионов справочника для карты ограничений курсирования: Natural Earth 5.1.2 (общественное достояние).

Ключи — названия регионов из stations.csv («Московская область + Москва» = объединение двух контуров),
страны без разбивки на регионы (Казахстан, Беларусь …) — контур страны.

Запуск: python3 scripts/build_region_shapes.py <папка с geojson Natural Earth>
Нужны ne_10m_admin_1_states_provinces.geojson и ne_50m_admin_0_countries.geojson.
Результат: dashboard/vendor/region-shapes.json
"""
import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_offline_labels import COUNTRIES_1520, simplify, r3, unwrap  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dashboard" / "vendor" / "region-shapes.json"

# название в справочнике → название в NE (name_ru, а где оно неоднозначно — английское name)
ALIAS = {
    "Алтайский край": "en:Altay", "Республика Алтай": "en:Gorno-Altay",
    "Кабардино-Балкарская Республика": "Кабардино-Балкария", "Карачаево-Черкесская Республика": "Карачаево-Черкесия",
    "Киевская область": "en:Kiev", "Киев": "en:Kiev City", "Ровенская область": "Ровненская область",
    "Республика Адыгея": "Адыгея", "Республика Бурятия": "Бурятия", "Республика Дагестан": "Дагестан",
    "Республика Ингушетия": "Ингушетия", "Республика Калмыкия": "Калмыкия", "Республика Карелия": "Карелия",
    "Республика Крым": "en:Crimea", "Республика Марий Эл": "Марий Эл", "Республика Мордовия": "Мордовия",
    "Республика Саха (Якутия)": "Якутия", "Республика Татарстан": "Татарстан", "Республика Хакасия": "Хакасия",
    "Республика Северная Осетия - Алания": "Республика Северная Осетия-Алания", "Удмуртская Республика": "Удмуртия", "Ханты-Мансийский автономный округ": "Ханты-Мансийский автономный округ — Югра",
    "Чеченская Республика": "Чечня", "Чувашская Республика": "Чувашия", "Республика Тыва": "Тыва",
}
COUNTRY = {"Азербайджан": "AZE", "Армения": "ARM", "Беларусь": "BLR", "Грузия": "GEO", "Казахстан": "KAZ", "Киргизия": "KGZ",
           "Латвия": "LVA", "Литва": "LTU", "Молдова": "MDA", "Таджикистан": "TJK", "Туркменистан": "TKM", "Узбекистан": "UZB",
           "Украина": "UKR", "Эстония": "EST"}


def polys(g):
    return [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]


def shape(g, tol):
    out = []
    for poly in polys(g):
        rings = [r3(unwrap(simplify(r, tol))) for r in poly]
        if len(rings[0]) >= 4:
            out.append([r for r in rings if len(r) >= 4])
    return out


def main(src):
    d = Path(src)
    adm1 = [f for f in json.loads((d / "ne_10m_admin_1_states_provinces.geojson").read_text(encoding="utf-8"))["features"]
            if f["properties"]["adm0_a3"] in COUNTRIES_1520 and f["geometry"]]
    adm0 = {f["properties"]["ADM0_A3"]: f for f in json.loads((d / "ne_50m_admin_0_countries.geojson").read_text(encoding="utf-8"))["features"]}
    by_ru = {f["properties"]["name_ru"]: f for f in adm1 if f["properties"]["name_ru"]}
    by_en = {f["properties"]["name"]: f for f in adm1}
    st = pd.read_csv(ROOT / "data" / "output" / "stations.csv", sep=";", dtype=str, keep_default_na=False)
    out, miss = {}, []
    for reg in sorted(set(st.region) - {""}):
        if reg in COUNTRY:
            out[reg] = shape(adm0[COUNTRY[reg]]["geometry"], 0.02)
            continue
        parts = []
        for p in (x.strip() for x in reg.split("+")):
            p = re.sub(r"\s*\((Россия|Украина)\)$", "", p)
            a = ALIAS.get(p, p)
            f = by_en.get(a[3:]) if a.startswith("en:") else by_ru.get(a)
            if not f:
                miss.append(p)
                continue
            parts += shape(f["geometry"], 0.02)
        if parts:
            out[reg] = parts
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{OUT}: {len(out)} регионов, {OUT.stat().st_size / 1e6:.2f} МБ; без контура: {miss}")


if __name__ == "__main__":
    main(sys.argv[1])

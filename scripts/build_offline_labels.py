#!/usr/bin/env python3
"""Подписи и вода для офлайн-подложки: Natural Earth 5.1.2 (общественное достояние).

  города и посёлки  — ne_10m_populated_places (NAME_RU, POP_MAX, MIN_ZOOM)
  страны            — ne_50m_admin_0_countries (NAME_RU, LABEL_X/Y, MIN_LABEL)
  моря и заливы     — ne_10m_geography_marine_polys (name_ru, min_label)
  озёра             — ne_10m_lakes (геометрия + name_ru)
  реки              — ne_10m_rivers_lake_centerlines (только линии: русских названий в NE нет)

Запуск: python3 scripts/build_offline_labels.py <папка с geojson Natural Earth>
Файлы: https://github.com/nvkelso/natural-earth-vector/tree/v5.1.2/geojson
Результат: dashboard/vendor/basemap-labels.json
"""
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "dashboard" / "vendor" / "basemap-labels.json"


def in_region(x, y):
    return (15 <= x <= 180 and 28 <= y <= 80) or (x <= -169 and y >= 60)


def r3(c):
    return [round(c[0], 3), round(c[1], 3)] if isinstance(c[0], (int, float)) else [r3(v) for v in c]


def points(geom):
    c = geom["coordinates"]
    flat = lambda v: [] if not v else [v] if isinstance(v[0], (int, float)) else [p for w in v for p in flat(w)]
    return flat(c)


def unwrap(c):  # Чукотка за 180°, как в build_offline_basemap.mjs
    if isinstance(c[0], (int, float)):
        return [c[0] + 360 if c[0] < -90 else c[0], c[1]]
    return [unwrap(v) for v in c]


def centroid(geom):
    """Центр крупнейшего кольца — точка для подписи моря или озера."""
    rings = [geom["coordinates"][0]] if geom["type"] == "Polygon" else [p[0] for p in geom["coordinates"]]
    best, area_best = None, -1
    for ring in rings:
        a = cx = cy = 0.0
        for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
            f = x1 * y2 - x2 * y1
            a += f; cx += (x1 + x2) * f; cy += (y1 + y2) * f
        if abs(a) > area_best and a:
            area_best, best = abs(a), [cx / (3 * a), cy / (3 * a)]
    return best


def load(d, name):
    return json.loads((d / f"{name}.geojson").read_text(encoding="utf-8"))["features"]


def main(src):
    d = Path(src)
    places = []
    for f in load(d, "ne_10m_populated_places"):
        p, (x, y) = f["properties"], f["geometry"]["coordinates"]
        if in_region(x, y) and p.get("NAME_RU"):
            cap = 2 if p["FEATURECLA"].startswith("Admin-0 capital") else 1 if p["FEATURECLA"] == "Admin-1 capital" else 0
            places.append([*r3(unwrap([x, y])), p["NAME_RU"], p["MIN_ZOOM"], p["POP_MAX"], cap])
    places.sort(key=lambda t: (-t[5], -t[4]))

    countries = []
    for f in load(d, "ne_50m_admin_0_countries"):
        p = f["properties"]
        if in_region(p["LABEL_X"], p["LABEL_Y"]) and p.get("NAME_RU"):
            countries.append([*r3([p["LABEL_X"], p["LABEL_Y"]]), p["NAME_RU"], p["MIN_LABEL"]])

    seas = []
    for f in load(d, "ne_10m_geography_marine_polys"):
        p, c = f["properties"], centroid(f["geometry"])
        if c and in_region(*c) and p.get("name_ru") and p.get("min_label", 9) <= 6:
            seas.append([*r3(c), p["name_ru"], p["min_label"]])

    water = []
    for f in load(d, "ne_10m_lakes"):
        p = f["properties"]
        if p["scalerank"] <= 6 and any(in_region(*pt) for pt in points(f["geometry"])):
            water.append({"type": "Feature", "properties": {"k": "lake"}, "geometry": {**f["geometry"], "coordinates": r3(f["geometry"]["coordinates"])}})
            c = centroid(f["geometry"])
            if p.get("name_ru") and p["scalerank"] <= 4 and c:
                seas.append([*r3(c), p["name_ru"], max(3, p.get("min_label", 5))])
    for f in load(d, "ne_10m_rivers_lake_centerlines"):
        p = f["properties"]
        if p["scalerank"] <= 6 and f["geometry"] and any(in_region(*pt) for pt in points(f["geometry"])):
            water.append({"type": "Feature", "properties": {"k": "river", "z": p["min_zoom"]},
                          "geometry": {**f["geometry"], "coordinates": r3(unwrap(f["geometry"]["coordinates"]))}})

    out = {"places": places, "countries": countries, "seas": seas, "water": {"type": "FeatureCollection", "features": water}}
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{OUT}: {len(places)} нас. пунктов, {len(countries)} стран, {len(seas)} морей/озёр, "
          f"{len(water)} объектов воды, {OUT.stat().st_size / 1e6:.2f} МБ")


if __name__ == "__main__":
    main(sys.argv[1])

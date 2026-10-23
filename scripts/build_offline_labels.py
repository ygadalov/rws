#!/usr/bin/env python3
"""Подписи и вода для офлайн-подложки: Natural Earth 5.1.2 (общественное достояние).

  города и посёлки  — ne_10m_populated_places (NAME_RU, POP_MAX, MIN_ZOOM)
  страны            — ne_50m_admin_0_countries (NAME_RU, LABEL_X/Y, MIN_LABEL)
  моря и заливы     — ne_10m_geography_marine_polys (name_ru, min_label)
  озёра             — ne_10m_lakes (геометрия + name_ru)
  реки              — ne_10m_rivers_lake_centerlines (только линии: русских названий в NE нет)
  регионы           — ne_10m_admin_1_states_provinces (границы и name_ru) для стран пространства 1520
  городские терр.   — ne_10m_urban_areas
  автодороги        — ne_10m_roads (магистрали и основные дороги)

Запуск: python3 scripts/build_offline_labels.py <папка с geojson Natural Earth>
Файлы: https://github.com/nvkelso/natural-earth-vector/tree/v5.1.2/geojson
Результат: dashboard/vendor/basemap-labels.json
"""
import json
import sys

sys.setrecursionlimit(100000)
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


COUNTRIES_1520 = {"RUS", "UKR", "BLR", "KAZ", "UZB", "KGZ", "TJK", "TKM", "AZE", "ARM", "GEO", "MDA", "LVA", "LTU", "EST"}


def simplify(line, tol):
    """Дуглас—Пекер: упрощение линии с допуском tol градусов (файл должен оставаться компактным)."""
    if len(line) < 3:
        return line
    (x1, y1), (x2, y2) = line[0], line[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = (dx * dx + dy * dy) ** 0.5
    dmax, idx = 0.0, 0
    for i in range(1, len(line) - 1):
        x, y = line[i]
        # замкнутое кольцо (начало = конец): расстояние до точки, иначе — до прямой
        d = abs(dy * x - dx * y + x2 * y1 - y2 * x1) / norm if norm > 1e-12 else ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5
        if d > dmax:
            dmax, idx = d, i
    if dmax <= tol:
        return [line[0], line[-1]]
    return simplify(line[: idx + 1], tol)[:-1] + simplify(line[idx:], tol)


def lines_of(geom):
    t, c = geom["type"], geom["coordinates"]
    return {"LineString": [c], "MultiLineString": c, "Polygon": c, "MultiPolygon": [r for p in c for r in p]}[t]


def simple_lines(geom, tol):
    return [r3(unwrap(l)) for l in (simplify(l, tol) for l in lines_of(geom)) if len(l) > 1]


def point_in_ring(x, y, ring):
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def country_test(d):
    """Точка внутри одной из стран 1520 (контуры ne_50m, с отбором по рамке для скорости)."""
    shapes = []
    for f in load(d, "ne_50m_admin_0_countries"):
        if f["properties"].get("ADM0_A3") in COUNTRIES_1520:
            g = f["geometry"]
            for poly in ([g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]):
                ring = poly[0]
                xs, ys = [p[0] for p in ring], [p[1] for p in ring]
                shapes.append((min(xs), min(ys), max(xs), max(ys), ring))

    def inside(x, y):
        return any(a <= x <= c and b <= y <= e and point_in_ring(x, y, r) for a, b, c, e, r in shapes)
    return inside


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
            g = f["geometry"]
            polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
            polys = [[r3(simplify(r, 0.004)) for r in poly] for poly in polys]
            water.append({"type": "Feature", "properties": {"k": "lake"},
                          "geometry": {"type": "MultiPolygon", "coordinates": [p for p in polys if len(p[0]) >= 4]}})
            c = centroid(f["geometry"])
            if p.get("name_ru") and p["scalerank"] <= 4 and c:
                seas.append([*r3(c), p["name_ru"], max(3, p.get("min_label", 5))])
    for f in load(d, "ne_10m_rivers_lake_centerlines"):
        p = f["properties"]
        if p["scalerank"] <= 6 and f["geometry"] and any(in_region(*pt) for pt in points(f["geometry"])):
            water.append({"type": "Feature", "properties": {"k": "river", "z": p["min_zoom"]},
                          "geometry": {"type": "MultiLineString", "coordinates": simple_lines(f["geometry"], 0.004)}})

    # регионы: только контуры (линии) + точки подписей
    regions = []
    for f in load(d, "ne_10m_admin_1_states_provinces"):
        p = f["properties"]
        if p["adm0_a3"] not in COUNTRIES_1520 or not f["geometry"]:
            continue
        water.append({"type": "Feature", "properties": {"k": "region"},
                      "geometry": {"type": "MultiLineString", "coordinates": simple_lines(f["geometry"], 0.012)}})
        if p.get("name_ru"):
            regions.append([*r3(unwrap([p["longitude"], p["latitude"]])), p["name_ru"], p.get("min_label") or 6])
    in_1520 = country_test(d)
    for f in load(d, "ne_10m_urban_areas"):
        g = f["geometry"]
        if g and in_1520(*points(g)[0]):
            polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
            # мелкие пятна (< ~2 км) на обзорной схеме не видны — пропускаем
            polys = [poly for poly in polys if max(x for x, _ in poly[0]) - min(x for x, _ in poly[0]) > 0.05]
            polys = [[r3(unwrap(simplify(r, 0.008))) for r in poly] for poly in polys]
            polys = [p for p in polys if len(p[0]) >= 4]
            if polys:
                water.append({"type": "Feature", "properties": {"k": "urban"}, "geometry": {"type": "MultiPolygon", "coordinates": polys}})
    for f in load(d, "ne_10m_roads"):
        p, g = f["properties"], f["geometry"]
        # тип в NE для СНГ почти всегда «Road»/«Unknown» — отбираем по значимости (scalerank)
        if p["type"] == "Ferry Route" or p["scalerank"] > 7 or not g:
            continue
        pts = points(g)
        if in_1520(*pts[0]) or in_1520(*pts[-1]):
            water.append({"type": "Feature", "properties": {"k": "road", "t": 1 if p["scalerank"] <= 4 else 2},
                          "geometry": {"type": "MultiLineString", "coordinates": simple_lines(g, 0.006)}})

    out = {"places": places, "countries": countries, "seas": seas, "regions": regions,
           "water": {"type": "FeatureCollection", "features": water}}
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{OUT}: {len(places)} нас. пунктов, {len(countries)} стран, {len(regions)} регионов, {len(seas)} морей/озёр, "
          f"{len(water)} объектов воды, {OUT.stat().st_size / 1e6:.2f} МБ")


if __name__ == "__main__":
    main(sys.argv[1])

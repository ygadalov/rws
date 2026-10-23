#!/usr/bin/env python3
"""Сборка самодостаточного HTML-дашборда: dashboard/template.html + данные → dashboard/rws1520.html.

Данные встраиваются в страницу компактно (повторяющиеся строки — через словари),
поэтому rws1520.html открывается двойным щелчком, без сервера.

Запуск (после build_stations.py): python3 scripts/build_dashboard.py
"""
import json
import re
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "output" / "stations.csv"
TEMPLATE = ROOT / "dashboard" / "template.html"
OUT = ROOT / "dashboard" / "rws1520.html"

# (колонка в stations.csv, ключ в странице, кодирование: s — строка, d — словарь, n — число, b — флаг)
FIELDS = [
    ("esr", "esr", "s"), ("name", "name", "s"), ("type", "type", "d"),
    ("lat", "lat", "n"), ("lon", "lon", "n"),
    ("coord_quality", "q", "d"), ("coord_precision_km", "prec", "n"), ("coord_source", "qsrc", "s"),
    ("coord_note", "qnote", "s"),
    ("country", "country", "d"), ("region", "region", "d"), ("railway", "railway", "d"),
    ("division", "division", "d"),
    ("express", "express", "s"), ("express_name", "exname", "s"), ("express_city", "excity", "d"),
    ("dup_esr", "dup", "s"), ("esr_crimea_old", "crold", "s"), ("esr_crimea_new", "crnew", "s"),
    ("in_tariff_list", "tariff", "b"), ("tr4_owner", "owner", "d"), ("tr4_pass_ops", "pops", "d"),
    ("tr4_freight_paragraphs", "fops", "d"), ("tr4_transit_nearest", "transit", "d"),
    ("tr4_transit_nearest_km", "transitkm", "n"), ("tr4_former_name", "former", "s"),
    ("tr4_comment", "comment", "s"),
    ("osm_id", "osm", "s"), ("osm_type", "osmtype", "d"), ("osm_name", "osmname", "s"),
    ("is_virtual", "virt", "b"), ("is_export", "exp", "b"), ("is_transship", "trans", "b"),
    ("is_narrow_gauge", "narrow", "b"), ("transit_conflict", "tconf", "b"),
    ("admin_inferred", "inferred", "d"),
    ("is_new_tr4", "newcode", "b"), ("tr4_date", "tr4date", "d"), ("tr4_excluded", "excluded", "b"),
    ("tr4_sections", "sections", "s"), ("tr4_port", "port", "s"), ("section_conflict", "sconf", "b"),
]
SECTIONS = ROOT / "data" / "output" / "tr4_sections.csv"
CITIES = ROOT / "data" / "output" / "tr4_cities.csv"


def section_lines():
    """Линии тарифных участков: соседние станции с координатами, без явных скачков."""
    import math
    sec = pd.read_csv(SECTIONS, sep=";", dtype={"esr": str}, keep_default_na=False)
    sec = sec[sec.coord_quality.isin(["A", "B", "D", "E"])]
    out = []
    for (sid, name, kind), d in sec.groupby(["sec", "sec_name", "sec_kind"], sort=False):
        pts, parts = [], []
        prev = None
        for r in d.itertuples():
            lat, lon = float(r.lat), float(r.lon)
            if prev:
                dist = 6371 * 2 * math.asin(math.sqrt(math.sin(math.radians(lat - prev[0]) / 2) ** 2 + math.cos(
                    math.radians(lat)) * math.cos(math.radians(prev[0])) * math.sin(math.radians(lon - prev[1]) / 2) ** 2))
                if dist > (r.km - prev[2]) * 1.3 + 5:  # разрыв: не рисуем ложный отрезок
                    if len(pts) > 1:
                        parts.append(pts)
                    pts = []
            pts.append([round(lon, 4), round(lat, 4)])
            prev = (lat, lon, r.km)
        if len(pts) > 1:
            parts.append(pts)
        if parts:
            out.append([sid, name, kind, parts])
    return out


VENDOR = ROOT / "dashboard" / "vendor"


def vendor(name):
    """Библиотека для встраивания в <script>/<style>: без ссылки на source map и без «</script»."""
    text = (VENDOR / name).read_text(encoding="utf-8")
    text = re.sub(r"\n?//# sourceMappingURL=\S+\s*$", "", text)
    text = re.sub(r"\n?/\*# sourceMappingURL=\S+ \*/\s*$", "", text)
    low = text.lower()
    if name.endswith(".js"):  # внутри <script> опасны только «</script» и пара «<!--» + «<script»
        assert "</script" not in low and not ("<!--" in low and "<script" in low), name
    else:
        assert "</style" not in low, name
    return text


def glyphs():
    """{шрифт: {диапазон: base64 PBF}} — подписи на своей карте без загрузки шрифтов из интернета."""
    import base64
    out = {}
    for f in sorted((VENDOR / "glyphs").glob("*/*.pbf")):
        out.setdefault(f.parent.name, {})[f.stem] = base64.b64encode(f.read_bytes()).decode()
    return out


def cities():
    c = pd.read_csv(CITIES, sep=";", dtype={"esr": str}, keep_default_na=False)
    return c[["city", "region", "esr", "km"]].values.tolist()


def main():
    st = pd.read_csv(SRC, sep=";", dtype=str, keep_default_na=False)
    dicts, cols = {}, []
    for src, key, kind in FIELDS:
        v = st[src]
        if kind == "d":
            values = sorted(set(v))
            idx = {x: i for i, x in enumerate(values)}
            dicts[key] = values
            cols.append(v.map(idx).tolist())
        elif kind == "n":
            num = pd.to_numeric(v, errors="coerce").round(5)
            cols.append([None if pd.isna(x) else (int(x) if float(x).is_integer() else float(x)) for x in num])
        elif kind == "b":
            cols.append((v == "True").astype(int).tolist())
        else:
            cols.append(v.tolist())
    data = {
        "built": date.today().isoformat(),
        "fields": [k for _, k, _ in FIELDS],
        "kinds": [t for _, _, t in FIELDS],
        "dicts": dicts,
        "columns": cols,  # по колонкам — так JSON заметно меньше
        "lines": section_lines(),
        "cities": cities(),
        # офлайн-подложка (Natural Earth 1:50m), см. scripts/build_offline_basemap.mjs
        "basemap": json.loads((VENDOR / "basemap-offline.json").read_text(encoding="utf-8")),
        # подписи и вода офлайн-схемы (Natural Earth), см. scripts/build_offline_labels.py
        "labels": json.loads((VENDOR / "basemap-labels.json").read_text(encoding="utf-8")),
        # своя карта из файла PMTiles: стиль Protomaps (русские подписи) и встроенные шрифты Noto Sans
        "pmstyle": json.loads((VENDOR / "protomaps-style.json").read_text(encoding="utf-8")),
        "glyphs": glyphs(),
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    html = TEMPLATE.read_text(encoding="utf-8")
    for key, name in (("MAPLIBRE_JS", "maplibre-gl.js"), ("MAPLIBRE_CSS", "maplibre-gl.css"), ("XLSX_JS", "xlsx.full.min.js"),
                      ("PMTILES_JS", "pmtiles.js")):
        html = html.replace(f"/*__{key}__*/", vendor(name), 1)
    html = html.replace("/*__DATA__*/null", payload)
    OUT.write_text(html, encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: {len(st)} станций, {OUT.stat().st_size / 1e6:.1f} МБ")


if __name__ == "__main__":
    main()

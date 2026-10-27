#!/usr/bin/env python3
"""Сборка самодостаточного HTML-дашборда: dashboard/template.html + данные → dashboard/rws1520_v<версия>.html.

Данные встраиваются в страницу компактно (повторяющиеся строки — через словари),
поэтому файл открывается двойным щелчком, без сервера. Версия — из файла VERSION в корне (см. CHANGELOG.md);
файлы прежних версий в dashboard/ удаляются (история — в git).

Запуск (после build_stations.py): python3 scripts/build_dashboard.py
Пробная сборка с функциями в разработке (ограничения курсирования, справка о нарушениях):
  python3 scripts/build_dashboard.py --preview  → dashboard/rws1520_v<версия>_preview.html (в git не хранится)
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "output" / "stations.csv"
TEMPLATE = ROOT / "dashboard" / "template.html"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
PREVIEW = "--preview" in sys.argv
OUT = ROOT / "dashboard" / f"rws1520_v{VERSION}{'_preview' if PREVIEW else ''}.html"

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
    ("ext_check", "xcheck", "d"), ("ext_osm_km", "xosm", "n"), ("ext_wd_km", "xwd", "n"), ("wikidata", "wd", "s"),
]
SECTIONS = ROOT / "data" / "output" / "tr4_sections.csv"
CITIES = ROOT / "data" / "output" / "tr4_cities.csv"
VERIFY = ROOT / "data" / "output" / "verification.json"
DEPOTS = ROOT / "data" / "output" / "depots.csv"
MAINLINES = ROOT / "data" / "output" / "mainlines.csv"
GRAPH = ROOT / "data" / "output" / "rail_graph.json"
SNAP_MAX_KM = 50  # станция вне тарифных участков привязывается к ближайшему узлу графа не дальше этого


def rail_graph(st):
    """Граф для расстояний по сети (scripts/build_rail_graph.py) + привязка каждой станции к узлу графа."""
    if not GRAPH.exists():
        return None, None, None
    import numpy as np
    from scipy.spatial import cKDTree
    g = json.loads(GRAPH.read_text(encoding="utf-8"))
    idx = {e: i for i, e in enumerate(g["nodes"])}
    lat = pd.to_numeric(st.lat, errors="coerce").values
    lon = pd.to_numeric(st.lon, errors="coerce").values
    node_of = [idx.get(e, -1) for e in st.esr]
    have = [(i, node_of[i]) for i in range(len(st)) if node_of[i] >= 0 and not np.isnan(lat[i])]
    tree = cKDTree(np.radians([[lat[i], lon[i]] for i, _ in have]))
    gnode, gsnap = [], []
    for i in range(len(st)):
        if node_of[i] >= 0:
            gnode.append(node_of[i]); gsnap.append(0)
        elif not np.isnan(lat[i]):
            d, k = tree.query(np.radians([lat[i], lon[i]]))
            km = float(d) * 6371
            ok = km <= SNAP_MAX_KM
            gnode.append(have[k][1] if ok else -1); gsnap.append(round(km * 1.2, 1) if ok else 0)
        else:
            gnode.append(-1); gsnap.append(0)
    e = g["edges"]
    graph = {"n": len(g["nodes"]), "a": [x[0] for x in e], "b": [x[1] for x in e], "w": [x[2] for x in e], "check": g.get("check")}
    return graph, gnode, gsnap


def depots(st):
    """Предприятия ремонта с местоположением (scripts/build_depots.py): [код, название, кратко, тип, владелец,
    индекс станции, ТР, ДР, КР, прочие виды ремонта]."""
    if not DEPOTS.exists():
        return []
    d = pd.read_csv(DEPOTS, sep=";", dtype=str, keep_default_na=False)
    row = {e: i for i, e in enumerate(st.esr)}
    d = d[d.esr.isin(row)]
    t = lambda v: 1 if v == "True" else 0
    return [[r.code, r.name, r.short_name, r.kind, r.owner, row[r.esr], t(r.tr), t(r.dr), t(r.kr), r.extra] for r in d.itertuples()]


def mainlines(st):
    """Главные широтные магистрали (scripts/build_mainlines.py): линии [код, название, описание] и станции
    [индекс станции, индекс линии, км от начала части, индекс названия начала]."""
    if not MAINLINES.exists():
        return None
    sys.path.insert(0, str(ROOT / "scripts"))
    from build_mainlines import MAINLINES as LINES
    m = pd.read_csv(MAINLINES, sep=";", dtype={"esr": str}, keep_default_na=False)
    row = {e: i for i, e in enumerate(st.esr)}
    codes = [c for c, *_ in LINES]
    origins = sorted(set(m["from"]))
    return {"lines": [[c, n, d] for c, n, d, *_ in LINES], "origins": origins,
            "st": [[row[r.esr], codes.index(r.line), int(r.km), origins.index(r._4)] for r in m.itertuples() if r.esr in row]}


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
    graph, gnode, gsnap = rail_graph(st)
    data = {
        "version": VERSION,
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
        # предприятия ремонта вагонов и граф сети для расстояний «по железной дороге»
        "depots": depots(st),
        # главные широтные магистрали (Транссиб, БАМ, Южсиб, Турксиб, Северный коридор ТАЖМ)
        "mainlines": mainlines(st),
        "graph": graph, "gnode": gnode, "gsnap": gsnap,
        # сверка со свежим OSM и Викиданными (scripts/verify_sources.py)
        "verify": json.loads(VERIFY.read_text(encoding="utf-8")) if VERIFY.exists() else None,
        # контуры регионов для запретных зон (ограничения курсирования, только в пробной сборке), см. scripts/build_region_shapes.py
        "regionShapes": json.loads((VENDOR / "region-shapes.json").read_text(encoding="utf-8")) if PREVIEW else {},
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # только в шаблоне, до встраивания библиотек
    html = TEMPLATE.read_text(encoding="utf-8").replace("__VERSION__", VERSION + (" превью" if PREVIEW else ""))
    html = html.replace("/*__PREVIEW__*/false", "true" if PREVIEW else "false")
    for key, name in (("MAPLIBRE_JS", "maplibre-gl.js"), ("MAPLIBRE_CSS", "maplibre-gl.css"), ("XLSX_JS", "xlsx.full.min.js"),
                      ("PMTILES_JS", "pmtiles.js"), ("INFO_TIP_CSS", "info-tooltip.css"), ("INFO_TIP_JS", "info-tooltip.js")):
        html = html.replace(f"/*__{key}__*/", vendor(name), 1)
    html = html.replace("/*__DATA__*/null", payload)
    for old in OUT.parent.glob("rws1520_v*_preview.html" if PREVIEW else "rws1520*.html"):
        if old != OUT and (PREVIEW or not old.stem.endswith("_preview")):
            old.unlink()
    OUT.write_text(html, encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: {len(st)} станций, {OUT.stat().st_size / 1e6:.1f} МБ")


if __name__ == "__main__":
    main()

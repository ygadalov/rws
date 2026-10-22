#!/usr/bin/env python3
"""Сборка самодостаточного HTML-дашборда: dashboard/template.html + данные → dashboard/index.html.

Данные встраиваются в страницу компактно (повторяющиеся строки — через словари),
поэтому index.html открывается двойным щелчком, без сервера.

Запуск (после build_stations.py): python3 scripts/build_dashboard.py
"""
import json
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "output" / "stations.csv"
TEMPLATE = ROOT / "dashboard" / "template.html"
OUT = ROOT / "dashboard" / "index.html"

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
]


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
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", payload)
    OUT.write_text(html, encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: {len(st)} станций, {OUT.stat().st_size / 1e6:.1f} МБ")


if __name__ == "__main__":
    main()

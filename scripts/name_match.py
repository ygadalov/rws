"""Коды без координат: поиск станции по названию среди всех станций и платформ OSM (data/raw/osm_stations_named.csv).

У объекта OSM код ЕСР не нужен — сопоставление идёт по названию (все варианты: name, name:ru, alt_name,
национальные языки; сравнение через транслитерацию: «Билясувар» = «Biləsuvar»). Совпадение засчитывается, если:
- кандидат один (или все кандидаты в пределах 3 км друг от друга);
- точка в своём регионе по справочнику ЕСР (запас 20 км);
- если на участке ТР №4 есть соседи с надёжными координатами (A/B/D) — расстояние по прямой до ближайшего не больше
  тарифного + 5 км и не меньше 0,6 тарифного − 5 км (класс B); соседей нет — класс C («по названию, без проверки по участку»);
- безликие названия («43 км», «Платформа 5», «Пост 12») — только с проверкой по участку.
Метро и городской рельсовый транспорт в выгрузку не попадают (scripts/fetch_sources.py).
"""
import re
import json

import numpy as np
import pandas as pd

from build_stations import RAW, haversine
from tr4_2026 import ANCHOR, REGION_SHAPES, _sec_frames, km_to_region
from verify_sources import CYR, CYR_LT, latin

REGION_TOL_KM = 20
GENERIC = re.compile(r"\d+\s*км|^\s*(платформа|пост|блок-?пост|о\.?\s?п\.?|рзд|разъезд|№)\s*[№\d]|^\s*\d+\s*$", re.I)
SAME_KM = 3


def name_keys(t):
    t = str(t)
    base = t.split("(")[0]  # «Рени (эксп.)» → «Рени»
    out = set()
    for x in {t, base}:
        for table in (CYR, CYR_LT):
            k = latin(x, table)
            if len(k) >= 3:
                out.add(k)
    return out


# азербайджанская латиница из русской записи: «Гаджигабул» → «qaciqabul» ≈ «Hacıqabul»
AZ = {**CYR, "г": "q", "х": "x", "я": "a", "ю": "u", "ы": "i"}


def az_key(t):
    t = str(t).split("(")[0].lower().replace("дж", "c").replace("кя", "ka").replace("гя", "ga")
    return latin(t, AZ)


FUZZY_MIN = 85  # нечёткое совпадение (второй проход) — только внутри региона и с подтверждением по участку


def load_index():
    p = RAW / "osm_stations_named.csv"
    if not p.exists():
        return None, {}
    o = pd.read_csv(p, sep=";", dtype={"osm_id": str, "esr": str}, keep_default_na=False)
    idx = {}
    for j, names in enumerate(o.names):
        for n in names.split("|"):
            for k in name_keys(n):
                idx.setdefault(k, set()).add(j)
    return o, idx


def fuzzy_candidates(o, keys, name, polys):
    """Похожие названия (≥ FUZZY_MIN) среди станций в рамке региона."""
    from rapidfuzz import fuzz
    xs = [x for p in polys for x, _ in p[0]]
    ys = [y for p in polys for _, y in p[0]]
    box = (o.lon >= min(xs) - 0.3) & (o.lon <= max(xs) + 0.3) & (o.lat >= min(ys) - 0.3) & (o.lat <= max(ys) + 0.3)
    mine = name_keys(name) | {az_key(name)}
    out = set()
    for j in np.flatnonzero(box.values):
        if any(fuzz.ratio(a, b) >= FUZZY_MIN for a in mine for b in keys[j]):
            out.add(j)
    return out


def section_anchor_check(sec_frames_by_code, e, la, lo):
    """None — нет соседей с координатами; True/False — согласуется ли точка с расстоянием до ближайшего соседа."""
    best = None
    for d, j in sec_frames_by_code.get(e, []):
        for k in range(len(d)):
            if k == j or d.coord_quality.iat[k] not in ANCHOR:
                continue
            dk = abs(d.km.iat[k] - d.km.iat[j])
            if best is None or dk < best[0]:
                best = (dk, d.lat.iat[k], d.lon.iat[k], d.sec.iat[j])
    if best is None:
        return None, None
    dist = haversine(la, lo, best[1], best[2])
    # по прямой — не больше тарифного расстояния и не сильно меньше (путь редко короче 0,6 прямой)
    return 0.6 * best[0] - 5 <= dist <= best[0] + 5, (best[3], best[0], dist)


def fill_by_osm_name(st, sec):
    o, idx = load_index()
    if o is None:
        return st, {"нет выгрузки": 0}
    keys = [set().union(*[name_keys(n) | {az_key(n)} for n in names.split("|")]) for names in o.names]
    shapes = json.loads(REGION_SHAPES.read_text(encoding="utf-8"))
    by_code = {}
    for d in _sec_frames(st, sec):
        for j, e in enumerate(d.esr):
            by_code.setdefault(e, []).append((d, j))
    cnt = {"B": 0, "C": 0, "нет кандидатов": 0, "вне региона": 0, "не согласуется с участком": 0, "неоднозначно": 0,
           "нет региона": 0}
    for i in st.index[st.coord_quality.eq("")]:
        name = st.at[i, "name"]
        cand = set()
        for k in name_keys(name):
            cand |= idx.get(k, set())
        polys = shapes.get(st.at[i, "region"])
        fuzzy = False
        if not cand and polys and not GENERIC.search(name):
            cand = fuzzy_candidates(o, keys, name, polys)
            fuzzy = bool(cand)
        if not cand:
            cnt["нет кандидатов"] += 1
            continue
        if not polys:
            cnt["нет региона"] += 1
            continue
        c = o.iloc[sorted(cand)]
        c = c[[km_to_region(a, b, polys) <= REGION_TOL_KM for a, b in zip(c.lat, c.lon)]]
        if not len(c):
            cnt["вне региона"] += 1
            continue
        if len(c) > 1:
            spread = haversine(c.lat.values[:, None], c.lon.values[:, None], c.lat.values[None], c.lon.values[None]).max()
            if spread > SAME_KM:
                ok = [r for r in c.itertuples() if section_anchor_check(by_code, st.at[i, "esr"], r.lat, r.lon)[0]]
                if len(ok) != 1:  # участок не помог выбрать единственного кандидата
                    cnt["неоднозначно"] += 1
                    continue
                c = c[c.osm_id == ok[0].osm_id]
        r = c.iloc[0]
        ok, info = section_anchor_check(by_code, st.at[i, "esr"], r.lat, r.lon)
        if ok is False:
            cnt["не согласуется с участком"] += 1
            continue
        if fuzzy and not ok:  # похожее, но не точное название — только с подтверждением по участку
            cnt["похожее название без участка"] = cnt.get("похожее название без участка", 0) + 1
            continue
        if ok is None and GENERIC.search(name):
            cnt["безликое название без участка"] = cnt.get("безликое название без участка", 0) + 1
            continue
        q = "B" if ok else "C"
        st.loc[i, ["lat", "lon"]] = round(float(r.lat), 6), round(float(r.lon), 6)
        st.at[i, "coord_quality"] = q
        st.at[i, "coord_precision_km"] = 0.5 if q == "B" else np.nan
        st.at[i, "coord_source"] = "OSM по названию"
        st.at[i, "coord_note"] = (f"станция OSM «{r.names.split('|')[0]}» ({r.osm_type}/{r.osm_id}) найдена по названию в своём регионе; "
                                  + (f"согласуется с участком {info[0]}: до соседа {info[2]:.1f} км по прямой при {info[1]:g} км по тарифу"
                                     .replace(".", ",") if ok else "соседей с координатами на участках ТР №4 нет — проверка только по региону"))
        st.at[i, "osm_fresh"] = f"{r.osm_type}/{r.osm_id}"
        st.at[i, "ext_check"] = "добавлено"
        cnt[q] += 1
    return st, cnt

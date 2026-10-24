#!/usr/bin/env python3
"""Сборка единого справочника ж/д станций ЕСР (пространство 1520) с координатами.

Вход:
  data/raw/esr.csv      — справочник ЕСР (названия, страна, регион, дорога, отделение, тип, Экспресс-код)
  data/raw/osm2esr.csv  — привязка кодов ЕСР к объектам OpenStreetMap (широта/долгота, много дублей)

Выход (data/output/):
  stations.csv          — единый справочник (все коды ЕСР + координаты + аналитика), UTF-8 BOM, ';'
  stations.xlsx         — тот же справочник + листы контроля качества и сводка
  stations.geojson      — точки для карты-дашборда (только записи с координатами)
  osm_candidates.csv    — все OSM-кандидаты с оценкой и отметкой выбранного (для ручной проверки)

Запуск: python3 scripts/build_stations.py
"""
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd
from rapidfuzz import fuzz
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "output"

EARTH_R = 6371.0
SAME_PLACE_KM = 5.0       # кандидаты ближе этого — одна и та же станция (парк, платформа, контур)
GEO_OK_KM = 50.0          # до ближайшего соседа по коду — правдоподобно
NAME_OK = 70.0            # порог похожести названий, 0..100
INTERP_MAX_GAP_KM = 60.0  # максимум между соседями для интерполяции
INTERP_MAX_CODE_GAP = 60  # максимум разницы 5-значных кодов до соседа

COUNTRY_BY_ISO = {
    "RU": "Россия", "UA": "Украина", "BY": "Беларусь", "KZ": "Казахстан", "UZ": "Узбекистан",
    "LV": "Латвия", "LT": "Литва", "EE": "Эстония", "GE": "Грузия", "AZ": "Азербайджан",
    "AM": "Армения", "MD": "Молдова", "KG": "Киргизия", "TJ": "Таджикистан", "TM": "Туркменистан",
}
COUNTRY_NORM = {
    "Российская Федерация": "Россия", "УКРАИНА": "Украина", "Республика Беларусь": "Беларусь",
    "Республика Казахстан": "Казахстан", "Республика Узбекистан": "Узбекистан",
    "Латвийская Республика": "Латвия", "Литовская Республика": "Литва",
    "Эстонская Республика": "Эстония", "Республика Грузия": "Грузия",
    "Азербайджанская Республика": "Азербайджан", "Республика Армения": "Армения",
    "Республика Молдова": "Молдова", "Республика Кыргызстан": "Киргизия",
    "Республика Таджикистан": "Таджикистан", "ТУРКМЕНИСТАН": "Туркменистан",
}
ISO_BY_COUNTRY = {v: k for k, v in COUNTRY_BY_ISO.items()}

QUALITY = {
    "A": "A — подтверждено",
    "B": "B — вероятно",
    "C": "C — сомнительно, проверить",
    "D": "D — по связанному коду / одноимённой станции",
    "E": "E — приблизительная оценка",
    "": "нет координат",
}


# ---------------------------------------------------------------- helpers

def haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_R * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def to_xyz(lat, lon):
    lat, lon = np.radians(lat), np.radians(lon)
    return np.c_[np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)]


def km_to_chord(km):
    return 2 * math.sin(km / EARTH_R / 2)


def esr_check_ok(code):
    """Контрольная цифра кода ЕСР: веса 1..5 mod 11, при 10 — веса 3..7, при повторном 10 — 0."""
    if not re.fullmatch(r"\d{6}", code):
        return False
    d = [int(c) for c in code[:5]]
    s = sum(x * w for x, w in zip(d, range(1, 6))) % 11
    if s == 10:
        s = sum(x * w for x, w in zip(d, range(3, 8))) % 11
        if s == 10:
            s = 0
    return s == int(code[5])


STOPWORDS = re.compile(
    r"\b(о\.?\s?п\.?|оп|ост\.?\s*пункт|остановочный\s+пункт|платформа|пл\.|ст\.|станция|"
    r"рзд\.?|разъезд|блок-?пост|бп|путевой\s+пост|пост|обгонный\s+пункт)\b"
)


def norm_name(s):
    s = (s or "").lower().replace("ё", "е")
    s = re.sub(r"\([^)]*\)?", " ", s)
    s = re.sub(r"километр\w*", "км", s)
    s = STOPWORDS.sub(" ", s)
    return re.sub(r"[^0-9a-zа-я]+", "", s)


def name_sim(ref_name, osm_names):
    """Похожесть названий 0..100; разные числа в названиях («158 км» / «138 км») — штраф."""
    a = norm_name(ref_name)
    best = 0.0
    for n in osm_names:
        for part in str(n).split(";"):
            b = norm_name(part)
            if not a or not b:
                continue
            r = fuzz.ratio(a, b)
            if min(len(a), len(b)) >= 5 and (a.startswith(b) or b.startswith(a)):
                r = max(r, 85.0)  # «Петрозаводск» / «Петрозаводск-Пассажирский»
            da, db = re.findall(r"\d+", a), re.findall(r"\d+", b)
            if da and db and da != db:
                r = min(r, 40.0)
            best = max(best, r)
    return best


def code_neighbors(codes_num, anchor_num, k):
    """Индексы k ближайших по номеру якорей (5-значный код, та же первая пара цифр), без самого кода."""
    order = np.argsort(anchor_num)
    srt = anchor_num[order]
    res = []
    for c in codes_num:
        pos = np.searchsorted(srt, c)
        lo, hi = max(0, pos - k - 1), min(len(srt), pos + k + 1)
        idx = [i for i in range(lo, hi) if srt[i] != c and srt[i] // 1000 == c // 1000]
        idx.sort(key=lambda i: abs(srt[i] - c))
        res.append(order[idx[:k]])
    return res


# ---------------------------------------------------------------- load

def load():
    ref = pd.read_csv(RAW / "esr.csv", sep=";", dtype=str, keep_default_na=False)
    osm = pd.read_csv(RAW / "osm2esr.csv", sep=";", dtype=str, keep_default_na=False)
    ref = ref.apply(lambda c: c.str.strip())
    osm = osm.apply(lambda c: c.str.strip())
    osm["lat"] = osm["lat"].astype(float)
    osm["lon"] = osm["lon"].astype(float)
    return ref, osm


def enrich_ref(ref):
    r = pd.DataFrame({"esr": ref["esr"]})
    r["esr5"] = ref["esr"].str[:5]
    r["esr_check_ok"] = ref["esr"].map(esr_check_ok)
    r["invalid_esr_src"] = ref["invalid_esr"] == "1"
    r["name"] = ref["name"]
    note = ref["name"].str.extract(r"\(([^)]*)\)?\s*$")[0].fillna("").str.strip()
    r["name_clean"] = ref["name"].str.replace(r"\s*\([^)]*\)?\s*$", "", regex=True).str.strip()
    r["name_note"] = note
    low = ref["name"].str.lower()
    nl = note.str.lower()
    r["is_km_name"] = r["name_clean"].str.contains(r"^\d+\s*км", regex=True)
    r["is_virtual"] = low.str.contains("условный код") | low.str.contains(r"\bузел$", regex=True)
    r["is_export"] = nl.str.startswith("эксп") | nl.str.startswith("экс")
    r["is_transship"] = nl.str.startswith("перев")
    r["is_narrow_gauge"] = nl.str.startswith("узк")
    r["is_foreign"] = nl.str.contains(
        r"польша|румыния|болгария|чехия|венгрия|финляндия|китай|словакия|германия|иран|монголия|афганистан",
        regex=True)
    r["type"] = ref["type"]
    r["express"] = ref["express"]
    r["has_express"] = ref["express"] != ""
    r["dup_esr"] = ref["dup_esr"]

    iso = ref["iso3166"]
    r["country_src"] = ref["country"]
    c_norm = ref["country"].map(COUNTRY_NORM).fillna("")
    c_iso = iso.str[:2].map(COUNTRY_BY_ISO).fillna("")
    r["country"] = c_norm.where(c_norm != "", c_iso)
    r["country_iso"] = r["country"].map(ISO_BY_COUNTRY).fillna("")
    r["country_iso_conflict"] = (c_norm != "") & (c_iso != "") & (c_norm != c_iso)
    r["region"] = ref["region"].str.replace(r"\s*\(неразобранное\)", "", regex=True)
    r.loc[r["region"].isin(["Беларусь", "Украина"]) & (r["country"] == ""), "country"] = r["region"]
    r["region_iso"] = iso
    r["railway"] = ref["railway"]
    r["division"] = ref["division"]
    r["source_file"] = ref["source"]
    return r


# ---------------------------------------------------------------- choose coordinates

def score_candidates(r, osm):
    ref_idx = r.set_index("esr")
    c = osm.copy()
    c["ref_name"] = c["esr"].map(ref_idx["name"])
    c["ref_type"] = c["esr"].map(ref_idx["type"])
    c["name_sim"] = [
        name_sim(rn, [n, a, o, f])
        for rn, n, a, o, f in zip(c["ref_name"], c["name"], c["alt_name"], c["old_name"], c["official_name"])
    ]
    exp = np.where(c["ref_type"] == "остановочный пункт", "halt",
                   np.where(c["ref_type"] == "", "", "station"))
    c["type_match"] = (exp == "") | (exp == c["railway"].values)
    c["esr_tag_match"] = [e in u.split("/") for e, u in zip(c["esr"], c["user"])]
    shared = c.groupby("osm_id")["esr"].nunique()
    c["osm_shared"] = c["osm_id"].map(shared) > 1

    # якоря: однозначные (одно место), подтверждённые, с совпадающим названием
    g = c.groupby("esr")
    spread = g.apply(lambda d: haversine(d.lat.values[:, None], d.lon.values[:, None],
                                         d.lat.values[None], d.lon.values[None]).max(),
                     include_groups=False)
    best_sim = g["name_sim"].max()
    any_st1 = g["status"].apply(lambda s: (s == "1").any())
    anchors = spread[(spread <= SAME_PLACE_KM) & (best_sim >= 85) & any_st1].index
    a = c[c.esr.isin(anchors)].sort_values("name_sim", ascending=False).drop_duplicates("esr")
    a_num = a["esr"].str[:5].astype(int).values
    a_lat, a_lon, a_esr = a["lat"].values, a["lon"].values, a["esr"].values

    codes = c["esr"].unique()
    nbrs = dict(zip(codes, code_neighbors(np.array([int(x[:5]) for x in codes]), a_num, 6)))
    d_min = []
    for e, la, lo in zip(c["esr"], c["lat"], c["lon"]):
        idx = [i for i in nbrs[e] if a_esr[i] != e]
        d_min.append(haversine(la, lo, a_lat[idx], a_lon[idx]).min() if idx else np.nan)
    c["nbr_dist_km"] = np.round(d_min, 1)
    geo_pen = np.clip((c["nbr_dist_km"].fillna(0) - 30) / 100, 0, 3)

    c["score"] = (2.0 * (c["status"] == "1") + 1.5 * c["esr_tag_match"] + 0.5 * (c["type"] == "0")
                  + 3.0 * c["name_sim"] / 100 + 0.5 * c["type_match"] - 1.5 * c["osm_shared"]
                  - 1.5 * geo_pen).round(3)
    return c, (a_num, a_lat, a_lon, a_esr)


def choose(c):
    rows, cand_flags = [], {}
    for esr, d in c.groupby("esr", sort=False):
        d = d.sort_values("score", ascending=False)
        best = d.iloc[0]
        dist = haversine(best.lat, best.lon, d.lat.values, d.lon.values)
        far = dist > SAME_PLACE_KM
        # число различных мест (жадная кластеризация)
        pts, places = list(zip(d.lat.values, d.lon.values)), []
        for p in pts:
            if not any(haversine(p[0], p[1], q[0], q[1]) <= SAME_PLACE_KM for q in places):
                places.append(p)
        margin = best.score - d.score.values[far].max() if far.any() else np.nan
        ok_status = best.status == "1"
        ok_name = best.name_sim >= NAME_OK
        ok_geo = np.isnan(best.nbr_dist_km) or best.nbr_dist_km <= GEO_OK_KM
        n_ok = int(ok_status) + int(ok_name) + int(ok_geo)
        if len(places) == 1:
            q = "A" if n_ok == 3 else "B" if n_ok == 2 else "C"
        else:
            q = "B" if (n_ok == 3 and margin >= 1.5) else "C"
        notes = []
        if not ok_status:
            notes.append("привязка OSM неподтверждённая (status=2)")
        if not ok_name:
            notes.append(f"название в OSM отличается ({best['name']})")
        if not ok_geo:
            notes.append(f"далеко от соседних кодов ({best.nbr_dist_km:.0f} км)")
        if len(places) > 1:
            notes.append(f"{len(places)} разных мест в OSM, разброс {dist.max():.0f} км")
        for i, (ix, dd) in enumerate(zip(d.index, dist)):
            cand_flags[ix] = (i == 0, round(float(dd), 2))
        rows.append(dict(
            esr=esr, lat=round(best.lat, 6), lon=round(best.lon, 6), coord_quality=q,
            coord_source="OSM", coord_precision_km=0.5 if q in "AB" else np.nan,
            coord_note="; ".join(notes),
            osm_id=best.osm_id, osm_type="way" if best.type == "1" else "node", osm_status=best.status, osm_railway=best.railway, osm_name=best["name"],
            osm_alt_name=best.alt_name, osm_old_name=best.old_name, osm_official_name=best.official_name,
            osm_name_sim=round(best.name_sim), nbr_dist_km=best.nbr_dist_km,
            osm_candidates=len(d), osm_places=len(places), osm_spread_km=round(float(dist.max()), 2),
            choice_margin=round(margin, 2) if not np.isnan(margin) else np.nan,
        ))
    c["chosen"] = [cand_flags[i][0] for i in c.index]
    c["dist_from_chosen_km"] = [cand_flags[i][1] for i in c.index]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- fill gaps

def fill_from_dup_links(st):
    """Коды-дубли (dup_esr) — одна и та же станция: берём координаты связанного кода."""
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            x = parent[x]
        return x

    for e, d in zip(st["esr"], st["dup_esr"]):
        if d and d != e:
            parent[find(e)] = find(d)
    st["_grp"] = st["esr"].map(find)
    rank = {"A": 0, "B": 1, "C": 2}
    good = st[st.coord_quality.isin(["A", "B"])].copy()
    good["_r"] = good.coord_quality.map(rank)
    donor = good.sort_values("_r").drop_duplicates("_grp").set_index("_grp")
    miss = st.coord_quality.eq("") & st._grp.isin(donor.index)
    for i in st.index[miss]:
        dn = donor.loc[st.at[i, "_grp"]]
        if dn.esr == st.at[i, "esr"]:
            continue
        st.loc[i, ["lat", "lon"]] = dn.lat, dn.lon
        st.loc[i, ["coord_quality", "coord_source", "coord_precision_km"]] = "D", f"основной код {dn.esr}", 3.0
        st.at[i, "coord_note"] = f"координаты связанного кода {dn.esr} ({dn['name']})"
    return st.drop(columns="_grp")


def fill_by_name(st, anchors):
    """Одноимённая станция с координатами рядом с соседями по коду (старый/новый код той же станции)."""
    a_num, a_lat, a_lon, a_esr = anchors
    have = st[st.coord_quality.isin(["A", "B"])]
    key = have["name_clean"].map(norm_name)
    by_name = {}
    for k, la, lo, e in zip(key, have.lat, have.lon, have.esr):
        if k:
            by_name.setdefault(k, []).append((la, lo, e))
    miss = st.index[st.coord_quality.eq("") & ~st.is_virtual]
    nbrs = code_neighbors(st.loc[miss, "esr"].str[:5].astype(int).values, a_num, 6)
    n = 0
    for i, nb in zip(miss, nbrs):
        k = norm_name(st.at[i, "name_clean"])
        if k not in by_name or len(nb) == 0:
            continue
        lim = 30.0 if st.at[i, "is_km_name"] else 80.0
        hits = []
        for la, lo, e in by_name[k]:
            dmin = haversine(la, lo, a_lat[nb], a_lon[nb]).min()
            if dmin <= lim:
                hits.append((la, lo, e, dmin))
        places = {(round(h[0], 2), round(h[1], 2)) for h in hits}
        if len(places) != 1:
            continue
        la, lo, e, dmin = hits[0]
        st.loc[i, ["lat", "lon"]] = la, lo
        st.loc[i, ["coord_quality", "coord_source", "coord_precision_km"]] = "D", f"одноимённая {e}", 2.0
        st.at[i, "coord_note"] = f"одноимённая станция {e} в {dmin:.0f} км от соседей по коду"
        n += 1
    return st, n


def fill_by_interpolation(st):
    """Промежуточные пункты: линейная интерполяция между ближайшими по коду соседями с обеих сторон."""
    have = st[st.coord_quality.isin(["A", "B", "D"])]
    num = have["esr"].str[:5].astype(int).values
    order = np.argsort(num)
    num, lat, lon = num[order], have.lat.values[order], have.lon.values[order]
    n = 0
    for i in st.index[st.coord_quality.eq("") & ~st.is_virtual & ~st.is_foreign]:
        c = int(st.at[i, "esr"][:5])
        p = np.searchsorted(num, c)
        if p == 0 or p >= len(num):
            continue
        lo_i, hi_i = p - 1, p
        if num[hi_i] == c:
            continue
        if num[lo_i] // 1000 != c // 1000 or num[hi_i] // 1000 != c // 1000:
            continue
        if c - num[lo_i] > INTERP_MAX_CODE_GAP or num[hi_i] - c > INTERP_MAX_CODE_GAP:
            continue
        gap = haversine(lat[lo_i], lon[lo_i], lat[hi_i], lon[hi_i])
        if gap > INTERP_MAX_GAP_KM:
            continue
        t = (c - num[lo_i]) / (num[hi_i] - num[lo_i])
        st.at[i, "lat"] = round(lat[lo_i] + t * (lat[hi_i] - lat[lo_i]), 6)
        st.at[i, "lon"] = round(lon[lo_i] + t * (lon[hi_i] - lon[lo_i]), 6)
        st.at[i, "coord_quality"] = "E"
        st.at[i, "coord_source"] = "интерполяция"
        st.at[i, "coord_precision_km"] = round(max(10.0, gap / 2), 1)
        st.at[i, "coord_note"] = (f"между кодами {num[lo_i]:05d}x и {num[hi_i]:05d}x "
                                  f"(расстояние между ними {gap:.0f} км)")
        n += 1
    return st, n


def validate_interpolation(st):
    """Проверка метода E на отложенной выборке: каждую точку A восстанавливаем по соседям и меряем ошибку."""
    a = st[st.coord_quality == "A"]
    num = a["esr"].str[:5].astype(int).values
    o = np.argsort(num)
    num, lat, lon = num[o], a.lat.values[o], a.lon.values[o]
    err, prec = [], []
    for j in range(1, len(num) - 1):
        c, lo, hi = num[j], j - 1, j + 1
        if num[lo] // 1000 != c // 1000 or num[hi] // 1000 != c // 1000 or num[lo] == c or num[hi] == c:
            continue
        if c - num[lo] > INTERP_MAX_CODE_GAP or num[hi] - c > INTERP_MAX_CODE_GAP:
            continue
        gap = haversine(lat[lo], lon[lo], lat[hi], lon[hi])
        if gap > INTERP_MAX_GAP_KM:
            continue
        t = (c - num[lo]) / (num[hi] - num[lo])
        err.append(haversine(lat[lo] + t * (lat[hi] - lat[lo]), lon[lo] + t * (lon[hi] - lon[lo]), lat[j], lon[j]))
        prec.append(max(10.0, gap / 2))
    err, prec = np.array(err), np.array(prec)
    return pd.DataFrame({"Показатель": [
        "Точек в проверке", "Медианная ошибка, км", "90-й перцентиль ошибки, км",
        "Доля в пределах заявленной точности, %", "Доля с ошибкой > 30 км, %"], "Значение": [
        len(err), round(float(np.median(err)), 1), round(float(np.percentile(err, 90)), 1),
        round(float((err <= prec).mean() * 100), 1), round(float((err > 30).mean() * 100), 1)]})


def flag_outliers(st):
    """Точки A/B/C/D, далеко ушедшие от своих соседей по коду и от станций своей страны."""
    have = st[st.coord_quality.isin(["A", "B", "C", "D"])]
    xyz = to_xyz(have.lat.values, have.lon.values)
    tree = cKDTree(xyz)
    _, idx = tree.query(xyz, k=11)
    ctry = have["country"].values
    bad = []
    for j, nb in enumerate(idx):
        others = [ctry[x] for x in nb[1:] if ctry[x]]
        if ctry[j] and len(others) >= 6 and others.count(ctry[j]) == 0:
            bad.append(have.index[j])
    st["geo_country_mismatch"] = st.index.isin(bad)
    return st


def infer_admin(st):
    """Заполнение пустых страны/региона/дороги/отделения по 7 ближайшим станциям (≤50 км, единогласно ≥4)."""
    have = st[st.coord_quality.isin(["A", "B", "C", "D", "E"])]
    filled = {i: [] for i in st.index}
    for col in ["country", "region", "railway", "division"]:
        known = have[have[col] != ""]
        if known.empty:
            continue
        ktree = cKDTree(to_xyz(known.lat.values, known.lon.values))
        vals = known[col].values
        tgt = have.index[have[col] == ""]
        if len(tgt) == 0:
            continue
        dist, idx = ktree.query(to_xyz(st.loc[tgt, "lat"].values, st.loc[tgt, "lon"].values),
                                k=7, distance_upper_bound=km_to_chord(50))
        for i, ds, ix in zip(tgt, dist, idx):
            v = [vals[x] for d, x in zip(ds, ix) if np.isfinite(d)]
            if len(v) >= 4 and len(set(v)) == 1:
                st.at[i, col] = v[0]
                filled[i].append(col)
    st["admin_inferred"] = [", ".join(filled[i]) for i in st.index]
    st["country_iso"] = st["country"].map(ISO_BY_COUNTRY).fillna("")
    return st


def infer_type(st):
    t = st["type"].copy()
    src = pd.Series("ЕСР", index=st.index)
    m = (t == "") & (st["tr4_point_type"] != "")
    t[m], src[m] = st.loc[m, "tr4_point_type"], "ТР №4"
    m = (t == "") & (st["osm_railway"] == "station")
    t[m], src[m] = "станция", "OSM"
    m = (t == "") & (st["osm_railway"] == "halt")
    t[m], src[m] = "остановочный пункт", "OSM"
    m = (t == "") & st["is_virtual"]
    t[m], src[m] = "условный пункт", "название"
    st["type"], st["type_source"] = t, src.where(t != "", "")
    return st


# ---------------------------------------------------------------- outputs

COLUMNS = [
    "esr", "esr5", "esr_check_ok", "invalid_esr_src", "is_new_tr4", "dup_esr", "esr_crimea_old", "esr_crimea_new",
    "express", "has_express", "express_name", "express_railway", "express_city",
    "name", "name_clean", "name_note", "type", "type_source",
    "is_km_name", "is_virtual", "is_export", "is_transship", "is_narrow_gauge", "is_foreign",
    "country", "country_iso", "country_src", "country_iso_conflict", "region", "region_iso",
    "railway", "railway_from_tr4", "division", "admin_inferred", "source_file",
    "in_tariff_list", "tr4_date", "tr4_excluded", "tr4_list", "tr4_sections", "tr4_port", "tr4_name", "tr4_former_name", "tr4_point_type", "tr4_owner",
    "tr4_pass_ops", "tr4_pass_ops_label", "tr4_passenger_open", "tr4_freight_paragraphs", "tr4_freight_open",
    "tr4_transit", "tr4_transit_nearest", "tr4_transit_nearest_km", "tr4_comment",
    "lat", "lon", "has_coords", "coord_quality", "coord_quality_label", "coord_source",
    "coord_precision_km", "coord_note", "geo_country_mismatch", "section_conflict",
    "transit_checks", "transit_violations", "transit_conflict",
    "osm_id", "osm_type", "osm_url", "osm_status", "osm_railway", "osm_name", "osm_alt_name", "osm_old_name",
    "osm_official_name", "osm_name_sim", "nbr_dist_km", "osm_candidates", "osm_places",
    "osm_spread_km", "choice_margin",
]

RU_HEADERS = {
    "esr": "Код ЕСР", "esr5": "Код ЕСР (5 зн.)", "esr_check_ok": "Контр. цифра верна",
    "invalid_esr_src": "Признак invalid_esr", "is_new_tr4": "Новый код (нет в ЕСР, есть в ТР №4 2026)", "dup_esr": "Основной код станции (dup_esr)",
    "esr_crimea_old": "Старый код (Крым, УЗ)", "esr_crimea_new": "Новый код (Крым)",
    "express": "Код Экспресс-3", "has_express": "Есть код Экспресс",
    "express_name": "Название в АСУ Экспресс", "express_railway": "Дорога (Экспресс)",
    "express_city": "Городской код Экспресс",
    "name": "Название", "name_clean": "Название (очищ.)", "name_note": "Пометка в названии",
    "type": "Тип пункта", "type_source": "Источник типа",
    "is_km_name": "Название-километр", "is_virtual": "Условный пункт/узел",
    "is_export": "Экспортная (эксп.)", "is_transship": "Перевалка", "is_narrow_gauge": "Узкая колея",
    "is_foreign": "Иностранная станция", "country": "Страна", "country_iso": "ISO страны",
    "country_src": "Страна (исходная)", "country_iso_conflict": "Страна ≠ ISO региона",
    "region": "Регион", "region_iso": "ISO 3166-2 региона", "railway": "Железная дорога",
    "railway_from_tr4": "Дорога взята из ТР №4", "division": "Отделение/РЖУ",
    "in_tariff_list": "Действует по ТР №4 на 21.10.2026", "tr4_date": "Дата ТР №4",
    "tr4_excluded": "Был в ТР №4 2023, исключён к 2026", "tr4_sections": "Тарифные участки (Книга 1)",
    "tr4_port": "Порт перевалки", "tr4_list": "Список ТР №4",
    "tr4_name": "Название (ТР №4)", "tr4_former_name": "Бывшее название (ТР №4)",
    "tr4_point_type": "Тип пункта (ТР №4)", "tr4_owner": "Владелец/дорога (ТР №4)",
    "tr4_pass_ops": "Пасс. операции (код)", "tr4_pass_ops_label": "Пасс. операции",
    "tr4_passenger_open": "Открыта для пассажиров", "tr4_freight_paragraphs": "Грузовые операции (параграфы)",
    "tr4_freight_open": "Открыта для грузовых операций", "tr4_transit": "Транзитные пункты (ТР №4)",
    "tr4_transit_nearest": "Ближайший транзитный пункт", "tr4_transit_nearest_km": "До транзитного пункта, км (тариф)",
    "tr4_comment": "Комментарий ТР №4", "admin_inferred": "Достроено по соседям", "source_file": "Источник (регион)",
    "lat": "Широта", "lon": "Долгота", "has_coords": "Есть координаты",
    "coord_quality": "Качество коорд.", "coord_quality_label": "Качество коорд. (описание)",
    "coord_source": "Источник коорд.", "coord_precision_km": "Точность, км", "coord_note": "Комментарий к коорд.",
    "geo_country_mismatch": "Точка среди станций другой страны",
    "section_conflict": "Противоречит расстояниям участка ТР №4",
    "transit_checks": "Проверок по транзитным пунктам", "transit_violations": "Нарушений тарифных расстояний",
    "transit_conflict": "Противоречит тарифным расстояниям",
    "osm_id": "OSM id", "osm_type": "OSM тип объекта", "osm_url": "Ссылка OSM", "osm_status": "OSM статус привязки",
    "osm_railway": "OSM railway", "osm_name": "OSM название", "osm_alt_name": "OSM alt_name",
    "osm_old_name": "OSM old_name", "osm_official_name": "OSM official_name",
    "osm_name_sim": "Похожесть названия, %", "nbr_dist_km": "До ближ. соседа по коду, км",
    "osm_candidates": "Кандидатов OSM", "osm_places": "Разных мест OSM",
    "osm_spread_km": "Разброс кандидатов, км", "choice_margin": "Отрыв лучшего кандидата",
}


def write_tr4_extras(st, tr):
    """Участки с координатами станций (для линий на карте) и города из Книги 2."""
    pos = st.set_index("esr")[["lat", "lon", "coord_quality"]]
    sec = tr["sections"].join(pos, on="esr")
    sec.to_csv(OUT / "tr4_sections.csv", sep=";", index=False, encoding="utf-8-sig")
    tr["cities"].to_csv(OUT / "tr4_cities.csv", sep=";", index=False, encoding="utf-8-sig")


def write_outputs(st, cand, interp_check):
    OUT.mkdir(parents=True, exist_ok=True)
    st = st[COLUMNS]
    st.to_csv(OUT / "stations.csv", sep=";", index=False, encoding="utf-8-sig")

    cand_cols = ["esr", "ref_name", "ref_type", "chosen", "score", "dist_from_chosen_km", "osm_id",
                 "status", "type", "railway", "name", "alt_name", "old_name", "official_name", "user",
                 "name_sim", "type_match", "esr_tag_match", "osm_shared", "nbr_dist_km", "lat", "lon"]
    cand = cand.sort_values(["esr", "score"], ascending=[True, False])[cand_cols]
    cand.to_csv(OUT / "osm_candidates.csv", sep=";", index=False, encoding="utf-8-sig")

    # GeoJSON — компактные свойства для карты
    import json
    pts = st[st.has_coords]
    feats = []
    for r in pts.itertuples(index=False):
        # пустые значения и False не пишем — файл заметно легче для браузера
        props = {k: v for k, v in zip(GEO_PROPS, (getattr(r, p) for p in GEO_PROPS))
                 if not (v is False or v == "" or v is None or (isinstance(v, float) and math.isnan(v)))}
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
                      "properties": props})
    with open(OUT / "stations.geojson", "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": feats}, f, ensure_ascii=False,
                  separators=(",", ":"), default=lambda o: o.item() if hasattr(o, "item") else str(o))

    summary = build_summary(st)
    summary["Проверка интерполяции"] = interp_check
    multi = set(cand.loc[cand.dist_from_chosen_km > SAME_PLACE_KM, "esr"])
    with pd.ExcelWriter(OUT / "stations.xlsx", engine="openpyxl") as xw:
        st.rename(columns=RU_HEADERS).to_excel(xw, sheet_name="Справочник", index=False)
        cand[cand.esr.isin(multi)].to_excel(xw, sheet_name="Дубли OSM (разные места)", index=False)
        st[st.coord_quality == "C"].rename(columns=RU_HEADERS).to_excel(xw, sheet_name="Проверить (C)", index=False)
        st[~st.has_coords].rename(columns=RU_HEADERS).to_excel(xw, sheet_name="Без координат", index=False)
        for name, df in summary.items():
            df.to_excel(xw, sheet_name=name, index=False)
        for ws in xw.book.worksheets:
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for col in ws.columns:
                width = max(len(str(c.value or "")) for c in list(col)[:300])
                ws.column_dimensions[col[0].column_letter].width = min(max(8, width + 2), 45)
    return summary


GEO_PROPS = ["esr", "name", "type", "country", "region", "railway", "division", "express",
             "in_tariff_list", "tr4_owner", "tr4_pass_ops", "tr4_freight_open", "transit_conflict",
             "coord_quality", "coord_source", "coord_precision_km", "osm_id",
             "is_virtual", "is_export", "is_transship", "is_narrow_gauge", "admin_inferred"]


def build_summary(st):
    q = st.groupby("coord_quality_label").size().rename("Кодов").reset_index()
    q.columns = ["Качество координат", "Кодов"]
    q["Доля, %"] = (q["Кодов"] / len(st) * 100).round(1)
    byc = st.assign(country=st.country.replace("", "(не указана)")).pivot_table(
        index="country", columns="coord_quality", values="esr", aggfunc="count", fill_value=0)
    byc = byc.rename(columns={"": "нет"})
    byc["Всего"] = byc.sum(axis=1)
    byc["С координатами, %"] = ((byc["Всего"] - byc.get("нет", 0)) / byc["Всего"] * 100).round(1)
    byc = byc.sort_values("Всего", ascending=False).reset_index().rename(columns={"country": "Страна"})
    byt = st.assign(type=st.type.replace("", "(не указан)")).groupby("type").agg(
        Кодов=("esr", "size"), С_координатами=("has_coords", "sum")).reset_index().rename(
        columns={"type": "Тип пункта"}).sort_values("Кодов", ascending=False)
    byr = st.assign(railway=st.railway.replace("", "(не указана)")).groupby("railway").agg(
        Кодов=("esr", "size"), С_координатами=("has_coords", "sum")).reset_index().rename(
        columns={"railway": "Железная дорога"}).sort_values("Кодов", ascending=False)
    ops = st[st.in_tariff_list].groupby(["tr4_pass_ops_label", "tr4_freight_open"]).size().rename(
        "Кодов").reset_index().rename(columns={"tr4_pass_ops_label": "Пассажирские операции",
                                               "tr4_freight_open": "Грузовые операции"})
    legend = pd.DataFrame({"Качество": list(QUALITY.values()), "Как получено": [
        "одна точка OSM, привязка подтверждена, название совпадает, рядом с соседними кодами",
        "выполнены 2 из 3 проверок или выбрано из нескольких мест с уверенным отрывом",
        "неподтверждённая привязка / другое название / далеко от соседей / неоднозначные дубли",
        "координаты кода-дубля (dup_esr) или одноимённой станции рядом с соседями по коду",
        "положение на тарифном участке между соседями (Книга 1 ТР №4) или интерполяция по соседним кодам",
        "нет данных",
    ]})
    return {"Сводка по качеству": q, "Операции ТР4": ops, "По странам": byc, "По типам": byt, "По дорогам": byr,
            "Легенда": legend}


# ---------------------------------------------------------------- main

def main():
    import extra_sources as xs
    import tr4_2026 as t26

    ref, osm = load()
    tr = t26.load()
    new_rows = t26.new_ref_rows(ref, tr)
    ref = pd.concat([ref, new_rows], ignore_index=True)
    st = enrich_ref(ref)
    st["is_new_tr4"] = st["source_file"] == "tr4_2026"
    cand, anchors = score_candidates(st, osm)
    chosen = choose(cand)
    st = st.merge(chosen, on="esr", how="left")
    st["coord_quality"] = st["coord_quality"].fillna("")
    st["coord_source"] = st["coord_source"].fillna("")
    st["coord_note"] = st["coord_note"].fillna("")
    for col in ["osm_id", "osm_type", "osm_status", "osm_railway", "osm_name", "osm_alt_name", "osm_old_name",
                "osm_official_name"]:
        st[col] = st[col].fillna("")

    sec = tr["sections"]
    st = fill_from_dup_links(st)
    st, n_crimea = xs.merge_crimea(st)
    st, n_sec_bad = t26.check_sections(st, sec)
    st, n_recoded = t26.fill_by_recoded_name(st)
    n_sec_name = 0
    for _ in range(6):  # перекодированные участки целиком: каждый проход даёт новые якоря соседям
        st, n = t26.fill_by_section_name(st, sec)
        n_sec_name += n
        if not n:
            break
    st, n_name = fill_by_name(st, anchors)
    st, sec_cnt = t26.apply_sections(st, sec)
    st, _ = t26.check_sections(st, sec)  # повторно — с учётом новых якорей
    st, n_interp = fill_by_interpolation(st)

    st = xs.merge_tariff(st, xs.load_tariff_all(tr))
    st["has_coords_tmp"] = st["coord_quality"] != ""
    st = xs.check_transit(st)
    # оценки E, противоречащие тарифным расстояниям до всех транзитных пунктов, снимаем
    drop = (st.coord_quality == "E") & st.transit_conflict
    st.loc[drop, ["lat", "lon", "coord_precision_km"]] = np.nan
    st.loc[drop, ["coord_quality", "coord_source", "coord_note"]] = ""
    st, n_transit = xs.fill_by_transit(st)
    # узлы и крайние станции участков — только оставшиеся без координат (прежние оценки не трогаем)
    st, n_end, n_end_rej = t26.fill_by_section_end(st, sec)
    st["has_coords_tmp"] = st["coord_quality"] != ""
    st = xs.check_transit(st)
    st = xs.merge_express(st)
    st = flag_outliers(st)
    st = infer_admin(st)
    st = infer_type(st)

    st["has_coords"] = st["coord_quality"] != ""
    for col in ["osm_candidates", "osm_places"]:
        st[col] = st[col].astype("Int64")
    st["coord_quality_label"] = st["coord_quality"].map(QUALITY)
    st["osm_url"] = np.where(st.osm_id != "", "https://www.openstreetmap.org/" + st.osm_type + "/" + st.osm_id, "")
    sec_text = t26.station_sections(sec)
    st["tr4_sections"] = st["esr"].map(sec_text).fillna("")
    st["tr4_port"] = st["esr"].map(tr["ports"]).fillna("")
    interp_check = pd.concat([
        validate_interpolation(st).assign(Метод="по соседним кодам ЕСР"),
        t26.validate_sections(st, sec).assign(Метод="по участкам ТР №4 (Книга 1)"),
        t26.validate_section_end(st, sec).assign(Метод="край участка ТР №4 (узлы, до 3 км от соседа)"),
        xs.validate_transit_pair(st).assign(Метод="между двумя транзитными пунктами ТР №4")], ignore_index=True)
    summary = write_outputs(st, cand, interp_check)
    write_tr4_extras(st, tr)

    print(f"Кодов ЕСР: {len(st)}; строк OSM: {len(osm)} ({osm.esr.nunique()} кодов)")
    print(summary["Сводка по качеству"].to_string(index=False))
    print(interp_check.to_string(index=False))
    print(f"новых кодов из ТР №4 2026: {len(new_rows)}, из них с координатами: "
          f"{(st.is_new_tr4 & (st.coord_quality != '')).sum()}")
    print(f"новые коды по старой нумерации: {n_recoded}; участки: понижено до C {n_sec_bad}, одноимённые по участку {n_sec_name}, {sec_cnt}, край участка {n_end} (отклонено вне своего региона или без региона: {n_end_rej})")
    print(f"Крым (старый↔новый код): {n_crimea}, по одноимённым: {n_name}, интерполяцией: {n_interp}, "
          f"снято E по тарифу: {drop.sum()}, по транзитному пункту: {n_transit}")
    print(f"в тарифном списке ТР №4: {st.in_tariff_list.sum()}; противоречат тарифу: "
          + st[st.transit_conflict].groupby("coord_quality").size().to_dict().__repr__())
    print(f"точек среди станций другой страны: {st.geo_country_mismatch.sum()}")
    print(f"атрибуты достроены по соседям: {(st.admin_inferred != '').sum()}")


if __name__ == "__main__":
    main()

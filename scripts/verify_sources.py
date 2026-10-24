"""Сверка координат справочника со свежими внешними источниками (scripts/fetch_sources.py):
OpenStreetMap (тег esr:user, выгрузка Overpass) и Викиданные (код ЕСР P2815).

Для каждого кода:
- нет координат: точка из источника, если название совпадает (OSM) или оба источника согласны;
  оба источника согласны — класс A, один — B;
- есть координаты и хотя бы один источник рядом (в пределах max(3 км, заявленной точности)) — «подтверждено»;
  оценки C и E при этом уточняются точкой источника и становятся B;
- точка далеко, а оба источника согласны между собой — «заменено» (большинство), класс B;
  оценка E далеко от одного источника с совпадающим названием — тоже «заменено»;
- остальные расхождения больше 5 км — «расхождение»: точка не меняется, код попадает в список для ручной проверки.
Новые и заменённые точки должны попасть в свой регион по справочнику ЕСР (запас 20 км); без региона — только
при согласии обоих источников.
"""
import json
import re

import numpy as np
import pandas as pd

from rapidfuzz import fuzz

from build_stations import OUT, RAW, haversine, name_sim
from tr4_2026 import REGION_SHAPES, km_to_region

AGREE_KM = 3     # источники согласны между собой
CONFIRM_KM = 3   # наша точка подтверждена источником
FAR_KM = 5       # расхождение
NAME_OK = 70
REGION_TOL_KM = 20


CYR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
               ["a", "b", "v", "g", "d", "e", "e", "z", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u",
                "f", "h", "c", "c", "s", "sc", "", "y", "", "e", "u", "a"]))
# я/ю: в эстонских названиях — «ä/ü» (Пяэскюла = Pääsküla), в литовских — «ia/iu» (Чютеляй = Čiuteliai)
CYR_LT = {**CYR, "я": "ia", "ю": "iu"}
LAT = str.maketrans("äöõüčšžėęųūįąłńóśźżāēīņļķģ", "aooucszeeuuialnoszzaeinlkg")
SUFFIX = re.compile(r"\b(train|railway)?\s*station\b|\b(stotis|jaam|stacija|peatus|stancija|stanc)\b", re.I)


def latin(t, table=CYR):
    """Грубая латиница для сравнения названий: «Пяэскюла» ≈ «Pääsküla», «Сидаряй» ≈ «Sidariai»."""
    t = SUFFIX.sub(" ", str(t).lower()).translate(LAT)
    return re.sub(r"[^a-z0-9]", "", "".join(table.get(ch, ch) for ch in t))


def name_ok(ref, cand):
    if not cand:
        return False
    if name_sim(ref, [cand]) >= NAME_OK:
        return True
    b = latin(cand)
    return bool(b) and max(fuzz.ratio(latin(ref, t), b) for t in (CYR, CYR_LT)) >= 75


def load_ext():
    osm = pd.read_csv(RAW / "osm_esr_fresh.csv", sep=";", dtype={"esr": str, "osm_id": str}, keep_default_na=False)
    wd = pd.read_csv(RAW / "wikidata_esr.csv", sep=";", dtype={"esr": str}, keep_default_na=False)
    meta = json.loads((RAW / "sources_meta.json").read_text(encoding="utf-8"))
    return ({e: d for e, d in osm.groupby("esr")}, {e: d for e, d in wd.groupby("esr")}, meta)


def pick(d, ref):
    """Объект источника для кода: ближайший к ref; без ref — если все объекты в одном месте (≤ 5 км)."""
    if d is None or not len(d):
        return None
    if ref is not None:
        k = int(np.argmin(haversine(ref[0], ref[1], d.lat.values, d.lon.values)))
        return d.iloc[k]
    spread = haversine(d.lat.values[:, None], d.lon.values[:, None], d.lat.values[None], d.lon.values[None]).max()
    if spread > FAR_KM:
        return None
    st = d[d.get("railway", pd.Series("", index=d.index)).isin(["station", "halt"])] if "railway" in d else d
    return (st if len(st) else d).iloc[0]


def verify_external(st):
    osm, wd, meta = load_ext()
    shapes = json.loads(REGION_SHAPES.read_text(encoding="utf-8"))
    for c in ["ext_check", "ext_osm_km", "ext_wd_km", "wikidata", "osm_fresh"]:
        st[c] = pd.Series("", index=st.index, dtype=object)
    cnt = {}
    for i in st.index:
        e = st.at[i, "esr"]
        if e not in osm and e not in wd:
            continue
        q = st.at[i, "coord_quality"]
        has = q != ""
        ref = (st.at[i, "lat"], st.at[i, "lon"]) if has else None
        o, w = pick(osm.get(e), ref), pick(wd.get(e), ref)
        if not has:  # без своей точки — выбираем объекты относительно друг друга
            if o is None and e in osm and w is not None:
                o = pick(osm[e], (w.lat, w.lon))
            if w is None and e in wd and o is not None:
                w = pick(wd[e], (o.lat, o.lon))
        name = st.at[i, "name"]
        o_ok = o is not None and name_ok(name, o["name"])
        w_ok = w is not None and name_ok(name, w["label"])
        agree = o is not None and w is not None and haversine(o.lat, o.lon, w.lat, w.lon) <= AGREE_KM
        if w is not None:
            st.at[i, "wikidata"] = w["qid"]
        if o is not None:
            st.at[i, "osm_fresh"] = f"{o.osm_type}/{o.osm_id}"

        def in_region(p):
            polys = shapes.get(st.at[i, "region"])
            return km_to_region(p.lat, p.lon, polys) <= REGION_TOL_KM if polys else agree

        def put(p, quality, check, note):
            st.loc[i, ["lat", "lon"]] = round(float(p.lat), 6), round(float(p.lon), 6)
            st.at[i, "coord_quality"] = quality
            st.at[i, "coord_precision_km"] = 0.5
            src = ("OSM " + meta["osm_base"]) if p is o else "Викиданные"
            st.at[i, "coord_source"] = src + (" + Викиданные" if agree and p is o else "")
            st.at[i, "coord_note"] = note
            st.at[i, "ext_check"] = check

        if not has:
            p = o if (o is not None and (o_ok or agree)) else w if (w is not None and (w_ok or agree)) else None
            if p is not None and in_region(p):
                put(p, "A" if agree else "B", "добавлено",
                    "координаты из " + ("свежего OSM и Викиданных (согласны)" if agree else
                                        "свежего OSM" if p is o else "Викиданных") + f" на {meta['fetched']}")
            elif p is not None or o is not None or w is not None:
                st.at[i, "ext_check"] = "расхождение"
        else:
            prec = float(st.at[i, "coord_precision_km"] or 0) if str(st.at[i, "coord_precision_km"]) not in ("", "nan") else 0
            tol = max(CONFIRM_KM, prec)
            d_o = haversine(ref[0], ref[1], o.lat, o.lon) if o is not None else None
            d_w = haversine(ref[0], ref[1], w.lat, w.lon) if w is not None else None
            st.at[i, "ext_osm_km"] = "" if d_o is None else round(float(d_o), 2)
            st.at[i, "ext_wd_km"] = "" if d_w is None else round(float(d_w), 2)
            near = [p for p, dd in ((o, d_o), (w, d_w)) if dd is not None and dd <= tol]
            far = [dd for dd in (d_o, d_w) if dd is not None and dd > FAR_KM]
            if near:
                st.at[i, "ext_check"] = "подтверждено"
                if q in ("C", "E"):
                    p = o if o is not None and d_o <= tol and (o_ok or agree) else w if w is not None and d_w <= tol else None
                    if p is not None:
                        put(p, "B", "уточнено", f"прежняя оценка {q} подтверждена "
                            + ("свежим OSM и Викиданными" if len(near) == 2 else "свежим OSM" if p is o else "Викиданными")
                            + f" ({min(x for x in (d_o, d_w) if x is not None):.1f} км); точка уточнена".replace(".", ","))
            elif far:
                single = o if o is not None and o_ok else w if w is not None and w_ok else None
                if agree and in_region(o):
                    put(o, "B", "заменено", f"прежняя точка ({q}) в {min(far):.0f} км от свежего OSM и Викиданных, "
                        "которые согласны между собой")
                elif q == "E" and single is not None and in_region(single):
                    put(single, "B", "заменено", f"оценка E в {min(far):.0f} км от точки "
                        + ("свежего OSM" if single is o else "Викиданных") + " с тем же названием")
                else:
                    st.at[i, "ext_check"] = "расхождение"
        cnt[st.at[i, "ext_check"]] = cnt.get(st.at[i, "ext_check"], 0) + 1
    return st, cnt, meta


def verification_stats(before, st, meta):
    """Сводка для окна «О данных» и листа отчёта: сверка исходных классов с внешними источниками."""
    b = before.set_index("esr")
    rows = []
    for q in ["A", "B", "C", "D", "E"]:
        s = st[(st.esr.map(b.coord_quality) == q)]
        d = pd.concat([pd.to_numeric(s.ext_osm_km, errors="coerce"), pd.to_numeric(s.ext_wd_km, errors="coerce")], axis=1).min(axis=1)
        chk = s[d.notna()]
        rows.append({"class": q, "checked": int(len(chk)), "median_km": round(float(d.median()), 2) if d.notna().any() else None,
                     "confirmed": int((s.ext_check.isin(["подтверждено", "уточнено"])).sum()),
                     "replaced": int((s.ext_check == "заменено").sum()), "conflict": int((s.ext_check == "расхождение").sum())})
    return {"date": meta["fetched"], "osm_base": meta["osm_base"], "osm_objects": meta["osm_objects"],
            "wikidata_items": meta["wikidata_items"], "classes": rows,
            "added": int((st.ext_check == "добавлено").sum()),
            "confirmed": int(st.ext_check.isin(["подтверждено", "уточнено"]).sum()),
            "refined": int((st.ext_check == "уточнено").sum()), "replaced": int((st.ext_check == "заменено").sum()),
            "conflict": int((st.ext_check == "расхождение").sum()),
            "no_coords_before": int((before.coord_quality == "").sum()), "no_coords_after": int((st.coord_quality == "").sum())}


def write_review(st):
    """Список для ручной проверки: расхождения больше 5 км, которые сверка не решила."""
    osm, wd, _ = load_ext()
    r = st[st.ext_check == "расхождение"].copy()
    out = []
    for x in r.itertuples():
        o = osm.get(x.esr)
        w = wd.get(x.esr)
        out.append({"Код ЕСР": x.esr, "Станция": x.name, "Регион": x.region, "Дорога": x.railway, "Класс": x.coord_quality,
                    "Широта (справочник)": x.lat, "Долгота (справочник)": x.lon,
                    "OSM, км от точки": x.ext_osm_km, "Викиданные, км от точки": x.ext_wd_km,
                    "OSM: объекты": " | ".join(f"{a.name} {a.lat:.5f},{a.lon:.5f} {a.osm_type}/{a.osm_id}" for a in o.itertuples()) if o is not None else "",
                    "Викиданные": " | ".join(f"{a.label} {a.lat:.5f},{a.lon:.5f} {a.qid}" for a in w.itertuples()) if w is not None else ""})
    pd.DataFrame(out).to_csv(OUT / "review_coordinates.csv", sep=";", index=False, encoding="utf-8-sig")
    return len(out)

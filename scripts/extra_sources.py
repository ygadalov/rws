"""Дополнительные источники для справочника станций.

  tr4_stations_rf.xls / tr4_stations_sng.xls — открытые данные «Тарифное руководство №4,
      книга 2, ч. 1. Алфавитный список железнодорожных станций» (по состоянию на 30.12.2023):
      коммерческие операции, владелец инфраструктуры, транзитные пункты с тарифными расстояниями
  express.csv — справочник АСУ «Экспресс» (официальные названия, сокращение дороги, городские коды)
  crimea.csv  — перекодировка станций Крыма: старый код (УЗ) → новый код (РЖД/КЖД)
"""
import re

import numpy as np
import pandas as pd

from build_stations import RAW, haversine, norm_name

TR4_FILES = {"tr4_stations_rf.xls": "РФ", "tr4_stations_sng.xls": "СНГ и Балтия"}

TR4_POINT_TYPES = {
    "оп.": "остановочный пункт", "пп.": "путевой пост", "бп.": "блок-пост",
    "обп.": "обгонный пункт", "обгп": "обгонный пункт", "рзд.": "разъезд",
}

# Расшифровка подтверждена текстом ТР №4 (кн. 2, ч. 1, табл. 1)
PASS_OPS = {
    "П": "продажа билетов на все поезда, приём и выдача багажа",
    "Б": "продажа билетов на все поезда, без багажа",
    "О": "посадка/высадка на пригородные и местные поезда, без багажа",
    "Х": "грузовые и коммерческие операции не производятся",
    "К": "приём и выдача контрейлерных отправок",
}

# названия дорог ТР №4 → как в справочнике ЕСР
TR4_RAILWAY_NORM = {
    "Латвийска": "Латвийская", "Казахстанская": "Казахстанские", "Узбекская": "Узбекские",
    'ЗАО "Южно-Кавказская железная дорога"': "Армянская", 'ФГУП "КЖД"': "Крымская",
}

TRANSIT_SLACK = 1.1     # прямая не длиннее тарифного расстояния × 1.1 + 10 км
TRANSIT_ADD_KM = 10.0
TRANSIT_FILL_MAX_KM = 15


# ---------------------------------------------------------------- ТР №4

def load_tariff():
    parts = []
    for f, label in TR4_FILES.items():
        d = pd.read_excel(RAW / f, dtype=str).fillna("")
        d.columns = ["name", "ops", "owner", "esr", "transit", "comment"]
        parts.append(d.assign(tr4_list=label))
    x = pd.concat(parts, ignore_index=True).apply(lambda c: c.str.strip())
    x = x[x.esr.str.fullmatch(r"\d{6}")].drop_duplicates("esr")

    note = x["name"].str.extract(r"\(([^()]*)\)\s*$")[0].fillna("").str.strip().str.lower()
    x["point_type"] = note.map(TR4_POINT_TYPES).fillna("станция")
    former = x["name"].str.extract(r"\(\s*(?:б\.|бывш\.?)\s*([^()]*)\)")[0].fillna("").str.strip(" .")
    x["former_name"] = former.where(former.str.len() >= 3, "")
    ops = x["ops"].str.extract(r"^\s*([ПБОХК])?\s*([0-9НнH,\s]*)$")
    x["pass_ops"] = ops[0].fillna("")
    x["freight_paragraphs"] = ops[1].fillna("").str.replace(r"\s+", "", regex=True).str.upper()
    x["transit_list"] = x["transit"].map(parse_transit)
    x["transit_resolved"] = resolve_transit(x)
    return x


def load_tariff_2026(tr):
    """Книга 2 ТР №4 на 21.10.2026 в том же виде, что и load_tariff (2023)."""
    x = tr["stations"].copy()
    x["owner"] = x["railway"]
    x["comment"] = ""
    x["tr4_list"] = x["list"].map({"РП": "РП", "ОП": "ОП"})
    former = x["name"].str.extract(r"\(\s*(?:б\.|бывш\.?)\s*([^()]*)\)")[0].fillna("").str.strip(" .")
    x["former_name"] = former.where(former.str.len() >= 3, "")
    ops = x["ops"].str.extract(r"^\s*([ПБОХК])?\s*([0-9НнH,\s]*)$")
    x["pass_ops"] = ops[0].fillna("")
    x["freight_paragraphs"] = ops[1].fillna("").str.replace(r"\s+", "", regex=True).str.upper().str.strip(",")
    # «254905 Агрыз - 25км, 647523 Акбаш - 276км» — код транзитного пункта уже указан
    tl = x["transit"].map(lambda t: [(m.group(1), m.group(2).strip(), int(m.group(3)))
                                     for m in re.finditer(r"(\d{6})\s+(.*?)\s*-\s*(\d+)\s*км", t or "")])
    x["transit_resolved"] = tl
    x["transit_list"] = tl.map(lambda l: [(n, km) for _, n, km in l])
    x["transit"] = tl.map(lambda l: ", ".join(f"{n}-{km}" for _, n, km in l))
    x["tr4_date"] = "21.10.2026"
    return x


def load_tariff_all(tr):
    """Действующий список (2026) + станции, которые были в ТР №4 на 30.12.2023, но исключены."""
    new, old = load_tariff_2026(tr), load_tariff().assign(tr4_date="30.12.2023")
    new["comment"] = new["esr"].map(old.set_index("esr")["comment"]).fillna("")
    cols = ["esr", "name", "owner", "transit", "comment", "tr4_list", "point_type", "former_name", "pass_ops",
            "freight_paragraphs", "transit_list", "transit_resolved", "tr4_date"]
    return pd.concat([new[cols], old.loc[~old.esr.isin(new.esr), cols]], ignore_index=True)


def parse_transit(s):
    res = []
    for part in re.split(r",\s*", s or ""):
        m = re.match(r"^(.*\S)\s*-\s*(\d+)\s*$", part.strip())
        if m:
            res.append((m.group(1), int(m.group(2))))
    return res


def resolve_transit(x):
    """Название транзитного пункта → код ЕСР по самому списку ТР №4 (при неоднозначности — та же дорога)."""
    exact, loose = {}, {}
    for n, e, o in zip(x["name"], x["esr"], x["owner"]):
        exact.setdefault(n.lower(), []).append((e, o))
        if not re.search(r"\([^()]*\)\s*$", n):  # «Лена», а не «Лена (перев.)»
            loose.setdefault(norm_name(n), []).append((e, o))
    out = []
    for lst, own in zip(x["transit_list"], x["owner"]):
        res = []
        for name, km in lst:
            c = exact.get(name.lower()) or loose.get(norm_name(name), [])
            if len(c) > 1:
                c = [t for t in c if t[1] == own]
            res.append((c[0][0] if len(c) == 1 else "", name, km))
        out.append(res)
    return out


def merge_tariff(st, x):
    x = x.set_index("esr")
    m = st["esr"].map
    st["tr4_date"] = m(x["tr4_date"]).fillna("") if "tr4_date" in x else ""
    st["in_tariff_list"] = st["tr4_date"] == "21.10.2026" if "tr4_date" in x else st["esr"].isin(x.index)
    st["tr4_excluded"] = st["tr4_date"] == "30.12.2023"
    st["tr4_list"] = m(x["tr4_list"]).fillna("")
    st["tr4_name"] = m(x["name"]).fillna("")
    st["tr4_point_type"] = m(x["point_type"]).fillna("")
    st["tr4_former_name"] = m(x["former_name"]).fillna("")
    st["tr4_owner"] = m(x["owner"]).fillna("")
    st["tr4_pass_ops"] = m(x["pass_ops"]).fillna("")
    st["tr4_pass_ops_label"] = st["tr4_pass_ops"].map(PASS_OPS).fillna("")
    st["tr4_freight_paragraphs"] = m(x["freight_paragraphs"]).fillna("")
    st["tr4_freight_open"] = st["tr4_freight_paragraphs"] != ""
    st["tr4_passenger_open"] = st["tr4_pass_ops"].isin(["П", "Б", "О"])
    st["tr4_transit"] = m(x["transit"]).fillna("")
    st["tr4_comment"] = m(x["comment"]).fillna("")
    near = x["transit_list"].map(lambda l: min(l, key=lambda t: t[1]) if l else ("", np.nan))
    st["tr4_transit_nearest"] = m(near.map(lambda t: t[0])).fillna("")
    st["tr4_transit_nearest_km"] = m(near.map(lambda t: t[1])).astype("Int64")
    st["_transit"] = m(x["transit_resolved"]).map(lambda v: v if isinstance(v, list) else [])

    # пустая дорога в ЕСР — берём владельца из ТР №4
    own = st["tr4_owner"].replace(TR4_RAILWAY_NORM)
    fill = (st["railway"] == "") & (own != "")
    st.loc[fill, "railway"] = own[fill]
    st["railway_from_tr4"] = fill
    return st


def validate_transit_pair(st):
    """Проверка на станциях класса A: скрываем точку и восстанавливаем по двум транзитным пунктам."""
    good = st[st.coord_quality.isin(["A", "B", "D"])].set_index("esr")[["lat", "lon", "name"]]
    errs, precs = [], []
    a = st[st.coord_quality == "A"]
    for e, lat, lon, tr in zip(a.esr, a.lat, a.lon, a._transit):
        pts = [p for p in tr if p[0] in good.index and p[0] != e]
        r = transit_pair_estimate(good, pts) if len(pts) > 1 else None
        if r:
            errs.append(haversine(r[0], r[1], lat, lon))
            precs.append(r[2])
    e, p = np.array(errs), np.array(precs)
    return pd.DataFrame({"Показатель": [
        "Точек в проверке", "Медианная ошибка, км", "90-й перцентиль ошибки, км",
        "Доля в пределах заявленной точности, %", "Доля с ошибкой > 10 км, %"], "Значение": [
        len(e), round(float(np.median(e)), 2), round(float(np.percentile(e, 90)), 1),
        round(float((e <= p).mean() * 100), 1), round(float((e > 10).mean() * 100), 1)]})


def check_transit(st):
    """Сверка координат с тарифными расстояниями до транзитных пунктов."""
    good = st[st.coord_quality.isin(["A", "B", "D"])].set_index("esr")[["lat", "lon"]]
    n_chk, n_bad = [], []
    for lat, lon, has, tr in zip(st.lat, st.lon, st.has_coords_tmp, st._transit):
        # 0 км — пункт входит в узел, с прямой не сравнивается
        pts = [(good.at[e, "lat"], good.at[e, "lon"], km) for e, _, km in tr if e in good.index and km > 0]
        if not has or not pts:
            n_chk.append(0)
            n_bad.append(0)
            continue
        la, lo, km = map(np.array, zip(*pts))
        d = haversine(lat, lon, la, lo)
        n_chk.append(len(pts))
        n_bad.append(int((d > km * TRANSIT_SLACK + TRANSIT_ADD_KM).sum()))
    st["transit_checks"] = n_chk
    st["transit_violations"] = n_bad
    # в самом ТР №4 встречаются ошибки в транзитных пунктах, поэтому одного расхождения мало
    st["transit_conflict"] = (st.transit_checks >= 2) & (st.transit_violations == st.transit_checks)
    return st


PAIR_MAX_KM = 120


def transit_pair_estimate(good, pts):
    """Станция на пути между двумя транзитными пунктами: ka + kb ≈ путь между ними.
    Ставим на отрезок пропорционально ka / (ka + kb). Возвращает (lat, lon, точность, a, b, ka, kb)."""
    best = None
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            (ea, na, ka), (eb, nb, kb) = pts[i], pts[j]
            if ka <= 0 or kb <= 0:
                continue
            la, loa, lb, lob = good.at[ea, "lat"], good.at[ea, "lon"], good.at[eb, "lat"], good.at[eb, "lon"]
            d, L = haversine(la, loa, lb, lob), ka + kb
            # пункты должны лежать «по разные стороны»: путь через станцию не короче прямой и не сильно длиннее
            if d < 1 or d > L * 1.05 + 2 or L > d * 1.6 + 10 or L > PAIR_MAX_KM:
                continue
            if best is None or L < best[0]:
                t = ka / L
                semi = np.sqrt(max(0.0, (L / 2) ** 2 - (d / 2) ** 2))  # полуось эллипса возможных положений
                best = (L, la + t * (lb - la), loa + t * (lob - loa), round(max(3.0, semi * 0.5), 1), na, nb, ka, kb)
    return best[1:] if best else None


def fill_by_transit(st):
    """Пункт без координат по транзитным пунктам ТР №4: между двумя пунктами по тарифным расстояниям,
    иначе — в ≤15 км (по тарифу) от ближайшего пункта."""
    good = st[st.coord_quality.isin(["A", "B", "D"])].set_index("esr")[["lat", "lon", "name"]]
    n = 0
    for i in st.index[st.coord_quality.eq("")]:
        pts = [(e, name, km) for e, name, km in st.at[i, "_transit"] if e in good.index]
        if not pts:
            continue
        pair = transit_pair_estimate(good, pts)
        if pair:
            lat, lon, prec, na, nb, ka, kb = pair
            st.loc[i, ["lat", "lon"]] = round(lat, 6), round(lon, 6)
            st.at[i, "coord_quality"] = "E"
            st.at[i, "coord_source"] = "транзитные пункты"
            st.at[i, "coord_precision_km"] = prec
            st.at[i, "coord_note"] = f"между транзитными пунктами {na} ({ka} км) и {nb} ({kb} км) по тарифным расстояниям"
            n += 1
            continue
        e, name, km = min(pts, key=lambda t: t[2])
        if km > TRANSIT_FILL_MAX_KM:
            continue
        la, lo = good.at[e, "lat"], good.at[e, "lon"]
        others = [(good.at[e2, "lat"], good.at[e2, "lon"], k2) for e2, _, k2 in pts if e2 != e]
        if any(haversine(la, lo, a, b) > k2 * TRANSIT_SLACK + TRANSIT_ADD_KM + km for a, b, k2 in others):
            continue
        st.loc[i, ["lat", "lon"]] = la, lo
        st.at[i, "coord_quality"] = "E"
        st.at[i, "coord_source"] = f"транзитный пункт {e}"
        st.at[i, "coord_precision_km"] = float(max(km, 2))
        st.at[i, "coord_note"] = f"{km} км (по тарифу) от транзитного пункта {name}"
        n += 1
    return st, n


# ---------------------------------------------------------------- Экспресс

def merge_express(st):
    e = pd.read_csv(RAW / "express.csv", sep=";", dtype=str, keep_default_na=False)
    e = e.apply(lambda c: c.str.strip())
    by_code = e.drop_duplicates("express").set_index("express")
    st["express_name"] = st["express"].map(by_code["name"]).fillna("")
    st["express_railway"] = st["express"].map(by_code["railway"]).fillna("")
    # городские коды: МОСКВА 2000000 объединяет вокзалы 2000001…2000009
    city = {}
    for parent, name, alias in zip(e.express, e.name, e.alias):
        for code in filter(None, alias.split(",")):
            city[code.strip()] = f"{parent} {name}"
    st["express_city"] = st["express"].map(city).fillna("")
    return st


# ---------------------------------------------------------------- Крым

def merge_crimea(st):
    """Колонки старого/нового кода и перенос координат между ними (это одна и та же станция)."""
    cr = pd.read_csv(RAW / "crimea.csv", sep=";", dtype=str, keep_default_na=False)
    cr = cr[(cr.old_esr != "") & (cr.esr != "")]
    st["esr_crimea_old"] = st["esr"].map(dict(zip(cr.esr, cr.old_esr))).fillna("")
    st["esr_crimea_new"] = st["esr"].map(dict(zip(cr.old_esr, cr.esr))).fillna("")
    rank = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4, "": 9}
    idx = pd.Series(st.index, index=st.esr)
    n = 0
    for old, new in zip(cr.old_esr, cr.esr):
        if old not in idx or new not in idx:
            continue
        i, j = idx[old], idx[new]
        qi, qj = rank[st.at[i, "coord_quality"]], rank[st.at[j, "coord_quality"]]
        if qi == qj or min(qi, qj) > 1:
            continue
        src, dst = (i, j) if qi < qj else (j, i)
        if rank[st.at[dst, "coord_quality"]] <= rank["D"]:
            continue
        st.loc[dst, ["lat", "lon"]] = st.at[src, "lat"], st.at[src, "lon"]
        st.at[dst, "coord_quality"] = "D"
        st.at[dst, "coord_source"] = f"код Крыма {st.at[src, 'esr']}"
        st.at[dst, "coord_precision_km"] = 0.5
        st.at[dst, "coord_note"] = f"координаты {'нового' if src == j else 'старого'} кода {st.at[src, 'esr']}"
        n += 1
    return st, n

"""Тарифное руководство №4 по состоянию на 21.10.2026 (сайт Совета по ж/д транспорту СНГ).

  tr4_book1_*.xls — Книга 1: тарифные участки, станции по порядку и расстояния от начала участка
  tr4_book2_*.xls — Книга 2: алфавитные списки раздельных (РП) и остановочных (ОП) пунктов,
                    города, порты перевалки
  tr4_book3_*.xls — Книга 3: расстояния между транзитными пунктами (для карты не используется)

Участки дают геометрию сети: станция без координат ставится между соседями по километражу,
а соседние станции с координатами проверяются на согласованность с тарифными расстояниями.
"""
import pickle
from pathlib import Path
import re

import numpy as np
import pandas as pd

from build_stations import OUT, RAW, haversine, norm_name

BOOK1 = RAW / "tr4_book1_2026-10-21.xls"
BOOK2 = RAW / "tr4_book2_2026-10-21.xls"
TR4_DATE = "21.10.2026"
CACHE = OUT / ".tr4_2026.pkl"  # разбор xls занимает ~минуту — кэшируем

# сокращение дороги в ТР №4 → (название как в справочнике ЕСР, ISO страны)
ROADS = {
    "Окт": ("Октябрьская", "RU"), "Клг": ("Калининградская", "RU"), "Моск": ("Московская", "RU"),
    "Горьк": ("Горьковская", "RU"), "Сев": ("Северная", "RU"), "С-Кав": ("Северо-Кавказская", "RU"),
    "Ю-Вост": ("Юго-Восточная", "RU"), "Прив": ("Приволжская", "RU"), "Кбш": ("Куйбышевская", "RU"),
    "Сверд": ("Свердловская", "RU"), "Ю-Ур": ("Южно-Уральская", "RU"), "З-Сиб": ("Западно-Сибирская", "RU"),
    "Крас": ("Красноярская", "RU"), "В-Сиб": ("Восточно-Сибирская", "RU"), "Заб": ("Забайкальская", "RU"),
    "Д-Вост": ("Дальневосточная", "RU"), "Крым": ("Крымская", "RU"), "Донец": ("Донецкая", "RU"),
    "ЛУГАН": ("Луганская", "RU"), "МЕЛИТ": ("Мелитопольская", "RU"),
    "Якут": ('ОАО АК "Железные дороги Якутии"', "RU"), "ИФР-1": ("ИФР-1", "RU"),
    "Ю-Зап": ("Юго-Западная", "UA"), "Льв": ("Львовская", "UA"), "Од": ("Одесская", "UA"),
    "Южн": ("Южная", "UA"), "Придн": ("Приднепровская", "UA"), "Дон": ("Донецкая", "UA"),
    "Эст": ("Эстонская", "EE"), "Лат": ("Латвийская", "LV"), "Лит": ("Литовская", "LT"),
    "Бел": ("Белорусская", "BY"), "Млд": ("Молдавская", "MD"), "Азерб": ("Азербайджанская", "AZ"),
    "Ю-Кав": ("Армянская", "AM"), "Грз": ("Грузинская", "GE"), "Кзх": ("Казахстанские", "KZ"),
    "Кирг": ("Кыргызская", "KG"), "Узбк": ("Узбекские", "UZ"), "Тадж": ("Таджикская", "TJ"),
    "Трк": ("Туркменская", "TM"),
}
POINT_TYPES = {"рзд": "разъезд", "пп": "путевой пост", "бп": "блок-пост", "обп": "обгонный пункт", "оп": "остановочный пункт"}
SEC_HEADER = re.compile(r"участок\s+(\S+)\s+\"(.*)\"\s*\(([^()]*)\)\s*$")


def road(s):
    m = re.match(r"\s*(\d+)\s+(.+?)\s*(\([РУ]\))?\s*$", s or "")
    return ROADS.get(m.group(2), (m.group(2), "")) if m else ("", "")


def _cells(path, sheet):
    return pd.read_excel(path, sheet_name=sheet, header=None, dtype=str).fillna("").apply(lambda c: c.str.strip())


# ---------------------------------------------------------------- Книга 1: участки

def parse_sections():
    xl = pd.ExcelFile(BOOK1)
    recs = []
    for sh in xl.sheet_names[2:]:
        sec = None
        for r in _cells(BOOK1, sh).itertuples(index=False):
            if "участок" in r[0]:
                m = SEC_HEADER.search(r[0])
                sec = (sh, m.group(1), m.group(2), m.group(3)) if m else None
                continue
            code, km = re.match(r"(\d{6})", r[1]), re.match(r"(\d+)", r[3])
            if sec and code and km:
                recs.append((*sec, code.group(1), r[2], int(km.group(1))))
    s = pd.DataFrame(recs, columns=["sheet", "sec", "sec_name", "sec_kind", "esr", "name", "km"])
    return s.sort_values(["sec", "km"], kind="stable").reset_index(drop=True)


# ---------------------------------------------------------------- Книга 2: списки станций

def _merge_continuations(d, text_cols):
    """Перенесённые строки (без № п/п) дописываем к предыдущей записи."""
    rows = []
    for r in d.itertuples(index=False):
        r = list(r)
        if r[0] == "" and rows:
            for c in text_cols:
                if r[c]:
                    sep = "," if c == 2 and not rows[-1][c].endswith(",") else ("" if c == 2 else " ")
                    rows[-1][c] = (rows[-1][c] + sep + r[c]).strip(", ")
        elif r[0]:
            rows.append(r)
    return rows


def parse_stations():
    out = []
    rp = _merge_continuations(_cells(BOOK2, "РП").iloc[6:], [1, 2, 4])
    for no, name, ops, rd, transit, code in (r[:6] for r in rp):
        out.append(("РП", code, name, ops, rd, transit))
    op = _merge_continuations(_cells(BOOK2, "ОП").iloc[6:], [1, 2])
    for no, name, ops, rd, code in (r[:5] for r in op):
        out.append(("ОП", code, name, ops, rd, ""))
    x = pd.DataFrame(out, columns=["list", "esr", "name", "ops", "road", "transit"])
    x = x[x.esr.str.fullmatch(r"\d{6}")].drop_duplicates("esr")
    note = x["name"].str.extract(r"\(([^()]*)\)\s*$")[0].fillna("").str.strip().str.lower().str.rstrip(".")
    x["point_type"] = np.where(x["list"] == "ОП", "остановочный пункт", note.map(POINT_TYPES).fillna("станция"))
    x.loc[(x["list"] == "РП") & (note == "оп"), "point_type"] = "остановочный пункт"
    x["railway"], x["iso"] = zip(*x["road"].map(road))
    return x


def parse_cities():
    rows = []
    d = _cells(BOOK2, "Города").iloc[7:]
    for r in d.itertuples(index=False):
        m = re.match(r"(\d{6})\s+(.*?)\s*-\s*(\d+)\s*км", r[3])
        if r[0] and m:
            rows.append((r[1], r[2].rstrip(","), m.group(1), m.group(2), int(m.group(3))))
        elif not r[0] and r[2] and rows:  # продолжение названия региона
            rows[-1] = (*rows[-1][:1], (rows[-1][1] + ", " + r[2]).strip(", "), *rows[-1][2:])
    return pd.DataFrame(rows, columns=["city", "region", "esr", "station", "km"])


def parse_ports():
    ports = {}
    for sh, col in (("Порт Мор.Пер.", 4), ("Порт Внутр.Пер.", 6)):
        for r in _cells(BOOK2, sh).iloc[7:].itertuples(index=False):
            m = re.match(r"(\d{6})", r[col])
            if r[0] and m and r[1]:
                ports.setdefault(m.group(1), []).append(r[1])
    return {k: "; ".join(dict.fromkeys(v)) for k, v in ports.items()}


def load():
    src_mtime = max(BOOK1.stat().st_mtime, BOOK2.stat().st_mtime)
    if CACHE.exists() and CACHE.stat().st_mtime > src_mtime:
        with open(CACHE, "rb") as f:
            return pickle.load(f)
    data = {"sections": parse_sections(), "stations": parse_stations(), "cities": parse_cities(), "ports": parse_ports()}
    OUT.mkdir(parents=True, exist_ok=True)
    with open(CACHE, "wb") as f:
        pickle.dump(data, f)
    return data


# ---------------------------------------------------------------- новые коды

def new_ref_rows(ref, tr):
    """Коды из ТР №4 2026, которых нет в справочнике ЕСР, — добавляем строками в формате esr.csv."""
    st, sec = tr["stations"], tr["sections"]
    known = set(ref["esr"])
    add = st[~st.esr.isin(known)]
    extra = sec[~sec.esr.isin(known) & ~sec.esr.isin(add.esr)].drop_duplicates("esr")
    rows = [dict(esr=r.esr, name=r.name, type=r.point_type, railway=r.railway, iso3166=r.iso) for r in add.itertuples()]
    for r in extra.itertuples():
        rw, iso = ROADS.get(r.sheet.replace(" (Р)", "").replace(" (У)", ""), ("", ""))
        rows.append(dict(esr=r.esr, name=re.sub(r"^ОП\s+", "", r.name), type="", railway=rw, iso3166=iso))
    new = pd.DataFrame(rows).reindex(columns=ref.columns).fillna("")
    new["invalid_esr"] = "0"
    new["source"] = "tr4_2026"
    return new


# ---------------------------------------------------------------- координаты по участкам

ANCHOR = ("A", "B", "D")
SEC_MAX_GAP_KM = 120


def _sec_frames(st, sec):
    pos = st.set_index("esr")[["lat", "lon", "coord_quality"]]
    s = sec.join(pos, on="esr")
    return [d for _, d in s.groupby("sec", sort=False)]


def fill_by_section_name(st, sec):
    """Новые коды и коды без координат: одноимённая станция с координатами рядом с соседями по участку."""
    have = st[st.coord_quality.isin(["A", "B"])]
    by_name = {}
    for k, la, lo, e in zip(have["name_clean"].map(norm_name), have.lat, have.lon, have.esr):
        if k:
            by_name.setdefault(k, []).append((la, lo, e))
    idx = pd.Series(st.index, index=st.esr)
    n = 0
    for d in _sec_frames(st, sec):
        q = d.coord_quality.fillna("").values
        for j in range(len(d)):
            e = d.esr.iat[j]
            if q[j] != "" or e not in idx:
                continue
            i = idx[e]
            if st.at[i, "coord_quality"] != "":
                continue
            nb = [k for k in range(max(0, j - 3), min(len(d), j + 4)) if k != j and q[k] in ANCHOR]
            key = norm_name(st.at[i, "name_clean"])
            if not nb or key not in by_name:
                continue
            hits = {(round(la, 3), round(lo, 3), e2) for la, lo, e2 in by_name[key] if e2 != e and
                    min(haversine(la, lo, d.lat.iat[k], d.lon.iat[k]) - abs(d.km.iat[k] - d.km.iat[j]) for k in nb) <= 15}
            if len({h[:2] for h in hits}) != 1:
                continue
            la, lo, e2 = next(iter(hits))
            st.loc[i, ["lat", "lon"]] = la, lo
            st.loc[i, ["coord_quality", "coord_source", "coord_precision_km"]] = "D", f"одноимённая {e2}", 2.0
            st.at[i, "coord_note"] = f"координаты одноимённой станции {e2} — совпадает с положением на участке ТР №4"
            q[j] = "D"
            n += 1
    return st, n


# перекодированные сети: новая дорога → дороги, где та же станция числилась под старым кодом
RECODED = {"Донецкая": {"Донецкая"}, "Луганская": {"Донецкая", "Луганская"}, "Мелитопольская": {"Приднепровская"}}


def fill_by_recoded_name(st):
    """Новый код без координат ← единственная одноимённая станция с координатами на той же (или прежней) дороге."""
    have = st[st.coord_quality.isin(["A", "B"]) & ~st.is_new_tr4]
    by = {}
    for k, rw, la, lo, e in zip(have["name_clean"].map(norm_name), have.railway, have.lat, have.lon, have.esr):
        if k:
            by.setdefault(k, []).append((rw, la, lo, e))
    n = 0
    for i in st.index[st.is_new_tr4 & st.coord_quality.eq("")]:
        rw = st.at[i, "railway"]
        allowed = RECODED.get(rw, set()) | {rw}
        hits = [h for h in by.get(norm_name(st.at[i, "name_clean"]), []) if h[0] in allowed]
        if len(hits) != 1 or st.at[i, "is_km_name"]:
            continue
        _, la, lo, e = hits[0]
        st.loc[i, ["lat", "lon"]] = la, lo
        st.loc[i, ["coord_quality", "coord_source", "coord_precision_km"]] = "D", f"старый код {e}", 2.0
        st.at[i, "coord_note"] = f"новый код ТР №4; координаты одноимённой станции {e} (прежняя нумерация)"
        n += 1
    return st, n


def section_estimates(st, sec, exclude_self=False):
    """Оценка положения каждого кода по ближайшим якорям (A/B/D) слева и справа на участке."""
    est = {}
    for d in _sec_frames(st, sec):
        q = d.coord_quality.fillna("").values
        anchors = [k for k in range(len(d)) if q[k] in ANCHOR]
        if len(anchors) < 2:
            continue
        for j in range(len(d)):
            left = [k for k in anchors if d.km.iat[k] <= d.km.iat[j] and k != j]
            right = [k for k in anchors if d.km.iat[k] >= d.km.iat[j] and k != j]
            if not left or not right:
                continue
            a, b = left[-1], right[0]
            gap = d.km.iat[b] - d.km.iat[a]
            if gap > SEC_MAX_GAP_KM or a == b:
                continue
            t = (d.km.iat[j] - d.km.iat[a]) / gap if gap else 0.5
            lat = d.lat.iat[a] + t * (d.lat.iat[b] - d.lat.iat[a])
            lon = d.lon.iat[a] + t * (d.lon.iat[b] - d.lon.iat[a])
            e = d.esr.iat[j]
            if e not in est or gap < est[e][2]:
                est[e] = (lat, lon, gap, d.sec.iat[j], d.esr.iat[a], d.esr.iat[b])
    return est


def precision_for_gap(gap):
    return round(max(2.0, gap * 0.1), 1)


def apply_sections(st, sec):
    """Расстановка по участкам: пустые и E (по кодам) — по участку; C сверяется с положением на участке."""
    est = section_estimates(st, sec)
    idx = pd.Series(st.index, index=st.esr)
    cnt = {"filled": 0, "replaced_E": 0, "C_confirmed": 0, "C_moved": 0}
    for e, (lat, lon, gap, s_id, a, b) in est.items():
        if e not in idx:
            continue
        i = idx[e]
        q, src = st.at[i, "coord_quality"], st.at[i, "coord_source"]
        prec = precision_for_gap(gap)
        note = f"по участку {s_id} ТР №4 между {a} и {b} ({gap} км)"
        if q == "C":
            d = haversine(st.at[i, "lat"], st.at[i, "lon"], lat, lon)
            if d <= max(3, 2 * prec):
                st.at[i, "coord_quality"] = "B"
                st.at[i, "coord_note"] = (st.at[i, "coord_note"] + "; " if st.at[i, "coord_note"] else "") + \
                    f"положение подтверждено участком {s_id} ТР №4 ({d:.1f} км)"
                cnt["C_confirmed"] += 1
                continue
            if d <= 15:
                continue
            cnt["C_moved"] += 1
            note = f"точка OSM была в {d:.0f} км от положения на участке; " + note
        elif q == "E" and src in ("интерполяция",) or q == "E" and src.startswith("транзитный"):
            cnt["replaced_E"] += 1
        elif q == "":
            cnt["filled"] += 1
        else:
            continue
        st.loc[i, ["lat", "lon"]] = round(lat, 6), round(lon, 6)
        st.at[i, "coord_quality"] = "E"
        st.at[i, "coord_source"] = f"участок {s_id}"
        st.at[i, "coord_precision_km"] = prec
        st.at[i, "coord_note"] = note
    return st, cnt


END_MAX_KM = 3      # станция без пары якорей: до ближайшего якоря по участку не дальше этого
END_DIR_KM = (1, 30)  # второй якорь для направления (экстраполяция) — на таком расстоянии от первого


def section_end_estimates(st, sec, codes=None):
    """Оценки для кодов, у которых якоря на участке только с одной стороны (крайняя станция участка, узел):
    точка ближайшего якоря (не дальше END_MAX_KM по участку), сдвинутая по направлению участка, если рядом есть
    второй якорь и путь между ними почти прямой. Оценки с разных участков (узел — конец нескольких) усредняются
    с весом 1/расстояние²: направления расходятся, и среднее ложится ближе к узлу."""
    parts = {}
    for d in _sec_frames(st, sec):
        q = d.coord_quality.fillna("").values
        anchors = [k for k in range(len(d)) if q[k] in ANCHOR]
        if not anchors:
            continue
        for j in range(len(d)):
            e = d.esr.iat[j]
            if codes is not None and e not in codes or codes is None and q[j] != "":
                continue
            # на том же километре — только та же станция под другим кодом («Высоцк» и «Высоцк (перев.)»):
            # разные станции на одном км бывают в разных местах (стыки, парки)
            others = [k for k in anchors if k != j and d.esr.iat[k] != e and (d.km.iat[k] != d.km.iat[j]
                      or norm_name(d.name.iat[k]) == norm_name(d.name.iat[j]))]
            near = sorted(others, key=lambda k: abs(d.km.iat[k] - d.km.iat[j]))
            if not near:
                continue
            a = near[0]
            dk = abs(d.km.iat[a] - d.km.iat[j])
            if dk > END_MAX_KM:
                continue
            la, lo, how = d.lat.iat[a], d.lon.iat[a], "соседняя точка"
            side = np.sign(d.km.iat[a] - d.km.iat[j])
            far = [k for k in near[1:] if np.sign(d.km.iat[k] - d.km.iat[a]) == side
                   and END_DIR_KM[0] <= abs(d.km.iat[k] - d.km.iat[a]) <= END_DIR_KM[1]]
            if dk and far:
                b = far[0]
                kb = abs(d.km.iat[b] - d.km.iat[a])
                if haversine(d.lat.iat[a], d.lon.iat[a], d.lat.iat[b], d.lon.iat[b]) >= 0.8 * kb:  # почти прямой путь
                    t = dk / kb
                    la, lo, how = la + t * (la - d.lat.iat[b]), lo + t * (lo - d.lon.iat[b]), "по направлению участка"
            parts.setdefault(e, []).append((la, lo, dk, d.sec.iat[j], d.esr.iat[a], how))
    est = {}
    for e, ps in parts.items():
        w = np.array([1 / max(p[2], 0.3) ** 2 for p in ps])
        la = float(np.dot(w, [p[0] for p in ps]) / w.sum())
        lo = float(np.dot(w, [p[1] for p in ps]) / w.sum())
        est[e] = (la, lo, max(p[2] for p in ps), ps)
    return est


def end_precision(dk):
    # километраж ТР №4 целый: «1 км» — это 0,5–1,5 км; на проверке ±(dk + 1) покрывает около 90% точек
    return float(dk) + 1.0


REGION_SHAPES = Path(__file__).resolve().parent.parent / "dashboard" / "vendor" / "region-shapes.json"
REGION_TOL_KM = 20


def _in_ring(x, y, ring):
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def km_to_region(lat, lon, polys):
    if any(_in_ring(lon, lat, p[0]) for p in polys):
        return 0.0
    return min(haversine(lat, lon, y, x) for p in polys for x, y in p[0])


def fill_by_section_end(st, sec):
    """Коды без координат на краю участка (узлы, стыковые) — по ближайшему якорю участка (класс E).
    Защита от ошибочных якорей: точка должна попасть в свой регион из справочника ЕСР (с запасом REGION_TOL_KM);
    без известного региона код не заполняется."""
    import json
    shapes = json.loads(REGION_SHAPES.read_text(encoding="utf-8"))
    est = section_end_estimates(st, sec)
    idx = pd.Series(st.index, index=st.esr)
    n = rejected = 0
    for e, (la, lo, dk, ps) in est.items():
        if e not in idx or st.at[idx[e], "coord_quality"] != "":
            continue
        i = idx[e]
        polys = shapes.get(st.at[i, "region"])
        if not polys or km_to_region(la, lo, polys) > REGION_TOL_KM:
            rejected += 1
            continue
        st.loc[i, ["lat", "lon"]] = round(la, 6), round(lo, 6)
        st.at[i, "coord_quality"] = "E"
        st.at[i, "coord_source"] = "край участка " + ", ".join(sorted({p[3] for p in ps}))
        st.at[i, "coord_precision_km"] = end_precision(dk)
        st.at[i, "coord_note"] = "по участкам ТР №4: " + "; ".join(
            f"{p[3]} — {p[2]:g} км от {p[4]} ({p[5]})" for p in ps)
        n += 1
    return st, n, rejected


def validate_section_end(st, sec):
    """Проверка на известных точках A: каждую считаем «без координат» и сравниваем оценку с настоящей."""
    a = st[st.coord_quality == "A"]
    codes = set(a.esr)
    est = section_end_estimates(st, sec, codes)
    pos = a.set_index("esr")[["lat", "lon"]]
    errs, precs = [], []
    for e, (la, lo, dk, ps) in est.items():
        errs.append(haversine(pos.at[e, "lat"], pos.at[e, "lon"], la, lo))
        precs.append(end_precision(dk))
    e, p = np.array(errs), np.array(precs)
    return pd.DataFrame({"Показатель": [
        "Точек в проверке", "Медианная ошибка, км", "90-й перцентиль ошибки, км",
        "Доля в пределах заявленной точности, %", "Доля с ошибкой > 10 км, %"], "Значение": [
        len(e), round(float(np.median(e)), 2), round(float(np.percentile(e, 90)), 1),
        round(float((e <= p).mean() * 100), 1), round(float((e > 10).mean() * 100), 1)]})


def check_sections(st, sec):
    """A/B, противоречащие тарифным расстояниям до обоих соседей по участку, понижаем до C."""
    idx = pd.Series(st.index, index=st.esr)
    bad = {}
    for d in _sec_frames(st, sec):
        k = [j for j in range(len(d)) if d.coord_quality.iat[j] in ANCHOR]
        for p, c in zip(k, k[1:]):
            dist = haversine(d.lat.iat[p], d.lon.iat[p], d.lat.iat[c], d.lon.iat[c])
            if dist > (d.km.iat[c] - d.km.iat[p]) * 1.1 + 5:
                for j in (p, c):
                    bad.setdefault(d.esr.iat[j], set()).add(d.esr.iat[c if j == p else p])
    loo = section_estimates(st.assign(coord_quality=st.coord_quality.where(~st.esr.isin(bad), "x")), sec)
    st["section_conflict"] = False
    n = 0
    for e, others in bad.items():
        if len(others) < 2 or e not in idx or e not in loo:
            continue
        i = idx[e]
        d = haversine(st.at[i, "lat"], st.at[i, "lon"], loo[e][0], loo[e][1])
        if d < 10:
            continue
        st.at[i, "section_conflict"] = True
        if st.at[i, "coord_quality"] in ("A", "B", "D"):
            st.at[i, "coord_quality"] = "C"
            st.at[i, "coord_note"] = (st.at[i, "coord_note"] + "; " if st.at[i, "coord_note"] else "") + \
                f"противоречит тарифным расстояниям участка {loo[e][3]}: по участку станция в {d:.0f} км отсюда"
            n += 1
    return st, n


def validate_sections(st, sec):
    errs, precs = [], []
    a_only = st.assign(coord_quality=st.coord_quality.where(st.coord_quality == "A", ""))
    for d in _sec_frames(a_only, sec):
        k = [j for j in range(len(d)) if d.coord_quality.iat[j] == "A"]
        for p, c, n_ in zip(k, k[1:], k[2:]):
            gap = d.km.iat[n_] - d.km.iat[p]
            if not gap or gap > SEC_MAX_GAP_KM:
                continue
            t = (d.km.iat[c] - d.km.iat[p]) / gap
            la = d.lat.iat[p] + t * (d.lat.iat[n_] - d.lat.iat[p])
            lo = d.lon.iat[p] + t * (d.lon.iat[n_] - d.lon.iat[p])
            errs.append(haversine(la, lo, d.lat.iat[c], d.lon.iat[c]))
            precs.append(precision_for_gap(gap))
    e, p = np.array(errs), np.array(precs)
    return pd.DataFrame({"Показатель": [
        "Точек в проверке", "Медианная ошибка, км", "90-й перцентиль ошибки, км",
        "Доля в пределах заявленной точности, %", "Доля с ошибкой > 10 км, %"], "Значение": [
        len(e), round(float(np.median(e)), 2), round(float(np.percentile(e, 90)), 1),
        round(float((e <= p).mean() * 100), 1), round(float((e > 10).mean() * 100), 1)]})


def station_sections(sec):
    """Для карточки станции: «17-002 Кусково — Орехово-Зуево, 14 км» (через « | »)."""
    txt = sec.assign(t=sec.sec + " " + sec.sec_name.str.replace(" - ", " — ", regex=False) + ", " + sec.km.astype(str) + " км")
    return txt.groupby("esr").t.agg(" | ".join)

#!/usr/bin/env python3
"""Справочник предприятий ремонта вагонов с местоположением → data/output/depots.csv.

Источники (data/raw/):
  sja_1001_17.csv   — СЖА 1001 17 «Условные коды предприятий» (2017): код, наименования, код станции, виды ремонта
                      (scripts/parse_sja.py)
  zel_dor_depo.csv  — справочник кодов депо из учётной системы (свежее, без станций): код, наименование
  depots_manual.csv — ручные уточнения (необязательно): code;esr;lat;lon;note — имеют приоритет

Местоположение по порядку:
  1) код станции из СЖА 1001 17;
  2) станция из названия («…депо Пермь-Сортировочная…», «участок ТОР Новый Ургал», «на станции Ташеба»);
  3) город из названия («в г. Березники», «Вагоноремонтный завод Пермь») → станция с тем же названием;
  4) ручное уточнение.
Ненайденные с вагонами в дислокациях — в data/output/depots_unplaced.csv для ручного заполнения.

Запуск (после build_stations.py): python3 scripts/build_depots.py
"""
import re
import sys
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz, process

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_stations import OUT, RAW, norm_name  # noqa: E402

LAT2CYR = str.maketrans("aceopxyAСEHKMOPTX", "асеорхуАСЕНКМОРТХ")
FLAGS = {"tr": "ТР", "dr": "ДР", "kr": "КР"}
EXTRA = ["pos", "kp", "sensor", "axle", "side_frame", "bolster", "wheelset", "wheel_survey", "solid_wheel", "brake"]


def fix(t):
    return " ".join(w.translate(LAT2CYR) if re.search("[а-яё]", w, re.I) and re.search("[a-z]", w, re.I) else w
                    for w in str(t).split())


def kind(name):
    n = name.lower()
    if re.search(r"локомотив|тепловоз|электровоз|\bтч\b|-тч\b", n):
        return "локомотивное"
    if "моторвагон" in n:
        return "моторвагонное"
    if re.search(r"пассажирск\w* вагонн|лвчд|пасс\. ваг|фпк|федеральн\w* пассажирск", n):
        return "пассажирское"
    if re.search(r"пункт\w* технического обслуживания|\bпто\b", n):
        return "ПТО"
    if re.search(r"отцепочн|\bтор\b|текущего ремонта", n):
        return "ТОР"
    if re.search(r"эксплуатационн\w* вагонн|вчдэ|вагонное \(эксплуатационное\)", n):
        return "ВЧДЭ"
    if re.search(r"вагонн\w* ремонтн\w* депо|вчдр|вагоноремонтн\w* депо", n):
        return "ВЧДр"
    if re.search(r"вагоноремонтн|вагонн\w* ремонтн|ремонт\w* вагон|врп\b|врз\b|вагонн\w* депо", n):
        return "ВРЗ/ВРП"
    if re.search(r"завод|машиностро|вагоностро", n):
        return "завод"
    return "прочее"


ABBR = [(r"Открыто\w* акционерн\w* обществ\w*", "ОАО"), (r"Закрыто\w* акционерн\w* обществ\w*", "ЗАО"),
        (r"Публичн\w* акционерн\w* обществ\w*", "ПАО"), (r"Акционерн\w* обществ\w*", "АО"),
        (r"Обществ\w* с ограниченной ответственностью", "ООО"), (r"Эксплуатационн\w* вагонн\w* депо", "ВЧДЭ"),
        (r"Вагонн\w* ремонтн\w* депо", "ВЧДр"), (r"Вагоноремонтн\w* завод", "ВРЗ"), (r"Пассажирск\w* вагонн\w* депо", "ЛВЧД"),
        (r"обособленн\w* структурн\w* подразделени\w*", "ОСП"), (r"структурн\w* подразделени\w*", "СП"),
        (r"(?:пункт|цех|участок)\w* текущего отцепочного ремонта", "ТОР"), (r"Пункт\w* технического обслуживания", "ПТО")]


def short_label(name, short):
    """Подпись на карте: краткое наименование из СЖА, иначе — сокращённое полное без «хвоста» головной организации."""
    if short and len(short) <= 60:
        return short
    t = name
    for p, a in ABBR:
        t = re.sub(p, a, t, flags=re.I)
    t = re.split(r"\s+[-–—]\s+(?=(?:ОСП|СП|филиал|ОАО|АО|ООО)\b)|\s*[-–—]\s*СП\b|\s*[-–—]\s*ОСП\b", t)[0]
    return re.sub(r"\s+", " ", t).strip()[:80]


WAGON_KINDS = {"ВЧДр", "ВРЗ/ВРП", "ВЧДЭ", "ТОР", "ПТО"}


def owner(name):
    n = name.replace("«", '"').replace("»", '"')
    for pat, o in [(r"ВРК[- ]?1|ремонтная компания-1|компани[яи] ?-?1\b", "ВРК-1"), (r"ВРК[- ]?2|ремонтная компания-2", "ВРК-2"),
                   (r"ВРК[- ]?3|ремонтная компания-3", "ВРК-3"), (r"ОМК|Стальной путь", "ОМК «Стальной путь»"),
                   (r"Новая вагоноремонтная|\bНВК\b", "НВК"), (r"Новотранс", "Новотранс"), (r"РЖД|дирекци\w+ инфраструктуры", "ОАО «РЖД»"),
                   (r"Федеральная пассажирская|ФПК", "АО «ФПК»"), (r"ВРС|Вагонный ремонтный сервисный", "ВРС"),
                   (r"Казахстан темир жолы|КТЖ|Камкор", "КТЖ"), (r"Белорусск\w+ железн|БЧ\b", "БЧ")]:
        if re.search(pat, n, re.I):
            return o
    return ""


# станция в названии: шаблоны по убыванию надёжности
TAIL = r"(?=\s*(?:[-–—]\s*(?:структурн|обособлен|СП\b|ОСП|филиал|производствен|участ)|структурн|обособлен|СП\b|ОСП|филиал|эксплуатационн|вагонн|пассажир|[Оо]ктябрьск|Северн|Южн|дирекци|,|\(|\"|«|$))"
PATTERNS = [r"(?:на\s+)?станци[ияи]\s+(?:ст\.\s*)?([А-ЯЁ][\w\-– ]+?)" + TAIL,
            r"(?:ТОР|ПТО|отцепочного ремонта|технического обслуживания)\s+(?:по\s+станции\s+|на\s+станции\s+|ст\.\s*)?\"?([А-ЯЁ][\w\-– ]+?)\"?" + TAIL,
            r"(?:депо|ВЧД[рэЭР]?(?:-\d+)?|ВЧДр)\s+(?:ст\.\s*)?([А-ЯЁ][\w\-– ]+?)" + TAIL,
            r"(?:завод|предприятие|филиал)\s+([А-ЯЁ][\w\-]+)(?=\s*[-–—]|\s*$|,)",
            r"\bв\s+г(?:ороде|\.)\s*([А-ЯЁ][\w\-]+(?:\s[А-ЯЁ][\w\-]+)?)"]


class StationIndex:
    def __init__(self, st):
        st = st[st.lat != ""]
        self.keys = {}
        for e, n, q in zip(st.esr, st.name_clean, st.coord_quality):
            k = self.key(n)
            if k:
                self.keys.setdefault(k, []).append((e, q))
        self.names = list(self.keys)

    @staticmethod
    def key(t):
        t = fix(t).lower().replace("ё", "е").replace("–", "-")
        t = re.sub(r"\b(сортировочн|пассажирск|товарн|московск|главн)(ые|ая|ый|ое|ий|ой)\b", r"\1", t)
        return norm_name(t)

    def find(self, t):
        k = self.key(t)
        if not k or len(k) < 3:
            return None
        if k in self.keys:
            return self.best(self.keys[k])
        # порядок частей («Санкт-Петербург-Московский-Сортировочный» = «…-Сортировочный-Московский»)
        parts = sorted(k.split())
        for kk, v in self.keys.items():
            if len(kk) > 4 and sorted(kk.split()) == parts:
                return self.best(v)
        m = process.extractOne(k, self.names, scorer=fuzz.ratio, score_cutoff=92)
        return self.best(self.keys[m[0]]) if m else None

    @staticmethod
    def best(v):
        rank = {"A": 0, "B": 1, "D": 2, "C": 3, "E": 4}
        return sorted(v, key=lambda x: rank.get(x[1], 9))[0][0]


def station_from_name(name, idx):
    quoted = re.findall(r"[«\"]([А-ЯЁ][^«»\"]{2,40})[»\"]", name)  # «Беломорск», «Волховстрой», «Вагонное депо Березники»
    for q in quoted[:1]:  # первое в кавычках — само подразделение; дальше обычно головная организация
        e = idx.find(q) or station_from_name(q, idx)[0] if not re.search(r"ремонтн|сервис|компани|транс|групп", q, re.I) else None
        if e:
            return e, q
    for p in PATTERNS:
        for m in re.finditer(p, name):
            s = m.group(1).strip(" -–\"")
            words = s.split()
            for cand in [s, " ".join(words[:3]), " ".join(words[:2]), words[0]]:
                e = idx.find(cand)
                if e:
                    return e, cand
    return None, None


def main():
    sja = pd.read_csv(RAW / "sja_1001_17.csv", sep=";", dtype=str, keep_default_na=False)
    zel = pd.read_csv(RAW / "zel_dor_depo.csv", sep=";", dtype=str, keep_default_na=False)
    st = pd.read_csv(OUT / "stations.csv", sep=";", dtype=str, keep_default_na=False)
    S = st.set_index("esr")
    idx = StationIndex(st)
    sja["k"], zel["k"] = sja.code.str.lstrip("0"), zel.id.str.lstrip("0")
    sj, zl = sja.set_index("k"), zel.set_index("k")
    man = pd.DataFrame(columns=["code", "esr", "lat", "lon", "note"])
    if (RAW / "depots_manual.csv").exists():
        man = pd.read_csv(RAW / "depots_manual.csv", sep=";", dtype=str, keep_default_na=False)
    man["k"] = man.code.str.lstrip("0")
    mn = man.set_index("k")
    rows = []
    for k in sorted(set(sj.index) | set(zl.index), key=lambda x: int(x) if x.isdigit() else 0):
        s = sj.loc[k] if k in sj.index else None
        z = zl.loc[k] if k in zl.index else None
        name = fix(z["name"] if z is not None else s["full_name"])
        short = fix(s["short_name"]) if s is not None else ""
        r = {"code": k.zfill(4), "name": name, "short_name": short_label(name, short), "kind": kind(name + " " + short), "owner": owner(name + " " + short),
             "esr": "", "station": "", "lat": "", "lon": "", "loc_source": "", "note": "",
             "in_sja": s is not None, "in_zel": z is not None, "since": s["since"] if s is not None else ""}
        for f, lab in FLAGS.items():
            r[f] = s is not None and s[f] == "+"
        r["extra"] = ",".join(f for f in EXTRA if s is not None and s[f] == "+")
        e, src = "", ""
        if k in mn.index:
            m = mn.loc[k]
            e, src, r["note"] = m.esr, "вручную", m.note
            if m.lat and m.lon:
                r["lat"], r["lon"] = m.lat, m.lon
        if not e and s is not None and re.fullmatch(r"\d{6}", s["esr"]) and s["esr"] in S.index:
            e, src = s["esr"], "СЖА 1001 17"
        if not e:
            e2, cand = station_from_name(name + " " + short, idx)
            if e2:
                e, src = e2, f"по названию («{cand}»)"
        if e and e in S.index:
            r["esr"], r["station"] = e, S.at[e, "name"]
            if not r["lat"] and S.at[e, "lat"]:
                r["lat"], r["lon"] = S.at[e, "lat"], S.at[e, "lon"]
        r["loc_source"] = src if r["lat"] else ""
        rows.append(r)
    d = pd.DataFrame(rows)
    d.to_csv(OUT / "depots.csv", sep=";", index=False, encoding="utf-8-sig")
    # для ручного заполнения: предприятия вагонного ремонта без места (станцию можно вписать в data/raw/depots_manual.csv)
    un = d[(d.lat == "") & (d.kind.isin(WAGON_KINDS) | d.tr | d.dr | d.kr | d.kind.eq("прочее"))]
    un[["code", "name", "kind", "owner"]].assign(esr="", lat="", lon="", note="").to_csv(
        OUT / "depots_unplaced.csv", sep=";", index=False, encoding="utf-8-sig")
    wagon = d[d.kind.isin(WAGON_KINDS) | d.tr | d.dr | d.kr]
    print(f"{OUT / 'depots.csv'}: {len(d)} предприятий; вагонного ремонта {len(wagon)}, из них с местоположением {(wagon.lat != '').sum()}")
    print("по типам:", d.kind.value_counts().to_dict())
    print("источник места:", d[d.lat != ""].loc_source.str.replace(r"\s*\(.*", "", regex=True).value_counts().to_dict())
    return d


if __name__ == "__main__":
    main()

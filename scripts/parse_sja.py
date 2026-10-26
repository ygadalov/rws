#!/usr/bin/env python3
"""СЖА 1001 17 «Условные коды предприятий» (PDF, 267 стр.) → data/raw/sja_1001_17.csv.

Отчёт напечатан таблицей, ширина колонок одинакова на всех страницах, но текст в ячейках переносится на несколько
строк. Разбор по координатам слов (pdftotext -tsv, poppler): слово относится к колонке по своей x-координате,
запись начинается с 4-значного кода в первой колонке и продолжается до следующего кода.

Запуск: python3 scripts/parse_sja.py <СЖА_1001_17….pdf>
"""
import csv
import io
import re
import subprocess
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "raw" / "sja_1001_17.csv"

# левая граница колонок (pt) — по шапке на стр. 1
COLS = [("code", 30), ("full_name", 66), ("short_name", 126), ("adm", 185), ("road", 213), ("esr", 248),
        ("tr", 286), ("dr", 314), ("kr", 341), ("pos", 368), ("kp", 395), ("sensor", 425), ("axle", 467),
        ("side_frame", 500), ("bolster", 539), ("wheelset", 575), ("wheel_survey", 620), ("solid_wheel", 647),
        ("brake", 683), ("since", 719)]
FLAGS = ["tr", "dr", "kr", "pos", "kp", "sensor", "axle", "side_frame", "bolster", "wheelset", "wheel_survey",
         "solid_wheel", "brake"]
HEADER_TOP = 140  # выше — шапка страницы


def col_of(x):
    name = COLS[0][0]
    for n, left in COLS:
        if x >= left - 2:
            name = n
    return name


# окончания целых слов: после них перенос — новое слово («акционерное | общество»), иначе — перенос по слогам
WORD_END = ("ое", "ый", "ий", "ая", "ой", "ые", "ие", "ых", "их", "ом", "ем", "ам", "ям", "ах", "ях", "ия", "ии",
            "ов", "ев", "ья", "ть", "во", "ло")
LAT2CYR = str.maketrans("aceopxyAСEHKMOPTX", "асеорхуАСЕНКМОРТХ")


def fix_mixed(word):
    """Латинские буквы-двойники внутри русских слов («Пеpмь» → «Пермь»)."""
    return word.translate(LAT2CYR) if re.search("[а-яё]", word, re.I) and re.search("[a-z]", word, re.I) else word


def join_wrapped(parts):
    """Склейка строк ячейки: «Днепропетро» + «вский» → «Днепропетровский», «акционерное» + «общество» — через пробел."""
    out = ""
    for p in parts:
        if not out:
            out = p
        elif out.endswith("-") and not out.endswith(" -"):
            out += p
        elif p[:1].islower() and out[-1:].isalpha() and (p[:1] == "й" or (
                len(out.split()[-1]) >= 9 and not out.lower().endswith(WORD_END))):
            out += p  # слог перенесён: строка ячейки заполнена целиком (~11 знаков)
        else:
            out += " " + p
    out = " ".join(fix_mixed(w) for w in out.split())
    return re.sub(r'""', '"', out).strip()


def main(pdf):
    tsv = subprocess.run(["pdftotext", "-tsv", pdf, "-"], capture_output=True, text=True, check=True).stdout
    words = [r for r in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE) if r["level"] == "5"]
    # слова → записи: запись начинается с 4-значного кода в первой колонке; слово относится к ближайшему коду
    # выше него на той же странице, слова над первым кодом страницы — продолжение записи с прошлой страницы
    ws = []
    for w in words:
        x, y, t, pg = float(w["left"]), float(w["top"]), w["text"], int(w["page_num"])
        if (pg == 1 and y < HEADER_TOP) or t.startswith("###"):
            continue
        c = col_of(x)
        # строка с кодом напечатана на ~2 pt ниже первой строки названий — код ставим чуть выше
        ws.append((pg, y - 4 if c == "code" else y, x, t, c))
    ws.sort()
    recs, cur = [], None
    for pg, y, x, t, c in ws:
        if c == "code" and re.fullmatch(r"\d{4}", t):
            cur = {"code": t, "lines": {}}
            recs.append(cur)
            continue
        if cur is None or c == "code":
            continue
        cur["lines"].setdefault(c, []).append((pg, y, x, t))
    rows, seen = [], set()
    for r in recs:
        if r["code"] in seen:  # строка, повторённая на стыке страниц
            continue
        seen.add(r["code"])
        o = {"code": r["code"]}
        for c, _ in COLS[1:]:
            ws = sorted(r["lines"].get(c, []))
            if c in ("full_name", "short_name"):
                # слова по строкам: одна строка ячейки → через пробел, перенос — склейка
                lines, last = [], None
                for p, y, x, t in ws:
                    key = (p, round(y))
                    if key != last:
                        lines.append([])
                        last = key
                    lines[-1].append(t)
                o[c] = join_wrapped([" ".join(l) for l in lines])
            elif c == "esr":
                o[c] = next((t for *_, t in ws if re.fullmatch(r"\d{6}", t)), "")
            elif c in FLAGS:
                o[c] = "".join(t for *_, t in ws)[:1]
            else:
                o[c] = " ".join(t for *_, t in ws)
        rows.append(o)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[c for c, _ in COLS], delimiter=";")
        w.writeheader()
        w.writerows(rows)
    n_st = sum(1 for r in rows if re.fullmatch(r'\d{6}', r['esr']))
    print(f"{OUT}: {len(rows)} предприятий, с кодом станции {n_st}")


if __name__ == "__main__":
    main(sys.argv[1])

#!/usr/bin/env python3
"""Граф ж/д сети по тарифным участкам ТР №4 (книга 1) — для расстояний «по железной дороге».

Узлы — коды ЕСР станций участков, рёбра — соседние станции участка с разностью километража.
Стыковка: один и тот же пункт часто записан в разных участках (и разных странах) под разными кодами —
связываем их ребром 0 км, если это:
  - коды одной станции по справочнику ЕСР (dup_esr — «основной код»);
  - одноимённые станции (без пометок в скобках: «(эксп.)», «(перев.)») не дальше 3 км друг от друга.
Станции вне участков (≈ 4 900 кодов) в браузере привязываются к ближайшему узлу по прямой.

Проверка: у ~10 тыс. станций ТР №4 указаны тарифные расстояния до транзитных пунктов («Петрозаводск-3») —
сравниваем с кратчайшим путём по графу.

Выход: data/output/rail_graph.json — {"nodes": [esr…], "edges": [[i, j, км]…]} и сводка проверки.
Запуск (после build_stations.py): python3 scripts/build_rail_graph.py
"""
import heapq
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_stations import OUT, haversine, norm_name  # noqa: E402

SAME_PLACE_KM = 3
BORDER_KM = 12            # пограничный переход: ближайшие станции двух несвязанных сетей не дальше этого
BORDER_FACTOR = 1.3       # путь через переход длиннее прямой
# переходы без грузового транзитного движения — не связываем (Абхазия)
NO_LINK = [{"веселое", "леселидзе"}]
NO_LINK_COUNTRIES = [{"Россия", "Грузия"}]  # единственный путь — через Абхазию, грузового транзита нет
# известные переходы, где у станции одной из сторон нет координат: (код, код, км)
KNOWN_LINKS = [("563606", "558701", 10.0)]  # Гардабани (эксп.) — Беюк-Кясик (эксп.): Грузия — Азербайджан


def base_name(n):
    return norm_name(re.sub(r"\(.*?\)?$", "", str(n)).strip())


def build():
    import tr4_2026 as t26
    sec = t26.load()["sections"]
    st = pd.read_csv(OUT / "stations.csv", sep=";", dtype=str, keep_default_na=False)
    adj = defaultdict(dict)

    def link(a, b, w):
        if a and b and a != b and w < adj[a].get(b, 1e18):
            adj[a][b] = adj[b][a] = w

    sec = split_reused_codes(sec, st)
    for _, d in sec.groupby("sec"):
        d = d.sort_values("km")
        e, km = d.esr.tolist(), d.km.astype(float).tolist()
        for k in range(1, len(e)):
            if km[k] >= km[k - 1]:
                link(e[k - 1], e[k], km[k] - km[k - 1])
    nodes = set(adj)
    n_dup = n_name = 0
    # коды одной станции
    for e, d in zip(st.esr, st.dup_esr):
        if d and e in nodes and d in nodes:
            link(e, d, 0.0)
            n_dup += 1
    # одноимённые узлы рядом
    # только надёжные точки (A/B; D — координаты той же станции под другим кодом) и не безликие названия: «175 км» на разных дорогах — разные пункты,
    # а точка D/E могла быть перенесена с одноимённой станции в другом конце страны
    g = st[st.esr.isin(nodes) & (st.lat != "")].assign(k=lambda x: x.name.map(base_name))
    g = g[(g.coord_quality.isin(["A", "B"]) | (g.coord_quality.eq("D") & ~g.coord_source.str.startswith("одноимённая")))
          & ~g.name.str.contains(r"\d+\s*км|^\s*(?:ост|о\.?п|пост|блок|рзд|платф)", case=False, regex=True)]
    g["lat"], g["lon"] = g.lat.astype(float), g.lon.astype(float)
    for _, d in g[g.k != ""].groupby("k"):
        if len(d) < 2:
            continue
        a = d[["esr", "lat", "lon"]].values
        for i in range(len(a)):
            for j in range(i + 1, len(a)):
                if haversine(a[i][1], a[i][2], a[j][1], a[j][2]) <= SAME_PLACE_KM:
                    if a[j][0] not in adj[a[i][0]]:
                        n_name += 1
                    link(a[i][0], a[j][0], 0.0)
    for a, b, km in KNOWN_LINKS:
        if a in adj and b in adj:
            link(a, b, km)
    gb = st[st.esr.isin(nodes) & (st.lat != "")].assign(k=lambda x: x.name.map(base_name))
    gb["lat"], gb["lon"] = gb.lat.astype(float), gb.lon.astype(float)
    rel = gb[gb.coord_quality.isin(["A", "B"]) | (gb.coord_quality.eq("D") & ~gb.coord_source.str.startswith("одноимённая"))]
    n_border = link_countries(adj, rel, link) + link_borders(adj, rel, link)
    return adj, st, sec, n_dup, n_name, n_border


CROSSING_SPACING_KM = 30


def link_countries(adj, g, link):
    """Пограничные переходы между каждой парой стран: все пары станций двух стран не дальше BORDER_KM,
    по одной на переход (следующая — не ближе CROSSING_SPACING_KM к уже найденной)."""
    from scipy.spatial import cKDTree
    names = g.set_index("esr")["name"]
    by = {c: d for c, d in g[g.country != ""].groupby("country")}
    n = 0
    cs = sorted(by)
    for i, a in enumerate(cs):
        ta = by[a]
        for b in cs[i + 1:]:
            if {a, b} in NO_LINK_COUNTRIES:
                continue
            tb = by[b]
            tree = cKDTree(np.radians(tb[["lat", "lon"]].values))
            dd, jj = tree.query(np.radians(ta[["lat", "lon"]].values), k=1)
            cand = sorted((float(d), k, int(j)) for k, (d, j) in enumerate(zip(dd, jj)) if d * 6371 <= BORDER_KM * 1.2)
            done = []
            for _, k, j in cand:
                ra, rb = ta.iloc[k], tb.iloc[j]
                km = haversine(ra.lat, ra.lon, rb.lat, rb.lon)
                if km > BORDER_KM or any(blocked <= {base_name(names[ra.esr]), base_name(names[rb.esr])} for blocked in NO_LINK):
                    continue
                if any(haversine(ra.lat, ra.lon, x, y) < CROSSING_SPACING_KM for x, y in done):
                    continue
                link(ra.esr, rb.esr, round(km * BORDER_FACTOR, 1))
                done.append((ra.lat, ra.lon))
                n += 1
    return n


def link_borders(adj, g, link):
    """Сети стран, не связанные одноимёнными станциями, — через ближайшую пару станций (пограничный переход).
    Пары сетей перебираются все (не только для связности): у Латвии — переходы и в Россию, и в Беларусь, и в Литву."""
    from scipy.spatial import cKDTree
    pos = g.set_index("esr")[["lat", "lon"]]
    names = g.set_index("esr")["name"]
    comp = component_map(adj)
    groups = {}
    for e, c in comp.items():
        if e in pos.index:
            groups.setdefault(c, []).append(e)
    keys = list(groups)
    trees = {c: cKDTree(np.radians(pos.loc[groups[c], ["lat", "lon"]].values)) for c in keys}
    n = 0
    for i, a in enumerate(keys):
        pa = np.radians(pos.loc[groups[a], ["lat", "lon"]].values)
        for b in keys[i + 1:]:
            dd, jj = trees[b].query(pa, k=1)
            for k in np.argsort(dd)[:50]:  # ближайшая пара; если переход запрещён — следующая
                ea, eb = groups[a][int(k)], groups[b][int(jj[k])]
                km = haversine(*pos.loc[ea], *pos.loc[eb])
                if km > BORDER_KM:
                    break
                if not any(blocked <= {base_name(names[ea]), base_name(names[eb])} for blocked in NO_LINK):
                    link(ea, eb, round(km * BORDER_FACTOR, 1))
                    n += 1
                    break
    return n


def component_map(adj):
    seen = {}
    for s0 in adj:
        if s0 in seen:
            continue
        q = [s0]
        seen[s0] = s0
        while q:
            x = q.pop()
            for y in adj[x]:
                if y not in seen:
                    seen[y] = s0
                    q.append(y)
    return seen


REUSE_KM = 400


def split_reused_codes(sec, st):
    """Один код ЕСР в участках, удалённых друг от друга (коды новых участков ТР №4 2026 совпали со старыми):
    иначе граф «телепортирует» через этот узел. Код оставляем в участках рядом с самой станцией,
    в далёких участках заменяем на отдельный узел «код@участок»."""
    pos = st[st.lat != ""].set_index("esr")[["lat", "lon"]].astype(float)
    cen = sec.join(pos, on="esr").groupby("sec")[["lat", "lon"]].median()
    sec = sec.copy()
    multi = sec.groupby("esr").sec.nunique()
    n = 0
    for e in multi[multi > 1].index:
        if not e:
            continue
        rows = sec.index[sec.esr == e]
        secs = sec.loc[rows, "sec"]
        ref = pos.loc[e].values if e in pos.index else cen.loc[secs].median().values
        for i, s_ in zip(rows, secs):
            c = cen.loc[s_]
            if c.notna().all() and haversine(ref[0], ref[1], c.lat, c.lon) > REUSE_KM:
                sec.at[i, "esr"] = f"{e}@{s_}"
                n += 1
    print(f"коды в удалённых участках разделены: {n}")
    return sec


def components(adj):
    return pd.Series(component_map(adj)).value_counts()


def dijkstra(adj, src, limit=None):
    dist, h = {src: 0.0}, [(0.0, src)]
    while h:
        d, x = heapq.heappop(h)
        if d > dist[x] or (limit and d > limit):
            continue
        for y, w in adj[x].items():
            nd = d + w
            if nd < dist.get(y, 1e18):
                dist[y] = nd
                heapq.heappush(h, (nd, y))
    return dist


def impossible_edges(adj, st):
    """Рёбра между надёжными точками, где по прямой заметно дальше, чем по тарифу, — признак склейки разных мест."""
    pos = st[st.coord_quality.isin(["A", "B"])].set_index("esr")[["lat", "lon"]].astype(float)
    bad = []
    for a in adj:
        for b, w in adj[a].items():
            if a < b and a in pos.index and b in pos.index:
                d = haversine(*pos.loc[a], *pos.loc[b])
                if d > w * 1.3 + 20:
                    bad.append((a, b, w, round(d)))
    return bad


def validate(adj, st):
    """Тарифные расстояния до транзитных пунктов (ТР №4) против кратчайшего пути по графу."""
    by_name = defaultdict(list)
    for e, n in zip(st.esr, st.name):
        if e in adj:
            by_name[base_name(n)].append(e)
    errs = []
    sample = st[(st.tr4_transit != "") & st.esr.isin(adj.keys())]
    sample = sample.sample(min(2500, len(sample)), random_state=1)
    for e, tr in zip(sample.esr, sample.tr4_transit):
        dist = dijkstra(adj, e, limit=1500)
        for part in tr.split(","):
            m = re.match(r"\s*(.+?)-(\d+)\s*$", part)
            if not m:
                continue
            cands = [dist[c] for c in by_name.get(base_name(m.group(1)), []) if c in dist]
            if cands:
                errs.append((min(cands), float(m.group(2))))
    a = np.array(errs)
    diff = np.abs(a[:, 0] - a[:, 1])
    rel = diff / np.maximum(a[:, 1], 1)
    return {"pairs": int(len(a)), "median_abs_km": round(float(np.median(diff)), 1),
            "exact_share": round(float((diff <= 1).mean() * 100), 1),
            "within_5pct": round(float((rel <= 0.05).mean() * 100), 1),
            "p90_abs_km": round(float(np.percentile(diff, 90)), 1)}


def main():
    adj, st, sec, n_dup, n_name, n_border = build()
    cnt = components(adj)
    nodes = sorted(adj)
    idx = {e: i for i, e in enumerate(nodes)}
    edges = sorted({(min(idx[a], idx[b]), max(idx[a], idx[b]), round(w, 1)) for a in adj for b, w in adj[a].items()})
    v = validate(adj, st)
    bad = impossible_edges(adj, st)
    v["impossible_edges"] = len(bad)
    for a, b, w, d in bad[:10]:
        print(f"  подозрительное ребро {a}—{b}: {w} км по тарифу, {d} км по прямой")
    out = {"nodes": nodes, "edges": [list(e) for e in edges], "check": v,
           "components": [int(x) for x in cnt.values[:10]]}
    (OUT / "rail_graph.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"узлов {len(nodes)}, рёбер {len(edges)}; стыковки: по основному коду {n_dup}, одноимённые рядом {n_name}, пограничные переходы {n_border}")
    print(f"связных частей {len(cnt)}, крупнейшие: {list(cnt.values[:8])} (в крупнейшей {cnt.iloc[0] / len(nodes):.0%})")
    print("проверка по тарифным расстояниям ТР №4:", v)


if __name__ == "__main__":
    main()

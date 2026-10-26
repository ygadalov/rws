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
NODE_KM = 30
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

    sec = sec[~sec.sec_name.str.contains(r"\(УЗК", regex=True)]  # узкоколейные линии — вагонам 1520 туда не попасть
    sec = drop_export_rows(sec)
    sec = split_reused_codes(sec, st)
    sec = drop_misplaced(sec, st)
    # внутри узлов тарифное расстояние часто 0 км («Московский узел»: Лихоборы — Кожухово 0 км) — для «ближайшего
    # предприятия» такое ребро берём по прямой между надёжными точками (до NODE_KM; дальше — ошибка точки)
    rp = st[st.coord_quality.isin(["A", "B"])].set_index("esr")[["lat", "lon"]].astype(float)
    rp = {e: (a, b) for e, a, b in zip(rp.index, rp.lat, rp.lon)}
    for _, d in sec.groupby("sec"):
        d = d.sort_values("km")
        e, km = d.esr.tolist(), d.km.astype(float).tolist()
        for k in range(1, len(e)):
            if km[k] >= km[k - 1]:
                w = km[k] - km[k - 1]
                if e[k - 1] in rp and e[k] in rp:
                    h = float(haversine(*rp[e[k - 1]], *rp[e[k]]))
                    if w == 0 and 1 < h <= NODE_KM:
                        w = round(h, 1)
                link(e[k - 1], e[k], w)
    nodes = set(adj)
    n_dup = n_name = 0
    # коды одной станции
    # (если оба кода стоят в участках и по километражу между соседями это разные места — не связываем:
    # 090204 в ТР №4 2026 — «Валка (рзд)», а по справочнику — код-дубль «Руиены» под Ригой)
    imp = implied_positions(sec, st).dropna()
    imp = imp.assign(esr=sec.loc[imp.index, "esr"]).groupby("esr").agg(lat=("lat", "median"), lon=("lon", "median"),
                                                                       gap=("gap", "min"))
    n_dup_skip = 0
    for e, d in zip(st.esr, st.dup_esr):
        if d and e in nodes and d in nodes:
            if e in imp.index and d in imp.index and haversine(imp.at[e, "lat"], imp.at[e, "lon"], imp.at[d, "lat"],
                                                               imp.at[d, "lon"]) > REUSE_KM + 0.5 * (imp.at[e, "gap"] + imp.at[d, "gap"]):
                n_dup_skip += 1
                continue
            link(e, d, 0.0)
            n_dup += 1
    print(f"связи по основному коду, противоречащие участкам, пропущены: {n_dup_skip}")
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


REUSE_KM = 25      # один код в разных участках дальше этого друг от друга — разные пункты
REUSE_FAR_KM = 400  # дальше этого от станции — отдельный узел, даже если положение в участке не вычислить
ANCHOR_GAP_KM = 30


def implied_positions(sec, st):
    """Положение каждой строки участка по километражу между ближайшими надёжными точками (A/B) того же участка,
    не считая строк с тем же кодом; gap — расстояние между опорами (чем больше, тем грубее). NaN — если опор нет."""
    pos = st[st.coord_quality.isin(["A", "B"])].set_index("esr")[["lat", "lon"]].astype(float)
    j = sec.join(pos, on="esr")
    out = pd.DataFrame(np.nan, index=sec.index, columns=["lat", "lon", "gap"])
    for _, d in j.groupby("sec"):
        km = d.km.astype(float).values
        for k, i in enumerate(d.index):
            o = d.lat.notna().values & (d.esr.values != d.esr.values[k])
            lo_, hi_ = np.where(o & (km <= km[k]))[0], np.where(o & (km >= km[k]))[0]
            a = lo_[np.argmax(km[lo_])] if len(lo_) else None
            b = hi_[np.argmin(km[hi_])] if len(hi_) else None
            if a is not None and b is not None:
                t = 0.0 if km[b] == km[a] else (km[k] - km[a]) / (km[b] - km[a])
                out.loc[i] = (d.lat.values[a] + t * (d.lat.values[b] - d.lat.values[a]),
                              d.lon.values[a] + t * (d.lon.values[b] - d.lon.values[a]), km[b] - km[a])
            elif (c := a if a is not None else b) is not None and abs(km[c] - km[k]) <= ANCHOR_GAP_KM:
                out.loc[i] = d.lat.values[c], d.lon.values[c], 2 * abs(km[c] - km[k])
    return out


def split_reused_codes(sec, st):
    """Один код ЕСР в участках, удалённых друг от друга (коды новых участков ТР №4 2026 совпали со старыми, 090204 —
    и «Валка (рзд)», и пункт под Ригой): иначе граф «телепортирует» через этот узел. В участке, с километражом
    которого точка станции не согласуется (по большинству надёжных соседей), код заменяем отдельным узлом
    «код@участок». Для кодов без надёжной точки — положение по километражу между соседями."""
    pos = st[st.coord_quality.isin(["A", "B"])].set_index("esr")[["lat", "lon"]].astype(float)
    allpos = st[st.lat != ""].set_index("esr")[["lat", "lon"]].astype(float)
    j = sec.join(pos, on="esr")
    j["km"] = j.km.astype(float)
    groups = {k: d for k, d in j.groupby("sec")}
    imp = None
    cen = sec.join(allpos, on="esr").groupby("sec")[["lat", "lon"]].median()
    sec = sec.copy()
    multi = sec.groupby("esr").sec.nunique()
    n = 0
    for e in multi[multi > 1].index:
        if not e:
            continue
        rows = sec.index[sec.esr == e]
        if e in pos.index:
            ref = pos.loc[e].values
            for i in rows:
                d = groups[sec.at[i, "sec"]]
                o = d[d.lat.notna() & (d.esr != e)]
                if len(o) < 2:
                    continue
                h = haversine(ref[0], ref[1], o.lat.values, o.lon.values)
                if (h > np.abs(o.km.values - j.at[i, "km"]) * 1.5 + 20).mean() > 0.5:
                    sec.at[i, "esr"] = f"{e}@{sec.at[i, 'sec']}"
                    n += 1
            continue
        if imp is None:
            imp = implied_positions(sec, st)
        p = imp.loc[rows].dropna()
        if len(p):
            r0 = p.gap.idxmin()
            ref, g0 = p.loc[r0, ["lat", "lon"]].values, p.at[r0, "gap"]
        else:
            ref, g0 = (allpos.loc[e].values if e in allpos.index else cen.loc[sec.loc[rows, "sec"]].median().values), None
        for i in rows:
            if i in p.index:
                far = haversine(ref[0], ref[1], p.at[i, "lat"], p.at[i, "lon"]) > REUSE_KM + 0.5 * (p.at[i, "gap"] + (g0 or 0))
            else:
                c = cen.loc[sec.at[i, "sec"]]
                far = c.notna().all() and haversine(ref[0], ref[1], c.lat, c.lon) > REUSE_FAR_KM
            if far:
                sec.at[i, "esr"] = f"{e}@{sec.at[i, 'sec']}"
                n += 1
    print(f"коды в удалённых участках разделены: {n}")
    return sec


EXPORT_RE = re.compile(r"эксп|перев|паром|перегруз|налив", re.I)


def drop_export_rows(sec):
    """Экспортные и перевалочные коды («Керчь-Порт (эксп.)», «Запорожье I (перев.)») в книге 1 часто приписаны к
    участку списком с 0 км — это не пункты линии, а условные коды станций. В цепочку участка их не включаем
    (кроме концов участка: «Вэлчинец (эксп.) — Окница» — пограничный переход); со своей станцией они связаны
    по основному коду."""
    keep = [not EXPORT_RE.search(n) or base_name(n) and base_name(n) in norm_name(sn)
            for n, sn in zip(sec.name, sec.sec_name)]
    print(f"экспортные и перевалочные коды внутри участков исключены из цепочек: {len(keep) - sum(keep)}")
    return sec[keep]


def drop_misplaced(sec, st):
    """Строки участка, чья точка не согласуется с километражом: в книге 1 к некоторым участкам приписаны чужие
    экспортные пункты с 0–3 км («Ковель — Ягодин»: Мукачево (эксп.), Вадул-Сирет (эксп.)…) — в графе это
    «телепорт» через полстраны. Строку убираем, если от большинства надёжных точек (A/B) того же участка она по прямой
    заметно дальше, чем по километражу. Линия при этом не рвётся — соседние строки связываются напрямую."""
    pos = st[st.coord_quality.isin(["A", "B", "D"])].set_index("esr")[["lat", "lon"]].astype(float)
    rel = set(st.esr[st.coord_quality.isin(["A", "B"])])
    j = sec.join(pos, on="esr")
    j["km"] = j.km.astype(float)
    drop = []
    for _, d in j[j.lat.notna()].groupby("sec"):
        if len(d) < 3:
            continue
        la, lo, km, anc = d.lat.values, d.lon.values, d.km.values, d.esr.isin(rel).values
        for k, i in enumerate(d.index):
            o = anc & (np.arange(len(d)) != k)
            if o.sum() < 2:
                continue
            h = haversine(la[k], lo[k], la[o], lo[o])
            if (h > np.abs(km[o] - km[k]) * 1.5 + 20).mean() > 0.5:
                drop.append(i)
    print(f"строки участков, не согласующиеся с километражом (чужие пункты или неверная точка), убраны: {len(drop)} "
          f"в {sec.loc[drop].sec.nunique()} участках")
    return sec.drop(index=drop)


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

# Графики для docs/Дедупликация координат ЕСР.pdf. Запуск из корня репозитория: python3 docs/dedup/charts.py <папка>
import sys, pandas as pd, numpy as np, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
OUT = sys.argv[1]
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.edgecolor": "#CBD5E1", "axes.labelcolor": "#475569", "xtick.color": "#64748B", "ytick.color": "#64748B"})
BRAND, TEAL, PINK, VIOLET, GREY = "#2457D6", "#0D9488", "#DB2777", "#8B5CF6", "#94A3B8"
QC = {"A": BRAND, "B": TEAL, "C": PINK, "D": VIOLET, "E": GREY}
QL = {"A": "A — подтверждено", "B": "B — вероятно", "C": "C — сомнительно", "D": "D — по связанному коду", "E": "E — приблизительно"}
s = pd.read_csv("data/output/stations.csv", sep=";", dtype=str, keep_default_na=False)
s = s[s.coord_quality != ""].copy(); s["lat"] = s.lat.astype(float); s["lon"] = s.lon.astype(float)
c = pd.read_csv("data/output/osm_candidates.csv", sep=";", dtype=str, keep_default_na=False)
c["lat"] = c.lat.astype(float); c["lon"] = c.lon.astype(float); c["d"] = c.dist_from_chosen_km.astype(float)

# 1. карта по классам
fig, ax = plt.subplots(figsize=(10, 4.6))
for q, sz in (("E", 1.2), ("D", 1.2), ("A", 1.0), ("B", 3), ("C", 9)):
    d = s[s.coord_quality == q]
    ax.scatter(d.lon, d.lat, s=sz, c=QC[q], lw=0, label=f"{QL[q]} ({len(d):,})".replace(",", " "), zorder={"C": 5, "B": 4}.get(q, 2))
ax.set_aspect(1.6); ax.set_xlabel("долгота"); ax.set_ylabel("широта")
h, l = ax.get_legend_handles_labels(); o = [2, 3, 4, 1, 0]
ax.legend([h[i] for i in o], [l[i] for i in o], loc="lower right", markerscale=4, frameon=False, fontsize=8)
fig.tight_layout(); fig.savefig(f"{OUT}/map.png", dpi=200); plt.close(fig)

# 2. кандидаты на код: одно место / разные места
g = c.groupby("esr").agg(n=("esr", "size"), far=("d", lambda x: (x > 5).any()))
g["k"] = g.n.clip(upper=4).map({1: "1", 2: "2", 3: "3", 4: "4–6"})
t = g.groupby(["k", "far"]).size().unstack(fill_value=0).reindex(["1", "2", "3", "4–6"])
fig, ax = plt.subplots(1, 2, figsize=(10, 3.2), gridspec_kw={"width_ratios": [1, 1.3]})
a = ax[0]; a.bar(["1 кандидат", "2 и больше"], [t.loc["1"].sum(), t.drop("1").values.sum()], color=[GREY, BRAND], width=.55)
for i, v in enumerate([t.loc["1"].sum(), t.drop("1").values.sum()]): a.text(i, v, f"{v:,}".replace(",", " "), ha="center", va="bottom")
a.set_title("Кодов ЕСР в osm2esr", fontsize=10, loc="left"); a.set_yticks([])
a = ax[1]; m = t.drop("1"); x = np.arange(len(m))
a.bar(x, m[False], color=TEAL, width=.55, label="все точки в пределах 5 км — одно место")
a.bar(x, m[True], bottom=m[False], color=PINK, width=.55, label="точки в разных местах — конфликт")
for i, (f, tr) in enumerate(zip(m[False], m[True])): a.text(i, f + tr, f"{f} + {tr}", ha="center", va="bottom", fontsize=8)
a.set_xticks(x, [f"{k} канд." for k in m.index]); a.set_yticks([]); a.legend(frameon=False, fontsize=8, loc="upper right")
a.set_title("Коды с несколькими кандидатами", fontsize=10, loc="left")
fig.tight_layout(); fig.savefig(f"{OUT}/cand.png", dpi=200); plt.close(fig)

# 3. конфликтные коды: выбранная точка и отвергнутые кандидаты
conf = g.index[g.far]
cc = c[c.esr.isin(conf)]
ch = cc[cc.chosen == "True"].set_index("esr")
rej = cc[(cc.chosen != "True") & (cc.d > 5)]
fig, ax = plt.subplots(figsize=(10, 4.6))
ax.scatter(s.lon, s.lat, s=.4, c="#E2E8F0", lw=0, zorder=1)
for r in rej.itertuples():
    p = ch.loc[r.esr]; ax.plot([p.lon, r.lon], [p.lat, r.lat], c=PINK, lw=.5, alpha=.5, zorder=2)
ax.scatter(rej.lon, rej.lat, s=7, facecolors="none", edgecolors=PINK, lw=.6, zorder=3, label=f"отвергнутый кандидат ({len(rej)})")
q = s.set_index("esr").reindex(ch.index).coord_quality
ax.scatter(ch.lon, ch.lat, s=9, c=[QC.get(v, GREY) for v in q], lw=0, zorder=4, label=f"выбранная точка ({len(ch)})")
ax.set_aspect(1.6); ax.set_xlabel("долгота"); ax.set_ylabel("широта"); ax.legend(loc="lower right", frameon=False, fontsize=8)
fig.tight_layout(); fig.savefig(f"{OUT}/conflicts.png", dpi=200); plt.close(fig)
med = rej.groupby("esr").d.max().median()
print("conf", len(conf), "rej", len(rej), "median jump km", round(med), "max", round(rej.d.max()), dict(t))

"""Случайная дислокация с договорами лизинга и запрещёнными регионами — для проверки ограничений курсирования.

Запуск: python3 scripts/make_restriction_sample.py <файл.xlsx> [число вагонов, по умолчанию 2400]
Около 2% вагонов с ограничениями ставятся в запрещённые регионы (или на запрещённую дорогу).
"""
import random, sys, pandas as pd, numpy as np
random.seed(11)
N = int(sys.argv[2]) if len(sys.argv) > 2 else 2400
st = pd.read_csv("data/output/stations.csv", sep=";", dtype=str, keep_default_na=False)
st = st[(st.lat != "") & (st.is_virtual != "True") & st.type.isin(["станция"])].reset_index(drop=True)
CONTRACTS = [("ДЛ-2024/017", "ЛК «Альфа»", "Республика Дагестан; Чеченская Республика; Республика Ингушетия; Республика Крым"),
    ("ДЛ-2023/112", "ЛК «Бета»", "Казахстан; Узбекистан; Киргизия"),
    ("ДЛ-2025/004", "ЛК «Гамма»", "Мурманская область, Республика Карелия, Архангельская область"),
    ("ДЛ-2022/089", "ЛК «Альфа»", "Забайкальский край; Амурская область; Приморский край"),
    ("ДЛ-2021/233", "ЛК «Дельта»", "Октябрьская ж.д.; Калининградская область"),
    ("Собственный парк", "—", "")]
pools = []
for c in CONTRACTS:
    forb = [x.strip().replace(" ж.д.", "") for x in c[2].replace(",", ";").split(";") if x.strip()]
    hit = st.region.apply(lambda r: any(f in r for f in forb)) | st.railway.isin(forb) if forb else pd.Series(False, index=st.index)
    pools.append((st.index[~hit].values, st.index[hit].values))
kinds = ["Полувагон", "Цистерна", "Крытый", "Платформа", "Хоппер"]
ci = np.random.default_rng(1).choice(len(CONTRACTS), N, p=[.2, .2, .15, .15, .1, .2])
rows, used = [], set()
for k in ci:
    c = CONTRACTS[k]; ok, bad = pools[k]
    i = random.choice(bad) if len(bad) and random.random() < 0.02 else random.choice(ok)
    s = st.iloc[i]
    while True:
        w = str(50000000 + random.randint(0, 49999999))
        if w not in used: used.add(w); break
    rows.append((w, random.choice(kinds), c[1], c[0], s.esr, s["name"], s.railway, c[2], random.choice(["Груженый", "Порожний"]),
                 f"{random.randint(1,22):02d}.10.2026 {random.randint(0,23):02d}:{random.randint(0,59):02d}"))
df = pd.DataFrame(rows, columns=["№ вагона", "Род ПС", "Собственник", "Договор лизинга", "Станция операции (код)", "Станция операции",
                                 "Дорога операции", "Запрещённые регионы курсирования", "Состояние", "Дата операции"])
df.to_excel(sys.argv[1], index=False)
print(len(df), df.shape)

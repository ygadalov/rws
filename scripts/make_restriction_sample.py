"""Случайная дислокация с договорами лизинга и запрещёнными регионами — для проверки модуля ограничений курсирования.

Запуск: python3 scripts/make_restriction_sample.py data/samples/дислокация_ограничения_пример.xlsx
"""
import random, sys, pandas as pd
random.seed(7)
st = pd.read_csv("data/output/stations.csv", sep=";", dtype=str, keep_default_na=False)
st = st[(st.lat != "") & (st.is_virtual != "True") & st.type.isin(["станция"])]
CONTRACTS = [  # договор, лизингодатель, запрещённые регионы (как могут прийти в дислокации)
    ("ДЛ-2024/017", "ЛК «Альфа»", "Республика Дагестан; Чеченская Республика; Республика Ингушетия; Республика Крым"),
    ("ДЛ-2023/112", "ЛК «Бета»", "Казахстан; Узбекистан; Киргизия"),
    ("ДЛ-2025/004", "ЛК «Гамма»", "Мурманская область, Республика Карелия, Архангельская область"),
    ("ДЛ-2022/089", "ЛК «Альфа»", "Забайкальский край; Амурская область; Приморский край"),
    ("Собственный парк", "—", ""),
]
def region_of(r, regs): return any(p.strip() in r.region or r.region in p for p in regs)
rows = []
kinds = ["Полувагон", "Цистерна", "Крытый", "Платформа", "Хоппер"]
for n in range(2400):
    c = random.choices(CONTRACTS, weights=[3, 3, 2, 2, 4])[0]
    forb = [x.strip() for x in c[2].replace(",", ";").split(";") if x.strip()]
    if forb and random.random() < 0.035:  # нарушитель
        pool = st[st.region.apply(lambda r: any(f in r or (f in ("Казахстан", "Узбекистан", "Киргизия") and r == f) for f in forb))]
    else:
        pool = st[~st.region.apply(lambda r: any(f in r for f in forb))] if forb else st
    s = pool.sample(1, random_state=random.randint(0, 10**9)).iloc[0]
    rows.append({"№ вагона": str(50000000 + random.randint(0, 9999999)), "Род ПС": random.choice(kinds),
                 "Собственник": c[1], "Договор лизинга": c[0], "Станция операции (код)": s.esr, "Станция операции": s["name"],
                 "Дорога операции": s.railway, "Запрещённые регионы курсирования": c[2],
                 "Состояние": random.choice(["Груженый", "Порожний"])})
df = pd.DataFrame(rows)
df.to_excel(sys.argv[1], index=False)
print(len(df), df["Договор лизинга"].value_counts().to_dict())

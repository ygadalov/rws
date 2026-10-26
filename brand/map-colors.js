// RWS 1520 — цвета карты v1.0
// Правило: синий = справочник/бренд, оранжевый = ТОЛЬКО данные пользователя.
// Категориальные цвета ниже не используют ни синий-бренд (кроме «станции»), ни оранжевый
// и (кроме приглушённых серых) дают ≥3:1 и на светлой, и на тёмной подложке.
// Меняется по теме только бренд-синий: используйте brand(dark).

const BRAND = { light: "#2457D6", dark: "#6D9BFF" };
const brand = (dark) => (dark ? BRAND.dark : BRAND.light);

const PAL = {
  teal:   "#0D9488",
  violet: "#8B5CF6",
  pink:   "#DB2777",
  lime:   "#4D7C0F",
  grey:   "#94A3B8",
  greyLt: "#CBD5E1",
};

// Обводка всех точек: отделяет маркер от воды, дорог и спутника.
const POINT_STROKE = { light: "#FFFFFF", dark: "#0F1115", width: 1.5 };

// Тип пункта. «станция» — бренд-синий (подставлять brand(dark)).
const TYPE_COLORS = {
  "станция":            "BRAND",
  "остановочный пункт": PAL.teal,
  "разъезд":            PAL.violet,
  "обгонный пункт":     PAL.pink,
  "блок-пост":          PAL.lime,   // три вида постов — один цвет
  "путевой пост":       PAL.lime,
  "пост":               PAL.lime,
  "условный пункт":     PAL.grey,   // + радиус меньше на 1–2 px
};
const typeColor = (type, dark) => {
  const c = TYPE_COLORS[type];
  return c === "BRAND" ? brand(dark) : (c || PAL.grey);
};

// Качество привязки координат A–E.
const QUALITY_COLORS = {
  A: "BRAND",      // подтверждено
  B: PAL.teal,     // вероятно
  C: PAL.pink,     // сомнительно — требует внимания
  D: PAL.violet,   // по связанному коду (было янтарным — конфликт с данными)
  E: PAL.grey,     // приблизительно
};

// Операции. Для дальтоников различие продублировано формой:
// «только пассажирские» — кольцо (fill = подложка, stroke = цвет), остальные — заливка.
const OPS_STYLES = {
  both:    { color: "BRAND",    hollow: false },
  pass:    { color: PAL.teal,   hollow: true  },
  freight: { color: PAL.pink,   hollow: false },
  none:    { color: PAL.grey,   hollow: false, radiusDelta: -1 },
  out:     { color: PAL.greyLt, hollow: false, radiusDelta: -1 },
};

// Пользовательские данные (вагоны).
const WG_COLOR = "#F97316";
const HEAT_RAMP = ["#FED976", "#FEB24C", "#FD8D3C", "#F03B20", "#BD0026"]; // без изменений

// Железнодорожные линии: база без изменений, наведение — бренд, а не оранжевый.
const RAIL_LINE = { light: "#5B6F8F", dark: "#8FA4C4" };
const RAIL_LINE_HOVER = brand; // brand(dark)

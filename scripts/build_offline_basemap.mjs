// Офлайн-подложка для дашборда: суша и границы государств Natural Earth 1:50m (public domain)
// из пакета world-atlas 2.0.2 → dashboard/vendor/basemap-offline.json (только область вокруг сети 1520).
// Запуск: npm i world-atlas@2.0.2 topojson-client@3.1.0 && node scripts/build_offline_basemap.mjs
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { feature, mesh } from "topojson-client";

const require = createRequire(import.meta.url);
const atlas = name => JSON.parse(fs.readFileSync(require.resolve(`world-atlas/${name}`), "utf8"));
const OUT = path.join(path.dirname(new URL(import.meta.url).pathname), "..", "dashboard", "vendor", "basemap-offline.json");
const BBOX = [5, 20, 180, 82];  // запад, юг, восток, север

// кольца, пересекающие 180-й меридиан (Чукотка), «разворачиваем» за 180°, иначе на карте горизонтальные полосы
const unwrapLine = line => {
  if (!line.some(([x]) => x < -90) || !line.some(([x]) => x > 90)) return line;
  return line.map(([x, y]) => [x < -90 ? x + 360 : x, y]);
};
const unwrapPoly = poly => poly.map(unwrapLine);
const round = c => typeof c[0] === "number" ? [Math.round(c[0] * 1e3) / 1e3, Math.round(c[1] * 1e3) / 1e3] : c.map(round);
const flat = c => typeof c[0] === "number" ? [c] : c.flatMap(flat);
const hits = coords => flat(coords).some(([x, y]) => (x >= BBOX[0] || x <= -170) && x <= BBOX[2] + 1 && y >= BBOX[1] && y <= BBOX[3]);

const land = feature(atlas("land-50m.json"), atlas("land-50m.json").objects.land);
const polys = land.features.flatMap(f => f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates)
  .filter(hits).map(unwrapPoly).map(round);
const c = atlas("countries-50m.json");
const borders = mesh(c, c.objects.countries, (a, b) => a !== b).coordinates.filter(hits).map(unwrapLine).map(round);

const out = { type: "FeatureCollection", features: [
  { type: "Feature", properties: { k: "land" }, geometry: { type: "MultiPolygon", coordinates: polys } },
  { type: "Feature", properties: { k: "border" }, geometry: { type: "MultiLineString", coordinates: borders } },
] };
fs.writeFileSync(OUT, JSON.stringify(out));
console.log(`${OUT}: ${polys.length} полигонов суши, ${borders.length} линий границ, ${(fs.statSync(OUT).size / 1e6).toFixed(2)} МБ`);

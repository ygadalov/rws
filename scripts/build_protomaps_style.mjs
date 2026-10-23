// Стиль для своей карты из файла PMTiles (схема Protomaps basemap v4): слои @protomaps/basemaps 5.7.2
// с русскими подписями → dashboard/vendor/protomaps-style.json ({ light: [...], dark: [...] }).
// Запуск: npm i @protomaps/basemaps@5.7.2 && node scripts/build_protomaps_style.mjs [путь к json]
import fs from "node:fs";
import { layers, namedFlavor } from "@protomaps/basemaps";

const out = process.argv[2] || new URL("../dashboard/vendor/protomaps-style.json", import.meta.url).pathname;
// иконки POI (спрайты) не встраиваем — слои с иконками убираем, остальное как есть
const noIcons = ls => ls.filter(l => !JSON.stringify(l.layout || {}).includes("icon-image"));
const style = {
  light: noIcons(layers("protomaps", namedFlavor("light"), { lang: "ru" })),
  dark: noIcons(layers("protomaps", namedFlavor("dark"), { lang: "ru" })),
};
fs.writeFileSync(out, JSON.stringify(style));
console.log(`${out}: ${style.light.length} слоёв, ${(fs.statSync(out).size / 1e3).toFixed(0)} КБ`);

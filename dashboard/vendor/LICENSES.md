# Сторонние компоненты, встроенные в дашборд (dashboard/rws1520_v*.html)

| Компонент | Версия | Лицензия | Файл |
|---|---|---|---|
| MapLibre GL JS | 4.7.1 | BSD-3-Clause | `maplibre-gl.js`, `maplibre-gl.css`, `LICENSE-maplibre-gl.txt` |
| SheetJS (xlsx) | 0.18.5 | Apache-2.0 | `xlsx.full.min.js`, `LICENSE-xlsx.txt` |
| PMTiles (JS) | 4.5.0 | BSD-3-Clause | `pmtiles.js` |
| @protomaps/basemaps (стиль) | 5.7.2 | BSD-3-Clause | `protomaps-style.json` (сгенерирован `scripts/build_protomaps_style.mjs`) |
| Шрифт Noto Sans (глифы) | basemaps-assets | SIL Open Font License 1.1 | `glyphs/`, `glyphs/OFL.txt` |
| Natural Earth | 4.1.0 / 5.1.2 | общественное достояние | `basemap-offline.json`, `basemap-labels.json` |
| world-atlas | 2.0.2 | ISC | источник для `basemap-offline.json` |

Данные своей карты (файл PMTiles, который пользователь открывает в дашборде) в сборку не входят;
сборки Protomaps основаны на OpenStreetMap — © участники OpenStreetMap, лицензия ODbL.

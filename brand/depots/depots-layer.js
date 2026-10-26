/* RWS 1520 — слой «Вагоноремонтные депо» v1.0
   Встраивается в <script> файла rws1520.html (после объявления map, S, BY_ESR, W, state).
   Использует из проекта: map, maplibregl, S, BY_ESR, W, state, store, $, esc, fmt,
   selectStation, updateLabels, renderLegend. Порядок вставки — см. DEPOTS.md, раздел 4. */

/* ---------- 1. Данные ----------
   Сформировать из depots.csv. lat/lon можно оставить null — тогда депо
   ставится на станцию примыкания (по коду ЕСР) со смещением значка. */
const DEPOTS = [
  // { id: "d1", name: "ВРД [название]", operator: "[владелец]", esr: "000000", lat: null, lon: null, note: "" },
];

let showDepots = store.get("depots") !== "0";

/* ---------- 2. Значки ----------
   Графитовая плашка с гаечным ключом (zoom ≥ 6) и квадрат 8 px (zoom < 6).
   Светлый вариант — для светлой подложки, тёмный — для тёмной и спутника. */
const DEPOT_WRENCH = ink => `<g fill="none" stroke="${ink}" stroke-width="2.6" stroke-linecap="round"><path d="M13.77 5.62A3.6 3.6 0 1 0 18.38 10.23"/><path d="M12.45 11.55L6 18"/></g>`;
const DEPOT_ICON = (fill, ink) => `<svg xmlns="http://www.w3.org/2000/svg" width="26" height="26" viewBox="0 0 26 26"><rect x="1" y="1" width="24" height="24" rx="6" fill="${fill}" stroke="${ink}" stroke-width="2"/><g transform="translate(4.5 4.5) scale(0.7083)">${DEPOT_WRENCH(ink)}</g></svg>`;
const DEPOT_ICON_SM = (fill, ink) => `<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10"><rect x="1" y="1" width="8" height="8" rx="2" fill="${fill}" stroke="${ink}" stroke-width="1.5"/></svg>`;
const DEPOT_ICONS = {
  "depot-light":    [DEPOT_ICON("#15181D", "#FFFFFF"), 26],
  "depot-dark":     [DEPOT_ICON("#E9ECF1", "#0F1115"), 26],
  "depot-sm-light": [DEPOT_ICON_SM("#15181D", "#FFFFFF"), 10],
  "depot-sm-dark":  [DEPOT_ICON_SM("#E9ECF1", "#0F1115"), 10],
};
// Растеризуем один раз при загрузке страницы; pixelRatio 2 — чётко на ретине.
const depotImagesReady = Promise.all(Object.entries(DEPOT_ICONS).map(([id, [svg, size]]) => new Promise((res, rej) => {
  const img = new Image(size * 2, size * 2);
  img.onload = () => res([id, img]); img.onerror = rej;
  img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
})));

/* ---------- 3. Геометрия ---------- */
const depotStation = d => d.esr == null || d.esr === "" ? null
  : BY_ESR.get(d.esr) ?? BY_ESR.get(String(d.esr)) ?? BY_ESR.get(Number(d.esr)) ?? null;

function depotFeatures() {
  const pts = [], links = [];
  for (const d of DEPOTS) {
    const st = depotStation(d);
    const has = Number.isFinite(d.lat) && Number.isFinite(d.lon);
    if (!has && !(st && Number.isFinite(st.lat))) { console.warn("Депо без координат и станции:", d.id, d.name); continue; }
    const coord = has ? [d.lon, d.lat] : [st.lon, st.lat];
    pts.push({ type: "Feature", id: pts.length, geometry: { type: "Point", coordinates: coord },
      properties: { id: d.id, approx: has ? 0 : 1, si: st ? st.i : -1 } });
    // Пунктир «подъездной путь» — только когда у депо свои координаты.
    if (has && st && Number.isFinite(st.lat))
      links.push({ type: "Feature", geometry: { type: "LineString", coordinates: [[st.lon, st.lat], coord] }, properties: {} });
  }
  return { pts: { type: "FeatureCollection", features: pts }, links: { type: "FeatureCollection", features: links } };
}

/* ---------- 4. Слои ----------
   Вызывать в конце addStationLayers(): addDepotLayers(dark);
   (там переменная dark = basemap === "dark" || basemap === "satellite"). */
async function addDepotLayers(dark) {
  const imgs = await depotImagesReady;
  if (!map.getLayer("sel-pt") || map.getLayer("depots")) return; // стиль успел смениться или слой уже есть
  for (const [id, img] of imgs) if (!map.hasImage(id)) map.addImage(id, img, { pixelRatio: 2 });
  const vis = showDepots ? "visible" : "none";
  const fc = depotFeatures();
  map.addSource("depot-links", { type: "geojson", data: fc.links });
  map.addLayer({ id: "depot-links", type: "line", source: "depot-links", minzoom: 8,
    layout: { visibility: vis, "line-cap": "round" },
    paint: { "line-color": dark ? "#8FA4C4" : "#5B6F8F", "line-width": 1.5, "line-dasharray": [2, 1.5] } }, "st");
  map.addSource("depots", { type: "geojson", data: fc.pts });
  map.addLayer({ id: "depots", type: "symbol", source: "depots",
    layout: {
      visibility: vis,
      "icon-image": ["step", ["zoom"], dark ? "depot-sm-dark" : "depot-sm-light", 6, dark ? "depot-dark" : "depot-light"],
      // < 6: квадрат 8 px; 6–9: плашка 20 px; ≥ 10: 26 px
      "icon-size": ["interpolate", ["linear"], ["zoom"], 3, 0.8, 5.99, 1, 6, 0.77, 10, 1],
      // Депо без своих координат сдвигаем с точки станции, чтобы не закрывать её
      "icon-offset": ["case", ["==", ["get", "approx"], 1], ["literal", [14, 14]], ["literal", [0, 0]]],
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
    } }, "sel-pt");   // поверх станций и вагонов, под выделением
  updateLabels();
}

/* ---------- 5. Клик и наведение ---------- */
const depotPopup = new maplibregl.Popup({ closeButton: true, maxWidth: "300px", offset: 16 });

function openDepot(f) {
  const d = DEPOTS.find(x => x.id === f.properties.id); if (!d) return;
  const st = f.properties.si >= 0 ? S[f.properties.si] : null;
  const n = st && W.loaded && W.agg ? W.agg.by.get(st.i)?.n : 0;
  depotPopup.setLngLat(f.geometry.coordinates).setHTML(`<div class="depot-card">
    <div class="depot-card-t">${esc(d.name)}</div>
    ${d.operator ? `<div class="depot-card-m">${esc(d.operator)}</div>` : ""}
    ${st ? `<button class="link-btn" data-depot-st="${st.i}">Станция примыкания: ${esc(st.name)} · <span class="mono">${esc(String(st.esr))}</span></button>` : ""}
    ${n ? `<div class="depot-card-m">На станции по вашим данным: <b class="mono">${fmt(n)}</b></div>` : ""}
    ${f.properties.approx ? `<div class="depot-card-m">Положение приблизительное — по станции примыкания</div>` : ""}
    ${d.note ? `<div class="depot-card-m">${esc(d.note)}</div>` : ""}
  </div>`).addTo(map);
}
document.addEventListener("click", e => {
  const b = e.target.closest("[data-depot-st]"); if (!b) return;
  depotPopup.remove(); selectStation(S[+b.dataset.depotSt], { fly: true });
});
map.on("mouseenter", "depots", () => { map.getCanvas().style.cursor = "pointer"; });
map.on("mouseleave", "depots", () => { map.getCanvas().style.cursor = ""; });

/* Вставить В НАЧАЛО обработчика map.on("click", e => { ... }) сразу после
   строки `const pad = 6, p = e.point;`:

   const dp = map.getLayer("depots") && showDepots
     ? map.queryRenderedFeatures([[p.x - 10, p.y - 10], [p.x + 10, p.y + 10]], { layers: ["depots"] }) : [];
   if (dp.length) return openDepot(dp[0]);
*/

/* ---------- 6. Подписи и бейдж данных ----------
   Вызвать первой строкой в updateLabels(): updateDepotMarkers();
   Подписи — DOM, как у станций (кириллица не зависит от шрифтов подложки). */
let depotMarkers = [];
function updateDepotMarkers() {
  depotMarkers.forEach(m => m.remove()); depotMarkers = [];
  if (!showDepots || !map.getLayer("depots")) return;
  const z = map.getZoom();
  const wg = state.tab === "wagons" && W.loaded && W.agg;
  if (z < 6 || (!wg && z < 10)) return;
  for (const f of map.queryRenderedFeatures({ layers: ["depots"] }).slice(0, 300)) {
    const d = DEPOTS.find(x => x.id === f.properties.id); if (!d) continue;
    const n = wg && f.properties.si >= 0 ? (W.agg.by.get(f.properties.si)?.n || 0) : 0;
    const label = z >= 10 ? d.name : "";
    if (!n && !label) continue;
    const el = document.createElement("div"); el.className = "depot-label";
    if (n) { const b = document.createElement("span"); b.className = "depot-badge mono"; b.textContent = fmt(n); el.append(b); }
    if (label) { const t = document.createElement("span"); t.className = "depot-name"; t.textContent = label; el.append(t); }
    const ox = (f.properties.approx ? 14 : 0) + (z >= 10 ? 16 : 13), oy = f.properties.approx ? 14 : 0;
    depotMarkers.push(new maplibregl.Marker({ element: el, anchor: "left", offset: [ox, oy] }).setLngLat(f.geometry.coordinates).addTo(map));
  }
}

/* ---------- 7. Переключатель в панели «Слои» ---------- */
$("depotsToggle").checked = showDepots;
$("depotsToggle").addEventListener("change", e => {
  showDepots = e.target.checked; store.set("depots", showDepots ? "1" : "0");
  for (const id of ["depots", "depot-links"]) if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", showDepots ? "visible" : "none");
  if (!showDepots) depotPopup.remove();
  updateLabels(); renderLegend();
});

/* ---------- 8. Строка легенды ----------
   В renderLegend(), в ветке станций, после присваивания $("legendItems").innerHTML = ...:
   if (showDepots && DEPOTS.length) $("legendItems").insertAdjacentHTML("beforeend", depotLegendItem());
*/
const depotLegendItem = () =>
  `<div class="legend-item"><span class="dot dot-depot"></span><span class="t">Вагоноремонтное депо</span><span class="c mono">${fmt(DEPOTS.length)}</span></div>`;

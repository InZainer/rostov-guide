// Карта на Leaflet: найденные места пронумерованы так же, как карточки в чате.

import { fmtDistance, h } from "./ui.js";

const ROSTOV = [47.2357, 39.7015];
// Тайлы самого OpenStreetMap: данные справочника тоже из OSM. Тёмная тема — CSS-фильтром
// на слое тайлов (.map-dark в app.css), отдельный тёмный сервер тайлов не нужен.
const TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">участники OpenStreetMap</a>';

function pinIcon(label, cls = "") {
  const pin = h("div", { class: `pin ${cls}` }, h("span", { text: label }));
  return window.L.divIcon({ className: "pin-wrap", html: pin.outerHTML, iconSize: [28, 28], iconAnchor: [14, 34], popupAnchor: [0, -30] });
}

function popupFor(card) {
  const lines = [h("b", { text: card.name }), h("div", { text: card.kind_label })];
  if (card.address) lines.push(h("div", { text: card.address }));
  if (card.hours_label) lines.push(h("div", { text: card.hours_label }));
  if (card.distance_m !== null && card.distance_m !== undefined) lines.push(h("div", { text: fmtDistance(card.distance_m) }));
  return h("div", {}, ...lines);
}

export class PlaceMap {
  constructor(el, { onPick, onSelect } = {}) {
    this.el = el;
    this.onPick = onPick;
    this.onSelect = onSelect;
    this.markers = new Map();
    this.picking = false;
    this.ready = false;
  }

  init(dark) {
    if (!window.L) {
      this.el.append(h("div", { class: "map-fallback", text: "Карта не загрузилась (нет доступа к CDN). Чат работает и без неё." }));
      return;
    }
    const L = window.L;
    this.map = L.map(this.el, { zoomControl: true, attributionControl: true }).setView(ROSTOV, 12);
    this.tiles = L.tileLayer(TILES, { attribution: ATTRIBUTION, maxZoom: 19 }).addTo(this.map);
    this.setTheme(dark);
    this.layer = L.layerGroup().addTo(this.map);
    this.map.on("click", (e) => {
      if (!this.picking) return;
      this.stopPick();
      this.onPick && this.onPick(e.latlng.lat, e.latlng.lng);
    });
    this.ready = true;
  }

  setTheme(dark) {
    this.el.classList.toggle("map-dark", dark);
  }

  invalidate() {
    if (this.map) setTimeout(() => this.map.invalidateSize(), 260);
  }

  showPlaces(cards, { origin = null, fit = true } = {}) {
    if (!this.ready) return;
    const L = window.L;
    this.layer.clearLayers();
    this.markers.clear();
    const bounds = [];
    cards.forEach((card, i) => {
      const marker = L.marker([card.lat, card.lon], { icon: pinIcon(String(i + 1)), riseOnHover: true, title: card.name })
        .bindPopup(popupFor(card));
      marker.on("click", () => this.onSelect && this.onSelect(card.id));
      marker.addTo(this.layer);
      this.markers.set(card.id, { marker, index: i + 1 });
      bounds.push([card.lat, card.lon]);
    });
    if (origin && origin.lat) {
      L.marker([origin.lat, origin.lon], { icon: pinIcon("●", "origin"), title: origin.label, zIndexOffset: -100 })
        .bindPopup(h("div", {}, h("b", { text: "Точка отсчёта" }), h("div", { text: origin.label })))
        .addTo(this.layer);
      bounds.push([origin.lat, origin.lon]);
    }
    if (this.userLatLng) bounds.push(this.userLatLng);
    if (fit && bounds.length) {
      this.map.flyToBounds(bounds, { padding: [48, 48], maxZoom: 16, duration: 0.6 });
    }
  }

  highlight(id, on) {
    const entry = this.markers.get(id);
    if (!entry) return;
    entry.marker.setIcon(pinIcon(String(entry.index), on ? "hl" : ""));
    entry.marker.setZIndexOffset(on ? 1000 : 0);
  }

  focus(id) {
    const entry = this.markers.get(id);
    if (!entry) return;
    this.map.flyTo(entry.marker.getLatLng(), Math.max(this.map.getZoom(), 16), { duration: 0.5 });
    setTimeout(() => entry.marker.openPopup(), 520);
  }

  setUser(location) {
    if (!this.ready) return;
    const L = window.L;
    if (this.userMarker) { this.userMarker.remove(); this.userMarker = null; }
    this.userLatLng = null;
    if (!location) return;
    this.userLatLng = [location.lat, location.lon];
    this.userMarker = L.marker(this.userLatLng, { icon: pinIcon("я", "me"), zIndexOffset: 500, title: "Вы здесь" })
      .bindPopup(h("div", {}, h("b", { text: "Вы здесь" }), h("div", { text: location.label || "" })))
      .addTo(this.map);
  }

  startPick() {
    this.picking = true;
    this.el.parentElement.classList.add("map-picking");
  }

  stopPick() {
    this.picking = false;
    this.el.parentElement.classList.remove("map-picking");
  }
}

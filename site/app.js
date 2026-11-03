/* Greater Orlando Transit Map: plain Leaflet, no build step. */
(async function () {
  const STOP_ZOOM = 14;
  const getJSON = (url) => fetch(url).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  const [config, manifest] = await Promise.all([getJSON('data/config.json'), getJSON('data/manifest.json')]);
  if (!config) {
    document.getElementById('layers').textContent = 'The map data has not been built yet.';
    return;
  }
  const built = (manifest && manifest.layers) || {};

  const map = L.map('map', { preferCanvas: true, zoomControl: true }).setView(config.region.center, config.region.zoom);
  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
  const renderer = L.canvas({ padding: 0.5 });

  // ---------- popups
  const routePopup = (p) => {
    const lines = [`<h4>${esc(p.name)}</h4>`];
    if (p.runs) lines.push(`<p><b>Runs:</b> ${esc(p.runs)}</p>`);
    (p.hours || []).forEach((h) => lines.push(`<p>${esc(h)}</p>`));
    if (p.every) lines.push(`<p>About every ${p.every} minutes on weekdays.</p>`);
    lines.push('<p class="src">Hours are first bus out to last bus in.</p>');
    return lines.join('');
  };
  const stopPopup = (p, feedName) => {
    const r = (p.routes || []).join(', ');
    return `<h4>${esc(p.name)}</h4><p><b>${esc(feedName)}:</b> ${esc(r)}</p>`;
  };
  const infoPopup = (p) =>
    `<h4>${esc(p.name)}</h4>` + (p.lines || []).map((l) => `<p>${esc(l)}</p>`).join('') + (p.source ? `<p class="src">Source: ${esc(p.source)}</p>` : '');

  // ---------- layer list UI
  const box = document.getElementById('layers');
  const sections = {};
  for (const title of ['Buses and trolleys', 'Trains', 'On-call rides', 'Robotaxis']) {
    const wrap = document.createElement('div');
    wrap.hidden = true;
    wrap.innerHTML = `<h3>${title}</h3>`;
    box.appendChild(wrap);
    sections[title] = wrap;
  }
  const section = (title) => { sections[title].hidden = false; return sections[title]; };
  const addToggle = (title, label, sub, color, layer, on = true, zone = false) => {
    const wrap = section(title);
    const row = document.createElement('label');
    row.className = 'layer';
    row.innerHTML = `<input type="checkbox" ${on ? 'checked' : ''}><span class="swatch${zone ? ' zone' : ''}" style="background:${color}"></span><span>${esc(label)}${sub ? `<small>${esc(sub)}</small>` : ''}</span>`;
    const cb = row.querySelector('input');
    cb.addEventListener('change', () => (cb.checked ? layer.addTo(map) : map.removeLayer(layer)));
    if (on) layer.addTo(map);
    wrap.appendChild(row);
  };
  const addSoon = (title, label, why) => {
    const wrap = section(title);
    const p = document.createElement('p');
    p.className = 'soon';
    p.textContent = `${label}: coming soon${why ? ` (${why})` : ''}`;
    wrap.appendChild(p);
  };

  // ---------- stops only when zoomed in
  const stopLayers = [];
  const syncStops = () => {
    const show = map.getZoom() >= STOP_ZOOM;
    document.getElementById('stop-hint').style.display = show ? 'none' : '';
    stopLayers.forEach(({ layer, parent }) => {
      const want = show && map.hasLayer(parent);
      if (want && !map.hasLayer(layer)) layer.addTo(map);
      if (!want && map.hasLayer(layer)) map.removeLayer(layer);
    });
  };
  let pending = false;
  const scheduleSync = () => { if (!pending) { pending = true; setTimeout(() => { pending = false; syncStops(); }, 50); } };
  map.on('zoomend', scheduleSync);
  box.addEventListener('change', scheduleSync);

  // ---------- GTFS feeds
  for (const feed of config.feeds) {
    const title = feed.mode === 'train' ? 'Trains' : 'Buses and trolleys';
    if (!built[feed.id]) {
      addSoon(title, feed.name, feed.todo ? 'data link needed' : '');
      continue;
    }
    const [routes, stops] = await Promise.all([getJSON(`data/${feed.id}-routes.geojson`), getJSON(`data/${feed.id}-stops.geojson`)]);
    if (!routes) continue;
    const groups = [{ id: feed.id, name: feed.name, sub: feed.area, color: feed.color }].concat(
      (feed.groups || []).map((g) => ({ id: g.id, name: g.name, sub: '', color: feed.color }))
    );
    for (const g of groups) {
      const feats = routes.features.filter((f) => f.properties.group === g.id);
      if (!feats.length) continue;
      const parent = L.featureGroup();
      L.geoJSON({ type: 'FeatureCollection', features: feats }, {
        renderer,
        style: (f) => ({ color: f.properties.color || feed.color, weight: feed.mode === 'train' ? 6 : 4, opacity: 0.85 }),
        onEachFeature: (f, l) => l.bindPopup(routePopup(f.properties)),
      }).addTo(parent);
      addToggle(title, g.name, g.sub, g.color, parent, true);
      if (g.id === feed.id && stops) {
        const stopLayer = L.geoJSON(stops, {
          renderer,
          pointToLayer: (f, ll) => L.circleMarker(ll, { renderer, radius: feed.mode === 'train' ? 8 : 5, color: '#1a1a1a', weight: 2, fillColor: '#fff', fillOpacity: 1 }),
          onEachFeature: (f, l) => l.bindPopup(stopPopup(f.properties, feed.mode === 'train' ? 'Lines' : 'Bus routes')),
        });
        stopLayers.push({ layer: stopLayer, parent });
      }
    }
  }

  // ---------- zones (Scout, NeighborLink)
  for (const z of config.zones || []) {
    if (!built[z.id]) { addSoon('On-call rides', z.name); continue; }
    const gj = await getJSON(`data/${z.id}-zones.geojson`);
    if (!gj) continue;
    const layer = L.geoJSON(gj, {
      renderer,
      style: () => ({ color: z.color, weight: 2, fillColor: z.color, fillOpacity: 0.18 }),
      onEachFeature: (f, l) => l.bindPopup(infoPopup(f.properties)),
    });
    addToggle('On-call rides', z.name, '', z.color, layer, true, true);
  }

  // ---------- hand-made layers
  for (const m of config.manual || []) {
    const gj = built[m.id] ? await getJSON(`data/${m.id}.geojson`) : null;
    const title = m.id === 'robotaxi' ? 'Robotaxis' : 'On-call rides';
    if (!gj || !gj.features || !gj.features.length) { addSoon(title, m.name); continue; }
    const layer = L.geoJSON(gj, {
      renderer,
      style: (f) => (f.geometry.type === 'Point' ? {} : { color: f.properties.color || m.color, weight: 2, dashArray: '6 6', fillColor: f.properties.color || m.color, fillOpacity: 0.12 }),
      pointToLayer: (f, ll) => L.circleMarker(ll, { renderer, radius: 9, color: '#fff', weight: 3, fillColor: m.color, fillOpacity: 1 }),
      onEachFeature: (f, l) => l.bindPopup(infoPopup(f.properties)),
    });
    addToggle(title, m.name, '', m.color, layer, true, m.id === 'robotaxi');
  }

  syncStops();

  // ---------- extras
  if (manifest && manifest.built) document.getElementById('updated').textContent = `Map data last updated ${manifest.built}.`;
  document.getElementById('locate').addEventListener('click', () => {
    map.locate({ setView: true, maxZoom: 16 });
  });
  map.on('locationfound', (e) => L.circle(e.latlng, { radius: Math.min(e.accuracy, 300), color: '#0b5cad' }).addTo(map).bindPopup('You are about here.').openPopup());
  map.on('locationerror', () => alert('Your location could not be found. Check that location is turned on for this browser.'));
  const panel = document.getElementById('panel');
  const tbtn = document.getElementById('toggle-panel');
  tbtn.addEventListener('click', () => {
    const hide = !panel.classList.contains('hidden');
    panel.classList.toggle('hidden', hide);
    tbtn.textContent = hide ? 'Show the list' : 'Hide the list';
    tbtn.setAttribute('aria-expanded', String(!hide));
    setTimeout(() => map.invalidateSize(), 50);
  });
})();

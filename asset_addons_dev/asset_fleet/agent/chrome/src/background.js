// Asset Location (IT): every few minutes ask Chrome for the position (Chrome's
// own location service - nearby Wi-Fi, no GPS needed) and send it to Odoo.
// Configuration (server, serial, token) comes from Chrome policy (managed storage).
const OFFSCREEN = 'offscreen.html';

async function config() {
  try { return await chrome.storage.managed.get(null); } catch (e) { return {}; }
}

async function schedule() {
  const cfg = await config();
  const minutes = Math.max(5, Number(cfg.interval || 10));
  await chrome.alarms.create('report', {delayInMinutes: 0.5, periodInMinutes: minutes});
}

async function ensureOffscreen() {
  if (chrome.offscreen.hasDocument && await chrome.offscreen.hasDocument()) return;
  await chrome.offscreen.createDocument({
    url: OFFSCREEN, reasons: ['GEOLOCATION'], justification: 'Laptop location for the IT asset register',
  });
}

async function position() {
  await ensureOffscreen();
  return await chrome.runtime.sendMessage({target: 'offscreen', type: 'get-location'});
}

async function report() {
  const cfg = await config();
  if (!cfg.server || !cfg.serial) return;
  let pos;
  try { pos = await position(); } catch (e) { pos = {error: String(e)}; }
  const url = cfg.server.replace(/\/$/, '') + '/api/asset/location/browser?db=' + encodeURIComponent(cfg.db || '');
  const body = {serial_number: cfg.serial, token: cfg.token || ''};
  if (pos && !pos.error) Object.assign(body, pos); else body.error = (pos && pos.error) || 'no position';
  try {
    await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  } catch (e) { /* offline - next round */ }
}

chrome.runtime.onInstalled.addListener(schedule);
chrome.runtime.onStartup.addListener(schedule);
chrome.storage.onChanged.addListener((changes, area) => { if (area === 'managed') schedule(); });
chrome.alarms.onAlarm.addListener((a) => { if (a.name === 'report') report(); });

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.target !== 'offscreen' || msg.type !== 'get-location') return false;
  navigator.geolocation.getCurrentPosition(
    (p) => sendResponse({latitude: p.coords.latitude, longitude: p.coords.longitude, accuracy: p.coords.accuracy}),
    (e) => sendResponse({error: e.message || ('geolocation error ' + e.code)}),
    {enableHighAccuracy: true, timeout: 30000, maximumAge: 300000});
  return true;
});


const fs = require("fs");

const address = process.env.SOLIS_DEBUGGER_ADDRESS || "127.0.0.1:9225";
const stationUrl = process.env.SOLIS_STATION_URL || "https://www.soliscloud.com/station?glyun_vue2=%2F%23%2Fstation";
const outputFile = process.env.SOLIS_CAPTURE_OUTPUT || "/Users/sushil/Documents/GOODWE/solis_network_capture.json";
const waitMs = Number(process.env.SOLIS_CAPTURE_MS || "45000");

async function getJson(path) {
  const response = await fetch(`http://${address}${path}`);
  if (!response.ok) throw new Error(`${path} returned ${response.status}`);
  return response.json();
}

function cdpConnect(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    let nextId = 1;
    const pending = new Map();
    const events = [];

    ws.onopen = () => {
      resolve({
        events,
        send(method, params = {}) {
          const id = nextId++;
          ws.send(JSON.stringify({ id, method, params }));
          return new Promise(resolveCommand => pending.set(id, resolveCommand));
        },
        close() {
          ws.close();
        },
      });
    };
    ws.onerror = error => reject(error);
    ws.onmessage = message => {
      const payload = JSON.parse(message.data);
      if (payload.id && pending.has(payload.id)) {
        pending.get(payload.id)(payload);
        pending.delete(payload.id);
      } else if (payload.method) {
        events.push(payload);
      }
    };
  });
}

function interestingUrl(url) {
  return /\/api\/|station|plant|inverter|energy|power|epc|overview/i.test(url || "");
}

(async () => {
  console.log(`Checking Chrome debug port at http://${address}/json/version`);
  const version = await getJson("/json/version");
  console.log(`Connected to ${version.Browser || "Chrome"}`);

  const tabs = await getJson("/json/list");
  let tab = tabs.find(item => /soliscloud/i.test(item.url || "")) || tabs[0];
  if (!tab) throw new Error("No Chrome tab found");
  console.log(`Using tab: ${tab.title || ""} ${tab.url || ""}`);

  const cdp = await cdpConnect(tab.webSocketDebuggerUrl);
  const requestById = {};
  const responses = [];

  cdp.events.push = new Proxy(cdp.events.push, {
    apply(target, thisArg, args) {
      const event = args[0];
      if (event.method === "Network.requestWillBeSent") {
        requestById[event.params.requestId] = event.params.request;
      }
      if (event.method === "Network.responseReceived") {
        const response = event.params.response || {};
        if (interestingUrl(response.url)) {
          responses.push({
            requestId: event.params.requestId,
            url: response.url,
            status: response.status,
            method: requestById[event.params.requestId]?.method,
            requestPostData: requestById[event.params.requestId]?.postData,
            mimeType: response.mimeType,
          });
        }
      }
      return Reflect.apply(target, thisArg, args);
    }
  });

  await cdp.send("Network.enable");
  await cdp.send("Page.enable");
  await cdp.send("Runtime.enable");
  console.log(`Navigating to ${stationUrl}`);
  await cdp.send("Page.navigate", { url: stationUrl });
  console.log(`Waiting ${Math.round(waitMs / 1000)} seconds for SolisCloud API calls...`);
  await new Promise(resolve => setTimeout(resolve, waitMs));

  for (const response of responses) {
    try {
      const body = await cdp.send("Network.getResponseBody", { requestId: response.requestId });
      response.body = body.result && body.result.base64Encoded
        ? Buffer.from(body.result.body, "base64").toString("utf8")
        : body.result?.body;
    } catch (error) {
      response.bodyError = String(error);
    }
  }

  const pageResult = await cdp.send("Runtime.evaluate", {
    expression: `JSON.stringify({
      title: document.title,
      url: location.href,
      bodyText: document.body.innerText.slice(0, 5000),
      localStorage: Object.fromEntries(Object.entries(localStorage).filter(([key]) => !/pass|psw|pwd|secret/i.test(key))),
      sessionStorageKeys: Object.keys(sessionStorage),
      resources: performance.getEntriesByType("resource").map(entry => entry.name)
    })`,
    returnByValue: true,
  });

  const payload = {
    captured_at: new Date().toISOString(),
    chrome: version,
    tab: { id: tab.id, title: tab.title, url: tab.url },
    page: JSON.parse(pageResult.result?.result?.value || "{}"),
    responses,
  };
  fs.writeFileSync(outputFile, JSON.stringify(payload, null, 2));
  console.log(`Saved Solis DevTools capture to ${outputFile}`);
  console.log(`Captured ${responses.length} interesting responses`);
  cdp.close();
})().catch(error => {
  console.error(error.stack || String(error));
  process.exit(1);
});

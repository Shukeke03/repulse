export async function api(path, options = {}) {
  const method = options.method || 'GET';
  const headers = {'Content-Type': 'application/json', ...options.headers};
  if (method !== 'GET') headers['Idempotency-Key'] ||= crypto.randomUUID();
  const response = await fetch(`/api${path}`, {...options, method, headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body)});
  let data;
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) {
    const detail = data.detail || data;
    const message = typeof detail === 'string' ? detail : detail.message || data.message || `请求失败（${response.status}）`;
    const error = new Error(message); error.code = detail.code; error.status = response.status; error.detail = detail;
    throw error;
  }
  return data;
}

export function socket(path, onData, onState) {
  let ws, retry = 0, timer, closed = false;
  function connect() {
    if (closed) return;
    ws = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api${path}`);
    ws.onopen = () => { retry = 0; onState?.(true); };
    ws.onmessage = event => { try { onData(JSON.parse(event.data)); } catch (error) { console.warn(error); } };
    ws.onerror = () => ws.close();
    ws.onclose = () => {
      if (closed) return;
      onState?.(false);
      timer = setTimeout(connect, [1000, 2000, 5000, 10000][Math.min(retry++, 3)]);
    };
  }
  function online() { clearTimeout(timer); if (ws?.readyState !== 1) connect(); }
  window.addEventListener('online', online); connect();
  return () => { closed = true; clearTimeout(timer); ws?.close(); window.removeEventListener('online', online); };
}

// HTTP 与 WebSocket 通信封装，统一向页面提供可读的错误和连接状态。
const messages = {
  ACTIVE_SESSION_EXISTS: '已有演示正在运行，请先返回恢复过程页面。',
  SOURCE_UNAVAILABLE: '所选处理环境暂不可用，请查看系统状态。',
  RAW_DATA_MISSING: '原始文件缺失，无法完成此操作。请查看数据与片段页面。',
  REVISION_CONFLICT: '状态已变化，请刷新后重试。',
  RECORD_IN_USE: '记录正在使用，无法删除。本次没有删除任何记录。',
};

// 将请求体编码为 JSON；写操作附带幂等键，便于服务端识别重复请求。
export async function api(path, options = {}) {
  const method = options.method || 'GET';
  const headers = {'Content-Type': 'application/json', ...options.headers};
  if (method !== 'GET') headers['Idempotency-Key'] ||= crypto.randomUUID();
  let response;
  try {
    response = await fetch(`/api${path}`, {
      ...options, method, headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
  } catch {
    throw new Error('无法连接本地服务。请确认项目已启动，连接恢复后再查看操作结果。');
  }
  let data;
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) {
    const detail = data.detail || data;
    const code = detail.code || data.code;
    const message = typeof detail === 'string' ? detail : detail.message || messages[code] || `操作未完成（HTTP ${response.status}）`;
    const error = new Error(message);
    error.code = code; error.status = response.status; error.detail = detail;
    throw error;
  }
  return data;
}

// 连接中断后逐步延长重试间隔，返回清理函数以释放连接与事件监听。
export function socket(path, onData, onState) {
  let ws, retry = 0, timer, closed = false;
  function connect() {
    if (closed) return;
    ws = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api${path}`);
    ws.onopen = () => { retry = 0; onState?.(true); };
    ws.onmessage = event => {
      try { onData(JSON.parse(event.data)); } catch (error) { console.warn(error); }
    };
    ws.onerror = () => ws.close();
    ws.onclose = () => {
      if (closed) return;
      onState?.(false);
      timer = setTimeout(connect, [1000, 2000, 5000, 10000][Math.min(retry++, 3)]);
    };
  }
  function online() {
    clearTimeout(timer);
    if (ws?.readyState !== WebSocket.OPEN && ws?.readyState !== WebSocket.CONNECTING) connect();
  }
  window.addEventListener('online', online);
  connect();
  return () => {
    closed = true; clearTimeout(timer); ws?.close();
    window.removeEventListener('online', online);
  };
}

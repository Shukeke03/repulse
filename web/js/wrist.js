// 腕端显示模块：以 240×280 帧缓冲模拟腕表屏幕，支持屏幕点按、滑动及键盘/实体按键。
// 页面只调用矩形、圆弧、文字和折线等固定分辨率绘图原语，输出前量化为 RGB565，便于对照移植到固件 UI 任务。

export const PANEL = {width: 240, height: 280, radius: 36, fps: 30};
const W = PANEL.width, H = PANEL.height, BAR = 34, TOP = 40, BOTTOM = 268;
const HOLD_MS = {back: 800, ok: 1200}, HOLD_HINT_MS = 250, REPEAT_DELAY = 450, REPEAT_MS = 110, SLIDE_MS = 170, TOAST_MS = 2800;
const NAV_TOP = 246, DRAG_PX = 8, SWIPE_PX = 36;

// 设备调色板：黑底保证户外对比度，颜色只用于表达脉率与数据状态。
const C = {
  bg: '#000000', card: '#0d141c', line: '#1b2430', dim: '#4a5767', mist: '#8c9aab', ink: '#eef3f8',
  focus: '#245ff2', pulse: '#ff4f5e', mint: '#2ed6a6', amber: '#ffb23e', read: '#4c7dff', danger: '#d23f4f',
};
const CJK = '"PingFang SC","Hiragino Sans GB","Microsoft YaHei","Noto Sans CJK SC",sans-serif';
const NUM = '"DIN Alternate","Bahnschrift","Roboto Condensed","Arial Narrow",sans-serif';

// 腕端字符串表比电脑端更短，固件可沿用同一份文案。
const STRATEGY = {continuous: '连续读取', critical: '关键窗口优先', periodic: '周期读取'};
const STAGE = {CREATED: '待开始', PREPARING: '起点前准备', RECOVERING: '恢复中', FINALIZING: '正在保存', COMPLETED: '已完成', CANCELLED: '已停止', INTERRUPTED: '已中断', FAILED: '处理失败'};
const STAGE_COLOR = {PREPARING: C.amber, RECOVERING: C.mint, FINALIZING: C.amber, COMPLETED: C.mint, FAILED: C.danger, INTERRUPTED: C.danger};
const REASON = {
  INSUFFICIENT_HISTORY: '起点前数据不足', HISTORY_INSUFFICIENT: '起点前数据不足', INSUFFICIENT_DATA: '有效样本不足',
  NOT_READY: '等待完整窗口', WAITING: '等待完整窗口', PENDING: '等待完整窗口', PREPARING: '正在读取',
  WARMING: '滤波预热中', WARMUP: '滤波预热中', WARMUP_INCOMPLETE: '滤波预热中', DATA_GAP: '数据缺口',
  NO_CONTACT: '接触不良', FLAT_SIGNAL: '波动不足', FLATLINE: '信号平直', LOW_AMPLITUDE: '波动不足',
  SATURATION: '信号饱和', SATURATED: '信号饱和', POOR_SIGNAL: '质量未通过', MOTION: '运动干扰较强',
  MOTION_ARTIFACT: '运动干扰较强', ACQUISITION_OFF: '此时段未读取', NOT_ACQUIRED: '此时段未读取', OFF: '此时段未读取',
  STALE: '输入已过期', SENSOR_FAULT: '采集异常', SENSOR_TIMEOUT: '采集超时', NOT_COLLECTED: '窗口未完成',
  INSUFFICIENT_INTERVALS: '间期不足', INSUFFICIENT_PEAKS: '间期不足', INTERVAL_INCONSISTENT: '间期不一致',
  IRREGULAR_INTERVALS: '间期不一致', AUTOCORRELATION_MISMATCH: '周期一致性不足', OUT_OF_RANGE: '超出40–200',
  SESSION_ENDED: '演示已结束', SOURCE_EOF: '片段已结束', VALID: '质量检查通过', UNKNOWN: '等待质量检查',
};
const WINDOW_STYLE = {
  valid: {label: '可用', short: '可用', arc: C.mint, text: C.mint},
  collecting: {label: '正在累计', short: '累计中', arc: C.amber, text: C.amber},
  waiting: {label: '尚未到达', short: '未到', arc: '#34414f', text: C.mist},
  missing: {label: '缺测', short: '缺测', arc: '#6b7889', text: C.mist},
};
const EVENT = {CREATED: '演示记录已创建', PREPARE_STARTED: '开始读取起点前数据', T0_LOCKED: '已到恢复起点', WINDOW_RESULT: '窗口处理完成', FINALIZING: '正在保存结果', COMPLETED: '演示完成', CANCELLED: '演示停止'};
const KEY_LABEL = {back: '返回', up: '上', ok: '确认', down: '下'};
const KIND_LABEL = {short: '短按', long: '长按', repeat: '按住', click: '点击', tap: '点击', swipe: '滑动', wheel: '滚动'};
const KEYMAP = {ArrowUp: 'up', ArrowDown: 'down', Enter: 'ok', ' ': 'ok', Escape: 'back', Backspace: 'back', ArrowLeft: 'back', ArrowRight: 'ok'};
const PHASES = [0, 5, 10, 15];
const NO_LINE_START = '，。、；：？！）》」』…%,.;:?!)';

// 腕端页面结构与电脑端功能一一对应，页面栏据此高亮当前位置并提供跳转。
export const SCREEN_MAP = [
  {id: 'face', label: '表盘'},
  {id: 'menu', label: '菜单', children: [
    {id: 'start', label: '新建演示'},
    {id: 'monitor', label: '恢复过程', pages: ['实时脉率', '脉搏波', '关键窗口', '读取计划', '处理事件']},
    {id: 'records', label: '记录库'},
    {id: 'replays', label: '记录回放'},
    {id: 'compare', label: '策略比较'},
    {id: 'data', label: '数据与片段'},
    {id: 'system', label: '系统状态', pages: ['系统概览', '处理任务', '关于本机']},
    {id: 'settings', label: '设置'},
  ]},
];
const MAP_IDS = new Set(['face', 'menu', ...SCREEN_MAP[1].children.map(item => item.id)]);
const MAP_ALIAS = {record: 'records', replay: 'replays', dataset: 'data'};

// —— 通用数据换算 ——
const clamp = (value, lo, hi) => Math.min(hi, Math.max(lo, value));
const fixed = (value, digits = 1) => Number.isFinite(value) ? value.toFixed(digits) : '--';
const pad2 = value => String(value).padStart(2, '0');
const idOf = record => record?.session_id || record?.id;
const phaseOf = record => record?.session_state || record?.status || 'IDLE';
const isFinished = record => ['COMPLETED', 'CANCELLED', 'INTERRUPTED', 'FAILED'].includes(phaseOf(record));
const metaOf = record => record?.dataset || record?.config_snapshot?.dataset_metadata || {};
const t0Of = record => record?.t0_us ?? record?.source_t0_us ?? metaOf(record).t0_us ?? 10_000_000;
const relSeconds = record => ((record?.cursor_us ?? record?.engine_time_us ?? 0) - t0Of(record)) / 1e6;
const reasonText = reasons => {
  const first = (Array.isArray(reasons) ? reasons : [reasons]).find(Boolean);
  return first ? REASON[typeof first === 'string' ? first : first.code] || '质量未通过' : '质量检查通过';
};
const engineName = record => record?.execution_mode === 'FREERTOS_QEMU' ? 'FreeRTOS / QEMU' : 'Python';
const datasetShort = dataset => dataset ? `${dataset.subject || '记录'} ${dataset.exercise_label || '有氧骑行'}` : '未选择';
const deltaText = value => Number.isFinite(value) ? `${value.toFixed(1)} BPM` : '—';
const clock = date => `${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
const dateText = date => `${date.getMonth() + 1}月${date.getDate()}日 周${'日一二三四五六'[date.getDay()]}`;
const shortDate = iso => {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '—' : `${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${clock(date)}`;
};
const withAlpha = (hex, alpha) => `rgba(${parseInt(hex.slice(1, 3), 16)},${parseInt(hex.slice(3, 5), 16)},${parseInt(hex.slice(5, 7), 16)},${alpha})`;

// 恢复段读取区间（相对恢复起点的秒数），与电脑端时间轴共用同一算法。
export function readingIntervals(config = {}) {
  const strategy = config.strategy || 'continuous';
  if (strategy === 'critical') return [[0, 10], [20, 30], [40, 50], [54, 64], [80, 90], [100, 110], [114, 124]];
  if (strategy !== 'periodic') return [[0, 124]];
  const phase = config.phase || 0;
  let width = config.periodic_width;
  if (width == null) {
    let lo = 0, hi = 20;
    for (let step = 0; step < 50; step++) {
      const mid = (lo + hi) / 2;
      let on = 0;
      for (let k = -1; k < 7; k++) on += Math.max(0, Math.min(124, phase + 20 * k + mid) - Math.max(0, phase + 20 * k));
      if (on < 70) lo = mid; else hi = mid;
    }
    width = (lo + hi) / 2;
  }
  const intervals = [];
  for (let k = -1; k < 7; k++) {
    const start = Math.max(0, phase + 20 * k), end = Math.min(124, phase + 20 * k + width);
    if (end > start) intervals.push([start, end]);
  }
  return intervals;
}

// 窗口末端到达前不显示结果；已保存记录直接显示最终状态。
function windowState(record, center, saved = false) {
  const w = (record?.key_windows || []).find(x => (x.center_s ?? x.center_seconds) === center) || {};
  const cursor = record?.cursor_us ?? record?.engine_time_us ?? 0, t0 = t0Of(record);
  const start = w.start_us ?? t0 + (center - 4) * 1e6, end = w.end_us ?? t0 + (center + 4) * 1e6;
  if (!saved && cursor < end) return {kind: cursor >= start ? 'collecting' : 'waiting', w};
  if (w.valid) return {kind: 'valid', w, bpm: w.pulse_bpm};
  if (!Object.keys(w).length || ['WAITING', 'PENDING'].includes(w.status)) return {kind: 'waiting', w};
  if (w.status === 'COLLECTING') return {kind: 'collecting', w};
  return {kind: 'missing', w};
}

// —— 绘图原语：固件端可用 LVGL 或自写帧缓冲接口一一替换 ——
class Gfx {
  constructor(ctx) { this.c = ctx; }
  clear(color = C.bg) { this.rect(0, 0, W, H, color); }
  rect(x, y, w, h, color) { this.c.fillStyle = color; this.c.fillRect(x, y, w, h); }
  rrect(x, y, w, h, r, fill, stroke, width = 1) {
    const c = this.c;
    c.beginPath(); c.roundRect(x, y, w, h, r);
    if (fill) { c.fillStyle = fill; c.fill(); }
    if (stroke) { c.strokeStyle = stroke; c.lineWidth = width; c.stroke(); }
  }
  circle(x, y, r, fill, stroke, width = 1.5) {
    const c = this.c;
    c.beginPath(); c.arc(x, y, r, 0, Math.PI * 2);
    if (fill) { c.fillStyle = fill; c.fill(); }
    if (stroke) { c.strokeStyle = stroke; c.lineWidth = width; c.stroke(); }
  }
  line(x1, y1, x2, y2, color, width = 1) {
    const c = this.c;
    c.beginPath(); c.moveTo(x1, y1); c.lineTo(x2, y2); c.strokeStyle = color; c.lineWidth = width; c.lineCap = 'butt'; c.stroke();
  }
  // 角度以度为单位，顺时针；圆弧用于表盘进度和窗口标记。
  arc(cx, cy, r, from, to, color, width, cap = 'round') {
    if (to <= from) return;
    const c = this.c;
    c.beginPath(); c.arc(cx, cy, r, from * Math.PI / 180, to * Math.PI / 180);
    c.strokeStyle = color; c.lineWidth = width; c.lineCap = cap; c.stroke();
  }
  font(size, num = false, weight = 400) { this.c.font = `${weight} ${size}px ${num ? NUM : CJK}`; }
  measure(value, size = 14, {num = false, weight = 400} = {}) { this.font(size, num, weight); return this.c.measureText(String(value)).width; }
  // 文字超出可用宽度时截断并加省略号，返回实际绘制宽度。
  text(value, x, y, {size = 14, color = C.ink, align = 'left', num = false, weight = 400, max} = {}) {
    const c = this.c;
    this.font(size, num, weight);
    let s = String(value ?? '');
    if (max && c.measureText(s).width > max) {
      while (s.length > 1 && c.measureText(`${s}…`).width > max) s = s.slice(0, -1);
      s += '…';
    }
    c.fillStyle = color; c.textAlign = align; c.textBaseline = 'alphabetic'; c.fillText(s, x, y);
    return c.measureText(s).width;
  }
  // 中文按字断行，英文命令也按字符断开；句读等标点不放在行首，允许悬挂在行尾。
  lines(value, width, size = 14) {
    this.font(size);
    const rows = [];
    let row = '';
    for (const ch of String(value ?? '')) {
      if (ch === '\n') { rows.push(row); row = ''; continue; }
      if (row && !NO_LINE_START.includes(ch) && this.c.measureText(row + ch).width > width) { rows.push(row); row = ch === ' ' ? '' : ch; }
      else row += ch;
    }
    if (row) rows.push(row);
    return rows;
  }
  wrap(value, x, y, width, {size = 14, color = C.mist, line = size + 6, max = 6, align = 'left'} = {}) {
    let rows = this.lines(value, width, size);
    if (rows.length > max) { rows = rows.slice(0, max); rows[max - 1] = `${rows[max - 1].slice(0, -1)}…`; }
    rows.forEach((row, i) => this.text(row, align === 'center' ? x + width / 2 : x, y + i * line, {size, color, align}));
    return rows.length * line;
  }
  icon(name, x, y, color, scale = 1) {
    const c = this.c;
    c.save(); c.translate(x, y); c.scale(scale, scale);
    c.fillStyle = color; c.strokeStyle = color; c.lineWidth = 2 / scale; c.lineCap = 'round'; c.lineJoin = 'round';
    (ICON[name] || ICON.info)(c);
    c.restore();
  }
  clip(x, y, w, h, draw) {
    const c = this.c;
    c.save(); c.beginPath(); c.rect(x, y, w, h); c.clip(); draw(); c.restore();
  }
}

// 16×16 栅格内的线形图标，以原点为中心绘制。
const ICON = {
  back(c) { c.beginPath(); c.moveTo(3, -6); c.lineTo(-3, 0); c.lineTo(3, 6); c.stroke(); },
  play(c) { c.beginPath(); c.moveTo(-4, -6); c.lineTo(6, 0); c.lineTo(-4, 6); c.closePath(); c.fill(); },
  pause(c) { c.fillRect(-5, -6, 3.5, 12); c.fillRect(1.5, -6, 3.5, 12); },
  restart(c) { c.fillRect(-6, -6, 2.2, 12); c.beginPath(); c.moveTo(6, -6); c.lineTo(-2.5, 0); c.lineTo(6, 6); c.closePath(); c.fill(); },
  pulse(c) { c.beginPath(); c.moveTo(-7, 1); c.lineTo(-3.5, 1); c.lineTo(-1.5, -5); c.lineTo(1.5, 6); c.lineTo(3.5, 1); c.lineTo(7, 1); c.stroke(); },
  heart(c) { c.beginPath(); c.moveTo(0, 6.5); c.bezierCurveTo(-9.5, 0, -7, -8.5, 0, -3.2); c.bezierCurveTo(7, -8.5, 9.5, 0, 0, 6.5); c.fill(); },
  list(c) { for (const y of [-5, 0, 5]) { c.beginPath(); c.moveTo(-6, y); c.lineTo(6, y); c.stroke(); } },
  replay(c) {
    c.beginPath(); c.arc(0, 0.5, 6, Math.PI * 1.15, Math.PI * 2.85); c.stroke();
    c.beginPath(); c.moveTo(-7, -5.5); c.lineTo(-5.4, -1.6); c.lineTo(-1.6, -3); c.stroke();
  },
  bars(c) { c.fillRect(-6.5, -1, 3.4, 7.5); c.fillRect(-1.7, -6.5, 3.4, 13); c.fillRect(3.1, -3.5, 3.4, 10); },
  data(c) {
    c.beginPath(); c.ellipse(0, -4, 6, 2.4, 0, 0, Math.PI * 2); c.stroke();
    c.beginPath(); c.moveTo(-6, -4); c.lineTo(-6, 4); c.ellipse(0, 4, 6, 2.4, 0, Math.PI, 0, true); c.lineTo(6, -4); c.stroke();
  },
  chip(c) {
    c.strokeRect(-4.5, -4.5, 9, 9);
    for (const p of [-2, 2]) {
      c.beginPath(); c.moveTo(p, -7); c.lineTo(p, -5); c.moveTo(p, 5); c.lineTo(p, 7); c.moveTo(-7, p); c.lineTo(-5, p); c.moveTo(5, p); c.lineTo(7, p); c.stroke();
    }
  },
  gear(c) {
    c.beginPath(); c.arc(0, 0, 3, 0, Math.PI * 2); c.stroke();
    for (let i = 0; i < 8; i++) {
      const a = i * Math.PI / 4;
      c.beginPath(); c.moveTo(Math.cos(a) * 4.6, Math.sin(a) * 4.6); c.lineTo(Math.cos(a) * 6.8, Math.sin(a) * 6.8); c.stroke();
    }
  },
  check(c) { c.beginPath(); c.moveTo(-5, 0); c.lineTo(-1.5, 4); c.lineTo(5.5, -4.5); c.stroke(); },
  cross(c) { c.beginPath(); c.moveTo(-4.5, -4.5); c.lineTo(4.5, 4.5); c.moveTo(4.5, -4.5); c.lineTo(-4.5, 4.5); c.stroke(); },
  chevron(c) { c.beginPath(); c.moveTo(-2, -5); c.lineTo(3, 0); c.lineTo(-2, 5); c.stroke(); },
  info(c) { c.beginPath(); c.arc(0, 0, 6.5, 0, Math.PI * 2); c.stroke(); c.fillRect(-1, -1, 2, 5.5); c.fillRect(-1, -4.5, 2, 2); },
  stop(c) { c.fillRect(-5, -5, 10, 10); },
  warn(c) { c.beginPath(); c.moveTo(0, -6.5); c.lineTo(7, 6); c.lineTo(-7, 6); c.closePath(); c.stroke(); c.fillRect(-1, -2.5, 2, 4.5); c.fillRect(-1, 3, 2, 1.6); },
  trash(c) { c.beginPath(); c.moveTo(-6, -4); c.lineTo(6, -4); c.moveTo(-2.5, -4); c.lineTo(-2, -6); c.lineTo(2, -6); c.lineTo(2.5, -4); c.moveTo(-4.5, -4); c.lineTo(-3.6, 6); c.lineTo(3.6, 6); c.lineTo(4.5, -4); c.stroke(); },
};

// 浏览器画布与屏幕同尺寸，帧输出前统一量化为 16 位 RGB565。
function panelCanvas() {
  const canvas = document.createElement('canvas');
  canvas.width = W; canvas.height = H;
  return canvas;
}
function quantize(ctx) {
  const image = ctx.getImageData(0, 0, W, H), data = image.data;
  for (let i = 0; i < data.length; i += 4) {
    const r = data[i] & 0xf8, g = data[i + 1] & 0xfc, b = data[i + 2] & 0xf8;
    data[i] = r | r >> 5; data[i + 1] = g | g >> 6; data[i + 2] = b | b >> 5;
  }
  ctx.putImageData(image, 0, 0);
}

// —— 可复用的屏幕部件 ——
function drawMessage(g, title, text = '', {icon = 'info', color = C.mist, now = 0} = {}) {
  if (icon === 'spin') g.arc(120, 112, 16, (now / 3) % 360, (now / 3) % 360 + 260, C.read, 4);
  else { g.circle(120, 112, 22, withAlpha(color, .16)); g.icon(icon, 120, 112, color, 1.3); }
  g.text(title, 120, 162, {size: 16, weight: 600, align: 'center', max: 200});
  if (text) g.wrap(text, 24, 188, 192, {size: 12, color: C.mist, line: 18, max: 4, align: 'center'});
}
function pageDots(g, count, index, x = 232, cy = 154) {
  const y0 = cy - (count - 1) * 5;
  for (let i = 0; i < count; i++) g.circle(x, y0 + i * 10, i === index ? 3 : 2, i === index ? C.ink : C.dim);
}
function connectionGlyph(g, x, y, connected) {
  [3, 6, 9].forEach((h, i) => g.rect(x + i * 4, y - h, 2.5, h, connected ? C.mint : C.dim));
  if (!connected) g.line(x - 1, y - 9, x + 11, y + 1, C.pulse, 1.5);
}
function drawWave(g, samples, {x, y, w, h, key = 'ppg', start, end, color = C.pulse, stride = 1, empty = '此时段没有可显示的数据'}) {
  const span = Math.max(1, end - start);
  const visible = samples.filter(p => p.time_us >= start && p.time_us <= end);
  const valid = p => p.on !== false && !p.gap && Number.isFinite(p[key]);
  const values = visible.filter(valid).map(p => p[key]);
  g.line(x, y + h / 2, x + w, y + h / 2, C.line);
  if (!values.length) { g.text(empty, x + w / 2, y + h / 2 + 18, {size: 12, color: C.dim, align: 'center'}); return; }
  let lo = Math.min(...values), hi = Math.max(...values);
  if (hi - lo < 1e-6) { lo -= 1; hi += 1; }
  const margin = (hi - lo) * .12; lo -= margin; hi += margin;
  g.clip(x, y, w, h, () => {
    const c = g.c;
    c.strokeStyle = color; c.lineWidth = 1.5; c.lineJoin = 'round'; c.lineCap = 'round'; c.beginPath();
    let pen = false, previous;
    for (const p of visible) {
      const ok = valid(p);
      const broken = previous && (p.segment_id !== previous.segment_id || p.time_us - previous.time_us > 15625 * stride * 1.6);
      if (!ok || broken) pen = false;
      if (ok) {
        const px = x + (p.time_us - start) / span * w, py = y + (hi - p[key]) / (hi - lo) * h;
        if (pen) c.lineTo(px, py); else c.moveTo(px, py);
        pen = true;
      }
      previous = p;
    }
    c.stroke();
  });
}
function drawMotion(g, samples, {x, y, w, h, start, end}) {
  const columns = Math.floor(w / 3), span = Math.max(1, end - start), peaks = new Array(columns).fill(null);
  for (const p of samples) {
    if (p.time_us < start || p.time_us > end || !Number.isFinite(p.accel)) continue;
    const i = Math.min(columns - 1, Math.floor((p.time_us - start) / span * columns));
    peaks[i] = Math.max(peaks[i] ?? 0, p.accel);
  }
  const ceiling = Math.max(.3, ...peaks.filter(v => v != null));
  g.line(x, y + h, x + w, y + h, C.line);
  peaks.forEach((v, i) => { if (v != null) { const bh = Math.max(1, v / ceiling * h); g.rect(x + i * 3, y + h - bh, 2, bh, C.mint); } });
}

// —— 通用列表：上/下移动焦点，确认执行当前项；焦点项自动滚入可视区域 ——
function rowHeight(g, item) {
  if (item.h) return item.h;
  if (item.kind === 'note') return g.lines(item.text, 196, 12).length * 18 + 10;
  return {picker: 52, button: 46, info: 36, radio: item.sub ? 52 : 44}[item.kind] || 48;
}
function drawRow(g, item, x, y, w, h, focused) {
  const disabled = !!item.disabled;
  if (item.kind === 'custom') {
    if (focused) g.rrect(x, y, w, h, 14, null, '#30425c', 1.5);
    item.draw(g, x, y, w, h, focused);
    return;
  }
  if (item.kind === 'note') {
    if (focused) g.rect(x, y + 4, 3, h - 12, C.focus);
    g.wrap(item.text, x + 10, y + 16, 196, {size: 12, color: focused ? C.mist : C.dim, line: 18});
    return;
  }
  if (item.kind === 'info') {
    if (focused) g.rect(x, y + 8, 3, h - 16, C.focus);
    g.text(item.label, x + 12, y + 23, {size: 14, color: C.mist});
    g.text(item.value, x + w - 10, y + 23, {size: 14, color: item.color || C.ink, align: 'right', max: w - 110});
    return;
  }
  if (item.kind === 'button') {
    const tint = item.danger ? C.danger : item.color || C.focus;
    g.rrect(x, y, w, h, 14, focused ? tint : C.card, focused ? null : withAlpha(tint, .45), 1.5);
    const color = focused ? (tint === C.focus || tint === C.danger ? '#ffffff' : '#05080b') : disabled ? C.dim : tint === C.focus ? C.ink : tint;
    g.text(item.label, x + w / 2, y + h / 2 + 6, {size: 16, weight: 600, align: 'center', color, max: w - 24});
    return;
  }
  if (focused) g.rrect(x, y, w, h, 14, item.danger ? C.danger : C.focus);
  const ink = disabled ? C.dim : focused ? '#ffffff' : item.danger ? C.pulse : C.ink;
  const soft = focused ? '#cddcff' : C.mist;
  let tx = x + 12;
  if (item.icon) {
    g.circle(x + 26, y + h / 2, 14, disabled ? C.line : item.color || C.mist);
    g.icon(item.icon, x + 26, y + h / 2, '#05080b', .95);
    tx = x + 50;
  }
  if (item.dot) { g.circle(x + 16, y + h / 2, 4, item.dot); tx = x + 30; }
  const right = x + w - (item.chevron ? 26 : 12) - (item.kind === 'radio' ? 18 : 0);
  if (item.kind === 'picker') {
    g.text(item.label, tx, y + 20, {size: 12, color: soft});
    g.text(item.value, tx, y + 41, {size: 16, color: disabled ? C.dim : ink, max: right - tx});
  } else if (item.sub) {
    g.text(item.label, tx, y + 21, {size: 16, color: ink, max: right - tx});
    g.text(item.sub, tx, y + 39, {size: 12, color: item.subColor && !focused ? item.subColor : soft, max: right - tx});
  } else {
    g.text(item.label, tx, y + h / 2 + 6, {size: 16, color: ink, max: (item.value ? right - 70 : right) - tx});
    if (item.value) g.text(item.value, right, y + h / 2 + 6, {size: 14, color: soft, align: 'right', max: 80});
  }
  if (item.kind === 'radio') g.circle(x + w - 20, y + h / 2, 6, item.radio ? (focused ? '#ffffff' : C.mint) : null, item.radio ? null : focused ? '#cddcff' : C.dim);
  if (item.chevron) g.icon('chevron', x + w - 14, y + h / 2, focused ? '#ffffff' : C.dim, .9);
}
function drawList(g, s, items, {top = TOP, bottom = BOTTOM, x = 10, w = 218, gap = 4} = {}) {
  const view = bottom - top;
  let y = 0;
  const rows = items.map(item => { const h = rowHeight(g, item), row = {y, h}; y += h + gap; return row; });
  const total = Math.max(0, y - gap);
  s.focus = clamp(s.focus, 0, Math.max(0, items.length - 1));
  const focused = rows[s.focus];
  if (focused && !s.manualScroll) {
    if (focused.y < s.scrollTarget) s.scrollTarget = focused.y;
    if (focused.y + focused.h > s.scrollTarget + view) s.scrollTarget = focused.y + focused.h - view;
  }
  s.scrollTarget = clamp(s.scrollTarget, 0, Math.max(0, total - view));
  s.scroll += (s.scrollTarget - s.scroll) * (s.reduced ? 1 : .38);
  if (Math.abs(s.scrollTarget - s.scroll) < .5) s.scroll = s.scrollTarget;
  // 保存这一帧真正可见的裁剪行，触点不使用未滚动的原始坐标。
  s.listLayout = {top, bottom, maxScroll: Math.max(0, total - view), rows: []};
  g.clip(0, top, W, view, () => {
    items.forEach((item, i) => {
      const row = rows[i], ry = top + row.y - s.scroll;
      if (ry + row.h >= top && ry <= bottom) {
        const drawnY = Math.round(ry), visibleY = Math.max(top, drawnY), visibleEnd = Math.min(bottom, drawnY + row.h);
        drawRow(g, item, x, drawnY, w, row.h, i === s.focus);
        if (visibleEnd > visibleY) s.listLayout.rows.push({index: i, x, y: visibleY, w, h: visibleEnd - visibleY});
      }
    });
  });
  if (total > view) {
    const thumb = Math.max(22, view * view / total), ty = top + (view - thumb) * (s.scroll / (total - view));
    g.rrect(232, top, 3, view, 1.5, C.line);
    g.rrect(232, ty, 3, thumb, 1.5, C.mist);
  }
}
function listKeys(os, s, items, key) {
  if (!items.length) return false;
  if (key === 'up' || key === 'down') {
    s.manualScroll = false;
    s.focus = (s.focus + (key === 'down' ? 1 : items.length - 1)) % items.length;
    return true;
  }
  if (key === 'ok') {
    if (s.busy || s.loading) return true;
    const item = items[s.focus];
    if (!item) return false;
    if (item.disabled) { os.toast(typeof item.disabled === 'string' ? item.disabled : '当前不可用', 'info'); return true; }
    item.ok?.(os, s);
    return true;
  }
  return false;
}
const rowLabel = item => !item ? '' : item.kind === 'note' ? item.text : ['info', 'picker'].includes(item.kind) ? `${item.label}：${item.value}` : item.label || '';
// 由数据项生成列表页面；空列表时显示提示，读取中显示加载动画。
function list(def) {
  return {
    ...def,
    list: true,
    focusLabel: (os, s) => rowLabel(def.items(os, s)[s.focus]),
    animate: (os, s) => !!s.loading || !!s.busy || def.animate?.(os, s),
    draw(g, os, s, now) {
      const items = def.items(os, s);
      if (!items.length) {
        s.listLayout = null;
        if (!s.loading) os.hit(10, TOP, 220, BOTTOM - TOP, () => os.dispatch('ok', 'tap', '屏幕'), 'empty');
        if (s.loading) return drawMessage(g, '正在读取', '', {icon: 'spin', now});
        if (s.error) return drawMessage(g, '读取失败', `${s.error} 按确认键重试。`, {icon: 'cross', color: C.pulse});
        const [title, text, icon, color] = def.empty?.(os, s) || ['没有内容', ''];
        return drawMessage(g, title, text, {icon, color, now});
      }
      drawList(g, s, items);
      for (const row of s.listLayout.rows) {
        const item = items[row.index];
        os.hit(row.x, row.y, row.w, row.h, () => {
          if (s.busy || s.loading) return;
          s.focus = row.index; s.manualScroll = true;
          os.dispatch('ok', 'tap', '屏幕');
        }, `row:${row.index}:${rowLabel(item)}`);
      }
      def.after?.(g, os, s, now);
    },
    key(os, s, key, kind) {
      if (def.key?.(os, s, key, kind)) return true;
      const items = def.items(os, s);
      if (!items.length && key === 'ok' && (s.error || def.retry) && def.enter) { def.enter(os, s); return true; }
      return listKeys(os, s, items, key);
    },
  };
}
// 页面数据异步读取；页面离开后丢弃迟到的响应。
async function load(os, s, key, request) {
  s.loading = true; s.error = null; os.touch();
  try { const value = await request(); if (s.alive) s[key] = value; }
  catch (error) { if (s.alive) s.error = error.message; }
  finally { if (s.alive) { s.loading = false; if (os.top() === s) os.changed(); else os.touch(); } }
}
function pickerRow(os, label, value, options, current, onPick, disabled = false) {
  return {kind: 'picker', label, value, disabled, chevron: true, ok: () => os.push('picker', {title: label, options, value: current, onPick})};
}
const infoRow = (label, value, color) => ({kind: 'info', label, value, color});

// —— 页面定义 ——
const SCREENS = {};
const APPS = [
  {id: 'start', label: '新建演示', icon: 'play', color: C.mint, sub: os => os.active() ? '已有演示运行中' : ''},
  {id: 'monitor', label: '恢复过程', icon: 'pulse', color: C.pulse, sub: os => liveSummary(os), subColor: C.mint},
  {id: 'records', label: '记录库', icon: 'list', color: C.read},
  {id: 'replays', label: '记录回放', icon: 'replay', color: C.read},
  {id: 'compare', label: '策略比较', icon: 'bars', color: C.amber},
  {id: 'data', label: '数据与片段', icon: 'data', color: C.mint},
  {id: 'system', label: '系统状态', icon: 'chip', color: C.mist},
  {id: 'settings', label: '设置', icon: 'gear', color: C.mist},
];
function liveSummary(os) {
  const live = os.live();
  if (!live) return '';
  const phase = phaseOf(live), rel = relSeconds(live);
  if (phase === 'PREPARING') return `起点前 ${fixed(Math.max(0, -rel), 0)} 秒`;
  if (phase === 'RECOVERING') return `恢复中 ${fixed(clamp(rel, 0, 124), 0)} 秒`;
  return STAGE[phase] || '';
}

// 表盘：空闲时显示时间与上次结果，演示进行时显示实时脉率和进度。
SCREENS.face = {
  title: '表盘', bar: false,
  enter(os) { os.loadRecent(); },
  draw(g, os) {
    const live = os.live(), active = os.active(), date = new Date();
    connectionGlyph(g, 114, 24, os.connected());
    if (active) {
      const phase = phaseOf(live), rel = relSeconds(live), result = live.latest_result || {};
      const valid = os.connected() && result.valid;
      g.text(clock(date), 120, 84, {size: 46, num: true, weight: 700, align: 'center'});
      g.text(dateText(date), 120, 106, {size: 12, color: C.mist, align: 'center'});
      g.rrect(14, 122, 212, 118, 18, C.card);
      g.circle(32, 142, 4, C.pulse);
      g.text(STAGE[phase] || '处理中', 44, 147, {size: 14});
      g.text(phase === 'PREPARING' ? `起点前 ${fixed(Math.max(0, -rel), 0)} 秒` : `${fixed(clamp(rel, 0, 124), 0)} / 124 秒`, 212, 147, {size: 12, color: C.mist, align: 'right'});
      const width = g.text(valid ? Math.round(result.pulse_bpm) : '--', 28, 206, {size: 48, num: true, weight: 700, color: valid ? C.pulse : C.dim});
      g.text('BPM', 34 + width, 206, {size: 12, color: C.mist});
      g.text(valid ? '质量检查通过' : os.connected() ? reasonText(result.reasons) : '连接中断', 212, 206, {size: 12, color: valid ? C.mint : C.amber, align: 'right'});
      g.rrect(28, 220, 184, 6, 3, C.line);
      if (rel > 0) g.rrect(28, 220, 184 * clamp(rel / 124, 0, 1), 6, 3, C.mint);
      g.text('按下键查看恢复过程', 120, 264, {size: 12, color: C.dim, align: 'center'});
      return;
    }
    g.text(clock(date), 120, 118, {size: 72, num: true, weight: 700, align: 'center'});
    g.text(dateText(date), 120, 146, {size: 14, color: C.mist, align: 'center'});
    g.rrect(14, 166, 212, 76, 18, C.card);
    const recent = os.cache.recent;
    if (recent) {
      g.icon('heart', 32, 186, C.pulse, .9);
      g.text('上次演示', 46, 191, {size: 12, color: C.mist});
      g.text(shortDate(recent.created_at_utc), 212, 191, {size: 12, color: C.mist, align: 'right'});
      [60, 120].forEach((center, i) => {
        const st = windowState(recent, center, true), x = 28 + i * 98;
        const width = g.text(`${center} 秒`, x, 226, {size: 12, color: C.mist});
        g.text(st.kind === 'valid' ? fixed(st.bpm, 0) : '缺测', x + width + 6, 228, {size: st.kind === 'valid' ? 22 : 14, num: st.kind === 'valid', weight: 700, color: st.kind === 'valid' ? C.mint : C.mist});
      });
    } else {
      g.text('还没有演示记录', 120, 200, {size: 14, color: C.mist, align: 'center'});
      g.text('新建演示后在这里显示结果', 120, 222, {size: 12, color: C.dim, align: 'center'});
    }
    g.text('按确认键打开菜单', 120, 266, {size: 12, color: C.dim, align: 'center'});
  },
  key(os, s, key) {
    if (key === 'ok') return os.push('menu');
    if (key === 'down') return os.push('monitor');
    if (key === 'up') return os.push('records');
    return false;
  },
};

SCREENS.menu = list({
  title: '菜单',
  items: os => APPS.map(app => ({label: app.label, sub: app.sub?.(os), subColor: app.subColor, icon: app.icon, color: app.color, chevron: true, ok: () => os.push(app.id)})),
});

// 通用选择页：列出可选值，确认后写回调用页面。
SCREENS.picker = list({
  title: (os, s) => s.title,
  init(s) { s.focus = Math.max(0, s.options.findIndex(option => option.value === s.value)); },
  items: (os, s) => s.options.map(option => ({
    kind: 'radio', label: option.label, sub: option.sub, radio: option.value === s.value, disabled: option.disabled,
    ok: () => { s.onPick(option.value); os.pop(); },
  })),
});

// 二次确认：危险操作的默认焦点放在安全选项上。
SCREENS.confirm = {
  title: (os, s) => s.title,
  bar: false,
  focusLabel: (os, s) => s.options[s.focus]?.label || '',
  animate: (os, s) => !!s.busy,
  draw(g, os, s, now) {
    const danger = s.options.some(option => option.danger), icon = s.icon || (danger ? 'warn' : 'info');
    g.circle(120, 62, 20, withAlpha(danger ? C.pulse : C.read, .16));
    g.icon(icon, 120, 62, danger ? C.pulse : C.read, icon === 'stop' ? .9 : 1.2);
    g.text(s.title, 120, 112, {size: 20, weight: 600, align: 'center', max: 200});
    g.wrap(s.text, 24, 138, 192, {size: 14, color: C.mist, line: 21, max: 3, align: 'center'});
    s.options.forEach((option, i) => {
      const y = 196 + i * 40, focused = i === s.focus, tint = option.danger ? C.danger : C.focus;
      g.rrect(24, y, 192, 34, 17, focused ? tint : C.card, focused ? null : C.line);
      const label = s.busy && focused ? '正在处理…' : option.label;
      g.text(label, 120, y + 23, {size: 14, weight: 600, align: 'center', color: focused ? '#ffffff' : option.danger ? C.pulse : C.ink});
    });
    if (s.busy) g.arc(120, 62, 26, (now / 3) % 360, (now / 3) % 360 + 200, C.mist, 2);
  },
  key(os, s, key) {
    if (s.busy) return true;
    if (key === 'up' || key === 'down') { s.focus = (s.focus + 1) % s.options.length; return true; }
    if (key !== 'ok') return false;
    const option = s.options[s.focus];
    if (!option.run) { os.pop(); return true; }
    s.busy = true;
    Promise.resolve(option.run()).then(() => {
      if (!s.alive) return;
      os.popScreens(1 + (option.alsoClose || 0));
    }).catch(error => {
      if (!s.alive) return;
      s.busy = false;
      os.toast(error.message, 'error');
    });
    return true;
  },
};

// 说明页：上下键滚动正文，确认或返回键关闭。
SCREENS.info = {
  title: (os, s) => s.title,
  init(s) { s.offset = 0; },
  draw(g, os, s) {
    const rows = g.lines(s.text, 200, 14), height = rows.length * 24;
    s.max = Math.max(0, height - (BOTTOM - TOP - 12));
    g.clip(0, TOP, W, BOTTOM - TOP, () => rows.forEach((row, i) => g.text(row, 20, TOP + 22 + i * 24 - s.offset, {size: 14, color: C.ink})));
    if (s.max) pageDots(g, 2, s.offset > s.max / 2 ? 1 : 0);
  },
  key(os, s, key) {
    if (key === 'up' || key === 'down') { s.offset = clamp(s.offset + (key === 'down' ? 48 : -48), 0, s.max || 0); return true; }
    if (key === 'ok') return os.pop();
    return false;
  },
};

// 新建演示：选择记录、处理环境、读取策略与速度后开始处理。
SCREENS.start = list({
  title: '新建演示',
  init(s, os) {
    const boot = os.ctx.bootstrap() || {}, settings = os.settings(), datasets = boot.datasets || [];
    const preferred = s.preset?.dataset || boot.defaults?.dataset_id;
    s.form = {
      dataset: datasets.some(d => d.id === preferred) ? preferred : datasets[0]?.id,
      mode: 'FREERTOS_QEMU', strategy: STRATEGY[settings.strategy] ? settings.strategy : 'continuous', phase: 0, speed: 1,
    };
  },
  retry: true,
  enter(os, s) { if (!(os.ctx.bootstrap()?.datasets || []).length) load(os, s, 'checked', () => os.ctx.refresh()); },
  empty: os => os.active()
    ? ['已有演示正在运行', '结束当前演示后才能新建。按确认键查看恢复过程。', 'pulse', C.pulse]
    : ['本机还没有公开数据', '在电脑终端运行 python -m scripts.download_dataset 后，按确认键重新检查。', 'data', C.amber],
  key: (os, s, key) => key === 'ok' && os.active() && os.replace('monitor'),
  items(os, s) {
    const boot = os.ctx.bootstrap() || {}, datasets = boot.datasets || [], caps = boot.capabilities || {};
    if (os.active() || !datasets.length) return [];
    const form = s.form, selected = datasets.find(d => d.id === form.dataset) || datasets[0];
    if (!form.dataset) form.dataset = selected.id;
    const limits = caps.qemu_limits || {};
    return [
      pickerRow(os, '数据记录', datasetShort(selected), datasets.map(d => ({value: d.id, label: datasetShort(d), sub: d.recovery_label || '主动恢复'})), form.dataset, value => { form.dataset = value; }),
      infoRow('处理环境', 'FreeRTOS / QEMU', caps.qemu ? C.mint : C.amber),
      pickerRow(os, '读取策略', STRATEGY[form.strategy], Object.entries(STRATEGY).map(([value, label]) => ({
        value, label, disabled: limits.strategies && !limits.strategies.includes(value) && '固件暂不支持此策略',
      })), form.strategy, value => { form.strategy = value; }),
      pickerRow(os, '起始偏移', `${form.phase} 秒`, PHASES.map(value => ({value, label: `${value} 秒`})), form.phase, value => { form.phase = value; }, form.strategy !== 'periodic' && '起始偏移仅用于周期读取'),
      pickerRow(os, '演示速度', `${form.speed} 倍`, [1, 2, 5, 10].map(value => ({
        value, label: `${value} 倍${value === 1 ? '（按记录时长）' : ''}`, disabled: limits.speeds && !limits.speeds.includes(value) && '固件不支持此速度',
      })), form.speed, value => { form.speed = value; }),
      {kind: 'button', label: s.busy ? '正在启动…' : '开始演示', color: C.pulse, disabled: !caps.qemu && 'FreeRTOS / QEMU 尚未就绪，请先构建固件并检查本地数据', ok: () => startDemo(os, s, selected)},
    ];
  },
});
async function startDemo(os, s, dataset) {
  if (s.busy) return;
  s.busy = true; os.note('开始演示'); os.touch();
  try {
    await os.ctx.startSession({
      name: `${dataset.label || datasetShort(dataset)}（腕端）`, origin: 'PUBLIC_DATA', dataset_id: dataset.id,
      execution_mode: s.form.mode, strategy: s.form.strategy, phase: s.form.phase, speed: s.form.speed,
    });
    if (s.alive) os.replace('monitor');
  } catch (error) { os.toast(error.message, 'error'); }
  finally { s.busy = false; os.touch(); }
}

// 恢复过程：五个页面分别显示脉率表盘、波形、关键窗口、读取计划和处理事件。
const MONITOR_PAGES = SCREEN_MAP[1].children[1].pages;
SCREENS.monitor = {
  title: (os, s) => os.live() ? MONITOR_PAGES[s.page] : '恢复过程',
  pages: MONITOR_PAGES,
  init(s) { s.signal = 'ppg'; },
  focusLabel: (os, s) => os.live() ? MONITOR_PAGES[s.page] : '当前没有演示',
  animate: (os, s) => s.page === 0 && !!os.active(),
  draw(g, os, s, now) {
    const live = os.live();
    if (!live) return drawMessage(g, '当前没有演示', '按确认键新建一次恢复演示。', {icon: 'pulse', color: C.pulse});
    [pagePulse, pageWave, pageWindows, pagePlan, pageEvents][s.page](g, os, live, s, now);
    pageDots(g, MONITOR_PAGES.length, s.page);
  },
  long: (os, s, key) => key === 'ok' && os.active() ? {ms: HOLD_MS.ok, label: '继续按住以停止演示', color: C.pulse} : null,
  key(os, s, key, kind) {
    if (!os.live()) {
      if (key === 'ok') return os.replace('start');
      return key === 'up' || key === 'down';
    }
    if (key === 'up' || key === 'down') {
      os.slide(key === 'down' ? 'up' : 'down');
      s.page = (s.page + (key === 'down' ? 1 : MONITOR_PAGES.length - 1)) % MONITOR_PAGES.length;
      return true;
    }
    if (key === 'ok' && kind === 'long') return os.confirmStop();
    if (key === 'ok') return os.push('actions', {monitor: s});
    return false;
  },
};
// 表盘覆盖整个处理片段（起点前 10 秒至恢复 124 秒），外圈为进度和三个关键窗口，内圈为策略读取时段。
function pagePulse(g, os, live, s, now) {
  const cx = 120, cy = 146, r = 84, angle = sec => 135 + 270 * (clamp(sec, -10, 124) + 10) / 134;
  const rel = relSeconds(live), finished = isFinished(live), phase = phaseOf(live);
  g.arc(cx, cy, r, 135, 405, C.line, 10);
  g.arc(cx, cy, r - 15, 135, 405, '#121a24', 4, 'butt');
  g.arc(cx, cy, r - 15, angle(-10), angle(0), C.read, 4, 'butt');
  for (const [a, b] of readingIntervals(live.config_snapshot)) g.arc(cx, cy, r - 15, angle(a), angle(b), C.read, 4, 'butt');
  if (rel > -10) g.arc(cx, cy, r, 135, angle(rel), '#c7d3e1', 10);
  for (const center of [0, 60, 120]) {
    const style = WINDOW_STYLE[windowState(live, center, finished).kind], a = angle(center) * Math.PI / 180;
    g.arc(cx, cy, r, angle(center - 4), angle(center + 4), style.arc, 13, 'butt');
    g.text(String(center), cx + Math.cos(a) * (r + 16), cy + Math.sin(a) * (r + 16) + 4, {size: 11, num: true, color: C.mist, align: 'center'});
  }
  const result = live.latest_result || {}, valid = os.connected() && result.valid && !finished;
  let value = valid ? String(Math.round(result.pulse_bpm)) : '--', caption = 'BPM', quality, qualityColor;
  if (finished) {
    const best = [120, 60, 0].map(center => ({center, ...windowState(live, center, true)})).find(item => item.kind === 'valid');
    value = best ? fixed(best.bpm, 0) : '--';
    caption = best ? `${best.center ? `${best.center} 秒` : '起点'}窗口 BPM` : '没有可用窗口';
    quality = '结果已保存'; qualityColor = C.mist;
  } else if (!os.connected()) { quality = '连接中断'; qualityColor = C.pulse; }
  else { quality = valid ? '质量检查通过' : reasonText(result.reasons); qualityColor = valid ? C.mint : C.amber; }
  let scale = 1.1;
  if (valid) { const beat = (now / 1000 * result.pulse_bpm / 60) % 1; scale = 1.1 + .25 * Math.max(0, 1 - beat / .2); }
  g.icon('heart', cx, cy - 48, valid || (finished && value !== '--') ? C.pulse : C.dim, scale);
  g.text(value, cx, cy + 16, {size: 58, num: true, weight: 700, align: 'center', color: value === '--' ? C.dim : finished ? C.mint : C.pulse});
  g.text(caption, cx, cy + 36, {size: 12, color: C.mist, align: 'center'});
  g.text(quality, cx, cy + 57, {size: 12, color: qualityColor, align: 'center', max: 120});
  const stage = phase === 'PREPARING' ? `距恢复起点 ${fixed(Math.max(0, -rel), 1)} 秒`
    : phase === 'RECOVERING' ? `恢复段 ${fixed(clamp(rel, 0, 124), 1)} 秒` : STAGE[phase] || '处理中';
  g.text(stage, cx, 262, {size: 14, align: 'center', color: phase === 'PREPARING' ? C.amber : C.ink});
}
function pageWave(g, os, live, s) {
  const end = live.engine_time_us || 0, start = end - 8e6, samples = live.waveform_tail || [];
  const result = live.latest_result || {}, valid = os.connected() && result.valid;
  g.rrect(18, 44, 62, 22, 11, C.card);
  g.text(s.signal === 'filtered' ? '滤波后' : '原始', 49, 60, {size: 12, color: C.mist, align: 'center'});
  g.text(valid ? `${Math.round(result.pulse_bpm)} BPM` : '-- BPM', 222, 61, {size: 16, num: true, weight: 700, align: 'right', color: valid ? C.pulse : C.dim});
  drawWave(g, samples, {x: 16, y: 74, w: 208, h: 100, key: s.signal, start, end});
  g.text('运动变化量', 18, 198, {size: 12, color: C.mist});
  drawMotion(g, samples, {x: 16, y: 206, w: 208, h: 32, start, end});
  g.text('最近 8 秒；断线为未读取或缺口', 120, 262, {size: 12, color: C.dim, align: 'center'});
}
function pageWindows(g, os, live) {
  const finished = isFinished(live);
  [0, 60, 120].forEach((center, i) => {
    const y = 42 + i * 54, st = windowState(live, center, finished), style = WINDOW_STYLE[st.kind];
    g.rrect(12, y, 216, 48, 12, C.card);
    g.rrect(12, y + 10, 3, 28, 1.5, style.arc);
    g.text(center === 0 ? '起点窗口' : `${center} 秒窗口`, 26, y + 21, {size: 14});
    g.text(`${center - 4}～${center + 4} 秒`, 26, y + 39, {size: 12, color: C.mist});
    if (st.kind === 'valid') g.text(fixed(st.bpm, 1), 218, y + 34, {size: 28, num: true, weight: 700, align: 'right', color: C.mint});
    else {
      g.text(style.label, 218, y + (st.kind === 'missing' ? 22 : 29), {size: 14, align: 'right', color: style.text});
      if (st.kind === 'missing') g.text(reasonText(st.w.reasons), 218, y + 39, {size: 12, align: 'right', color: C.dim, max: 100});
    }
  });
  const recovery = live.recovery || {};
  g.text('起点 − 60 秒', 18, 228, {size: 12, color: C.mist});
  g.text(deltaText(recovery.delta60), 222, 229, {size: 16, num: true, weight: 700, align: 'right'});
  g.text('起点 − 120 秒', 18, 254, {size: 12, color: C.mist});
  g.text(deltaText(recovery.delta120), 222, 255, {size: 16, num: true, weight: 700, align: 'right'});
}
function pagePlan(g, os, live) {
  const config = live.config_snapshot || {}, intervals = readingIntervals(config);
  const x = sec => 20 + sec / 124 * 200;
  g.text(STRATEGY[config.strategy] || '读取策略', 20, 66, {size: 16, weight: 600});
  g.text(`${engineName(live)}，${config.speed || 1} 倍演示`, 20, 86, {size: 12, color: C.mist});
  g.rrect(20, 112, 200, 12, 3, C.line);
  for (const [a, b] of intervals) g.rect(x(a), 112, x(b) - x(a), 12, C.read);
  for (const center of [0, 60, 120]) {
    g.rect(x(center) - .5, 104, 1, 28, C.mist);
    g.text(String(center), x(center), 148, {size: 11, num: true, color: C.mist, align: 'center'});
  }
  if (phaseOf(live) !== 'PREPARING') g.rect(x(clamp(relSeconds(live), 0, 124)) - 1, 100, 2, 36, C.mint);
  const total = intervals.reduce((sum, [a, b]) => sum + b - a, 0);
  g.text('恢复段读取', 20, 184, {size: 12, color: C.mist});
  g.text(`${fixed(total, 0)} / 124 秒`, 220, 185, {size: 16, num: true, weight: 700, align: 'right'});
  const [label, color] = {ON: ['正在读取', C.mint], WARMING: ['滤波预热', C.amber]}[live.acquisition_state] || ['未读取', C.dim];
  g.text('当前读取状态', 20, 212, {size: 12, color: C.mist});
  const width = g.text(isFinished(live) ? '已结束' : label, 220, 213, {size: 14, align: 'right', color: isFinished(live) ? C.mist : C.ink});
  if (!isFinished(live)) g.circle(220 - width - 9, 208, 4, color);
  g.text('蓝色为读取时段，绿色为当前时刻', 120, 262, {size: 12, color: C.dim, align: 'center'});
}
function pageEvents(g, os, live) {
  const events = (live.events || []).slice(-5).reverse(), t0 = t0Of(live);
  if (!events.length) return drawMessage(g, '尚无处理事件', '');
  events.forEach((event, i) => {
    const y = 44 + i * 44;
    if (i) g.rect(20, y - 4, 200, 1, C.line);
    g.text(`${fixed((event.time_us - t0) / 1e6, 1)} 秒`, 20, y + 14, {size: 12, num: true, color: C.mist});
    g.text(event.message || EVENT[event.type] || '处理状态已更新', 20, y + 33, {size: 14, max: 200, color: event.severity === 'warning' ? C.amber : event.severity === 'error' ? C.pulse : C.ink});
  });
}
SCREENS.actions = list({
  title: '演示操作',
  items(os, s) {
    const live = os.live(), monitor = s.monitor;
    if (!live) return [{label: '新建演示', icon: 'play', color: C.mint, ok: () => os.replace('start')}];
    const items = [
      {label: monitor.signal === 'filtered' ? '显示原始波形' : '显示滤波后波形', icon: 'pulse', color: C.pulse, ok: () => { monitor.signal = monitor.signal === 'filtered' ? 'ppg' : 'filtered'; monitor.page = 1; os.pop(); }},
      {label: '恢复起点说明', icon: 'info', color: C.read, ok: () => os.push('info', {title: '恢复起点', text: `起点取自原始记录的阶段标记，表示高负荷骑行结束、进入低阻力放松踩踏。\n本次片段第 ${fixed(t0Of(live) / 1e6, 1)} 秒为恢复起点。演示速度不会改变这个时刻。`})},
    ];
    if (os.active()) items.push({label: '停止演示', icon: 'stop', color: C.pulse, danger: true, ok: () => os.confirmStop(1)});
    else {
      items.push({label: '查看记录详情', icon: 'list', color: C.read, ok: () => os.push('record', {recordId: idOf(live)})});
      items.push({label: '新建演示', icon: 'play', color: C.mint, ok: () => os.replace('start')});
    }
    return items;
  },
});

// 记录库：最近 20 条公开数据处理记录，可进入详情、回放或删除。
// 默认名称较长，列表改用“受试者 + 读取策略”；用户改过的名称保持原样。
const recordTitle = record => {
  const meta = metaOf(record), name = record.name || '';
  return name && !(meta.label && name.startsWith(meta.label)) ? name : `${meta.subject || '记录'} ${STRATEGY[record.config_snapshot?.strategy] || ''}`.trim();
};
const recordRows = (os, records, open) => records.map(record => ({
  label: recordTitle(record), sub: `${shortDate(record.created_at_utc)}  ${STAGE[phaseOf(record)] || ''}`,
  dot: STAGE_COLOR[phaseOf(record)] || C.mist, chevron: true, ok: () => open(record),
}));
SCREENS.records = list({
  title: '记录库',
  enter(os, s) { load(os, s, 'records', () => os.ctx.api('/sessions?origin=PUBLIC_DATA&page_size=20').then(r => r.items || [])); },
  empty: () => ['还没有处理记录', '在新建演示中开始一次后，结果会保存在这里。', 'list', C.read],
  items: (os, s) => recordRows(os, s.records || [], record => os.push('record', {recordId: idOf(record)})),
});
SCREENS.record = list({
  title: '记录详情',
  enter(os, s) { load(os, s, 'record', () => os.ctx.api(`/sessions/${s.recordId}`)); },
  items(os, s) {
    const record = s.record;
    if (!record) return [];
    const done = isFinished(record), config = record.config_snapshot || {};
    const items = [
      {kind: 'custom', h: 96, label: '窗口结果', draw: (g, x, y, w) => drawWindowSummary(g, record, x, y, w)},
      done
        ? {kind: 'button', label: '回放此记录', ok: () => os.push('replay', {recordId: s.recordId, name: recordTitle(record)})}
        : {kind: 'button', label: '查看恢复过程', ok: () => os.push('monitor')},
      infoRow('起点 − 60 秒', deltaText(record.recovery?.delta60)),
      infoRow('起点 − 120 秒', deltaText(record.recovery?.delta120)),
      infoRow('状态', STAGE[phaseOf(record)] || '未知', STAGE_COLOR[phaseOf(record)]),
      infoRow('处理环境', engineName(record)),
      infoRow('读取策略', STRATEGY[config.strategy] || '未记录'),
      infoRow('创建时间', shortDate(record.created_at_utc)),
    ];
    if (done) items.push({kind: 'button', label: '删除记录', danger: true, ok: () => os.push('confirm', {
      title: '删除这条记录？', text: '处理归档会被删除，原始公开数据不受影响。', icon: 'trash',
      options: [{label: '保留记录'}, {label: '删除记录', danger: true, alsoClose: 1, run: async () => { await os.ctx.deleteRecord(s.recordId); os.toast('记录已删除', 'info'); }}],
    })});
    items.push({kind: 'note', text: '备注、标签与文件导出请在电脑端记录库中操作。'});
    return items;
  },
});
function drawWindowSummary(g, record, x, y, w) {
  g.text(recordTitle(record), x + 12, y + 22, {size: 14, weight: 600, max: w - 24});
  [0, 60, 120].forEach((center, i) => {
    const st = windowState(record, center, true), cx = x + 12 + i * 70;
    g.text(center ? `${center} 秒` : '起点', cx, y + 50, {size: 12, color: C.mist});
    if (st.kind === 'valid') g.text(fixed(st.bpm, 1), cx, y + 80, {size: 22, num: true, weight: 700, color: C.mint});
    else g.text(WINDOW_STYLE[st.kind].label, cx, y + 78, {size: 14, color: C.mist});
  });
}

// 记录回放：选择已结束的记录后，用播放、定位和逐秒前进检查窗口重算过程。
SCREENS.replays = list({
  title: '记录回放',
  enter(os, s) { load(os, s, 'records', () => os.ctx.api('/sessions?origin=PUBLIC_DATA&page_size=20').then(r => (r.items || []).filter(isFinished))); },
  empty: () => ['还没有可回放的记录', '完成或停止一次演示后，可以在这里回放。', 'replay', C.read],
  items: (os, s) => recordRows(os, s.records || [], record => os.push('replay', {recordId: idOf(record), name: recordTitle(record)})),
});
const REPLAY_CONTROLS = [
  {id: 'restart', label: '回到开头'}, {id: 'back', label: '后退 10 秒'}, {id: 'play', label: '播放或暂停'},
  {id: 'step', label: '前进 1 秒'}, {id: 'speed', label: '切换播放速度'},
];
const SPEEDS = [.5, 1, 2, 5, 10];
SCREENS.replay = {
  title: '记录回放', transient: true,
  init(s) { s.focus = 2; s.speed = 1; s.connected = true; },
  focusLabel: (os, s) => REPLAY_CONTROLS[s.focus].label,
  animate: (os, s) => !s.snapshot || !!s.snapshot.playing,
  enter(os, s) { if (!s.replayId && !s.opening) openReplay(os, s); },
  dispose(os, s) {
    s.stopSocket?.();
    if (s.replayId) os.ctx.api(`/replays/${s.replayId}/commands`, {method: 'POST', body: {type: 'close'}}).catch(() => {});
  },
  draw(g, os, s, now) {
    const value = s.snapshot;
    if (!value) return drawMessage(g, '正在打开回放', '', {icon: 'spin', now});
    const cursor = value.cursor_us ?? 0, duration = value.duration_us ?? 134e6, rel = relSeconds(value);
    g.text(s.name || '处理记录', 18, 56, {size: 12, color: C.mist, max: 204});
    const width = g.text(fixed(cursor / 1e6, 1), 18, 92, {size: 34, num: true, weight: 700});
    g.text(`/ ${fixed(duration / 1e6, 0)} 秒`, 24 + width, 92, {size: 12, color: C.mist});
    g.text(rel < 0 ? `起点前 ${fixed(-rel, 1)} 秒` : `恢复 ${fixed(rel, 1)} 秒`, 222, 92, {size: 12, color: C.mist, align: 'right'});
    g.rrect(18, 100, 204, 4, 2, C.line);
    g.rrect(18, 100, 204 * clamp(cursor / duration, 0, 1), 4, 2, C.read);
    const result = value.latest_result || {}, valid = s.connected && result.valid;
    g.icon('heart', 27, 126, valid ? C.pulse : C.dim);
    const bpm = g.text(valid ? fixed(result.pulse_bpm, 0) : '--', 40, 135, {size: 24, num: true, weight: 700, color: valid ? C.pulse : C.dim});
    g.text('BPM', 45 + bpm, 135, {size: 12, color: C.mist});
    g.text(s.connected ? valid ? '质量检查通过' : reasonText(result.reasons) : '回放连接中断', 222, 134, {size: 12, align: 'right', color: valid ? C.mint : C.amber, max: 110});
    drawWave(g, value.waveform_tail || [], {x: 16, y: 146, w: 208, h: 44, start: cursor - 8e6, end: cursor, empty: '尚无游标前数据'});
    [0, 60, 120].forEach((center, i) => {
      const st = windowState(value, center), style = WINDOW_STYLE[st.kind], x = 14 + i * 72;
      g.rrect(x, 196, 68, 22, 11, withAlpha(style.arc, .2));
      g.text(`${center ? center : '起点'} ${st.kind === 'valid' ? fixed(st.bpm, 0) : style.short}`, x + 34, 211, {size: 12, align: 'center', color: style.text, max: 62});
    });
    g.text(s.busy ? '正在执行…' : REPLAY_CONTROLS[s.focus].label, 120, 236, {size: 12, color: C.mist, align: 'center'});
    REPLAY_CONTROLS.forEach((control, i) => {
      const x = 17 + i * 42, focused = i === s.focus, color = focused ? '#ffffff' : C.ink;
      const disabled = control.id === 'step' && value.playing;
      g.rrect(x, 242, 38, 28, 10, focused ? C.focus : C.card);
      if (control.id === 'restart') g.icon('restart', x + 19, 256, color, .85);
      else if (control.id === 'play') g.icon(value.playing ? 'pause' : 'play', x + 19, 256, color, .85);
      else g.text({back: '−10', step: '+1', speed: `${s.speed}×`}[control.id], x + 19, 261, {size: 13, num: true, weight: 700, align: 'center', color: disabled ? C.dim : color});
    });
  },
  key(os, s, key) {
    if (key === 'up' || key === 'down') { s.focus = (s.focus + (key === 'down' ? 1 : REPLAY_CONTROLS.length - 1)) % REPLAY_CONTROLS.length; return true; }
    if (key !== 'ok') return false;
    const value = s.snapshot;
    if (!value || s.busy) return true;
    const id = REPLAY_CONTROLS[s.focus].id;
    if (id === 'restart') replayCommand(os, s, 'restart');
    else if (id === 'back') replayCommand(os, s, 'seek', {time_us: Math.max(value.first_us ?? 0, (value.cursor_us ?? 0) - 10e6)});
    else if (id === 'play') replayCommand(os, s, value.playing ? 'pause' : 'play');
    else if (id === 'step') { if (value.playing) os.toast('请先暂停，再逐秒前进', 'info'); else replayCommand(os, s, 'step'); }
    else { s.speed = SPEEDS[(SPEEDS.indexOf(s.speed) + 1) % SPEEDS.length]; replayCommand(os, s, value.playing ? 'play' : 'pause'); }
    return true;
  },
};
async function openReplay(os, s) {
  s.opening = true;
  try {
    const created = await os.ctx.api('/replays', {method: 'POST', body: {source_session_id: s.recordId}});
    const replayId = created.replay_id || created.id;
    if (!s.alive) { os.ctx.api(`/replays/${replayId}/commands`, {method: 'POST', body: {type: 'close'}}).catch(() => {}); return; }
    s.replayId = replayId; s.snapshot = created;
    s.stopSocket = os.ctx.socket(`/replays/${replayId}/live`, value => { if (s.alive) { s.snapshot = value; os.touch(); } }, connected => { s.connected = connected; os.touch(); });
  } catch (error) {
    if (s.alive) { os.toast(error.message, 'error'); os.removeScreen(s); }
  } finally { s.opening = false; os.touch(); }
}
async function replayCommand(os, s, type, extra = {}) {
  s.busy = true; os.touch();
  try {
    await os.ctx.api(`/replays/${s.replayId}/commands`, {method: 'POST', body: {type, speed: s.speed, ...extra}});
    const value = await os.ctx.api(`/replays/${s.replayId}`);
    if (s.alive) s.snapshot = value;
  } catch (error) {
    if (!s.alive) return;
    os.toast(error.message, 'error');
    if (error.status === 404) os.removeScreen(s);
  } finally { s.busy = false; os.touch(); }
}

// 策略比较：在腕端发起或停止后台比较，并查看三种策略的窗口可用率和读取时长。
const jobRunning = job => ['RUNNING', 'PENDING', 'CANCELLING'].includes(job?.status);
const JOB_STATUS = {RUNNING: '正在比较', PENDING: '等待运行', CANCELLING: '正在停止', CANCELLED: '已停止', COMPLETED: '已完成', COMPLETED_WITH_ERRORS: '已结束，含失败项', INTERRUPTED: '已中断'};
SCREENS.compare = list({
  title: '策略比较',
  init(s, os) { s.dataset = os.ctx.bootstrap()?.defaults?.dataset_id; },
  enter(os, s) {
    refreshCompare(os, s, true);
    s.timer = setInterval(() => { if (jobRunning(s.job)) refreshCompare(os, s); }, 1000);
  },
  leave(os, s) { clearInterval(s.timer); },
  items(os, s) {
    const datasets = os.ctx.bootstrap()?.datasets || [], running = jobRunning(s.job);
    const selected = datasets.find(d => d.id === s.dataset) || datasets[0];
    if (selected && !datasets.some(d => d.id === s.dataset)) s.dataset = selected.id;
    return [
      {kind: 'custom', h: 72, label: '比较进度', draw: (g, x, y, w) => drawCompareStatus(g, s, x, y, w, datasets)},
      pickerRow(os, '比较记录', datasetShort(selected), datasets.map(d => ({value: d.id, label: datasetShort(d)})), s.dataset, value => { s.dataset = value; }, running && '比较进行中，结束后再更换记录'),
      running
        ? {kind: 'button', label: s.job.status === 'CANCELLING' ? '正在停止…' : '停止比较', danger: true, ok: () => compareAction(os, s, 'cancel')}
        : {kind: 'button', label: s.busy ? '正在提交…' : '开始比较', color: C.amber, disabled: !selected && '本机还没有公开数据', ok: () => compareAction(os, s, 'start')},
      {kind: 'custom', h: 112, label: '窗口可用率', draw: (g, x, y, w) => drawCompareRates(g, s.job, x, y, w)},
      {kind: 'custom', h: 96, label: '读取时长', draw: (g, x, y, w) => drawCompareSeconds(g, s.job, x, y, w)},
      {kind: 'note', text: '每条记录按 0、5、10、15 秒四个偏移各运行三种策略。可用率是通过质量检查的比例，不是医学准确率。'},
    ];
  },
});
async function refreshCompare(os, s, first = false) {
  if (s.busy) return;
  const version = s.requestVersion = (s.requestVersion || 0) + 1;
  try {
    const job = s.job && !first ? await os.ctx.api(`/comparisons/${s.job.id}`) : (await os.ctx.api('/comparisons')).items?.[0] || null;
    if (s.alive && s.requestVersion === version) { s.job = job; s.error = null; os.touch(); }
  } catch (error) { if (s.alive && s.requestVersion === version && first) os.toast(error.message, 'error'); }
}
async function compareAction(os, s, action) {
  if (s.busy) return;
  s.busy = true; os.touch();
  // 操作使在途查询失效，防止迟到的旧结果覆盖新任务或取消状态。
  const version = s.requestVersion = (s.requestVersion || 0) + 1;
  try {
    const job = action === 'start'
      ? await os.ctx.api('/comparisons', {method: 'POST', body: {dataset_ids: [s.dataset], phases: PHASES}})
      : await os.ctx.api(`/comparisons/${s.job.id}/cancel`, {method: 'POST', body: {}});
    if (s.alive && s.requestVersion === version) {
      s.job = job;
      os.toast(action === 'start' ? '比较已开始' : '正在停止比较', 'info');
    }
  } catch (error) { os.toast(error.message, 'error'); }
  finally { s.busy = false; os.touch(); }
}
function drawCompareStatus(g, s, x, y, w, datasets) {
  const job = s.job;
  if (!job) {
    g.text('尚无比较任务', x + 12, y + 28, {size: 16, weight: 600});
    g.text('选择记录后开始一次比较', x + 12, y + 52, {size: 12, color: C.mist});
    return;
  }
  const progress = job.progress || {}, done = (progress.completed || 0) + (progress.failed || 0), total = progress.total || 0;
  g.text(JOB_STATUS[job.status] || job.status, x + 12, y + 26, {size: 16, weight: 600, color: jobRunning(job) ? C.amber : C.ink});
  g.text(`${done} / ${total} 次`, x + w - 12, y + 26, {size: 14, num: true, color: C.mist, align: 'right'});
  g.rrect(x + 12, y + 38, w - 24, 6, 3, C.line);
  if (total) g.rrect(x + 12, y + 38, (w - 24) * clamp(done / total, 0, 1), 6, 3, jobRunning(job) ? C.amber : C.mint);
  const dataset = datasets.find(d => d.id === (job.config?.dataset_ids?.[0] || job.config?.dataset_id));
  g.text(`${datasetShort(dataset)}，失败 ${progress.failed || 0} 次`, x + 12, y + 64, {size: 12, color: C.mist, max: w - 24});
}
const compareMetrics = job => {
  const metrics = Array.isArray(job?.metrics) ? job.metrics : job?.summary?.strategies || [];
  return ['continuous', 'critical', 'periodic'].map(strategy => metrics.find(m => m.strategy === strategy)).filter(Boolean);
};
function drawCompareRates(g, job, x, y, w) {
  const metrics = compareMetrics(job);
  g.text('窗口可用率', x + 12, y + 22, {size: 12, color: C.mist});
  ['起点', '60 秒', '120 秒'].forEach((label, i) => g.text(label, x + 104 + i * 40, y + 22, {size: 11, color: C.dim, align: 'center'}));
  if (!metrics.length) { g.text('完成一组三策略配对后显示', x + 12, y + 62, {size: 12, color: C.dim}); return; }
  metrics.forEach((m, row) => {
    const ry = y + 46 + row * 22;
    g.text({continuous: '连续', critical: '关键窗口', periodic: '周期'}[m.strategy], x + 12, ry, {size: 12, color: C.ink});
    [0, 60, 120].forEach((center, i) => {
      const rate = m[`window${center}_rate`] ?? m.window_availability?.[String(center)];
      const color = !Number.isFinite(rate) ? C.dim : rate >= .8 ? C.mint : rate >= .3 ? C.amber : C.mist;
      g.text(Number.isFinite(rate) ? `${Math.round(rate * 100)}%` : '—', x + 104 + i * 40, ry, {size: 14, num: true, weight: 700, color, align: 'center'});
    });
  });
}
function drawCompareSeconds(g, job, x, y, w) {
  const metrics = compareMetrics(job);
  g.text('恢复段平均读取时长', x + 12, y + 22, {size: 12, color: C.mist});
  if (!metrics.length) { g.text('暂无数据', x + 12, y + 52, {size: 12, color: C.dim}); return; }
  metrics.forEach((m, row) => {
    const value = m.on_seconds ?? m.mean_on_seconds ?? 0, ry = y + 38 + row * 19;
    g.rrect(x + 12, ry, 120, 8, 4, C.line);
    g.rrect(x + 12, ry, 120 * clamp(value / 124, 0, 1), 8, 4, m.strategy === 'critical' ? C.mint : C.read);
    g.text(`${fixed(value, 1)} 秒`, x + w - 12, ry + 9, {size: 12, num: true, color: C.ink, align: 'right'});
  });
}

// 数据与片段：查看本机公开记录的来源信息，并逐列绘制原始脉搏波预览。
const RANGES = [{value: 0, label: '起点窗口 −4～4 秒'}, {value: 60, label: '60 秒窗口 56～64 秒'}, {value: 120, label: '120 秒窗口 116～124 秒'}, {value: 'all', label: '完整片段 134 秒'}];
SCREENS.data = list({
  title: '数据与片段',
  retry: true,
  enter(os, s) { load(os, s, 'datasets', () => os.ctx.api('/datasets').then(r => r.items || [])); },
  empty: () => ['本机还没有公开数据', '在电脑终端运行 python -m scripts.download_dataset 后，按确认键重新检查。', 'data', C.amber],
  items: (os, s) => (s.datasets || []).map(dataset => ({
    label: datasetShort(dataset), sub: `${dataset.duration_seconds || 134} 秒片段，起点第 ${fixed((dataset.t0_us ?? 10e6) / 1e6, 0)} 秒`,
    icon: 'data', color: C.mint, chevron: true, ok: () => os.push('dataset', {dataset}),
  })),
});
SCREENS.dataset = list({
  title: (os, s) => s.dataset.subject || '数据详情',
  init(s) { s.range = 0; },
  enter(os, s) { if (!s.samples && !s.loadingSamples) loadSamples(os, s); },
  items(os, s) {
    const d = s.dataset, range = RANGES[s.range];
    const nextRange = () => { s.range = (s.range + 1) % RANGES.length; loadSamples(os, s); };
    return [
      {kind: 'custom', h: 116, label: '原始片段预览', draw: (g, x, y, w) => drawDatasetPreview(g, s, x, y, w), ok: nextRange},
      {label: '预览范围', sub: range.label, chevron: true, ok: nextRange},
      infoRow('受试者', d.subject || '—'),
      infoRow('脉搏波', `${d.sample_hz || 64} Hz`),
      infoRow('三轴加速度', `${d.accel_sample_hz || 32} Hz`),
      infoRow('处理片段', `${d.duration_seconds || 134} 秒`),
      infoRow('恢复起点', `第 ${fixed((d.t0_us ?? 10e6) / 1e6, 1)} 秒`),
      infoRow('恢复方式', d.recovery_label || '主动恢复'),
      infoRow('许可', 'ODC-By 1.0'),
      {kind: 'button', label: '用此记录新建演示', color: C.mint, ok: () => os.push('start', {preset: {dataset: d.id}})},
      {kind: 'note', text: 'HR 文件由同一脉搏波计算，只作对照，不是独立心电参考。'},
    ];
  },
});
async function loadSamples(os, s) {
  const d = s.dataset, choice = RANGES[s.range].value, t0 = d.t0_us ?? 10e6;
  const start = choice === 'all' ? 0 : t0 + (choice - 4) * 1e6, end = choice === 'all' ? d.duration_us || 134e6 : t0 + (choice + 4) * 1e6;
  const version = (s.version || 0) + 1;
  s.version = version; s.loadingSamples = true; os.touch();
  try {
    const response = await os.ctx.api(`/datasets/${encodeURIComponent(d.id)}/samples?start_us=${start}&end_us=${end}&max_points=416`);
    if (s.alive && version === s.version) s.samples = {start, end, rows: response.samples || [], stride: response.decimation || 1};
  } catch (error) { if (s.alive) os.toast(error.message, 'error'); }
  finally { if (s.alive && version === s.version) { s.loadingSamples = false; os.touch(); } }
}
function drawDatasetPreview(g, s, x, y, w) {
  g.text('原始脉搏波（设备相对单位）', x + 12, y + 20, {size: 12, color: C.mist});
  if (s.loadingSamples || !s.samples) { g.text('正在读取片段', x + w / 2, y + 70, {size: 12, color: C.dim, align: 'center'}); return; }
  const {start, end, rows, stride} = s.samples;
  drawWave(g, rows, {x: x + 8, y: y + 30, w: w - 16, h: 76, start, end, stride, color: C.pulse});
}

// 系统状态：本地服务、处理任务（FreeRTOS 时为内核实测）和本机信息，每 2 秒刷新。
const SYSTEM_PAGES = SCREEN_MAP[1].children[6].pages;
const TASK_NAMES = {Acquisition: '采集读取', DSP: '信号处理', Session: '状态控制', Feedback: '提示反馈', UI: '屏幕显示', System: '诊断上报', SessionEngine: 'Python 会话'};
const TASK_STATES = {0: '执行中', 1: '就绪', 2: '等待', 3: '挂起', 4: '已删除', 5: '未知'};
SCREENS.system = {
  title: (os, s) => SYSTEM_PAGES[s.page],
  pages: SYSTEM_PAGES,
  focusLabel: (os, s) => SYSTEM_PAGES[s.page],
  enter(os, s) { refreshSystem(os, s); s.timer = setInterval(() => refreshSystem(os, s), 2000); },
  leave(os, s) { clearInterval(s.timer); },
  animate: (os, s) => !s.status && !s.error,
  draw(g, os, s, now) {
    const status = s.status;
    if (!status) {
      if (s.error) drawMessage(g, '读取失败', s.error, {icon: 'cross', color: C.pulse});
      else drawMessage(g, '正在读取', '', {icon: 'spin', now});
    } else [systemOverview, systemTasks, systemAbout][s.page](g, os, status);
    pageDots(g, SYSTEM_PAGES.length, s.page);
  },
  key(os, s, key) {
    if (key === 'up' || key === 'down') {
      os.slide(key === 'down' ? 'up' : 'down');
      s.page = (s.page + (key === 'down' ? 1 : SYSTEM_PAGES.length - 1)) % SYSTEM_PAGES.length;
      return true;
    }
    if (key === 'ok') { refreshSystem(os, s).then(() => os.toast('状态已刷新', 'info')); return true; }
    return false;
  },
};
async function refreshSystem(os, s) {
  try { const status = await os.ctx.api('/system/status'); if (s.alive) { s.status = status; s.error = null; } }
  catch (error) { if (s.alive) s.error = error.message; }
  finally { if (s.alive) os.touch(); }
}
function rows(g, entries, top = 44) {
  entries.forEach(([label, value, color], i) => {
    const y = top + i * 40;
    if (i) g.rect(18, y - 6, 204, 1, C.line);
    g.text(label, 18, y + 19, {size: 14, color: C.mist});
    g.text(value, 222, y + 19, {size: 14, color: color || C.ink, align: 'right', max: 120});
  });
}
const fmtBytes = value => typeof value !== 'number' ? '—' : value >= 1e9 ? `${(value / 1e9).toFixed(1)} GB` : `${(value / 1e6).toFixed(0)} MB`;
const fmtUptime = seconds => !Number.isFinite(seconds) ? '—' : seconds < 60 ? `${Math.round(seconds)} 秒` : seconds < 3600 ? `${Math.floor(seconds / 60)} 分钟` : `${(seconds / 3600).toFixed(1)} 小时`;
function systemOverview(g, os, status) {
  const qemu = !!status.rtos_running;
  rows(g, [
    ['本地服务', status.service === 'RUNNING' ? '运行中' : '未就绪', status.service === 'RUNNING' ? C.mint : C.amber],
    ['已运行', fmtUptime(status.uptime_seconds)],
    ['处理环境', 'FreeRTOS / QEMU', qemu ? C.mint : C.mist],
    ['可用空间', fmtBytes(status.storage?.free_bytes)],
    ['处理记录', `${status.storage?.sessions ?? '—'} 条`],
  ]);
}
function systemTasks(g, os, status) {
  const tasks = status.tasks || [];
  if (!tasks.length) return drawMessage(g, '当前没有处理任务', '开始演示后显示对应处理环境的任务。', {icon: 'chip'});
  const qemu = !!status.rtos_running;
  g.text('FreeRTOS 内核实测', 18, 58, {size: 12, color: C.mist});
  g.text(qemu ? '心跳 / 栈余量' : '状态', 222, 58, {size: 12, color: C.dim, align: 'right'});
  tasks.slice(0, 6).forEach((task, i) => {
    const y = 72 + i * 32;
    g.rect(18, y, 204, 1, C.line);
    g.text(TASK_NAMES[task.name] || task.name, 18, y + 22, {size: 14, max: 84});
    const state = typeof task.state === 'number' ? TASK_STATES[task.state] || '未知' : STAGE[task.state] || task.state || '—';
    g.text(state, 108, y + 22, {size: 12, color: C.mist});
    if (qemu) g.text(`${task.heartbeat ?? '—'} / ${task.stack_high_water_words ?? '—'}`, 222, y + 22, {size: 12, num: true, align: 'right', color: C.ink});
  });
}
function systemAbout(g, os, status) {
  rows(g, [
    ['屏幕', '1.69 英寸 240×280'],
    ['颜色', 'RGB565 16 位'],
    ['按键', '4 个实体按键'],
    ['实时系统', 'FreeRTOS V11.2.0'],
    ['应用 / 算法', `${status.version || '0.2.0'} / ${status.algorithm_version || '—'}`],
  ]);
  g.text('非医疗器械，未做临床准确度验证', 120, 258, {size: 12, color: C.dim, align: 'center'});
}

// 设置：刷新、图表与默认策略写入本地服务；屏幕常亮，手动亮度只保存在本机。
const SETTING_KEYS = ['ui_hz', 'chart_seconds', 'strategy'];
const SETTING_DEFAULTS = {ui_hz: 10, chart_seconds: 10, strategy: 'continuous'};
const pickSettings = settings => Object.fromEntries(SETTING_KEYS.map(key => [key, settings?.[key] ?? SETTING_DEFAULTS[key]]));
const settingsDirty = s => SETTING_KEYS.some(key => s.draft[key] !== s.saved[key]);
SCREENS.settings = list({
  title: '设置',
  init(s, os) { s.saved = pickSettings(os.settings()); s.draft = {...s.saved}; },
  enter(os, s) {
    // 电脑端也能保存设置；恢复保留的腕端页面时同步已保存值，保留正在编辑的草稿。
    if (!s.busy && !settingsDirty(s)) { s.saved = pickSettings(os.settings()); s.draft = {...s.saved}; }
  },
  items(os, s) {
    const d = s.draft, dirty = settingsDirty(s);
    const set = key => value => { d[key] = value; };
    return [
      pickerRow(os, '界面刷新', `每秒 ${d.ui_hz} 次`, [5, 10].map(v => ({value: v, label: `每秒 ${v} 次`, sub: '只影响显示，处理仍为 64 Hz'})), d.ui_hz, set('ui_hz')),
      pickerRow(os, '电脑端图表时长', `最近 ${d.chart_seconds} 秒`, [10, 30].map(v => ({value: v, label: `最近 ${v} 秒`})), d.chart_seconds, set('chart_seconds')),
      pickerRow(os, '默认读取策略', STRATEGY[d.strategy], Object.entries(STRATEGY).map(([value, label]) => ({value, label, sub: '用于之后新建的演示'})), d.strategy, set('strategy')),
      pickerRow(os, '屏幕亮度（本机）', `${os.brightness}%`, [40, 60, 80, 100].map(v => ({value: v, label: `${v}%`, sub: '立即生效，不写入服务'})), os.brightness, value => os.setBrightness(value)),
      {kind: 'button', label: s.busy ? '正在保存…' : dirty ? '保存设置' : '设置已保存', color: C.mint, disabled: !dirty && !s.busy && '没有需要保存的修改', ok: () => saveSettings(os, s)},
      {kind: 'button', label: '恢复默认值', ok: () => { Object.assign(s.draft, SETTING_DEFAULTS); os.toast('已恢复默认值，保存后生效', 'info'); }},
    ];
  },
  key(os, s, key, kind) {
    if (key !== 'back' || !settingsDirty(s)) return false;
    if (kind === 'long') return os.home();
    return os.push('confirm', {
      title: '放弃修改？', text: '设置尚未保存，离开后恢复为已保存的值。',
      options: [{label: '继续编辑'}, {label: '放弃修改', danger: true, alsoClose: 1, run: () => {}}],
    });
  },
});
async function saveSettings(os, s) {
  if (s.busy) return;
  s.busy = true; os.touch();
  try {
    const saved = await os.ctx.saveSettings(s.draft);
    if (s.alive) { s.saved = pickSettings(saved); s.draft = {...s.saved}; }
    os.toast('设置已保存', 'info');
  } catch (error) { os.toast(error.message, 'error'); }
  finally { s.busy = false; os.touch(); }
}

// —— 腕端系统核心：页面栈、按键事件、提示与刷新循环 ——
class WristOS {
  constructor(ctx) {
    this.ctx = ctx;
    this.fb = panelCanvas(); this.layer = panelCanvas(); this.prev = panelCanvas();
    this.out = this.fb.getContext('2d', {willReadFrequently: true});
    this.g = new Gfx(this.layer.getContext('2d'));
    this.overlay = new Gfx(this.out);
    this.reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.stack = [this.make('face')];
    this.queue = []; this.current = null;
    this.held = {}; this.keyboard = {}; this.hold = null; this.trans = null;
    this.dirty = true; this.lastFrame = 0;
    this.brightness = Number(localStorage.getItem('repulse.wrist.brightness')) || 100;
    this.cache = {recent: null, recentAt: -Infinity};
    this.seen = {sid: undefined};
    this.log = []; this.noteText = '';
    this.running = false; this.interactive = false; this.target = null; this.root = null; this.hooks = {};
    this.hits = []; this.hitScreen = null; this.pointer = null;
    this.frame = this.frame.bind(this);
  }

  live() { return this.ctx.live(); }
  connected() { return this.ctx.connected(); }
  active() { const live = this.live(); return !!live && !isFinished(live); }
  settings() { return this.ctx.settings() || {}; }
  top() { return this.stack.at(-1); }
  titleOf(s) { const title = SCREENS[s.id].title; return typeof title === 'function' ? title(this, s) : title; }
  focusLabel(s) { return SCREENS[s.id].focusLabel?.(this, s) || ''; }
  touch() { this.dirty = true; }
  note(text) { this.noteText = text; }

  // 页面栈：进入新页面时暂停当前页面的轮询，返回时恢复。
  make(id, params = {}) {
    const s = {id, focus: 0, scroll: 0, scrollTarget: 0, page: 0, alive: true, reduced: this.reduced, ...params};
    SCREENS[id].init?.(s, this);
    return s;
  }
  push(id, params = {}) {
    if (!SCREENS[id]) return false;
    this.slide('left');
    const from = this.top();
    if (from) SCREENS[from.id].leave?.(this, from);
    const s = this.make(id, params);
    this.stack.push(s);
    if (this.running) SCREENS[id].enter?.(this, s);
    this.changed();
    return true;
  }
  discard(s) {
    SCREENS[s.id].leave?.(this, s);
    s.alive = false;
    SCREENS[s.id].dispose?.(this, s);
  }
  pop() {
    if (this.stack.length <= 1) return false;
    return this.popScreens(1);
  }
  popScreens(count) {
    count = Math.min(count, this.stack.length - 1);
    if (count <= 0) return false;
    this.slide('right');
    for (let i = 0; i < count; i++) this.discard(this.stack.pop());
    const top = this.top();
    if (this.running) SCREENS[top.id].enter?.(this, top);
    this.changed();
    return true;
  }
  replace(id, params = {}) {
    this.slide('left');
    this.discard(this.stack.pop());
    const s = this.make(id, params);
    this.stack.push(s);
    if (this.running) SCREENS[id].enter?.(this, s);
    this.changed();
    return true;
  }
  hasUnsavedSettings() { return this.stack.some(s => s.id === 'settings' && settingsDirty(s)); }
  discardSettingsDrafts() {
    for (const s of this.stack) if (s.id === 'settings') s.draft = {...s.saved};
  }
  // 所有会移除设置页的快捷入口都先保护草稿，包括从选择页长按返回。
  leaveSettings(action) {
    if (!this.hasUnsavedSettings()) return action();
    return this.push('confirm', {
      title: '放弃修改？', text: '设置尚未保存，离开后恢复为已保存的值。',
      options: [{label: '继续编辑'}, {label: '放弃修改', danger: true, run: () => {
        this.discardSettingsDrafts(); action();
      }}],
    });
  }
  home() { return this.leaveSettings(() => this.popScreens(this.stack.length - 1)); }
  removeScreen(s) {
    const index = this.stack.indexOf(s);
    if (index <= 0) return;
    if (index === this.stack.length - 1) { this.pop(); return; }
    this.stack.splice(index, 1);
    this.discard(s);
    this.changed();
  }
  // 页面栏跳转：重建“表盘 → 菜单 → 目标页面”的栈，与逐级按键到达的状态一致。
  jump(id, page = 0) {
    if (!MAP_IDS.has(id)) return;
    return this.leaveSettings(() => this.jumpTo(id, page));
  }
  jumpTo(id, page = 0) {
    this.slide('left');
    while (this.stack.length > 1) this.discard(this.stack.pop());
    if (id !== 'face') this.stack.push(this.make('menu'));
    if (id !== 'face' && id !== 'menu') this.stack.push(this.make(id, {page}));
    const top = this.top();
    if (this.running) SCREENS[top.id].enter?.(this, top);
    this.record('页面栏', 'click', `跳转到${this.titleOf(top)}`);
    this.changed();
  }
  slide(direction) {
    if (this.reduced || !this.running) return;
    const ctx = this.prev.getContext('2d');
    ctx.clearRect(0, 0, W, H); ctx.drawImage(this.layer, 0, 0);
    this.trans = {direction, start: performance.now()};
  }
  confirmStop(alsoClose = 0) {
    return this.push('confirm', {
      title: '停止演示？', text: '已处理的数据会保存，尚未到达的窗口记为缺测。', icon: 'stop',
      options: [
        {label: '继续演示'},
        {label: '停止演示', danger: true, alsoClose, run: async () => { await this.ctx.cancelSession(); this.toast('演示已停止，记录已保存', 'info'); }},
      ],
    });
  }

  // 提示条与振动：窗口结果、阶段变化和错误及时显示，屏幕始终保持常亮。
  toast(text, kind = 'info', detail = '') {
    const title = kind === 'error' ? '操作未完成' : text, body = kind === 'error' ? text : detail;
    // 新的数据提示替换尚未看完的旧提示，避免高倍速演示时提示滞后于实际进度。
    if (kind === 'notify') {
      this.queue = this.queue.filter(item => item.kind !== 'notify');
      if (this.current?.kind === 'notify') {
        const age = performance.now() - this.current.start;
        this.current.ms = Math.min(this.current.ms, Math.max(age + 180, 900));
      }
    }
    this.queue.push({title, body, kind, ms: kind === 'error' ? TOAST_MS + 1500 : TOAST_MS});
    if (this.queue.length > 3) this.queue.shift();
    this.touch();
  }
  notify(title, detail) {
    this.toast(title, 'notify', detail);
    const watch = this.root?.querySelector('.watch');
    if (watch && !this.reduced) { watch.classList.remove('buzz'); void watch.offsetWidth; watch.classList.add('buzz'); }
  }
  setBrightness(value) {
    this.brightness = value;
    localStorage.setItem('repulse.wrist.brightness', String(value));
    this.applyBacklight();
  }
  applyBacklight() {
    if (!this.target || !this.interactive) return;
    this.target.style.filter = this.brightness < 100 ? `brightness(${this.brightness / 100})` : '';
  }
  async loadRecent() {
    if (performance.now() - this.cache.recentAt < 8000) return;
    this.cache.recentAt = performance.now();
    try {
      const response = await this.ctx.api('/sessions?origin=PUBLIC_DATA&page_size=10');
      this.cache.recent = (response.items || []).find(isFinished) || null;
      this.touch();
    } catch { this.cache.recentAt = -Infinity; }
  }
  // 比较演示快照中的新事件，转换为腕端提示；首次接入时只同步状态不提示。
  watchLive() {
    const live = this.live(), sid = idOf(live) || null, phase = phaseOf(live);
    const keyOf = event => event.id || `${event.type}-${event.time_us}`;
    if (sid !== this.seen.sid) {
      const fresh = this.seen.sid !== undefined && sid && !isFinished(live);
      this.seen = {sid, ids: new Set((live?.events || []).map(keyOf)), phase};
      if (fresh) {
        this.notify('演示已开始', `${metaOf(live).subject || ''} ${STRATEGY[live.config_snapshot?.strategy] || ''}`.trim());
        if (['face', 'menu'].includes(this.top().id)) this.push('monitor');
      }
      return;
    }
    if (!live) return;
    for (const event of live.events || []) {
      const key = keyOf(event);
      if (this.seen.ids.has(key)) continue;
      this.seen.ids.add(key);
      if (event.type === 'T0_LOCKED') this.notify('已到恢复起点', '开始计算 0、60、120 秒窗口');
      else if (event.type === 'WINDOW_RESULT') {
        const w = event.payload?.window || {}, center = w.center_s ?? w.center_seconds, label = center ? `${center} 秒窗口` : '起点窗口';
        if (w.valid) this.notify(`${label} ${fixed(w.pulse_bpm, 1)} BPM`, '质量检查通过');
        else this.notify(`${label}缺测`, reasonText(w.reasons));
      } else if (event.type === 'PROCESSING_ERROR') this.notify('处理出错', event.message || '');
    }
    if (phase !== this.seen.phase) {
      this.seen.phase = phase;
      if (phase === 'COMPLETED') this.notify('演示完成', '结果已保存到记录库');
      else if (phase === 'INTERRUPTED' || phase === 'FAILED') this.notify(`演示${STAGE[phase]}`, '已处理的数据已保存');
      if (isFinished(live)) { this.cache.recentAt = -Infinity; this.loadRecent(); }
    }
  }

  // —— 按键：上/下立即响应并可连续移动；返回与确认区分短按和长按 ——
  keyDown(key) {
    if (this.held[key]) return;
    this.cancelPointer();
    const now = performance.now(), held = this.held[key] = {start: now};
    this.pressVisual(key, true);
    if (key === 'up' || key === 'down') {
      this.dispatch(key, 'short');
      held.timer = setTimeout(() => { held.repeat = setInterval(() => { this.dispatch(key, 'repeat'); }, REPEAT_MS); }, REPEAT_DELAY);
      return;
    }
    const s = this.top();
    const long = key === 'back'
      ? this.stack.length > 1 ? {ms: HOLD_MS.back, label: '继续按住回到表盘', color: C.mist} : null
      : SCREENS[s.id].long?.(this, s, key);
    if (long) {
      this.hold = {...long, key, start: now};
      held.timer = setTimeout(() => { held.fired = true; this.hold = null; this.dispatch(key, 'long'); }, long.ms);
    }
    this.touch();
  }
  keyUp(key) {
    const held = this.held[key];
    if (!held) return;
    delete this.held[key];
    clearTimeout(held.timer); clearInterval(held.repeat);
    this.pressVisual(key, false);
    if (this.hold?.key === key) this.hold = null;
    if (key === 'up' || key === 'down' || held.fired) { this.touch(); return; }
    this.dispatch(key, 'short');
  }
  releaseAll() {
    for (const [key, held] of Object.entries(this.held)) { clearTimeout(held.timer); clearInterval(held.repeat); this.pressVisual(key, false); }
    this.held = {}; this.keyboard = {}; this.hold = null;
  }
  pressVisual(key, down) { this.root?.querySelectorAll(`[data-key="${key}"]`).forEach(el => el.classList.toggle('pressed', down)); }
  dispatch(key, kind, source) {
    const before = this.top(), depth = this.stack.length, page = before.page, focus = before.focus;
    let handled = SCREENS[before.id].key?.(this, before, key, kind);
    if (!handled && key === 'back') handled = kind === 'long' ? this.home() : this.pop();
    if (kind !== 'repeat') this.record(source || key, kind, this.resultOf(before, depth, page, focus));
    this.touch(); this.changed();
  }
  resultOf(before, depth, page, focus) {
    const now = this.top();
    if (now !== before) return `${this.stack.length < depth ? '返回' : '进入'}${this.titleOf(now)}`;
    if (now.page !== page) return `切换到${this.titleOf(now)}`;
    if (now.focus !== focus) return `选中${this.focusLabel(now)}`;
    const note = this.noteText;
    this.noteText = '';
    return note || '页面不变';
  }
  record(key, kind, result) {
    const date = new Date();
    this.log.unshift({time: `${clock(date)}:${pad2(date.getSeconds())}`, key: KEY_LABEL[key] || key, kind: KIND_LABEL[kind] || '', result});
    this.log.length = Math.min(this.log.length, 8);
    this.hooks.onInput?.(this.log);
  }
  mapNode() {
    for (let i = this.stack.length - 1; i >= 0; i--) {
      const s = this.stack[i], id = MAP_ALIAS[s.id] || s.id;
      if (MAP_IDS.has(id)) return {id, page: SCREENS[s.id].pages && s.id === id ? s.page : null};
    }
    return {id: 'face', page: null};
  }
  describe() {
    const s = this.top(), title = this.titleOf(s), focus = this.focusLabel(s);
    return focus && focus !== title ? `${title}，${focus}` : title;
  }
  changed() {
    this.touch();
    const s = this.top();
    this.emitted = this.focusLabel(s);
    this.hooks.onChange?.({screen: s.id, page: s.page, focus: this.focusLabel(s), node: this.mapNode(), description: this.describe()});
  }

  // —— 生命周期：显示时运行刷新循环，离开页面时暂停轮询并关闭回放 ——
  start({target, root = null, interactive = false, onChange, onInput} = {}) {
    this.stop();
    this.running = true; this.interactive = interactive; this.root = root; this.hooks = {onChange, onInput};
    this.setTarget(target);
    const live = this.live();
    this.seen = {sid: idOf(live) || null, ids: new Set((live?.events || []).map(e => e.id || `${e.type}-${e.time_us}`)), phase: phaseOf(live)};
    if (interactive && root) this.bind(root);
    const top = this.top();
    SCREENS[top.id].enter?.(this, top);
    this.changed();
    this.raf = requestAnimationFrame(this.frame);
  }
  stop() {
    if (!this.running) return;
    this.running = false;
    cancelAnimationFrame(this.raf);
    this.unbind?.(); this.unbind = null;
    this.releaseAll();
    const top = this.top();
    SCREENS[top.id].leave?.(this, top);
    const transient = this.stack.findIndex(s => SCREENS[s.id].transient);
    if (transient > 0) while (this.stack.length > transient) this.discard(this.stack.pop());
    this.trans = null;
    if (this.target) this.target.style.filter = '';
    this.target = null; this.root = null; this.hooks = {};
  }
  setTarget(canvas) {
    this.target = canvas || null;
    this.applyBacklight();
    this.touch();
  }
  // 命中区域采用固定屏幕坐标；列表由本帧可见的裁剪行提供区域。
  hit(x, y, w, h, run, id) { this.hits.push({x, y, w, h, run, id}); }
  hitAt(point) {
    return this.hits.findLast(hit => point.x >= hit.x && point.x < hit.x + hit.w && point.y >= hit.y && point.y < hit.y + hit.h);
  }
  inputBlocked(s) {
    return !this.running || !this.interactive || this.top() !== s || this.hitScreen !== s
      || !!(this.trans && performance.now() - this.trans.start < SLIDE_MS);
  }
  activateHit(hit, point) {
    const before = this.top(), depth = this.stack.length, page = before.page, focus = before.focus, previous = this.log[0];
    hit.run(point);
    if (this.log[0] === previous) this.record('屏幕', 'tap', this.resultOf(before, depth, page, focus));
    this.changed();
  }
  registerTouch(s) {
    if (s.id === 'face') {
      this.hit(0, 0, W, H, () => this.dispatch('ok', 'tap', '屏幕'), 'menu');
      if (this.active()) this.hit(14, 122, 212, 118, () => this.push('monitor'), 'active');
      else if (this.cache.recent) this.hit(14, 166, 212, 76, () => this.push('record', {recordId: idOf(this.cache.recent)}), 'recent');
    } else if (s.id === 'confirm') {
      s.options.forEach((option, i) => this.hit(24, 196 + i * 40, 192, 34, () => {
        if (s.busy) return;
        s.focus = i; this.dispatch('ok', 'tap', '屏幕');
      }, `confirm:${i}`));
    } else if (s.id === 'replay' && s.snapshot) {
      REPLAY_CONTROLS.forEach((control, i) => this.hit(17 + i * 42, 242, 38, 28, () => {
        if (s.busy) return;
        s.focus = i; this.dispatch('ok', 'tap', '屏幕');
      }, `replay:${control.id}`));
      this.hit(18, 96, 204, 16, point => {
        if (s.busy) return;
        const first = s.snapshot.first_us ?? 0, duration = s.snapshot.duration_us ?? 134e6;
        replayCommand(this, s, 'seek', {time_us: Math.round(first + clamp(point.x - 18, 0, 204) / 204 * (duration - first))});
      }, 'replay:seek');
    } else if (s.id === 'info') {
      this.hit(0, TOP, W, BOTTOM - TOP, () => this.dispatch('ok', 'tap', '屏幕'), 'info:close');
    }
    if (s.id === 'monitor' || s.id === 'system') {
      ['up', 'ok', 'down'].forEach((key, i) => this.hit(8 + i * 78, NAV_TOP, 68, H - NAV_TOP,
        () => this.dispatch(key, 'tap', '屏幕'), `nav:${key}`));
    }
    if (this.stack.length > 1) this.hit(0, 0, 38, BAR, () => this.dispatch('back', 'tap', '屏幕'), 'back');
  }
  drawFooter(s) {
    const g = this.overlay;
    g.rect(0, NAV_TOP, W, H - NAV_TOP, C.bg);
    const middle = s.id === 'system' ? '刷新' : this.live() ? '操作' : '新建';
    ['上一页', middle, '下一页'].forEach((label, i) => {
      const x = 8 + i * 78;
      g.rrect(x, NAV_TOP + 3, 68, 27, 10, i === 1 ? '#173575' : C.card, C.line);
      g.text(label, x + 34, NAV_TOP + 22, {size: 12, align: 'center', color: i === 1 ? '#c8dcff' : C.mist});
    });
  }
  screenPoint(event, canvas) {
    const rect = canvas.getBoundingClientRect();
    const x = (event.clientX - rect.left) * W / rect.width, y = (event.clientY - rect.top) * H / rect.height;
    // CSS 缩放和设备像素比只影响显示，触点始终映射到面板逻辑坐标。
    const r = PANEL.radius, cx = clamp(x, r, W - r), cy = clamp(y, r, H - r);
    return {x, y, inside: x >= 0 && x <= W && y >= 0 && y <= H && Math.hypot(x - cx, y - cy) <= r};
  }
  cancelPointer() {
    const pointer = this.pointer;
    this.pointer = null;
    if (!pointer) return;
    pointer.canvas.classList.remove('dragging');
    if (pointer.canvas.hasPointerCapture?.(pointer.id)) pointer.canvas.releasePointerCapture(pointer.id);
  }
  bindTouch(canvas) {
    if (!canvas) return () => {};
    const down = event => {
      if (event.isPrimary === false) { this.cancelPointer(); return; }
      if (event.button > 0 || this.pointer) return;
      const s = this.top(), point = this.screenPoint(event, canvas);
      if (!point.inside || this.inputBlocked(s)) return;
      event.preventDefault();
      canvas.focus({preventScroll: true});
      const hit = this.hitAt(point);
      this.pointer = {id: event.pointerId, canvas, s, start: point, last: point, moved: false,
        hit: hit?.id, scroll: s.scroll, offset: s.offset || 0};
      canvas.setPointerCapture?.(event.pointerId);
    };
    const move = event => {
      const p = this.pointer;
      if (!p || p.id !== event.pointerId) return;
      event.preventDefault();
      if (this.top() !== p.s) { this.cancelPointer(); return; }
      const point = this.screenPoint(event, canvas), dx = point.x - p.start.x, dy = point.y - p.start.y;
      p.last = point;
      if (Math.hypot(dx, dy) >= DRAG_PX) { p.moved = true; canvas.classList.add('dragging'); }
      if (!p.moved || Math.abs(dy) <= Math.abs(dx)) return;
      if (p.s.listLayout) {
        p.s.manualScroll = true;
        p.s.scrollTarget = clamp(p.scroll - dy, 0, p.s.listLayout.maxScroll);
        p.s.scroll = p.s.scrollTarget;
        p.scrolled = true;
      } else if (p.s.id === 'info') {
        p.s.offset = clamp(p.offset - dy, 0, p.s.max || 0);
        p.scrolled = true;
      }
      this.touch();
    };
    const up = event => {
      const p = this.pointer;
      if (!p || p.id !== event.pointerId) return;
      event.preventDefault();
      const point = this.screenPoint(event, canvas), dx = point.x - p.start.x, dy = point.y - p.start.y;
      this.cancelPointer();
      if (this.inputBlocked(p.s)) return;
      if (p.moved || Math.hypot(dx, dy) >= DRAG_PX) {
        if (dx >= SWIPE_PX && dx > Math.abs(dy)) this.dispatch('back', 'swipe', '屏幕');
        else if (p.scrolled) { this.record('屏幕', 'swipe', '滚动内容'); this.changed(); }
        else if (SCREENS[p.s.id].pages && Math.max(Math.abs(dx), Math.abs(dy)) >= SWIPE_PX) {
          this.dispatch(Math.abs(dy) > Math.abs(dx) ? dy < 0 ? 'down' : 'up' : 'down', 'swipe', '屏幕');
        }
        return;
      }
      if (!point.inside) return;
      const hit = this.hitAt(point);
      if (hit && hit.id === p.hit) this.activateHit(hit, point);
    };
    const cancel = event => { if (this.pointer?.id === event.pointerId) this.cancelPointer(); };
    const wheel = event => {
      const s = this.top();
      if (this.inputBlocked(s)) return;
      event.preventDefault();
      const factor = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? H : 1;
      const delta = event.deltaY * factor * H / canvas.getBoundingClientRect().height;
      if (s.listLayout) {
        s.manualScroll = true;
        s.scrollTarget = clamp(s.scrollTarget + delta, 0, s.listLayout.maxScroll);
        s.scroll = s.scrollTarget;
        this.record('屏幕', 'wheel', '滚动列表'); this.changed();
      } else if (s.id === 'info') {
        s.offset = clamp(s.offset + delta, 0, s.max || 0); this.touch();
      } else if (SCREENS[s.id].pages && delta && performance.now() - (this.lastWheel || -Infinity) > 150) {
        this.lastWheel = performance.now(); this.dispatch(delta > 0 ? 'down' : 'up', 'wheel', '屏幕');
      }
    };
    const handlers = {pointerdown: down, pointermove: move, pointerup: up, pointercancel: cancel, lostpointercapture: cancel};
    for (const [type, handler] of Object.entries(handlers)) canvas.addEventListener(type, handler);
    canvas.addEventListener('wheel', wheel, {passive: false});
    return () => {
      this.cancelPointer();
      for (const [type, handler] of Object.entries(handlers)) canvas.removeEventListener(type, handler);
      canvas.removeEventListener('wheel', wheel);
    };
  }
  bind(root) {
    const unbindTouch = this.bindTouch(this.target);
    const keyOfTarget = event => event.target.closest?.('[data-key]');
    const down = event => {
      const el = keyOfTarget(event);
      if (!el || event.button > 0) return;
      event.preventDefault();
      el.setPointerCapture?.(event.pointerId);
      this.keyDown(el.dataset.key);
    };
    const up = event => { const el = keyOfTarget(event); if (el) this.keyUp(el.dataset.key); };
    const click = event => {
      const el = keyOfTarget(event);
      if (el && event.detail === 0) { this.keyDown(el.dataset.key); this.keyUp(el.dataset.key); }
    };
    const keydown = event => {
      const key = KEYMAP[event.key];
      if (!key || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target;
      if (target.closest?.('input,select,textarea,[contenteditable="true"],#drawer-root')) return;
      const pusher = target.closest?.('[data-key]');
      if ((event.key === 'Enter' || event.key === ' ') && !pusher && target.closest?.('button,a,summary')) return;
      event.preventDefault();
      if (event.repeat) return;
      const mapped = pusher && (event.key === 'Enter' || event.key === ' ') ? pusher.dataset.key : key;
      this.keyboard[event.code] = mapped;
      this.keyDown(mapped);
    };
    const keyup = event => {
      const mapped = this.keyboard[event.code];
      if (!mapped) return;
      delete this.keyboard[event.code];
      this.keyUp(mapped);
    };
    const blur = () => { this.releaseAll(); this.cancelPointer(); };
    root.addEventListener('pointerdown', down);
    root.addEventListener('pointerup', up);
    root.addEventListener('pointercancel', up);
    root.addEventListener('lostpointercapture', up);
    root.addEventListener('click', click);
    window.addEventListener('keydown', keydown);
    window.addEventListener('keyup', keyup);
    window.addEventListener('blur', blur);
    this.unbind = () => {
      unbindTouch();
      root.removeEventListener('pointerdown', down);
      root.removeEventListener('pointerup', up);
      root.removeEventListener('pointercancel', up);
      root.removeEventListener('lostpointercapture', up);
      root.removeEventListener('click', click);
      window.removeEventListener('keydown', keydown);
      window.removeEventListener('keyup', keyup);
      window.removeEventListener('blur', blur);
    };
  }
  frame(now) {
    if (!this.running) return;
    this.raf = requestAnimationFrame(this.frame);
    this.watchLive();
    const s = this.top(), def = SCREENS[s.id];
    const moving = this.trans || this.hold || this.current || this.queue.length || Math.abs(s.scroll - s.scrollTarget) > .5 || def.animate?.(this, s);
    const interval = 1000 / (moving ? PANEL.fps : this.settings().ui_hz || 10);
    if (!this.dirty && now - this.lastFrame < interval) return;
    this.dirty = false; this.lastFrame = now;
    this.render(now);
    if (this.focusLabel(s) !== this.emitted) this.changed();
  }
  // 一帧：页面内容 → 切换动画合成 → 状态栏与提示 → RGB565 量化 → 输出到屏幕画布。
  render(now) {
    const s = this.top(), def = SCREENS[s.id], g = this.g;
    this.hits = []; this.hitScreen = s;
    g.clear();
    const paged = s.id === 'monitor' || s.id === 'system';
    if (paged) {
      // 为屏幕内的翻页按钮保留底栏；旧页面坐标在内容区内等比压缩，不遮挡数据。
      g.c.save(); g.c.beginPath(); g.c.rect(0, BAR, W, NAV_TOP - BAR); g.c.clip();
      g.c.translate(0, BAR); g.c.scale(1, (NAV_TOP - BAR) / (H - BAR)); g.c.translate(0, -BAR);
    }
    def.draw(g, this, s, now);
    if (paged) g.c.restore();
    this.registerTouch(s);
    const out = this.out;
    out.fillStyle = C.bg; out.fillRect(0, 0, W, H);
    const t = this.trans;
    if (t) {
      const p = clamp((now - t.start) / SLIDE_MS, 0, 1), e = 1 - (1 - p) ** 3;
      const horizontal = t.direction === 'left' || t.direction === 'right', sign = t.direction === 'left' || t.direction === 'up' ? -1 : 1;
      const offset = Math.round(sign * e * (horizontal ? W : H - BAR));
      out.save(); out.beginPath(); out.rect(0, def.bar === false ? 0 : BAR, W, H); out.clip();
      if (horizontal) { out.drawImage(this.prev, offset, 0); out.drawImage(this.layer, offset - sign * W, 0); }
      else { out.drawImage(this.prev, 0, offset); out.drawImage(this.layer, 0, offset - sign * (H - BAR)); }
      out.restore();
      if (p >= 1) this.trans = null;
    } else out.drawImage(this.layer, 0, 0);
    if (paged) this.drawFooter(s);
    this.drawOverlays(now);
    // 返回入口始终可见；提示不能遮挡它，也不能吞掉返回操作。
    if (def.bar !== false) this.drawBar(s);
    else if (this.stack.length > 1) this.drawBack();
    if (this.stack.length > 1) this.hit(0, 0, 38, BAR, () => this.dispatch('back', 'tap', '屏幕'), 'back');
    quantize(out);
    if (this.target) {
      const ctx = this.target.getContext('2d');
      ctx.drawImage(this.fb, 0, 0, this.target.width, this.target.height);
    }
  }
  drawBar(s) {
    const g = this.overlay;
    g.rect(0, 0, W, BAR, C.bg);
    this.drawBack();
    g.text(this.titleOf(s), 42, 25, {size: 14, weight: 600, max: 102});
    const width = g.text(clock(new Date()), 216, 25, {size: 16, num: true, weight: 700, align: 'right'});
    let x = 216 - width - 22;
    connectionGlyph(g, x, 24, this.connected());
    if (this.active()) g.circle(x - 10, 19, 3.5, C.pulse);
  }
  drawBack() {
    const g = this.overlay;
    g.rrect(7, 6, 29, 25, 9, C.card);
    g.icon('back', 22, 19, C.ink, .85);
  }
  drawOverlays(now) {
    const g = this.overlay;
    if (this.hold && now - this.hold.start > HOLD_HINT_MS) {
      const p = clamp((now - this.hold.start - HOLD_HINT_MS) / (this.hold.ms - HOLD_HINT_MS), 0, 1);
      g.rrect(26, 222, 188, 40, 20, '#16202b');
      g.clip(26, 222, 188 * p, 40, () => g.rrect(26, 222, 188, 40, 20, withAlpha(this.hold.color, .45)));
      g.text(this.hold.label, 120, 247, {size: 14, align: 'center'});
      this.hit(26, 222, 188, 40, () => {}, 'hold');
    }
    if (!this.current && this.queue.length) this.current = {...this.queue.shift(), start: now};
    const toast = this.current;
    if (!toast) return;
    const age = now - toast.start;
    if (age > toast.ms) { this.current = null; this.touch(); return; }
    const k = Math.min(clamp(age / 180, 0, 1), clamp((toast.ms - age) / 180, 0, 1)), ease = this.reduced ? 1 : 1 - (1 - k) ** 3;
    const body = toast.body ? g.lines(toast.body, 164, 12).slice(0, 3) : [];
    const h = 38 + body.length * 17, inset = this.top().id === 'face' ? 8 : BAR + 4;
    const y = Math.round(-h + ease * (h + inset));
    const error = toast.kind === 'error';
    g.rrect(10, y, 220, h, 18, error ? '#2a0f14' : '#17212c', error ? '#7a2732' : '#263445');
    const tint = error ? C.pulse : toast.kind === 'notify' ? C.pulse : C.mint;
    g.circle(34, y + 24, 12, withAlpha(tint, .2));
    g.icon(error ? 'cross' : toast.kind === 'notify' ? 'heart' : 'check', 34, y + 24, tint, error ? .8 : .75);
    g.text(toast.title, 54, y + 29, {size: 14, weight: 600, max: 166});
    body.forEach((row, i) => g.text(row, 54, y + 48 + i * 17, {size: 12, color: C.mist}));
    // 提示覆盖屏幕内容时，先关闭提示，不能穿透到下面的返回或菜单项。
    const top = Math.max(0, y), bottom = Math.min(H, y + h);
    if (bottom > top) this.hit(10, top, 220, bottom - top, () => { this.current = null; this.touch(); }, 'toast');
  }
}

export function createWrist(ctx) {
  return new WristOS(ctx);
}

// Canvas 绘图模块：展示真实波形并导出图片；腕端屏幕由 wrist.js 绘制。
// 根据屏幕像素比绘制坐标轴，横轴统一使用相对恢复起点的记录时间。
export function drawChart(canvas, samples = [], options = {}) {
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(rect.width, 240), height = rect.height || 220;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = width * dpr; canvas.height = height * dpr;
  const ctx = canvas.getContext('2d'); ctx.scale(dpr, dpr);
  ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, width, height);
  const pad = {left: 58, right: 18, top: 20, bottom: 32};
  const pw = width - pad.left - pad.right, ph = height - pad.top - pad.bottom;
  const key = options.key || 'ppg', color = options.color || '#245ff2';
  const end = options.end_us ?? samples.at(-1)?.time_us ?? 0;
  const start = options.start_us ?? Math.max(0, end - (options.seconds || 10) * 1e6);
  const visible = samples.filter(s => s.time_us >= start && s.time_us <= end);
  const motion=key==='accel';
  const values = visible.filter(s => (motion || s.on !== false && !s.gap) && Number.isFinite(s[key])).map(s => s[key]);
  let min = values.length ? Math.min(...values) : -1;
  let max = values.length ? Math.max(...values) : 1;
  if (max - min < .00001) { min -= 1; max += 1; }
  const margin = (max - min) * .15; min = motion ? Math.max(0,min-margin) : min-margin; max += margin;
  ctx.font = '11px ui-monospace, monospace'; ctx.fillStyle = '#667991';
  ctx.strokeStyle = '#e9eef5'; ctx.lineWidth = 1;
  for (let j = 0; j <= 4; j++) {
    const y = pad.top + ph * j / 4;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(width - pad.right, y); ctx.stroke();
    ctx.fillText((max - (max - min) * j / 4).toFixed(key === 'accel' ? 3 : 1), 3, y + 4);
  }
  const span = Math.max(end - start, 15625);
  for (let j = 0; j <= 5; j++) {
    const x = pad.left + pw * j / 5;
    ctx.beginPath(); ctx.moveTo(x, pad.top); ctx.lineTo(x, height - pad.bottom); ctx.stroke();
    ctx.fillText(`${((start + span * j / 5 - (options.t0_us || 0)) / 1e6).toFixed(0)}s`, x - 11, height - 9);
  }
  if (options.t0_us >= start && options.t0_us <= end) {
    const x = pad.left + (options.t0_us - start) / span * pw;
    ctx.strokeStyle = '#b47722'; ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.moveTo(x, pad.top); ctx.lineTo(x, height - pad.bottom); ctx.stroke();
    ctx.setLineDash([]); ctx.fillStyle = '#916723'; ctx.fillText('恢复起点', Math.min(x + 4, width - 72), 13);
  }
  ctx.strokeStyle = color; ctx.lineWidth = key === 'accel' ? 1.5 : 1.7; ctx.lineJoin = 'round'; ctx.beginPath();
  const stride = options.decimation || 1, period = options.period_us || 15625;
  let pen = false, previous;
  // PPG 在未读取或缺失处断线；加速度不受 PPG 读取策略影响。
  for (const sample of visible) {
    const valid = (motion || sample.on !== false && !sample.gap) && Number.isFinite(sample[key]);
    const broken = previous && (!motion && sample.segment_id !== previous.segment_id || sample.time_us - previous.time_us > period * stride * 1.6);
    if (!valid || broken) pen = false;
    if (valid) {
      const x = pad.left + (sample.time_us - start) / span * pw;
      const y = pad.top + (max - sample[key]) / (max - min) * ph;
      if (pen) ctx.lineTo(x, y); else ctx.moveTo(x, y);
      pen = true;
    }
    previous = sample;
  }
  ctx.stroke();
  if (!values.length) {
    ctx.fillStyle = '#63758d'; ctx.font = '14px -apple-system, sans-serif'; ctx.textAlign = 'center';
    ctx.fillText(options.empty || '此时段没有可显示的数据', width / 2, height / 2); ctx.textAlign = 'left';
  }
  canvas._chart = {samples, options};
}

// 在波形上方补充标题、时间轴说明和导出时间，生成可下载的 PNG。
export function exportPNG(canvas, title = 'RePulse · 公开人体记录') {
  if (!canvas) throw new Error('当前没有可导出的图表。');
  const out = document.createElement('canvas'); out.width = canvas.width; out.height = canvas.height + 72;
  const ctx = out.getContext('2d'); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, out.width, out.height);
  ctx.fillStyle = '#152238'; ctx.font = '15px -apple-system, sans-serif';
  ctx.fillText(title, 20, 27, out.width - 40);
  ctx.font = '12px -apple-system, sans-serif'; ctx.fillStyle = '#61748d';
  ctx.fillText(`导出时间：${new Date().toLocaleString('zh-CN')}；横轴为相对恢复起点的记录时间。`, 20, 50, out.width - 40);
  ctx.drawImage(canvas, 0, 72);
  const a = document.createElement('a'); a.href = out.toDataURL('image/png'); a.download = `repulse-${Date.now()}.png`; a.click();
}

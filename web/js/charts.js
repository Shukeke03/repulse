export function drawChart(canvas, samples = [], options = {}) {
  if (!canvas) return;
  const rect = canvas.getBoundingClientRect(), width = Math.max(rect.width, 240), height = rect.height || 200;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = width * dpr; canvas.height = height * dpr;
  const ctx = canvas.getContext('2d'); ctx.scale(dpr, dpr);
  ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, width, height);
  const pad = {left: 44, right: 14, top: 18, bottom: 26};
  const pw = width - pad.left - pad.right, ph = height - pad.top - pad.bottom;
  ctx.strokeStyle = '#edf1f7'; ctx.lineWidth = 1; ctx.font = '10px ui-monospace, monospace'; ctx.fillStyle = '#8a97a9';
  for (let j = 0; j <= 4; j++) {
    const y = pad.top + ph * j / 4;
    ctx.beginPath(); ctx.moveTo(pad.left, y); ctx.lineTo(width - pad.right, y); ctx.stroke();
  }
  const key = options.key || 'ppg', color = options.color || '#245ff2';
  const end = options.end_us ?? samples.at(-1)?.time_us ?? 0;
  const start = options.start_us ?? Math.max(0, end - (options.seconds || 10) * 1e6);
  const visible = samples.filter(s => s.time_us >= start && s.time_us <= end);
  const values = visible.filter(s => s.on !== false && !s.gap && Number.isFinite(s[key])).map(s => s[key]);
  let min = values.length ? Math.min(...values) : 0, max = values.length ? Math.max(...values) : 1;
  if (max - min < .00001) { min -= 1; max += 1; }
  const margin = (max - min) * .16; min -= margin; max += margin;
  for (let j = 0; j <= 4; j++) ctx.fillText((max - (max - min) * j / 4).toFixed(key === 'accel' ? 2 : 0), 4, pad.top + ph * j / 4 + 3);
  const span = Math.max(end - start, 1e6);
  for (let j = 0; j <= 5; j++) {
    const x = pad.left + pw * j / 5;
    ctx.beginPath(); ctx.moveTo(x, pad.top); ctx.lineTo(x, height - pad.bottom); ctx.stroke();
    ctx.fillText(`${((start + span * j / 5 - (options.t0_us || 0)) / 1e6).toFixed(0)}s`, x - 10, height - 7);
  }
  ctx.strokeStyle = color; ctx.lineWidth = key === 'accel' ? 1.5 : 1.8; ctx.lineJoin = 'round'; ctx.beginPath();
  let pen = false, previous;
  for (const s of visible) {
    const valid = s.on !== false && !s.gap && Number.isFinite(s[key]);
    if (!valid || (previous && (s.segment_id !== previous.segment_id || s.time_us - previous.time_us > 40000))) pen = false;
    if (valid) {
      const x = pad.left + (s.time_us - start) / span * pw, y = pad.top + (max - s[key]) / (max - min) * ph;
      if (pen) ctx.lineTo(x, y); else ctx.moveTo(x, y);
      pen = true;
    }
    previous = s;
  }
  ctx.stroke();
  if (!values.length) {
    ctx.fillStyle = '#8794a7'; ctx.font = '13px -apple-system, sans-serif'; ctx.textAlign = 'center';
    ctx.fillText(options.empty || '等待采集数据', width / 2, height / 2); ctx.textAlign = 'left';
  }
  canvas._chart = {samples, options};
}

export function exportPNG(canvas, title = 'RePulse · 模拟数据') {
  if (!canvas) return;
  const out = document.createElement('canvas'); out.width = canvas.width; out.height = canvas.height + 54;
  const ctx = out.getContext('2d'); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, out.width, out.height);
  ctx.fillStyle = '#152238'; ctx.font = '16px -apple-system, sans-serif'; ctx.fillText(`${title} · ${new Date().toLocaleString('zh-CN')}`, 24, 30);
  ctx.drawImage(canvas, 0, 54);
  const a = document.createElement('a'); a.href = out.toDataURL('image/png'); a.download = `repulse-${Date.now()}.png`; a.click();
}

export function drawWrist(canvas, snapshot, page = 0) {
  if (!canvas) return;
  canvas.width = 128; canvas.height = 64; const c = canvas.getContext('2d');
  c.fillStyle = '#122e31'; c.fillRect(0, 0, 128, 64); c.fillStyle = '#9af1d6';
  c.font = '7px monospace'; c.fillText(snapshot?.execution_mode==='FREERTOS_QEMU'?'SIM / FREERTOS':'SIM / PYTHON', 5, 8); c.fillText('BAT --', 92, 8);
  c.fillRect(4, 12, 120, 1);
  const state = snapshot?.session_state || snapshot?.status || 'IDLE';
  const result = snapshot?.latest_result || {}, elapsed = snapshot?.t0_us == null ? (snapshot?.engine_time_us || 0) / 1e6 : ((snapshot?.engine_time_us || 0) - snapshot.t0_us) / 1e6;
  if (!snapshot) { c.font = '12px monospace'; c.fillText('RePulse', 32, 34); c.font = '7px monospace'; c.fillText('READY FOR RECOVERY', 20, 50); }
  else if (page % 3 === 1) {
    c.font = '8px monospace'; c.fillText('KEY WINDOWS', 5, 25);
    (snapshot.key_windows || []).forEach((w, i) => c.fillText(`${w.center_s ?? [0,60,120][i]}s ${w.valid ? Math.round(w.pulse_bpm) + ' BPM' : (w.status || 'WAIT')}`, 5, 36 + i * 10));
  } else if (page % 3 === 2) {
    c.font = '8px monospace'; c.fillText('SIGNAL QUALITY', 5, 25); c.fillText(result.valid ? 'VALID' : (result.quality || 'WARMING'), 5, 39);
    c.font = '7px monospace'; c.fillText(String(result.reasons?.[0] || snapshot.acquisition_state || '--').slice(0, 21), 5, 52);
  } else {
    c.font = '22px monospace'; c.fillText(result.valid && result.pulse_bpm != null ? String(Math.round(result.pulse_bpm)) : '--', 8, 39);
    c.font = '8px monospace'; c.fillText('BPM', 82, 36); c.font = '7px monospace'; c.fillText(`${state.slice(0,12)} ${Math.max(0, elapsed).toFixed(0)}s`, 5, 53);
    if (state === 'PREPARING') c.fillText('A: MARK END', 5, 62);
  }
}

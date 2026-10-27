import {api, socket} from './api.js';
import {drawChart, drawWrist, exportPNG} from './charts.js';

const paths = {
  pulse: '<path d="M2 12h5l3-8 4 16 3-8h5"/>',
  home: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  archive: '<path d="M4 8h16v12H4zM3 4h18v4H3zM9 12h6"/>',
  replay: '<path d="M4 10a8 8 0 1 1 0 5M4 4v6h6"/><path d="m10 8 6 4-6 4z"/>',
  compare: '<path d="M5 20V10m7 10V4m7 16v-7M2 21h20"/>',
  data: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
  system: '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4M7 9h2m2 0h6m-10 4h10"/>',
  settings: '<path d="m10 3-1 3-3 1-3 3 2 2-1 4 4 1 2 4 3-2 3 1 2-4 3-2-2-3 1-3-4-1-2-3z"/><circle cx="12" cy="12" r="3"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  play: '<path d="m8 4 12 8-12 8z"/>',
  pause: '<path d="M8 4v16m8-16v16"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
};
const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.info}</svg>`;
const esc = value => String(value ?? '').replace(/[&<>"']/g, x => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[x]));
const number = (value, digits = 1) => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—';
const items = value => Array.isArray(value) ? value : value?.items || [];
const idOf = value => value?.session_id || value?.id;
const phaseOf = value => value?.session_state || value?.status || 'IDLE';
const finished = value => ['COMPLETED', 'CANCELLED', 'INTERRUPTED', 'FAILED'].includes(phaseOf(value));
const metaOf = value => value?.dataset || value?.config_snapshot?.dataset_metadata || value?.config_snapshot?.dataset || {};
const t0Of = value => value?.t0_us ?? metaOf(value).t0_us ?? 10000000;
const relative = value => ((value?.cursor_us ?? value?.engine_time_us ?? 0) - t0Of(value)) / 1e6;
const datasetName = value => value?.label || value?.name || value?.id || '公开运动记录';
const strategyNames = {continuous:'连续读取', critical:'关键窗口优先', periodic:'周期读取（约70秒）'};
const stageNames = {IDLE:'尚未开始', CREATED:'待开始', PREPARING:'起点前准备', RECOVERING:'恢复段处理', FINALIZING:'保存结果', COMPLETED:'已完成', CANCELLED:'已停止', INTERRUPTED:'已中断', FAILED:'失败', RUNNING:'运行中', PENDING:'待运行', CANCELLING:'正在停止', CANCELLED_JOB:'已停止', COMPLETED_WITH_ERRORS:'已结束，含失败项'};
const reasonsMap = {
  INSUFFICIENT_HISTORY:'起点前数据不足', HISTORY_INSUFFICIENT:'起点前数据不足',
  WARMING:'滤波预热中', WARMUP:'滤波预热中', WARMUP_INCOMPLETE:'滤波预热尚未完成',
  INSUFFICIENT_DATA:'有效样本不足', DATA_GAP:'输入数据有缺口', NO_CONTACT:'接触质量未通过检查',
  FLAT_SIGNAL:'信号波动不足', FLATLINE:'信号为平线', LOW_AMPLITUDE:'信号波动不足',
  SATURATION:'信号饱和', SATURATED:'信号饱和', POOR_SIGNAL:'信号质量未通过检查',
  MOTION:'运动干扰较强', MOTION_ARTIFACT:'运动干扰较强', ACQUISITION_OFF:'当前策略未读取此时段',
  NOT_ACQUIRED:'当前策略未读取此时段', OFF:'当前策略未读取此时段', STALE:'最近输入已超出有效时限',
  SENSOR_FAULT:'原始采集状态异常', SENSOR_TIMEOUT:'原始采集超时', NOT_READY:'尚未收到完整窗口',
  NOT_COLLECTED:'未处理到完整窗口', WAITING:'尚未到达窗口', PENDING:'尚未到达窗口',
  INSUFFICIENT_INTERVALS:'可用脉搏间期不足', INSUFFICIENT_PEAKS:'可用脉搏间期不足',
  INTERVAL_INCONSISTENT:'脉搏间期不一致', IRREGULAR_INTERVALS:'脉搏间期不一致',
  AUTOCORRELATION_MISMATCH:'波形周期一致性不足', OUT_OF_RANGE:'估计超出40–200 BPM范围',
  SESSION_ENDED:'演示已结束', PREPARING:'正在读取起点前数据', VALID:'质量检查通过', UNKNOWN:'等待质量检查',
  SERVICE_RESTART:'上次服务退出，处理已中断', SOURCE_EOF:'已到原始片段末尾',
};
const explain = value => (Array.isArray(value) ? value : value ? [value] : []).map(x => typeof x === 'string' ? reasonsMap[x] || '数据未通过质量检查' : x.message || '依赖窗口不可用').join('；');
const localDate = value => value ? new Date(value).toLocaleString('zh-CN', {year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}) : '—';
const fmtBytes = value => typeof value === 'number' ? value >= 1e9 ? `${(value/1e9).toFixed(1)} GB` : value >= 1e6 ? `${(value/1e6).toFixed(2)} MB` : `${Math.round(value/1000)} KB` : '—';
const titles = {'/':'工作台', '/monitor':'恢复过程', '/sessions':'记录库', '/replay':'记录回放', '/compare':'策略比较', '/lab':'数据与片段', '/system':'系统状态', '/settings':'设置'};
const state = {
  bootstrap:null, live:null, connected:false, route:'', token:0, cleanups:[],
  settings:null, settingsDirty:false, frozen:false, frozenSnapshot:null, signal:'ppg', seconds:10,
  wristPage:0, wristActivity:performance.now(), lastUI:0, drawerDirty:false, drawerRestore:null,
};

const on = (selector, event, callback) => document.querySelector(selector)?.addEventListener(event, callback);
const main = () => document.querySelector('#main');
const currentPath = () => (location.hash.slice(1) || '/').split('?')[0].replace('/datasets', '/lab');
const isCurrent = token => token === state.token;
const badge = (text, color = '') => `<span class="badge ${color}">${esc(text)}</span>`;
const stageBadge = record => badge(stageNames[phaseOf(record)] || '未知状态', ['COMPLETED'].includes(phaseOf(record)) ? 'green' : ['PREPARING','RECOVERING','RUNNING'].includes(phaseOf(record)) ? 'blue' : ['FAILED','INTERRUPTED'].includes(phaseOf(record)) ? 'red' : '');
const btn = (text, id, cls = '', image = '') => `<button type="button" class="btn ${cls}" ${id ? `id="${id}"` : ''}>${image ? icon(image) : ''}${text}</button>`;
const panel = (title, content, extra = '', cls = '') => `<section class="panel ${cls}"><div class="panel-header"><h2>${title}</h2>${extra}</div><div class="panel-body">${content}</div></section>`;
const heading = (title, text, actions = '') => `<div class="page-head"><div><h1>${title}</h1><p>${text}</p></div><div class="head-actions">${actions}</div></div>`;
const empty = (title, text, action = '') => `<div class="empty">${icon('data')}<h3>${title}</h3><p>${text}</p>${action}</div>`;
const foot = () => '<footer class="footer-line"><span>RePulse 复脉 · 运动恢复监护</span><span>公开人体记录 · 本地处理与保存</span></footer>';
const dataList = () => items(state.bootstrap?.datasets);
const sourceText = record => record?.origin === 'PUBLIC_DATA' ? '公开人体记录' : record?.origin === 'SIM' ? '历史模拟记录' : '来源未确认';
const engineText = record => record?.execution_mode === 'FREERTOS_QEMU' ? 'FreeRTOS / QEMU' : record?.execution_mode === 'STM32_DEVICE' ? 'STM32设备' : 'Python';
const active = () => state.live && !finished(state.live) ? state.live : null;

function toast(text, error = false) {
  const node = document.createElement('div'); node.className = `toast ${error ? 'error' : ''}`; node.textContent = text;
  document.querySelector('#toasts').append(node); setTimeout(() => node.remove(), error ? 7500 : 4000);
}
async function run(button, action) {
  if (button?.disabled) return;
  const old = button?.innerHTML;
  if (button) {button.disabled = true; button.innerHTML = '<span class="spinner"></span>处理中';}
  try { return await action(); }
  catch (error) { toast(error.message, true); }
  finally {if (button?.isConnected) {button.disabled = false; button.innerHTML = old;}}
}
function navigate(path) {location.hash = `#${path}`;}
function shell() {
  const nav = [['/','home'], ['/monitor','pulse'], ['/sessions','archive'], ['/replay','replay'], ['/compare','compare'], ['/lab','data'], ['/system','system'], ['/settings','settings']];
  document.querySelector('#app').innerHTML = `<aside class="sidebar"><a class="brand" href="#/"><span class="brand-mark">${icon('pulse')}</span><span><strong>RePulse</strong><small>复脉 · 运动恢复监护</small></span></a><nav aria-label="主要页面">${nav.map(([path,name]) => `<a class="nav-item" href="#${path}" data-nav="${path}">${icon(name)}<span>${titles[path]}</span></a>`).join('')}</nav><div class="sidebar-foot">数据保存在本机项目目录<br>界面按原始记录时间推进</div></aside><div class="content"><header class="topbar"><div class="breadcrumb"><a href="#/">RePulse 复脉</a><span>/</span><strong id="breadcrumb-title">工作台</strong></div><div class="top-right"><span id="global-source">公开数据演示</span><span id="connection-indicator">正在连接</span></div></header><div class="connection-banner" id="connection-banner" role="status">与本地服务的连接已断开，正在重连。当前读数暂不可用；后台处理由服务继续执行。</div><main class="main" id="main" tabindex="-1"></main></div>`;
}
function connection(connected) {
  state.connected = connected;
  const indicator = document.querySelector('#connection-indicator');
  if (indicator) indicator.innerHTML = `<i class="live-dot ${connected ? '' : 'off'}"></i>${connected ? '服务已连接' : '连接已断开'}`;
  document.querySelector('#connection-banner')?.classList.toggle('show', !connected);
  if (state.route === '/monitor') updateMonitor();
}
function globalState() {
  const node = document.querySelector('#global-source');
  if (node) node.textContent = active() ? `${engineText(state.live)} · ${stageNames[phaseOf(state.live)]}` : '公开数据演示';
}
async function bootstrap() {
  const data = await api('/bootstrap');
  state.bootstrap = data; state.settings = data.defaults || state.settings || {};
  state.seconds = state.settings.chart_seconds || 10;
  state.live = data.active_session || (finished(state.live) ? state.live : null);
  globalState(); return data;
}
async function sessionCommand(type) {
  if (!state.live) throw new Error('当前没有演示记录。');
  const record = state.live;
  const result = await api(`/sessions/${idOf(record)}/commands`, {method:'POST', body:{type, expected_revision:record.revision}});
  state.live = result.snapshot || await api(`/sessions/${idOf(record)}/snapshot`);
  globalState(); updateMonitor(); return result;
}
function bindSessionControls() {
  on('#cancel-session','click', event => {
    if (confirm('停止这次演示？已处理的数据会保存，尚未到达的窗口将显示缺测。')) {
      run(event.currentTarget, async () => {await sessionCommand('cancel'); toast('演示已停止，部分记录已保存。');});
    }
  });
  on('#view-result','click', () => openDetail(idOf(state.live)));
}
function metadataRows(meta) {
  return `<dl class="detail-list"><dt>数据记录</dt><dd>${esc(datasetName(meta))}</dd><dt>受试者编号</dt><dd>${esc(meta.subject || meta.subject_id || '—')}</dd><dt>恢复方式</dt><dd>${esc(meta.phase_description || meta.phase_event?.label || meta.recovery_description || '高负荷骑行结束后的主动恢复')}</dd><dt>原始采样率</dt><dd>脉搏波 ${number(meta.sample_hz || meta.sample_rate || 64,0)} Hz；三轴加速度 ${number(meta.accel_sample_hz || meta.accel_hz || 32,0)} Hz</dd><dt>处理片段</dt><dd>${number((meta.duration_us || 134000000)/1e6,1)}秒；恢复起点位于片段第${number((meta.t0_us ?? 10000000)/1e6,1)}秒</dd></dl>`;
}
function missingData() {
  return panel('本地还没有真实数据', `<p>演示需要先准备公开数据文件。文件会保存在项目的 <code>data/</code> 目录，后续运行无需联网。</p><ol class="help-list"><li>在项目终端激活 <code>repulse</code> 环境。</li><li>执行 <code>python -m scripts.download_dataset</code>。</li><li>下载完成后点击“重新检查数据”。</li></ol>${btn('重新检查数据','reload-datasets','primary','data')}`);
}
async function home(token) {
  const [records] = await Promise.all([api('/sessions?origin=PUBLIC_DATA&page_size=5'), bootstrap()]);
  if (!isCurrent(token)) return;
  const datasets = dataList(), running = active();
  main().innerHTML = `${heading('运动恢复演示','读取本地保存的公开人体记录，查看脉率、信号质量和恢复窗口。', running ? '<a class="btn primary" href="#/monitor">继续查看恢复过程</a>' : '')}
    ${datasets.length ? `<div class="grid two"><section class="panel"><div class="panel-header"><h2>新建演示</h2>${badge('真实记录','green')}</div><div class="panel-body"><form id="create-form"><div class="form-grid"><div class="field full"><label for="session-dataset">数据记录</label><select id="session-dataset">${datasets.map(d => `<option value="${esc(d.id)}">${esc(datasetName(d))}</option>`).join('')}</select></div><div class="field"><label for="session-name">演示名称（可选）</label><input id="session-name" maxlength="100" placeholder="留空使用记录名称"></div><div class="field"><label for="session-source">处理环境</label><select id="session-source"><option value="PYTHON">Python · 本机处理</option><option value="FREERTOS_QEMU" ${state.bootstrap?.capabilities?.qemu ? '' : 'disabled'}>FreeRTOS / QEMU${state.bootstrap?.capabilities?.qemu ? '' : ' · 尚未就绪'}</option></select><small id="source-hint">信号处理在本机Python中执行。</small></div><div class="field"><label for="session-speed">演示速度</label><select id="session-speed">${[1,2,5,10].map(v => `<option value="${v}">${v}倍${v===1?'（按记录时长）':''}</option>`).join('')}</select><small>只改变推进速度，不改变采样率或计算窗口。</small></div><div class="field"><label for="session-phase">周期读取起始偏移（秒）</label><select id="session-phase">${[0,5,10,15].map(v => `<option value="${v}">${v}秒</option>`).join('')}</select><small id="phase-help">仅周期读取使用，表示相对恢复起点的启动偏移。</small></div><div class="field full"><label>读取策略</label><div class="choices">${Object.entries(strategyNames).map(([v,label]) => `<button type="button" data-strategy="${v}" class="choice"><strong>${label}</strong><small>${v==='continuous'?'恢复段完整读取64 Hz波形':v==='critical'?'优先保留起点、60秒和120秒窗口':'每20秒读取一段，总时长约70秒，与关键窗口方案接近'}</small></button>`).join('')}</div></div></div></form></div><div class="card-footer"><span class="notice-label">${running ? '已有演示运行中，请先结束当前演示。' : '起点由原始阶段标记确定，会自动进入恢复段。'}</span><button class="btn primary" id="start-session" ${running?'disabled':''}>${icon('play')}开始演示</button></div></section><div class="stack">${panel('所选记录', '<div id="selected-dataset"></div>', '<a href="#/lab" id="selected-dataset-link" class="small">查看数据详情</a>')}${panel('处理环境', `<div class="env-item"><span>Python</span>${badge('已就绪','green')}</div><div class="env-item"><span>FreeRTOS / QEMU</span>${badge(state.bootstrap?.capabilities?.qemu?'已就绪':'尚未就绪',state.bootstrap?.capabilities?.qemu?'green':'')}</div><p class="panel-note">两种环境读取相同的真实文件。QEMU模式由固件中的任务完成处理；没有连接实时腕带。</p>`, '<a href="#/system" class="small">查看系统状态</a>')}</div></div>` : missingData()}
    <section class="panel spacer"><div class="panel-header"><h2>最近处理记录</h2><a href="#/sessions" class="small">查看记录库</a></div>${items(records).length ? sessionTable(items(records),false) : empty('还没有处理记录','开始一次演示后，可以在这里查看结果、回放或导出数据。')}</section>${foot()}`;
  on('#reload-datasets','click', e => run(e.currentTarget, async () => {await bootstrap(); await home(token);}));
  if (!datasets.length) return;
  document.querySelector('#session-dataset').value = datasets.some(d=>d.id===state.bootstrap?.defaults?.dataset_id) ? state.bootstrap.defaults.dataset_id : datasets[0].id;
  let strategy = state.settings?.strategy || 'continuous';
  if (!(strategy in strategyNames)) strategy = 'continuous';
  function updateSelection() {
    const selected = datasets.find(d => d.id === document.querySelector('#session-dataset').value) || datasets[0];
    document.querySelector('#selected-dataset-link').href = `#/lab?dataset_id=${encodeURIComponent(selected.id)}`;
    document.querySelector('#selected-dataset').innerHTML = metadataRows(selected)+`<div class="info-strip">${icon('info')}<span>这是已录制的数据，演示起点自动对齐原始事件标记。当前片段为主动恢复，仍有无阻力踩踏活动。</span></div>`;
    const qemu = document.querySelector('#session-source').value === 'FREERTOS_QEMU';
    const allowed = state.bootstrap?.capabilities?.qemu_limits?.strategies || ['continuous','critical','periodic'];
    document.querySelectorAll('[data-strategy]').forEach(el => {el.disabled = qemu && !allowed.includes(el.dataset.strategy);});
    if (qemu && !allowed.includes(strategy)) strategy = allowed[0] || 'continuous';
    document.querySelectorAll('[data-strategy]').forEach(el => el.classList.toggle('selected', el.dataset.strategy===strategy));
    document.querySelector('#session-phase').disabled = strategy !== 'periodic';
    document.querySelector('#source-hint').textContent = qemu ? '真实样本传入FreeRTOS，由固件计算窗口结果。' : '信号处理在本机Python中执行。';
    const speeds = state.bootstrap?.capabilities?.qemu_limits?.speeds || [1,2,5,10];
    for (const option of document.querySelector('#session-speed').options) option.disabled = qemu && !speeds.includes(Number(option.value));
    if (document.querySelector('#session-speed').selectedOptions[0].disabled) document.querySelector('#session-speed').value = String(speeds[0] || 1);
  }
  document.querySelectorAll('[data-strategy]').forEach(el => el.addEventListener('click', () => {strategy=el.dataset.strategy; updateSelection();}));
  on('#session-dataset','change',updateSelection); on('#session-source','change',updateSelection);
  on('#start-session','click', e => run(e.currentTarget, async () => {
    const selected = datasets.find(d => d.id===document.querySelector('#session-dataset').value);
    const record = await api('/sessions',{method:'POST',body:{name:document.querySelector('#session-name').value.trim() || datasetName(selected),origin:'PUBLIC_DATA',dataset_id:selected.id,execution_mode:document.querySelector('#session-source').value,strategy,phase:Number(document.querySelector('#session-phase').value),speed:Number(document.querySelector('#session-speed').value)}});
    state.live=record;
    await sessionCommand('start_prepare');
    state.frozen=false; state.frozenSnapshot=null;
    navigate('/monitor');
  }));
  updateSelection(); bindTable();
}

function metric(title, value, unit, detail, cls = '') {
  return `<section class="panel metric"><h2>${title}</h2><div class="metric-value ${cls}">${value}<small>${unit}</small></div><p>${detail}</p></section>`;
}
function windowsHTML(record, allowSaved = false) {
  const cursor = record?.cursor_us ?? record?.engine_time_us ?? 0, t0 = t0Of(record);
  return [0,60,120].map(center => {
    let w = (record?.key_windows || []).find(x => (x.center_s ?? x.center_seconds)===center) || {};
    const start = w.start_us ?? t0+(center-4)*1e6, end = w.end_us ?? t0+(center+4)*1e6;
    if (!allowSaved && cursor < end) w={...w,valid:false,pulse_bpm:null,status:cursor>=start?'COLLECTING':'WAITING',reasons:['NOT_READY']};
    const pending = ['WAITING','COLLECTING','PENDING'].includes(w.status) || !Object.keys(w).length;
    const label = w.valid ? '可用' : pending ? w.status==='COLLECTING'?'正在累计':'尚未到达' : '缺测';
    return `<article class="window-card ${w.valid?'valid':'pending'}"><div class="window-label"><strong>${center===0?'起点窗口':`恢复${center}秒窗口`}</strong>${badge(label,w.valid?'green':'')}</div><div class="window-value">${w.valid?number(w.pulse_bpm):'—'}<small>BPM</small></div><p class="window-reason">相对起点 ${center-4}～${center+4}秒</p><p class="window-reason">${esc(w.valid?'质量检查通过':pending?`到达恢复${center+4}秒后给出结果`:explain(w.reasons)||'未获得有效结果')}</p></article>`;
  }).join('');
}
function deltasHTML(record) {
  return `<div class="delta-row">${[60,120].map(center => `<div><label>起点窗口脉率 − ${center}秒窗口脉率</label><strong>${number(record?.recovery?.[`delta${center}`])}<small>BPM</small></strong></div>`).join('')}</div><p class="panel-note">BPM表示每分钟脉搏次数。任一依赖窗口不可用时，对应差值显示“—”。</p>`;
}
function timelineHTML(record) {
  const strategy=record?.config_snapshot?.strategy || 'continuous';
  let intervals = [[0,124]];
  if (strategy==='critical') intervals=[[0,10],[20,30],[40,50],[54,64],[80,90],[100,110],[114,124]];
  if (strategy==='periodic') {
    const phase=record.config_snapshot.phase||0;
    let lo=0,hi=20;
    for(let step=0;step<50;step++){const mid=(lo+hi)/2;let on=0;for(let k=-1;k<7;k++)on+=Math.max(0,Math.min(124,phase+20*k+mid)-Math.max(0,phase+20*k));if(on<70)lo=mid;else hi=mid;}
    const width=record.config_snapshot.periodic_width??(lo+hi)/2;
    intervals=[];
    for (let k=-1;k<7;k++) {const start=Math.max(0,phase+20*k),end=Math.min(124,phase+20*k+width);if(end>start)intervals.push([start,end]);}
  }
  const cursor=Math.max(0,Math.min(124,relative(record)));
  return `<div class="timeline" aria-label="恢复段读取计划；蓝色表示读取时段，绿色表示当前记录时刻">${intervals.map(([a,b])=>`<span class="segment" style="left:${a/124*100}%;width:${(b-a)/124*100}%" title="读取${a.toFixed(1)}～${b.toFixed(1)}秒"></span>`).join('')}${[0,60,120].map(t=>`<span class="target" style="left:${t/124*100}%"><span>${t}秒</span></span>`).join('')}<span class="cursor" style="left:${cursor/124*100}%"></span></div><p class="panel-note">蓝色为策略读取时段，绿色为当前记录时刻。未读取的区间不参与本次处理。</p>`;
}
function monitor() {
  main().innerHTML = `${heading('恢复过程','按原始记录时间推进真实波形；当前展示历史采集数据。',btn(state.frozen?'返回最新数据':'冻结当前视图','freeze-view','',state.frozen?'play':'pause')+btn('导出波形PNG','export-chart','','download'))}<div id="monitor-content"></div>${foot()}`;
  on('#freeze-view','click',()=>{
    if (!state.live) return;
    state.frozen=!state.frozen;
    if (state.frozen) state.frozenSnapshot=structuredClone(state.live);
    document.querySelector('#freeze-view').innerHTML=icon(state.frozen?'play':'pause')+(state.frozen?'返回最新数据':'冻结当前视图');
    updateMonitor(true);
  });
  on('#export-chart','click',()=>run(null,()=>exportPNG(document.querySelector('#ppg-chart'),`${sourceText(state.live)} · ${metaOf(state.live).subject || ''} · ${engineText(state.live)} · 脉搏波（设备相对单位）`)));
  updateMonitor(true);
}
function updateMonitor(rebuild=false) {
  const container=document.querySelector('#monitor-content'); if (!container) return;
  const current=state.live;
  for(const id of ['#freeze-view','#export-chart']){const button=document.querySelector(id);if(button)button.disabled=!current;}
  if (!current) {container.innerHTML=empty('当前没有演示','在工作台选择一条真实记录并开始演示。','<a class="btn primary" href="#/">前往工作台</a>');return;}
  const record=state.frozen&&state.frozenSnapshot?state.frozenSnapshot:current;
  const meta=metaOf(record), result=record.latest_result||{}, phase=phaseOf(record);
  if (rebuild || container.dataset.session!==idOf(record)) {
    container.dataset.session=idOf(record);
    container.innerHTML=`<div class="active-banner"><span id="monitor-origin"></span><span id="monitor-clock"></span></div><div id="frozen-notice" class="info-strip warning hidden" role="status"></div><div class="monitor-layout"><div class="stack"><div class="grid three" id="monitor-metrics"></div><section class="panel"><div class="chart-head"><h2>脉搏波 <small>64 Hz · 设备相对单位</small></h2><div class="segmented" aria-label="波形显示"><button type="button" data-signal="ppg">原始</button><button type="button" data-signal="filtered">滤波后</button></div></div><canvas id="ppg-chart" class="chart-canvas" aria-label="脉搏波图，横轴为相对恢复起点的秒数"></canvas><div class="chart-caption"><span>缺口和策略未读取区间保持断线</span><div class="segmented" aria-label="波形显示时长"><button data-seconds="10">最近10秒</button><button data-seconds="30">最近30秒</button></div></div><div class="panel-separator"></div><div class="chart-head"><h2>运动变化量 <small>g</small></h2></div><canvas id="accel-chart" class="chart-canvas accel" aria-label="运动变化量图"></canvas><p class="chart-caption">g为重力加速度单位。此曲线来自原始三轴加速度的变化量，不是运动强度评分。</p></section><section class="panel"><div class="panel-header"><h2>恢复窗口</h2><span id="monitor-strategy" class="small"></span></div><div class="panel-body"><div id="monitor-timeline"></div><div class="grid three" id="monitor-windows"></div><div id="monitor-deltas"></div></div></section></div><aside class="stack"><section class="panel"><div class="wrist-panel"><h2>腕端显示预览</h2><p class="panel-note">电脑上的128×64像素界面预览</p><div class="watch-shell"><canvas id="wrist-canvas" class="wrist-canvas" aria-label="腕端显示预览"></canvas></div><div class="watch-buttons"><button type="button" class="watch-key" id="wrist-a" aria-label="查看恢复起点说明">A</button><button type="button" class="watch-key" id="wrist-b" aria-label="切换腕端页面；长按停止演示">B</button></div><p class="wrist-help">A：查看起点说明　B：切换页面<br>长按B一秒：停止演示<br>此预览不表示已连接腕带。</p></div><div id="session-controls" class="session-controls"></div></section><section class="panel"><div class="panel-header"><h2>最近事件</h2></div><div class="panel-body"><ul id="monitor-events" class="event-list"></ul></div></section></aside></div>`;
    document.querySelectorAll('[data-signal]').forEach(el=>el.addEventListener('click',()=>{state.signal=el.dataset.signal;updateMonitor();}));
    document.querySelectorAll('[data-seconds]').forEach(el=>el.addEventListener('click',()=>{state.seconds=Number(el.dataset.seconds);updateMonitor();}));
    on('#wrist-a','click',()=>{state.wristActivity=performance.now();openInfo('恢复起点',`<p>起点取自原始记录的阶段标记，表示高负荷骑行结束、进入低阻力放松踩踏。</p><p>本次片段第${number((metaOf(state.live).t0_us??10000000)/1e6,1)}秒为恢复起点。演示启动和播放速度不会修改这个时刻。</p>`);});
    let holdTimer, held=false;
    on('#wrist-b','pointerdown',()=>{state.wristActivity=performance.now();held=false;holdTimer=setTimeout(()=>{held=true;document.querySelector('#cancel-session')?.click();},1000);});
    for (const event of ['pointerup','pointerleave','pointercancel']) on('#wrist-b',event,()=>clearTimeout(holdTimer));
    on('#wrist-b','click',()=>{state.wristActivity=performance.now();if(!held){state.wristPage=(state.wristPage+1)%3;updateMonitor();}});
    state.cleanups.push(()=>clearTimeout(holdTimer));
  }
  document.querySelector('#monitor-origin').textContent=`${sourceText(record)} · ${meta.subject||meta.subject_id||'受试者编号未记录'} · ${engineText(record)}`;
  const speed=record.config_snapshot?.speed||1;
  document.querySelector('#monitor-clock').textContent=`${number(speed,0)}倍演示 · 数据时间不变`;
  const frozen=document.querySelector('#frozen-notice');frozen.classList.toggle('hidden',!state.frozen);
  frozen.textContent=`视图已冻结在片段第${number((record.engine_time_us||0)/1e6,1)}秒。后台仍在处理，点击“返回最新数据”继续查看。`;
  const valid=state.connected&&result.valid;
  const reference=record.waveform_tail?.at(-1)?.reference_bpm;
  const referenceNote=state.connected&&Number.isFinite(reference)?`<br>设备记录：${number(reference)} BPM（同一BVP的10秒平均，仅供对照）`:'';
  const phaseValue=phase==='PREPARING'?number(Math.max(0,(t0Of(record)-(record.engine_time_us||0))/1e6),1):number(Math.max(0,relative(record)),1);
  const phaseLabel=phase==='PREPARING'?'距离恢复起点':'恢复段进度';
  document.querySelector('#monitor-metrics').innerHTML=metric('算法脉率',valid?number(result.pulse_bpm):'—','BPM',(state.connected?(result.data_age_s!=null?`最近输入距当前记录时刻 ${number(result.data_age_s)}秒`:'正在等待输入'):'连接中断，读数暂不可用')+referenceNote)+metric('信号质量',!state.connected?'连接中断':result.valid?'可用':'暂不可用','',esc(state.connected?explain(result.reasons)||'质量检查通过':'连接恢复后重新获取状态'),'quality')+metric(phaseLabel,phaseValue,'秒',esc(stageNames[phase]||phase));
  const samples=record.waveform_tail||[];
  drawChart(document.querySelector('#ppg-chart'),samples,{key:state.signal,seconds:state.seconds,end_us:record.engine_time_us,t0_us:t0Of(record),period_us:15625});
  drawChart(document.querySelector('#accel-chart'),samples,{key:'accel',seconds:state.seconds,end_us:record.engine_time_us,t0_us:t0Of(record),period_us:15625,color:'#16836f'});
  document.querySelectorAll('[data-signal]').forEach(el=>el.classList.toggle('active',el.dataset.signal===state.signal));
  document.querySelectorAll('[data-seconds]').forEach(el=>el.classList.toggle('active',Number(el.dataset.seconds)===state.seconds));
  document.querySelector('#monitor-strategy').textContent=strategyNames[record.config_snapshot?.strategy]||'读取策略';
  document.querySelector('#monitor-timeline').innerHTML=timelineHTML(record);
  document.querySelector('#monitor-windows').innerHTML=windowsHTML(record);
  document.querySelector('#monitor-deltas').innerHTML=deltasHTML(record);
  drawWrist(document.querySelector('#wrist-canvas'),record,state.wristPage);
  document.querySelector('#wrist-canvas').style.opacity=performance.now()-state.wristActivity>(state.settings?.screen_timeout_s||30)*1000?'.35':'1';
  const controls=document.querySelector('#session-controls'), nowPhase=phaseOf(current);
  if (controls.dataset.phase!==nowPhase) {
    controls.dataset.phase=nowPhase;
    controls.innerHTML=finished(current)?`${stageBadge(current)}${btn('查看保存结果','view-result','primary')}<a class="btn" href="#/">新建演示</a>`:`<p class="panel-note">${nowPhase==='PREPARING'?'原记录起点位于片段第10秒，将自动进入恢复段。':'恢复段处理到124秒后自动保存。'}</p>${btn('停止演示','cancel-session','danger')}`;
    bindSessionControls();
  }
  document.querySelector('#monitor-events').innerHTML=(record.events||[]).slice(-10).reverse().map(e=>`<li><span class="mono">${number((e.time_us-t0Of(record))/1e6,1)}秒</span><span>${esc(e.message || eventName(e.type))}</span></li>`).join('') || '<li>尚无处理事件。</li>';
}
function eventName(type) {
  return ({CREATED:'演示记录已创建',PREPARE_STARTED:'开始读取起点前数据',T0_LOCKED:'已到原始恢复起点',WINDOW_RESULT:'窗口处理完成',FINALIZING:'正在保存结果',COMPLETED:'演示完成',CANCELLED:'演示停止',SERVICE_SHUTDOWN:'服务退出，演示中断'})[type] || '处理状态已更新';
}

function sessionTable(records, selectable=true) {
  return `<div class="table-wrap"><table><thead><tr>${selectable?'<th><input type="checkbox" id="select-all" aria-label="选择本页全部记录"></th>':''}<th>名称与创建时间</th><th>数据与处理环境</th><th>状态</th><th>起点 − 60秒</th><th>起点 − 120秒</th><th>操作</th></tr></thead><tbody>${records.map(record=>`<tr>${selectable?`<td><input type="checkbox" class="session-check" value="${esc(idOf(record))}" aria-label="选择${esc(record.name)}"></td>`:''}<td><strong class="table-title">${esc(record.name||'恢复记录')}</strong><span class="muted">${localDate(record.created_at_utc)}</span></td><td>${esc(metaOf(record).subject||'公开记录')}<span class="muted">${esc(engineText(record))}</span></td><td>${stageBadge(record)}</td><td>${number(record.recovery?.delta60)} BPM</td><td>${number(record.recovery?.delta120)} BPM</td><td><button type="button" class="btn small" data-detail="${esc(idOf(record))}">详情</button>${finished(record)?`<a class="btn small ghost" href="#/replay/${esc(idOf(record))}">回放</a>`:'<a class="btn small ghost" href="#/monitor">查看进度</a>'}</td></tr>`).join('')}</tbody></table></div>`;
}
function bindTable() {
  document.querySelectorAll('[data-detail]').forEach(el=>el.addEventListener('click',()=>openDetail(el.dataset.detail)));
  on('#select-all','change',e=>document.querySelectorAll('.session-check').forEach(el=>el.checked=e.currentTarget.checked));
}
async function sessions(token) {
  main().innerHTML=`${heading('记录库','保存每次处理的配置、窗口结果、波形和事件。','<a class="btn primary" href="#/">新建演示</a>')}<section class="panel"><div class="panel-header"><h2>真实数据处理记录</h2><span id="record-count" class="badge">正在读取</span></div><form class="filterbar" id="session-search"><label class="sr-only" for="search-text">搜索名称或备注</label><input id="search-text" placeholder="搜索名称或备注"><label class="sr-only" for="status-filter">处理状态</label><select id="status-filter"><option value="">全部状态</option>${['COMPLETED','CANCELLED','INTERRUPTED','FAILED','PREPARING','RECOVERING'].map(v=>`<option value="${v}">${stageNames[v]}</option>`).join('')}</select><label class="sr-only" for="tag-filter">按标签筛选</label><input id="tag-filter" placeholder="标签"><button class="btn small" type="submit">筛选</button><button class="btn small" type="button" id="clear-filters">清除筛选</button><button class="btn small danger" type="button" id="delete-selected">删除所选</button></form><div id="sessions-table"></div><div class="pagination"><span id="pagination-label"></span><div class="button-row">${btn('上一页','prev-page','small')}${btn('下一页','next-page','small')}</div></div></section>${foot()}`;
  let page=1,total=0;
  async function load() {
    const query=new URLSearchParams({origin:'PUBLIC_DATA',page:String(page),page_size:'20'});
    for (const [key,id] of [['search','#search-text'],['status','#status-filter'],['tag','#tag-filter']]) {
      const value=document.querySelector(id)?.value.trim();if(value)query.set(key,value);
    }
    document.querySelector('#sessions-table').innerHTML='<div class="loading"><span class="spinner"></span>正在读取记录</div>';
    const response=await api(`/sessions?${query}`);
    if(!isCurrent(token))return;
    const records=items(response);total=response.total??records.length;
    if(!records.length&&page>1){page--;return load();}
    document.querySelector('#record-count').textContent=`共${total}条`;
    document.querySelector('#sessions-table').innerHTML=records.length?sessionTable(records):empty('没有符合条件的记录','可清除筛选条件，或在工作台开始一次真实数据演示。');
    document.querySelector('#pagination-label').textContent=`第${page}页，每页最多20条`;
    document.querySelector('#prev-page').disabled=page===1;
    document.querySelector('#next-page').disabled=page*20>=total;
    bindTable();
  }
  on('#session-search','submit',e=>{e.preventDefault();page=1;run(e.submitter,load);});
  on('#clear-filters','click',e=>run(e.currentTarget,async()=>{for(const id of ['#search-text','#status-filter','#tag-filter'])document.querySelector(id).value='';page=1;await load();}));
  on('#prev-page','click',e=>run(e.currentTarget,async()=>{page--;await load();}));
  on('#next-page','click',e=>run(e.currentTarget,async()=>{page++;await load();}));
  on('#delete-selected','click',e=>run(e.currentTarget,async()=>{
    const ids=[...document.querySelectorAll('.session-check:checked')].map(el=>el.value);
    if(!ids.length)throw new Error('请先选择要删除的记录。');
    if(!confirm(`删除所选${ids.length}条处理记录及其归档文件？原始公开数据文件不受影响。`))return;
    await api('/sessions/delete',{method:'POST',body:{ids}});
    toast(`已删除${ids.length}条处理记录。`);await load();
  }));
  await load();
}

function closeDrawer(force=false) {
  if(state.drawerDirty&&!force&&!confirm('备注或标签尚未保存。关闭后放弃这些修改？'))return false;
  state.drawerCleanup?.();state.drawerCleanup=null;state.drawerDirty=false;
  document.querySelector('#drawer-root').innerHTML='';
  state.drawerRestore?.focus?.();state.drawerRestore=null;
  return true;
}
function openDialog(title,content,footer='') {
  if(!closeDrawer())return false;
  state.drawerRestore=document.activeElement;
  document.querySelector('#drawer-root').innerHTML=`<div class="drawer-backdrop"></div><section class="drawer" role="dialog" aria-modal="true" aria-labelledby="detail-title"><header class="drawer-head"><h2 id="detail-title">${esc(title)}</h2><button type="button" class="btn ghost" id="close-drawer" aria-label="关闭详情">${icon('close')}</button></header><div class="drawer-body">${content}</div>${footer?`<footer class="drawer-footer">${footer}</footer>`:''}</section>`;
  on('#close-drawer','click',()=>closeDrawer());on('.drawer-backdrop','click',()=>closeDrawer());
  function keys(event) {
    if(event.key==='Escape'){event.preventDefault();closeDrawer();return;}
    if(event.key!=='Tab')return;
    const focusable=[...document.querySelectorAll('.drawer a[href],.drawer button:not([disabled]),.drawer input,.drawer select,.drawer textarea,.drawer summary')].filter(el=>el.offsetParent!==null);
    if(!focusable.length)return;
    const first=focusable[0],last=focusable.at(-1);
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  }
  window.addEventListener('keydown',keys);state.drawerCleanup=()=>window.removeEventListener('keydown',keys);
  document.querySelector('#close-drawer').focus();return true;
}
function openInfo(title,content){openDialog(title,content);}
async function downloadSession(id,format) {
  const result=await api(`/sessions/${id}/exports`,{method:'POST',body:{format}});
  downloadURL(result.url,result.filename);
}
function downloadURL(url,name) {
  const a=document.createElement('a');a.href=url;if(name)a.download=name;a.click();
}
function downloadJSON(value,name) {
  const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
  downloadURL(url,name);setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function openDetail(id) {
  return run(null,async()=>{
    const token=state.token;
    let record=await api(`/sessions/${id}`);
    if(!isCurrent(token))return;
    let tab='overview',tabToken=0;
    const source=metaOf(record);
    const footer=finished(record)?`<a class="btn primary" id="detail-replay" href="#/replay/${esc(id)}">回放记录</a><button type="button" class="btn" data-export="summary">摘要JSON</button><button type="button" class="btn" data-export="csv">结果CSV</button><button type="button" class="btn" data-export="raw">完整记录ZIP</button>${btn('删除记录','delete-record','danger')}`:'<a class="btn primary" id="detail-live" href="#/monitor">查看恢复过程</a>';
    if(!openDialog(record.name||'处理记录',`<p class="muted">创建于${localDate(record.created_at_utc)} · ${esc(engineText(record))}</p><div class="tabbar" role="tablist">${[['overview','结果与备注'],['windows','波形与窗口'],['events','处理事件'],['config','来源与配置']].map(([v,label])=>`<button type="button" role="tab" data-tab="${v}" aria-selected="${v===tab}">${label}</button>`).join('')}</div><div id="detail-content"></div>`,footer))return;
    document.querySelector('.drawer').dataset.recordId=id;
    const alive=()=>document.querySelector('.drawer')?.dataset.recordId===id;
    async function renderTab(next) {
      const version=++tabToken;tab=next;
      document.querySelectorAll('[data-tab]').forEach(el=>el.setAttribute('aria-selected',el.dataset.tab===tab));
      const target=document.querySelector('#detail-content');
      if(tab==='overview') {
        target.innerHTML=`${stageBadge(record)}<div class="grid three spacer">${windowsHTML(record,true)}</div>${deltasHTML(record)}<form id="annotation-form" class="spacer"><div class="field"><label for="annotation-name">名称</label><input id="annotation-name" maxlength="100" value="${esc(record.name)}" required></div><div class="field spacer"><label for="annotation-notes">备注</label><textarea id="annotation-notes" maxlength="10000" rows="4">${esc(record.notes||record.annotation?.notes||'')}</textarea></div><div class="field spacer"><label for="annotation-tags">标签（用逗号分隔）</label><input id="annotation-tags" value="${esc((record.tags||record.annotation?.tags||[]).join(', '))}" placeholder="例如：有氧、连续读取"></div><div class="button-row spacer"><button class="btn primary" type="submit" id="save-annotation">保存名称、备注与标签</button><span id="annotation-status" class="small muted">编辑不会修改原始波形或窗口结果。</span></div></form>`;
        document.querySelectorAll('#annotation-form input,#annotation-form textarea').forEach(el=>el.addEventListener('input',()=>{state.drawerDirty=true;document.querySelector('#annotation-status').textContent='有未保存修改';}));
        on('#annotation-form','submit',e=>{
          e.preventDefault();run(e.submitter,async()=>{
            const name=document.querySelector('#annotation-name').value.trim();
            if(!name)throw new Error('名称不能为空。');
            const response=await api(`/sessions/${id}/annotation`,{method:'PATCH',body:{name,notes:document.querySelector('#annotation-notes').value,tags:document.querySelector('#annotation-tags').value.split(/[,，]/).map(x=>x.trim()).filter(Boolean)}});
            if(!alive())return;
            record=response;state.drawerDirty=false;
            document.querySelectorAll('[data-detail]').forEach(button=>{if(button.dataset.detail===id){const title=button.closest('tr')?.querySelector('.table-title');if(title)title.textContent=record.name;}});
            document.querySelector('#detail-title').textContent=record.name;
            document.querySelector('#annotation-status').textContent='已保存';toast('名称、备注与标签已保存。');
          });
        });
      } else if(tab==='windows') {
        target.innerHTML=`<div class="field"><label for="detail-window">查看原始窗口</label><select id="detail-window">${[0,60,120].map(v=>`<option value="${v}">${v===0?'起点':`恢复${v}秒`}窗口：${v-4}～${v+4}秒</option>`).join('')}</select></div><canvas class="chart-canvas spacer" id="detail-chart"></canvas><div id="detail-window-info"></div>`;
        async function windowData() {
          const center=Number(document.querySelector('#detail-window').value),t0=t0Of(record);
          const row=(record.key_windows||[]).find(w=>(w.center_s??w.center_seconds)===center)||{};
          document.querySelector('#detail-window-info').innerHTML=`<p class="panel-note">${row.valid?'该窗口质量检查通过。':esc(explain(row.reasons)||'尚无结果。')}</p><dl class="detail-list"><dt>统计区间</dt><dd>相对起点${center-4}～${center+4}秒，末端不包含在内</dd><dt>有效脉率</dt><dd>${row.valid?number(row.pulse_bpm)+' BPM':'—'}</dd><dt>样本数</dt><dd>${row.sample_count??'未记录'}</dd><dt>来源区段</dt><dd>${esc((row.segment_ids||[]).join('、')||'未记录')}</dd></dl>`;
          const response=await api(`/sessions/${id}/samples?start_us=${t0+(center-4)*1e6}&end_us=${t0+(center+4)*1e6}&max_points=1000`);
          if(!alive()||tab!=='windows'||version!==tabToken)return;
          drawChart(document.querySelector('#detail-chart'),response.samples||[],{start_us:t0+(center-4)*1e6,end_us:t0+(center+4)*1e6,t0_us:t0,decimation:response.decimation||1});
        }
        on('#detail-window','change',()=>run(null,windowData));await windowData();
      } else if(tab==='events') {
        target.innerHTML='<div class="loading">正在读取处理事件</div>';
        const response=await api(`/sessions/${id}/events?limit=500`);
        if(!alive()||version!==tabToken)return;
        target.innerHTML=`<p class="panel-note">以下时间均相对原始恢复起点。事件保存于本次处理记录。</p><ul class="event-list">${items(response).map(e=>`<li><span class="mono">${number((e.time_us-t0Of(record))/1e6,2)}秒</span><span>${esc(e.message||eventName(e.type))}</span></li>`).join('')||'<li>没有保存的事件。</li>'}</ul>`;
      } else {
        target.innerHTML=metadataRows(source)+`<dl class="detail-list"><dt>数据性质</dt><dd>${esc(sourceText(record))}</dd><dt>计算位置</dt><dd>${esc(engineText(record))}</dd><dt>读取策略</dt><dd>${esc(strategyNames[record.config_snapshot?.strategy]||'未记录')}</dd><dt>数据来源</dt><dd>${safeLink(source.source_url,'PhysioNet数据页面')}</dd><dt>输入标识</dt><dd class="mono">${esc(source.input_hash||record.config_snapshot?.input_hash||'未记录')}</dd></dl><details><summary>查看保存时的完整配置</summary><pre class="code-block">${esc(JSON.stringify(record.config_snapshot,null,2))}</pre></details>`;
      }
    }
    document.querySelectorAll('[data-tab]').forEach(el=>el.addEventListener('click',()=>{
      if(state.drawerDirty&&!confirm('备注尚未保存，切换后放弃修改？'))return;
      state.drawerDirty=false;run(null,()=>renderTab(el.dataset.tab));
    }));
    document.querySelectorAll('[data-export]').forEach(el=>el.addEventListener('click',()=>run(el,()=>downloadSession(id,el.dataset.export))));
    on('#delete-record','click',e=>run(e.currentTarget,async()=>{
      if(!confirm(`删除“${record.name}”及本次处理归档？原始公开数据不会删除。`))return;
      await api('/sessions/delete',{method:'POST',body:{ids:[id]}});if(idOf(state.live)===id){state.live=null;globalState();}closeDrawer(true);toast('处理记录已删除。');await route(true);
    }));
    await renderTab(tab);
  });
}
function safeLink(url,label) {
  if(typeof url!=='string'||!/^https?:\/\//.test(url))return '未提供';
  return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label||url)}</a>`;
}

async function replay(recordId,token) {
  if(!recordId) {
    const response=await api('/sessions?origin=PUBLIC_DATA&page_size=100');
    if(!isCurrent(token))return;
    const records=items(response).filter(finished);
    main().innerHTML=`${heading('记录回放','选择一条已保存的处理记录，检查原始波形及重新计算的窗口结果。')}${records.length?panel('选择处理记录',`<div class="field"><label for="replay-record">已完成或已停止的记录</label><select id="replay-record">${records.map(r=>`<option value="${esc(idOf(r))}">${esc(r.name)} · ${localDate(r.created_at_utc)}</option>`).join('')}</select></div><div class="button-row spacer">${btn('打开回放','open-replay','primary','play')}</div>`):empty('还没有可回放的记录','完成或停止一次演示后，记录会出现在这里。','<a class="btn primary" href="#/">前往工作台</a>')}${foot()}`;
    on('#open-replay','click',()=>navigate(`/replay/${document.querySelector('#replay-record').value}`));return;
  }
  const created=await api('/replays',{method:'POST',body:{source_session_id:recordId}});
  if(!isCurrent(token)) {api(`/replays/${created.id}/commands`,{method:'POST',body:{type:'close'}}).catch(()=>{});return;}
  const replayId=created.replay_id||created.id;
  let snapshot=created,originalMode=false,original=null,replayConnected=true;
  main().innerHTML=`${heading('记录回放','独立推进已保存记录，不影响正在运行的演示。',btn('记录详情','replay-detail','','archive')+btn('导出波形PNG','replay-png','','download'))}<div class="active-banner"><span id="replay-label"></span><span id="replay-state">已暂停</span></div><div id="replay-disconnected" class="info-strip warning hidden">回放连接已断开，正在重连。原始记录仍保存在本机。</div><div class="grid three" id="replay-metrics"></div><section class="panel spacer"><div class="chart-head"><h2>原始脉搏波 <small>64 Hz · 设备相对单位</small></h2></div><canvas id="replay-chart" class="chart-canvas large"></canvas><div class="panel-body"><div class="range-row"><span id="replay-cursor" class="mono">0.0秒</span><input type="range" id="replay-seek" min="0" max="134" step="0.1" aria-label="定位到片段中的时间"><span id="replay-duration" class="mono"></span></div><div class="button-row"><button type="button" class="btn primary" id="replay-play">${icon('play')}播放</button>${btn('前进1秒','replay-step')}${btn('回到片段开头','replay-restart')}<label for="replay-speed">速度</label><select class="control" id="replay-speed">${[.5,1,2,5,10].map(v=>`<option value="${v}" ${v===1?'selected':''}>${v}倍</option>`).join('')}</select><span class="small muted" id="replay-rebuild"></span></div><p class="panel-note">时间定位会从起点前数据重新建立滤波状态，保留原始时间轴和数据缺口。</p></div></section><section class="panel spacer"><div class="panel-header"><h2>窗口结果</h2><div class="segmented"><button type="button" class="active" id="derived-view">游标前数据重算</button><button type="button" id="original-view">已保存的最终结果</button></div></div><div class="panel-body"><p class="panel-note" id="replay-mode-explanation">重算只使用游标之前的数据，尚未完整到达的窗口不会显示结果。</p><div class="grid three spacer" id="replay-windows"></div><div id="replay-deltas"></div></div></section>${foot()}`;
  function render(value) {
    if(!isCurrent(token))return;
    snapshot=value;
    const cursor=value.cursor_us??value.engine_time_us??0,duration=value.duration_us??value.last_us??134000000;
    document.querySelector('#replay-label').textContent=`${sourceText(value)} · ${metaOf(value).subject||'公开记录'} · 原记录由${engineText(value)}处理`;
    document.querySelector('#replay-state').textContent=value.playing?'正在播放':cursor>=duration?'已到片段末尾':'已暂停';
    document.querySelector('#replay-cursor').textContent=`${number(cursor/1e6)}秒`;
    document.querySelector('#replay-duration').textContent=`共${number(duration/1e6)}秒`;
    const seek=document.querySelector('#replay-seek');if(document.activeElement!==seek){seek.max=duration/1e6;seek.value=cursor/1e6;}
    document.querySelector('#replay-play').innerHTML=icon(value.playing?'pause':'play')+(value.playing?'暂停':'播放');
    document.querySelector('#replay-step').disabled=!!value.playing;
    const result=value.latest_result||{};
    document.querySelector('#replay-metrics').innerHTML=metric('游标处脉率',replayConnected&&result.valid?number(result.pulse_bpm):'—','BPM',esc(replayConnected?explain(result.reasons)||'质量检查通过':'回放连接中断'))+metric('相对恢复起点',number(relative(value)),'秒',relative(value)<0?'负值表示起点前准备数据':'记录中的恢复经过时间')+metric('查看方式','历史回放','',value.playing?'回放时钟正在推进':'回放时钟已暂停','quality');
    drawChart(document.querySelector('#replay-chart'),value.waveform_tail||[],{end_us:cursor,t0_us:t0Of(value),seconds:30});
    const derived={...value,key_windows:value.derived_key_windows||[],recovery:value.derived_recovery||{}};
    const display=originalMode&&original?original:derived;
    document.querySelector('#replay-windows').innerHTML=windowsHTML(display,originalMode);
    document.querySelector('#replay-deltas').innerHTML=deltasHTML(display);
  }
  async function replayCommand(type,extra={}) {
    await api(`/replays/${replayId}/commands`,{method:'POST',body:{type,speed:Number(document.querySelector('#replay-speed').value),...extra}});
    render(await api(`/replays/${replayId}`));
  }
  on('#replay-play','click',e=>run(e.currentTarget,()=>replayCommand(snapshot.playing?'pause':'play')));
  on('#replay-step','click',e=>run(e.currentTarget,()=>replayCommand('step')));
  on('#replay-restart','click',e=>run(e.currentTarget,()=>replayCommand('restart')));
  on('#replay-speed','change',()=>run(null,()=>replayCommand(snapshot.playing?'play':'pause')));
  on('#replay-seek','change',e=>run(null,async()=>{
    document.querySelector('#replay-rebuild').textContent='正在重建滤波状态';
    try {await replayCommand('seek',{time_us:Math.round(Number(e.currentTarget.value)*1e6)});}
    finally {if(isCurrent(token))document.querySelector('#replay-rebuild').textContent='';}
  }));
  async function mode(saved) {
    if(saved&&!original)original=await api(`/sessions/${recordId}`);
    if(!isCurrent(token))return;
    originalMode=saved;
    document.querySelector('#derived-view').classList.toggle('active',!saved);
    document.querySelector('#original-view').classList.toggle('active',saved);
    document.querySelector('#replay-mode-explanation').textContent=saved?'以下显示已保存的最终结果，不随回放游标变化。':'重算只使用游标之前的数据，尚未完整到达的窗口不会显示结果。';
    render(snapshot);
  }
  on('#original-view','click',e=>run(e.currentTarget,()=>mode(true)));on('#derived-view','click',e=>run(e.currentTarget,()=>mode(false)));
  on('#replay-detail','click',()=>openDetail(recordId));
  on('#replay-png','click',()=>run(null,()=>exportPNG(document.querySelector('#replay-chart'),`公开人体记录 · ${metaOf(snapshot).subject||''} · 历史回放 · 脉搏波（设备相对单位）`)));
  render(created);
  const stopSocket=socket(`/replays/${replayId}/live`,render,connected=>{
    replayConnected=connected;
    document.querySelector('#replay-disconnected')?.classList.toggle('hidden',!connected);
    if(isCurrent(token))render(snapshot);
  });
  state.cleanups.push(stopSocket,()=>api(`/replays/${replayId}/commands`,{method:'POST',body:{type:'close'}}).catch(()=>{}));
}

async function compare(token) {
  const datasets=dataList();
  main().innerHTML=`${heading('策略比较','对同一条真实波形分别应用三种读取策略，比较窗口可用情况和读取时长。')}${!datasets.length?missingData():`<div class="compare-layout"><section class="panel"><div class="panel-header"><h2>比较设置</h2></div><div class="panel-body"><form id="compare-form"><div class="field"><label for="compare-dataset">真实数据片段</label><select id="compare-dataset">${datasets.map(d=>`<option value="${esc(d.id)}">${esc(datasetName(d))}</option>`).join('')}</select></div><div class="field spacer"><label for="compare-phases">周期方案起始偏移（秒）</label><input id="compare-phases" value="0, 5, 10, 15"><small>填写0～19之间不重复的整数，用逗号分隔。每个偏移都执行一次三种策略的对照。</small></div><p id="compare-run-count" class="panel-note spacer">默认共12次策略运行。</p><div class="info-strip spacer">${icon('info')}<span>各策略使用相同的原始片段和恢复起点。连续读取作为工程对照，不能替代独立心电参考。</span></div><button class="btn primary full-width spacer" type="submit" id="start-compare">开始比较</button></form></div></section><div class="stack"><section class="panel"><div class="panel-header"><h2>比较进度</h2><span class="badge" id="compare-badge">尚未开始</span></div><div class="panel-body"><p class="notice-label hidden" id="compare-changed">表单已修改。下方结果仍对应任务开始时的设置。</p><div id="compare-progress">${empty('尚无比较任务','选择真实片段后开始比较。离开页面不会停止后台任务。')}</div><div class="button-row spacer">${btn('停止比较','cancel-compare','danger hidden')}${btn('导出JSON','export-compare-json','hidden','download')}${btn('导出CSV','export-compare-csv','hidden','download')}</div></div></section><section class="panel"><div class="panel-header"><h2>比较结果</h2></div><div id="compare-results">${empty('尚无结果','完成一个三策略配对后，这里显示可用率与实际读取时长。')}</div></section></div></div>`}${foot()}`;
  on('#reload-datasets','click',e=>run(e.currentTarget,async()=>{await bootstrap();await compare(token);}));
  if(!datasets.length)return;
  document.querySelector('#compare-dataset').value = datasets.some(d=>d.id===state.bootstrap?.defaults?.dataset_id) ? state.bootstrap.defaults.dataset_id : datasets[0].id;
  let job=null;
  const config=()=>({dataset_ids:[document.querySelector('#compare-dataset').value],phases:document.querySelector('#compare-phases').value.split(/[,，\s]+/).filter(Boolean).map(Number)});
  function changed() {
    const c=config();document.querySelector('#compare-run-count').textContent=`当前设置共${c.phases.length*3}次策略运行。`;
    if(job)document.querySelector('#compare-changed').classList.remove('hidden');
  }
  on('#compare-dataset','change',changed);on('#compare-phases','input',changed);
  function render(value) {
    if(!isCurrent(token))return;
    job=value;
    const p=value.progress||{},done=p.completed||0,failed=p.failed||0,total=p.total||0;
    const running=['RUNNING','PENDING','CANCELLING'].includes(value.status);
    const percent=total?Math.min(100,(done+failed)/total*100):0;
    document.querySelector('#compare-badge').textContent=stageNames[value.status]||value.status;
    document.querySelector('#compare-badge').className=`badge ${value.status==='COMPLETED'?'green':running?'blue':''}`;
    document.querySelector('#compare-progress').innerHTML=`<div class="progress-caption"><span>已完成${done}项 / 共${total}项</span><span>${percent.toFixed(0)}%</span></div><div class="progress-track" aria-label="比较进度"><div style="width:${percent}%"></div></div><div class="progress-caption"><span>运行失败${failed}项；未纳入配对${value.excluded_count||value.excluded_units||0}组</span><span>${running?'后台任务仍在进行':'任务已结束'}</span></div><p class="panel-note">${esc(datasetName(datasets.find(d=>d.id===(value.config?.dataset_ids?.[0]||value.config?.dataset_id))))}</p>${value.errors?.length?`<details><summary>查看运行失败原因</summary><ul class="help-list">${value.errors.map(e=>`<li>${esc(e.message||e)}</li>`).join('')}</ul></details>`:''}`;
    document.querySelector('#start-compare').disabled=running;
    document.querySelector('#cancel-compare').classList.toggle('hidden',!running);
    document.querySelector('#cancel-compare').disabled=value.status==='CANCELLING';
    for(const id of ['#export-compare-json','#export-compare-csv'])document.querySelector(id).classList.toggle('hidden',running);
    compareResults(value);
  }
  async function poll() {if(job&&['RUNNING','PENDING','CANCELLING'].includes(job.status))render(await api(`/comparisons/${job.id}`));}
  on('#compare-form','submit',e=>{
    e.preventDefault();run(e.submitter,async()=>{
      const c=config();
      if(!c.phases.length||c.phases.some(v=>!Number.isInteger(v)||v<0||v>19)||new Set(c.phases).size!==c.phases.length)throw new Error('起始偏移应为0～19之间不重复的整数。');
      document.querySelector('#compare-changed').classList.add('hidden');
      render(await api('/comparisons',{method:'POST',body:c}));
    });
  });
  on('#cancel-compare','click',e=>run(e.currentTarget,async()=>{render(await api(`/comparisons/${job.id}/cancel`,{method:'POST',body:{}}));}));
  for(const format of ['json','csv'])on(`#export-compare-${format}`,'click',e=>run(e.currentTarget,async()=>{
    const result=await api(`/comparisons/${job.id}/exports`,{method:'POST',body:{format}});downloadURL(result.url,result.filename);
  }));
  const previous=await api('/comparisons');if(!isCurrent(token))return;
  if(items(previous).length)render(items(previous)[0]);
  const timer=setInterval(()=>poll().catch(error=>toast(error.message,true)),1000);state.cleanups.push(()=>clearInterval(timer));
}
function compareResults(job) {
  const metrics=Array.isArray(job.metrics)?job.metrics:job.summary?.strategies||[];
  const target=document.querySelector('#compare-results');
  if(!metrics.length){target.innerHTML=empty('尚无完整配对结果','每组连续、周期、关键窗口三种策略都结束后才纳入汇总。');return;}
  const rate=(m,center)=>m[`window${center}_rate`]??m.window_availability?.[String(center)];
  const pct=value=>typeof value==='number'?`${(value*100).toFixed(1)}%`:'—';
  target.innerHTML=`<div class="table-wrap"><table><thead><tr><th>读取策略</th><th>起点窗口可用率</th><th>60秒窗口可用率</th><th>120秒窗口可用率</th><th>平均读取时长</th></tr></thead><tbody>${metrics.map(m=>`<tr><td><strong>${esc(strategyNames[m.strategy]||m.strategy)}</strong><span class="muted">纳入${m.runs??m.count??0}次运行</span></td>${[0,60,120].map(center=>`<td>${pct(rate(m,center))}</td>`).join('')}<td>${number(m.on_seconds??m.mean_on_seconds,2)}秒</td></tr>`).join('')}</tbody></table></div><div class="panel-body spacer"><h3>恢复段实际读取时长</h3><div class="compare-chart spacer">${metrics.map(m=>{const value=m.on_seconds??m.mean_on_seconds??0;return `<div class="bar-row"><span>${esc(strategyNames[m.strategy]||m.strategy)}</span><div class="bar-track"><div class="bar-fill ${m.strategy==='critical'?'teal':''}" style="width:${Math.min(100,value/124*100)}%"></div></div><span>${number(value,2)}秒</span></div>`;}).join('')}</div><p class="panel-note">比较区间为恢复起点后0～124秒。可用率表示该输入片段在不同读取偏移下，通过质量检查的比例，不表示医学准确率。</p><details class="spacer"><summary>查看差值可用率与完整指标</summary><div class="table-wrap"><table><thead><tr><th>策略</th><th>起点−60秒差值可用率</th><th>起点−120秒差值可用率</th></tr></thead><tbody>${metrics.map(m=>`<tr><td>${esc(strategyNames[m.strategy]||m.strategy)}</td><td>${pct(m.delta60_rate??m.delta60_availability)}</td><td>${pct(m.delta120_rate??m.delta120_availability)}</td></tr>`).join('')}</tbody></table></div><h3 class="spacer">相对连续读取结果的平均绝对差</h3><div class="table-wrap"><table><thead><tr><th>策略</th><th>起点差异</th><th>60秒差异</th><th>120秒差异</th><th>估算能耗</th></tr></thead><tbody>${metrics.map(m=>`<tr><td>${esc(strategyNames[m.strategy]||m.strategy)}</td>${[0,60,120].map(c=>`<td>${number(m.same_window_error_bpm?.[String(c)],2)} BPM<span class="muted">可比较${m.same_window_comparable_count?.[String(c)]??0}次</span></td>`).join('')}<td>${number(m.energy_estimate_mj,1)} mJ</td></tr>`).join('')}</tbody></table></div><p class="panel-note">平均绝对差只统计两种策略都有效的相同窗口，“—”表示没有可比较的窗口。能耗使用固定电流模型估算，mJ为毫焦耳，不是腕带实测值。</p></details>${job.results?.length?`<details class="spacer"><summary>查看每次运行的窗口结果和缺测原因</summary><div class="table-wrap"><table><thead><tr><th>策略</th><th>起始偏移</th><th>起点窗口</th><th>60秒窗口</th><th>120秒窗口</th></tr></thead><tbody>${job.results.map(r=>`<tr><td>${esc(strategyNames[r.strategy]||r.strategy)}</td><td>${r.phase??0}秒</td>${[0,60,120].map(center=>{const w=(r.windows||r.key_windows||[]).find(v=>(v.center_s??v.center_seconds)===center)||{};return `<td>${w.valid?`${number(w.pulse_bpm)} BPM`:esc(explain(w.reasons)||'无有效结果')}</td>`;}).join('')}</tr>`).join('')}</tbody></table></div></details>`:''}</div>`;
}

async function dataPage(token) {
  const response=await api('/datasets');if(!isCurrent(token))return;
  const datasets=items(response);
  if(!datasets.length){main().innerHTML=heading('数据与片段','查看本地真实数据、采样率和恢复起点。')+missingData()+foot();on('#reload-datasets','click',e=>run(e.currentTarget,async()=>{await bootstrap();await dataPage(token);}));return;}
  main().innerHTML=`${heading('数据与片段','原始文件保存在项目内。页面仅查看数据，不修改或生成波形。')}<div class="grid two"><section class="panel"><div class="panel-header"><h2>数据来源</h2></div><div class="panel-body"><div class="field"><label for="data-record">选择真实记录</label><select id="data-record">${datasets.map(d=>`<option value="${esc(d.id)}">${esc(datasetName(d))}</option>`).join('')}</select></div><div id="data-metadata" class="spacer"></div></div></section><div class="stack"><section class="panel"><div class="panel-header"><h2>本地文件与处理说明</h2></div><div class="panel-body" id="data-files"></div></section><div class="info-strip">${icon('info')}<span>这些数据来自已完成的人体采集。HR文件也是由同一脉搏波计算的设备输出，只作观察对照，不是独立心电参考。</span></div></div></div><section class="panel spacer"><div class="panel-header"><h2>原始片段预览</h2><div class="field inline"><label for="data-range">时间范围</label><select id="data-range"><option value="0">起点窗口：−4～4秒</option><option value="60">恢复60秒窗口：56～64秒</option><option value="120">恢复120秒窗口：116～124秒</option><option value="all">完整片段</option></select></div></div><div class="chart-head"><h3>脉搏波 <small>设备相对单位</small></h3></div><canvas id="data-ppg" class="chart-canvas large"></canvas><div class="panel-separator"></div><div class="chart-head"><h3>运动变化量 <small>g</small></h3></div><canvas id="data-accel" class="chart-canvas accel"></canvas><div class="panel-body"><p class="panel-note" id="data-preview-caption">正在读取原始片段。</p></div></section>${foot()}`;
  const desired=new URLSearchParams(location.hash.split('?')[1]||'').get('dataset_id')||state.bootstrap?.defaults?.dataset_id;
  document.querySelector('#data-record').value=datasets.some(d=>d.id===desired)?desired:datasets[0].id;
  let selected=datasets[0],previewVersion=0;
  async function preview() {
    const version=++previewVersion;
    const choice=document.querySelector('#data-range').value,t0=selected.t0_us??10000000;
    const start=choice==='all'?0:t0+(Number(choice)-4)*1e6,end=choice==='all'?selected.duration_us||134000000:t0+(Number(choice)+4)*1e6;
    document.querySelector('#data-preview-caption').textContent='正在读取文件中的波形。';
    const response=await api(`/datasets/${selected.id}/samples?start_us=${start}&end_us=${end}&max_points=1800`);
    if(!isCurrent(token)||version!==previewVersion)return;
    const options={start_us:start,end_us:end,t0_us:t0,decimation:response.decimation||1,period_us:15625};
    drawChart(document.querySelector('#data-ppg'),response.samples||[],options);
    drawChart(document.querySelector('#data-accel'),response.samples||[],{...options,key:'accel',color:'#16836f'});
    document.querySelector('#data-preview-caption').textContent=`横轴为相对恢复起点的记录时间。${response.decimation>1?'此预览为便于显示已降采样，实际处理使用完整64 Hz波形。':'此窗口显示完整64 Hz脉搏波样本。'}运动变化量由原始32 Hz三轴加速度计算并按时间对齐。`;
  }
  async function select() {
    selected=await api(`/datasets/${document.querySelector('#data-record').value}`);
    if(!isCurrent(token))return;
    document.querySelector('#data-metadata').innerHTML=metadataRows(selected)+`<dl class="detail-list"><dt>发布机构</dt><dd>${esc(selected.publisher||'PhysioNet')}</dd><dt>数据页面</dt><dd>${safeLink(selected.source_url,'打开官方数据说明')}</dd><dt>许可</dt><dd>${esc(typeof selected.license==='object'?selected.license.name:selected.license||'Open Data Commons Attribution 1.0')}</dd></dl>`;
    const files=Array.isArray(selected.files)?selected.files:Object.entries(selected.files||{}).map(([name,details])=>typeof details==='object'?{name,...details}:{name,value:details});
    document.querySelector('#data-files').innerHTML=`<p class="small">原始BVP为带正负号的设备相对单位。算法保持其原始时间轴，不把这些数值称为MAX30102的ADC读数。</p><dl class="detail-list"><dt>本地目录</dt><dd class="mono">${esc(selected.local_dir||selected.data_dir||selected.path||'项目 data/public/ 目录')}</dd><dt>处理采样率</dt><dd>64 Hz，8秒窗口，重启后预热2秒</dd><dt>恢复起点</dt><dd>${esc(selected.phase_description||'高负荷结束、开始低阻力放松踩踏')}，位于片段第${number((selected.t0_us??10000000)/1e6,1)}秒</dd></dl>${files.length?`<details><summary>查看原始文件与校验值</summary><ul class="file-list">${files.map(f=>`<li><strong>${esc(f.name||f.filename||'原始文件')}</strong><a href="/api/datasets/${encodeURIComponent(selected.id)}/files/${encodeURIComponent(f.name||f.filename)}" download>${fmtBytes(f.size??f.bytes)} · 下载原始文件</a><code>${esc(f.sha256||f.hash||'')}</code></li>`).join('')}</ul></details>`:''}<p class="panel-note spacer">原始文件和处理结果分别保存。删除处理记录不会删除这里的公开数据。</p>`;
    await preview();
  }
  on('#data-record','change',()=>run(null,select));on('#data-range','change',()=>run(null,preview));await select();
}

async function system(token) {
  main().innerHTML=`${heading('系统状态','查看服务、数据文件与当前处理环境。',btn('重新检查','refresh-system','','system')+btn('下载诊断JSON','export-diagnostics','','download'))}<div class="grid three" id="system-summary"></div><section class="panel spacer"><div class="panel-header"><h2>任务状态</h2><span class="small" id="system-task-note"></span></div><div id="system-tasks"></div></section><section class="panel spacer"><div class="panel-header"><h2>最近处理事件</h2>${btn('冻结日志显示','pause-logs','small')}</div><div class="panel-body"><div id="system-logs" class="log-list"></div></div></section><details class="panel spacer advanced"><summary>查看数据通道、版本和原始诊断</summary><div class="panel-body" id="system-advanced"></div></details>${foot()}`;
  let latest=null,paused=false;
  const taskName={Acquisition:'采集读取',DSP:'信号处理',Session:'状态控制',Feedback:'提示',UI:'屏幕',System:'诊断',SessionEngine:'Python会话处理'};
  const taskState={0:'正在执行',1:'就绪',2:'等待',3:'挂起',4:'已删除',5:'未知'};
  function render(value) {
    if(!isCurrent(token))return;latest=value;
    const storage=value.storage||{},qemu=!!value.rtos_running;
    document.querySelector('#system-summary').innerHTML=metric('本地服务',value.service==='RUNNING'?'运行中':'未就绪','',`已运行${number(value.uptime_seconds,0)}秒`,'quality')+metric('当前处理环境',qemu?'FreeRTOS':'Python',qemu?'QEMU':'',qemu?'任务信息来自运行中的FreeRTOS内核':'使用Python进程处理本地记录','quality')+metric('数据存储',fmtBytes(storage.free_bytes),'可用空间',`${storage.sessions??'—'}条索引记录`,'quality');
    const tasks=value.tasks||[];
    document.querySelector('#system-task-note').textContent=qemu?'真实FreeRTOS任务；栈余量单位为32位字':'当前未运行FreeRTOS会话';
    document.querySelector('#system-tasks').innerHTML=tasks.length?`<div class="table-wrap"><table><thead><tr><th>任务</th><th>状态</th><th>优先级</th><th>心跳次数</th><th>最小剩余栈（字）</th></tr></thead><tbody>${tasks.map(t=>`<tr><td>${esc(taskName[t.name]||t.name)}</td><td>${esc(typeof t.state==='number'?taskState[t.state]||'未知':stageNames[t.state]||t.state||'—')}</td><td>${t.priority??'不适用'}</td><td>${t.heartbeat??'不适用'}</td><td>${t.stack_high_water_words??t.stack_high_water??'不适用'}</td></tr>`).join('')}</tbody></table></div>`:empty('当前没有处理任务','开始演示后会显示相应处理环境的任务状态。');
    if(!paused)document.querySelector('#system-logs').textContent=(value.logs||[]).map(e=>`${number((e.time_us||0)/1e6,2)}秒  ${e.message||eventName(e.type)}`).join('\n')||'没有最近处理事件。';
    document.querySelector('#system-advanced').innerHTML=`<dl class="detail-list"><dt>数据目录</dt><dd class="mono">${esc(value.data_dir||storage.path||'—')}</dd><dt>Python版本</dt><dd>${esc(value.python_version||'—')}</dd><dt>算法版本</dt><dd>${esc(value.algorithm_version||'—')}</dd><dt>协议版本</dt><dd>${esc(value.protocol_version??'—')}</dd><dt>QEMU可用性</dt><dd>${value.capabilities?.qemu?'已就绪':'尚未就绪'}；${esc(value.capabilities?.qemu_reason||'')}</dd></dl><pre class="code-block">${esc(JSON.stringify(value,null,2))}</pre>`;
  }
  async function refresh(){render(await api('/system/status'));}
  on('#refresh-system','click',e=>run(e.currentTarget,refresh));
  on('#pause-logs','click',e=>{paused=!paused;e.currentTarget.textContent=paused?'恢复日志显示':'冻结日志显示';if(!paused&&latest)render(latest);});
  on('#export-diagnostics','click',()=>{if(latest)downloadJSON(latest,`repulse-diagnostics-${Date.now()}.json`);});
  await refresh();const timer=setInterval(()=>refresh().catch(()=>{}),2000);state.cleanups.push(()=>clearInterval(timer));
}

async function settings(token) {
  const original=await api('/settings');if(!isCurrent(token))return;
  let values=structuredClone(original),section='display';
  const defaults={chart_seconds:10,ui_hz:10,screen_timeout_s:30,strategy:'continuous'};
  const fieldSelect=(key,options,value)=>`<select data-setting="${key}" aria-label="${({chart_seconds:'图表显示范围',ui_hz:'界面更新频率',strategy:'默认读取策略'})[key]||key}">${options.map(([v,label])=>`<option value="${v}" ${String(v)===String(value)?'selected':''}>${label}</option>`).join('')}</select>`;
  const row=(title,description,control)=>`<div class="setting-row"><div><h3>${title}</h3><p>${description}</p></div><div class="setting-value">${control}</div></div>`;
  function render() {
    let body='';
    if(section==='display')body=`<h2>图表与腕端预览</h2>${row('图表显示范围','仅改变可见时间范围，原始数据和计算窗口保持不变。',fieldSelect('chart_seconds',[[10,'最近10秒'],[30,'最近30秒']],values.chart_seconds||10))}${row('界面更新频率','只影响画面更新，脉搏波始终按64 Hz处理。',fieldSelect('ui_hz',[[5,'每秒5次'],[10,'每秒10次']],values.ui_hz||10))}${row('腕端预览亮屏时长','超过此时长后预览变暗，按A或B唤醒。',`<input aria-label="腕端预览亮屏时长" type="number" min="10" max="120" data-setting="screen_timeout_s" value="${values.screen_timeout_s||30}"><span>秒</span>`)}`;
    if(section==='algorithm')body=`<h2>固定处理参数</h2>${row('脉搏波采样率','使用数据集原始时间间隔，正负幅值为设备相对单位。','<span class="setting-fixed">64 Hz</span>')}${row('运动输入采样率','原始三轴加速度为32 Hz，按时间与脉搏波对齐。','<span class="setting-fixed">32 Hz</span>')}${row('滤波与窗口','因果滤波，保留跨块状态；8秒窗口每秒更新。','<span class="setting-fixed">0.5–5 Hz / 8秒</span>')}${row('重新读取后的预热','策略重新开启或输入中断后，先建立滤波状态。','<span class="setting-fixed">2秒</span>')}${row('结果范围','用于识别不可靠的工程估计，不是临床风险等级。','<span class="setting-fixed">40–200 BPM</span>')}<p class="panel-note">这些参数与Python、FreeRTOS处理代码保持一致，在此页只读。</p>`;
    if(section==='strategy')body=`<h2>新演示的默认策略</h2>${row('读取策略','只影响之后创建的演示，当前演示保持创建时的配置。',fieldSelect('strategy',Object.entries(strategyNames),values.strategy||'continuous'))}<p class="panel-note">连续读取保留恢复段的全部观测；关键窗口优先和周期方案会在读取前排除部分时段，用于比较窗口覆盖。</p>`;
    if(section==='storage')body=`<h2>本地文件</h2>${row('原始公开数据','下载到项目内，保留数据版本、来源与校验信息。','<span class="setting-fixed mono">data/public/</span>')}${row('处理记录与索引','结果、波形、事件与SQLite索引保存在项目数据目录。','<span class="setting-fixed mono">data/</span>')}${row('记录清理','在记录库中选择需要删除的处理记录；原始公开数据不随记录删除。','<a class="btn small" href="#/sessions">打开记录库</a>')}<p class="panel-note">整个项目文件夹可以一起备份。清理文件前先停止项目服务。</p>`;
    main().innerHTML=`${heading('设置','显示偏好即时生效，默认策略用于下一次演示。')}<section class="panel"><div class="settings-layout"><nav class="settings-nav" aria-label="设置分类">${[['display','显示'],['algorithm','算法参数'],['strategy','默认策略'],['storage','本地文件']].map(([v,label])=>`<button type="button" data-setting-tab="${v}" class="${section===v?'active':''}">${label}</button>`).join('')}</nav><div class="setting-section">${body}</div></div><div class="settings-save"><span id="dirty-label" role="status">${state.settingsDirty?'有未保存修改':'所有修改已保存'}</span><div class="button-row">${btn('恢复默认值','reset-settings')}${btn('放弃修改','discard-settings')}${btn('保存设置','save-settings','primary')}</div></div></section>${foot()}`;
    document.querySelectorAll('[data-setting-tab]').forEach(el=>el.addEventListener('click',()=>{section=el.dataset.settingTab;render();}));
    document.querySelectorAll('[data-setting]').forEach(el=>el.addEventListener('change',()=>{
      values[el.dataset.setting]=el.type==='number'||['chart_seconds','ui_hz'].includes(el.dataset.setting)?Number(el.value):el.value;
      state.settingsDirty=true;document.querySelector('#dirty-label').textContent='有未保存修改';
    }));
    on('#save-settings','click',e=>run(e.currentTarget,async()=>{
      if(![10,30].includes(values.chart_seconds)||![5,10].includes(values.ui_hz??10))throw new Error('请选择页面提供的图表时长和更新频率。');
      const timeout=values.screen_timeout_s??30;
      if(!Number.isFinite(timeout)||timeout<10||timeout>120)throw new Error('腕端亮屏时长应为10～120秒。');
      const saved=await api('/settings',{method:'PUT',body:{...values,version:original.version}});
      Object.assign(original,saved);values=structuredClone(saved);state.settings=structuredClone(saved);state.seconds=values.chart_seconds||10;
      state.settingsDirty=false;render();toast('设置已保存。显示偏好已更新，默认策略用于下一次演示。');
    }));
    on('#discard-settings','click',()=>{values=structuredClone(original);state.settingsDirty=false;render();});
    on('#reset-settings','click',()=>{Object.assign(values,defaults);state.settingsDirty=true;render();});
  }
  state.settingsDirty=false;render();
}

async function route(force=false) {
  const next=currentPath();
  if(!force&&next===state.route)return;
  if(state.settingsDirty&&state.route==='/settings'&&next!=='/settings') {
    if(!confirm('设置尚未保存。离开后放弃这些修改？')) {history.replaceState(null,'',`#${state.route}`);return;}
    state.settingsDirty=false;
  }
  if(!closeDrawer()) {history.replaceState(null,'',`#${state.route}`);return;}
  state.cleanups.splice(0).forEach(cleanup=>{try{cleanup();}catch{}});
  const token=++state.token;state.route=next;
  const base=next.startsWith('/replay')?'/replay':next;
  document.querySelectorAll('[data-nav]').forEach(el=>el.classList.toggle('active',el.dataset.nav===base));
  document.querySelector('#breadcrumb-title').textContent=titles[base]||'工作台';
  document.title=`${titles[base]||'工作台'} · RePulse 复脉`;
  main().innerHTML='<div class="loading"><span class="spinner"></span>正在读取页面数据</div>';
  try {
    if(base==='/replay')await replay(next.split('/')[2],token);
    else await ({'/':home,'/monitor':monitor,'/sessions':sessions,'/compare':compare,'/lab':dataPage,'/system':system,'/settings':settings}[next]||home)(token);
  } catch(error) {
    if(!isCurrent(token))return;
    main().innerHTML=`${heading(titles[base]||'页面','当前数据未能载入。')}<div class="error-block"><p>${esc(error.message)}</p>${btn('重新载入','retry-route')}<a class="btn" href="#/">返回工作台</a></div>`;
    on('#retry-route','click',()=>route(true));
  }
}
async function start() {
  shell();
  try {await bootstrap();connection(true);}catch(error){connection(false);toast(error.message,true);}
  socket('/live',value=>{
    if(value.type==='idle') {
      if(state.live){state.live=null;if(state.bootstrap)state.bootstrap.active_session=null;globalState();if(state.route==='/monitor')updateMonitor();}
      return;
    }
    if(!value.session_id&&!value.id)return;
    if(state.live?.stream_id===value.stream_id&&value.seq!=null&&value.seq<=state.live.seq)return;
    const changed=state.live?.stream_id&&value.stream_id&&state.live.stream_id!==value.stream_id;
    state.live=value;globalState();
    if(changed)api('/bootstrap').then(data=>{state.bootstrap=data;}).catch(()=>{});
    if(state.route==='/monitor'&&performance.now()-state.lastUI>=1000/(state.settings?.ui_hz||10)){state.lastUI=performance.now();updateMonitor();}
  },connection);
  window.addEventListener('hashchange',()=>route());
  window.addEventListener('resize',()=>{
    if(state.route==='/monitor')updateMonitor();
    else document.querySelectorAll('canvas').forEach(canvas=>{if(canvas._chart)drawChart(canvas,canvas._chart.samples,canvas._chart.options);});
  });
  window.addEventListener('beforeunload',event=>{if(state.settingsDirty||state.drawerDirty){event.preventDefault();event.returnValue='';}});
  await route();
}
start();

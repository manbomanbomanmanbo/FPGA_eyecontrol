const el = id => document.getElementById(id);
const ctx = el('canvas').getContext('2d');
let running = false, wanted = false, allowDisplay = false;
let epoch = 0, busy = false, phase = 0, freshUntil = 0, last = null, session = null;

function draw(r) {
  ctx.clearRect(0, 0, 1920, 1080);
  ctx.strokeStyle = '#263b50'; ctx.lineWidth = 2;
  for (let x = 0; x <= 1920; x += 240) {
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, 1080); ctx.stroke();
  }
  for (let y = 0; y <= 1080; y += 180) {
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(1920, y); ctx.stroke();
  }
  ctx.fillStyle = '#8ca2b8'; ctx.font = '25px sans-serif';
  ctx.fillText('1920 × 1080  |  VIRTUAL ONLY', 28, 48);
  if (r) {
    ctx.beginPath(); ctx.arc(r.screen_x, r.screen_y, 25, 0, Math.PI * 2);
    ctx.fillStyle = '#56dfc0'; ctx.fill();
    ctx.beginPath(); ctx.arc(r.screen_x, r.screen_y, 42, 0, Math.PI * 2);
    ctx.strokeStyle = '#56dfc0'; ctx.lineWidth = 3; ctx.stroke();
  } else {
    ctx.fillStyle = '#b1c0d0'; ctx.textAlign = 'center'; ctx.font = '34px sans-serif';
    ctx.fillText('无新鲜有效结果', 960, 560); ctx.textAlign = 'left';
  }
}

function invalidate() {
  ++epoch; freshUntil = 0; last = null; draw(null);
  el('coords').textContent = '无可用坐标';
}

function render(s, sampleStartedAt = null) {
  const r = !s.error && wanted && allowDisplay && !el('invalid').checked &&
    s.connected && s.session_id === session && s.result?.status === 'ok' ? s.result : null;
  // A delayed HTTP response cannot start a new TTL. Use the local request-start time.
  if (sampleStartedAt !== null && r) freshUntil = sampleStartedAt + 500;
  last = r && performance.now() < freshUntil ? r : null;
  draw(last);
  el('state').textContent = !wanted || !s.connected ? '未连接' : last ? '响应有效' : '等待新鲜有效结果';
  el('coords').textContent = last ? `x = ${last.screen_x}   y = ${last.screen_y}` : '无可用坐标';
  el('details').textContent = `会话：${s.session_id || '无'}\n帧号：${s.result?.frame_id ?? '无'}\n状态：${s.result?.status || '无结果'}\n${s.error || s.last_error || ''}`;
}

async function request(path, body = null) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 400);
  try {
    const options = {signal: controller.signal, cache: 'no-store'};
    if (body !== null) Object.assign(options, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const response = await fetch('/api/' + path, options);
    return await response.json();
  } finally { clearTimeout(timer); }
}

el('connect').onclick = async () => {
  running = wanted = allowDisplay = false; invalidate();
  const id = epoch;
  try {
    const s = await request('connect', {});
    if (id === epoch) { wanted = !!s.connected && !s.error; session = s.session_id; render(s); }
  } catch (e) { if (id === epoch) render({error: String(e)}); }
};
el('start').onclick = () => { if (wanted) { running = allowDisplay = true; } };
el('pause').onclick = () => { running = allowDisplay = false; invalidate(); };
el('invalid').onchange = () => { invalidate(); };
el('disconnect').onclick = async () => {
  running = wanted = allowDisplay = false; invalidate(); render({});
  try { await request('disconnect', {}); } catch (_) {}
};

async function tick() {
  if (!busy) {
    busy = true;
    const id = epoch;
    try {
      if (running && wanted) {
        phase += 0.055;
        const started = performance.now();
        const s = await request('sample', {session_id: session,
          gx_q16: Math.round((0.5 + 0.35 * Math.cos(phase)) * 65536),
          gy_q16: Math.round((0.5 + 0.35 * Math.sin(phase)) * 65536),
          valid: el('invalid').checked ? 0 : 1});
        if (id === epoch) {
          if (!s.connected || s.error) { running = wanted = allowDisplay = false; }
          render(s, started);
        }
      } else {
        const s = await request('status');
        if (id === epoch) {
          if (!s.connected || s.error) { running = wanted = allowDisplay = false; }
          render(s);
        }
      }
    } catch (e) {
      if (id === epoch) { running = wanted = allowDisplay = false; invalidate(); render({error: String(e)}); }
    } finally { busy = false; }
  }
  setTimeout(tick, 60);
}

// Expiration is independent of pending HTTP fetches.
setInterval(() => {
  if (last && performance.now() >= freshUntil) {
    last = null; draw(null); el('coords').textContent = '无可用坐标';
    el('state').textContent = '结果已过期';
  }
}, 25);
draw(null); tick();

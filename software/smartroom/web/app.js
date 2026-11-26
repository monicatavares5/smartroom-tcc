// SmartRoom - painel. Recebe o estado completo do servidor por Server-Sent Events
// (/api/stream, 10x por segundo; a saúde das placas vem 1x por segundo). Se o stream cair, tenta /api/estado com espera crescente.
'use strict';

const PLACAS = ['ESP_A', 'ESP_B', 'ESP_C', 'ESP_D'];
const ROTULO = { ESP_A: 'Unidade A', ESP_B: 'Unidade B', ESP_C: 'Unidade C', ESP_D: 'Unidade D' };
const POSICAO = { perto_a: [17, 19], perto_b: [83, 19], perto_c: [17, 81], perto_d: [83, 81], centro: [50, 50] };
const ZONA = {
  perto_a: 'Próximo à unidade A', perto_b: 'Próximo à unidade B',
  perto_c: 'Próximo à unidade C', perto_d: 'Próximo à unidade D', centro: 'Centro do ambiente',
};
const PRESENCA = {
  presenca: 'Presença detectada', vazio: 'Ambiente vazio', incerto: 'Analisando sinais',
  calibrando: 'Calibrando ambiente', sem_calibracao: 'Calibração necessária',
};
const STATUS = { ok: 'OK', atencao: 'Atenção', critico: 'Crítico', offline: 'Offline', aguardando: 'Aguardando' };
const BARRAS = { 'Ótimo': 4, 'Bom': 3, 'Fraco': 2, 'Muito fraco': 1 };
const OTA = { 1: 'baixando firmware…', 2: 'firmware gravado, reiniciando', 3: 'falha na atualização' };

const $ = (id) => document.getElementById(id);
const esc = (t) => String(t ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const tempo = (s) => s == null ? '—' : s < 60 ? Math.round(s) + ' s' : s < 3600 ? Math.floor(s / 60) + ' min' : Math.floor(s / 3600) + ' h ' + Math.floor(s % 3600 / 60) + ' min';

// ------------------------------------------------------------------ montagem inicial
$('nodes').innerHTML = PLACAS.map((n) => `
  <div class="node"><div class="node-top"><span>${ROTULO[n]}</span><span class="status" id="state-${n}">Offline</span></div>
  <div class="rssi" id="rssi-${n}">— <small>dBm</small></div><div class="meter"><div class="fill" id="fill-${n}"></div></div>
  <div class="score" id="score-${n}">Índice: —</div></div>`).join('');

$('health').innerHTML = PLACAS.map((n) => `
  <div class="hcard" id="h-${n}"><div class="htop"><span>${ROTULO[n]}</span><span class="chip" id="hc-${n}">—</span></div>
  <div class="bars" id="hb-${n}"><i style="height:25%"></i><i style="height:50%"></i><i style="height:75%"></i><i style="height:100%"></i></div>
  <div class="hrssi" id="hr-${n}">Sem dados</div><div class="kv" id="hk-${n}"></div><ul class="warns" id="hw-${n}"></ul></div>`).join('');

// ------------------------------------------------------------------ ações
async function post(url, corpo) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(corpo || {}) });
  const v = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(v.erro || v.error || 'HTTP ' + r.status);
  return v;
}

$('calibrate').addEventListener('click', async () => {
  if (!confirm('O ambiente está vazio e continuará vazio por 60 segundos?')) return;
  try { await post('/api/calibrar'); } catch (e) { alert('Falha na calibração: ' + e.message); }
});

$('ota').addEventListener('click', async () => {
  if (!confirm('Enviar o firmware compilado para todas as placas online pelo Wi-Fi? Elas reiniciam ao terminar.')) return;
  try {
    const v = await post('/api/ota', { placas: 'todas' });
    $('ota-info').textContent = v.mensagem || 'Atualização iniciada';
  } catch (e) { $('ota-info').textContent = 'Não foi possível iniciar: ' + e.message; }
});

// ------------------------------------------------------------------ renderização
const ROTULOS = [['vazio', 'Quarto vazio'], ['andando', 'Andando'], ['perto_a', 'Parado perto da A'], ['perto_b', 'Parado perto da B'],
  ['perto_c', 'Parado perto da C'], ['perto_d', 'Parado perto da D'], ['centro', 'Parado no centro'], ['nao_marcado', 'Sem rótulo']];
$('labels').innerHTML = ROTULOS.map(([v, t]) => `<button data-r="${v}">${t}</button>`).join('');
$('labels').addEventListener('click', async (ev) => {
  const r = ev.target.dataset && ev.target.dataset.r;
  if (!r) return;
  try { await post('/api/rotulo', { rotulo: r }); } catch (e) { alert('Falha ao marcar: ' + e.message); }
});
let sensTimer = null;
$('sens').addEventListener('input', () => {
  $('sens-v').textContent = Number($('sens').value).toFixed(1);
  clearTimeout(sensTimer);
  sensTimer = setTimeout(() => post('/api/sensibilidade', { valor: Number($('sens').value) }).catch(() => {}), 150);
});

let salaMontada = false;
function montarSala(sala) {
  if (salaMontada || !sala) return;
  salaMontada = true;
  const room = $('room');
  room.style.setProperty('--aspecto', `${sala.largura}/${sala.profundidade}`);
  room.style.setProperty('--largura-max', sala.largura >= sala.profundidade ? '560px' : `${Math.round(480 * sala.largura / sala.profundidade)}px`);
  room.dataset.largura = sala.largura.toFixed(1).replace('.', ',') + ' m';
  room.dataset.profundidade = sala.profundidade.toFixed(1).replace('.', ',') + ' m';
  const pct = ([x, y]) => [100 * x / sala.largura, 100 * y / sala.profundidade];
  for (const n of PLACAS) {
    const [x, y] = pct(sala.placas[n]);
    Object.assign($('pin-' + n).style, { left: x + '%', top: y + '%', right: 'auto', bottom: 'auto' });
  }
  const [rx, ry] = pct(sala.roteador);
  Object.assign($('router').style, { left: rx + '%', top: ry + '%' });
  window.SALA = sala;
}

function renderPresenca(nos, rt, classico, sensibilidade) {
  const st = rt.status;
  $('presence').textContent = PRESENCA[st] || 'Analisando';
  $('pulse').className = 'pulse ' + (st === 'presenca' ? 'yes' : st === 'vazio' ? 'no' : '');
  $('prob').style.width = rt.probabilidade == null ? '0%' : Math.round(rt.probabilidade * 100) + '%';
  $('calibration').textContent = st === 'calibrando' ? `Calibração em andamento: ${rt.progresso || 0}% — mantenha o quarto vazio`
    : rt.calibracao ? (rt.detail || 'Ambiente calibrado · detecção ativa') : (rt.detail || 'Calibre com o quarto vazio para iniciar');
  $('calibrate').disabled = st === 'calibrando';
  $('calibrate').textContent = st === 'calibrando' ? 'Calibrando...' : rt.calibracao ? 'Refazer calibração · 60 s' : 'Calibrar ambiente vazio · 60 s';
  if (document.activeElement !== $('sens') && sensibilidade != null) {
    $('sens').value = sensibilidade;
    $('sens-v').textContent = Number(sensibilidade).toFixed(1);
  }
  $('classic').textContent = 'Detector clássico (V7.4), para comparação: ' + (PRESENCA[classico.status] || classico.status || '—');

  const limiar = rt.limiar || 3;
  const pos = rt.posicao;
  for (const n of PLACAS) {
    const v = nos[n] || {};
    const s = $('state-' + n);
    s.textContent = v.online ? '● Online' : '○ Offline';
    s.style.color = v.online ? '#36dda6' : '#94a7bf';
    $('rssi-' + n).innerHTML = (v.rssi == null ? '—' : esc(v.rssi)) + ' <small>dBm</small>';
    const z = (rt.placas || {})[n];
    $('score-' + n).textContent = z ? `Escore ${z.escore} · movimento ${z.movimento} · forma ${z.forma} · RSSI ${z.rssi}` : 'Escore: —';
    const f = $('fill-' + n);
    f.style.width = z ? Math.min(100, Math.max(0, 100 * z.escore / (2 * limiar))) + '%' : '0%';
    f.className = 'fill' + (z && z.escore >= limiar ? ' mov' : '');
    const letra = n.slice(-1).toLowerCase();
    $('pin-' + n).className = 'pin ' + letra + (v.online ? ' live' : '') + (pos && pos.regiao === 'perto_' + letra ? ' hot' : '');
  }

  const pessoa = $('person');
  if (st === 'presenca' && pos && window.SALA) {
    pessoa.style.display = 'block';
    pessoa.style.left = (100 * pos.x / SALA.largura) + '%';
    pessoa.style.top = (100 * pos.y / SALA.profundidade) + '%';
    const diam = Math.max(30, Math.min(260, 2 * pos.incerteza_m / SALA.largura * $('room').clientWidth));
    Object.assign($('halo').style, { width: diam + 'px', height: diam + 'px' });
    $('person-label').textContent = ZONA[pos.regiao] || 'Presença';
    $('zone').textContent = ZONA[pos.regiao] || 'Presença';
    $('zone-detail').textContent = `x = ${pos.x.toFixed(2)} m · y = ${pos.y.toFixed(2)} m · incerteza ±${pos.incerteza_m.toFixed(2)} m`;
  } else {
    pessoa.style.display = 'none';
    $('zone').textContent = st === 'vazio' ? 'Sem presença' : st === 'presenca' ? 'Estimando posição' : 'Aguardando';
    $('zone-detail').textContent = 'A posição aparece assim que a presença é detectada';
  }
  $('update').textContent = 'Última leitura: ' + new Date().toLocaleTimeString('pt-BR');
}

let etapaFalada = null;
let audio = null;
function falar(texto) {
  try {
    const u = new SpeechSynthesisUtterance(texto);
    u.lang = 'pt-BR';
    const voz = speechSynthesis.getVoices().find((v) => v.lang && v.lang.toLowerCase().startsWith('pt'));
    if (voz) u.voice = voz;
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
  } catch (e) { /* navegador sem voz: o texto continua na tela */ }
}
function bip(freq = 880, dur = 0.12) {
  try {
    audio = audio || new AudioContext();
    const o = audio.createOscillator(), g = audio.createGain();
    o.frequency.value = freq; g.gain.value = 0.15;
    o.connect(g); g.connect(audio.destination);
    o.start(); o.stop(audio.currentTime + dur);
  } catch (e) { /* sem áudio */ }
}
let ultimoSegundo = null;
function renderTeste(t) {
  if (!t) return;
  $('teste-iniciar').hidden = t.ativo;
  $('teste-parar').hidden = !t.ativo;
  $('guia').hidden = !t.ativo;
  $('guia-msg').textContent = !t.ativo && t.mensagem ? t.mensagem : '';
  if (!t.ativo) { etapaFalada = null; return; }
  $('guia-etapa').textContent = `Etapa ${t.etapa} de ${t.total} · faltam ${tempo(t.restante_total_s)} no total`;
  $('guia-instrucao').textContent = t.instrucao;
  $('guia-tempo').textContent = Math.ceil(t.restante_s) + ' s';
  $('guia-proxima').textContent = 'Próxima: ' + t.proxima;
  if (t.etapa !== etapaFalada) { etapaFalada = t.etapa; bip(660, 0.2); falar(t.instrucao); }
  const s = Math.ceil(t.restante_s);
  if (s <= 3 && s >= 1 && s !== ultimoSegundo) bip();
  ultimoSegundo = s;
}
$('teste-iniciar').addEventListener('click', async () => {
  falar('Teste guiado iniciado.');   // libera a voz no navegador (precisa de um clique)
  try { await post('/api/teste/iniciar'); } catch (e) { alert('Não foi possível iniciar: ' + e.message); }
});
$('teste-parar').addEventListener('click', () => post('/api/teste/parar').catch(() => {}));

function renderRotulo(rotulo) {
  for (const b of $('labels').children) b.classList.toggle('on', b.dataset.r === rotulo);
}

function renderMonitor(m, ota) {
  const problemas = [];
  for (const n of PLACAS) {
    const v = m.placas[n] || {};
    $('h-' + n).className = 'hcard ' + (v.status || '');
    const chip = $('hc-' + n);
    chip.className = 'chip ' + (v.status || '');
    chip.textContent = STATUS[v.status] || '—';
    const barras = v.online ? (BARRAS[v.sinal] || 0) : 0;
    const bars = $('hb-' + n);
    bars.className = 'bars ' + (v.sinal_nivel || '');
    [...bars.children].forEach((b, i) => b.classList.toggle('on', i < barras));
    $('hr-' + n).textContent = v.online ? `${v.sinal || '?'} · ${v.rssi_mediana} dBm` : (v.status === 'aguardando' ? 'Nunca conectou' : 'Desconectada');

    const fw = v.firmware || {};
    const linhas = [
      ['IP', v.ip || '—'], ['Pacotes/s', v.online ? v.taxa : '—'],
      ['Perda', v.perda == null ? '—' : Math.round(v.perda * 100) + '%'],
      ['CSI válido', v.csi_valido == null ? '—' : Math.round(v.csi_valido * 100) + '%'],
      ['Último pacote', v.ultimo_pacote_s == null ? '—' : 'há ' + tempo(v.ultimo_pacote_s)],
      ['Conectada há', tempo(v.conectada_ha_s)], ['Quedas / reinícios', `${v.desconexoes ?? 0} / ${v.reinicios ?? 0}`],
    ];
    if (v.mac) linhas.push(['MAC', v.mac]);
    if (fw.versao_fw) linhas.push(['Firmware', fw.versao_fw]);
    if (fw.uptime_s != null) linhas.push(['Ligada há', tempo(fw.uptime_s)], ['Wi-Fi caiu', (fw.wifi_quedas ?? 0) + 'x'], ['Erros de envio', fw.erros_envio ?? 0]);
    $('hk-' + n).innerHTML = linhas.map(([k, x]) => `<span>${esc(k)}</span><b>${esc(x)}</b>`).join('');

    const avisos = [...(v.avisos || [])];
    if (fw.ota_estado && OTA[fw.ota_estado]) avisos.unshift('OTA: ' + OTA[fw.ota_estado]);
    $('hw-' + n).innerHTML = avisos.map((a) => `<li>${esc(a)}</li>`).join('');
    if (v.status !== 'ok') problemas.push(`${ROTULO[n]}: ${(v.avisos || [])[0] || STATUS[v.status]}`);
  }

  const r = m.resumo;
  $('health-sub').textContent = `${r.online} de ${r.total} placas online · servidor ativo há ${tempo(m.servidor.ativo_ha_s)} · IP do PC: ${(m.servidor.ips || []).join(', ') || '—'}`;
  const barra = $('alertbar');
  if (problemas.length) { barra.className = 'alertbar show' + (r.online < r.total ? ' err' : ''); barra.textContent = '⚠ ' + problemas.join('   •   '); }
  else barra.className = 'alertbar';

  $('events').innerHTML = m.eventos.length
    ? m.eventos.map((e) => `<div class="ev-${esc(e.nivel)}">${esc(e.hora)}  ${esc(e.placa)}: ${esc(e.mensagem)}</div>`).join('')
    : '<span class="muted">Nenhum evento ainda.</span>';

  $('ota').hidden = !ota;
  if (ota) {
    $('ota').disabled = !ota.disponivel || ota.ativo;
    $('ota-info').textContent = ota.ativo ? ota.mensagem : (ota.disponivel ? `Firmware pronto: ${ota.arquivo}` : 'Compile o firmware para habilitar (smartroom.ps1 -Firmware -Ota)');
  }
}

function aplicar(e) {
  const c = $('connection');
  c.textContent = e.replay ? '● Replay de gravação' : '● Sistema conectado';
  c.classList.add('good');
  $('modo').textContent = e.replay ? `Reproduzindo ${e.replay}` : 'Atualização em tempo real (10x/s)';
  montarSala(e.sala);
  renderPresenca(e.placas, e.tempo_real, e.presenca, e.sensibilidade);
  renderRotulo(e.rotulo);
  renderTeste(e.teste);
  if (e.monitor) renderMonitor(e.monitor, e.ota);
}

function semConexao(tentativa) {
  const c = $('connection');
  c.textContent = '● Sem conexão'; c.classList.remove('good');
  const barra = $('alertbar');
  barra.className = 'alertbar show err';
  barra.textContent = `⚠ Sem conexão com o servidor — tentando reconectar automaticamente (tentativa ${tentativa}). Verifique se o terminal do SmartRoom continua aberto.`;
}

// ------------------------------------------------------------------ conexão (SSE com reconexão)
let falhas = 0;
function conectar() {
  if (!window.EventSource) return consultar();
  const fonte = new EventSource('/api/stream');
  fonte.onmessage = (ev) => { falhas = 0; try { aplicar(JSON.parse(ev.data)); } catch (err) { console.error(err); } };
  fonte.onerror = () => {
    fonte.close();
    falhas++;
    semConexao(falhas);
    setTimeout(conectar, Math.min(8000, 1000 * 2 ** Math.min(falhas, 3)));
  };
}

async function consultar() {   // alternativa sem SSE
  try {
    const r = await fetch('/api/estado', { cache: 'no-store' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    aplicar(await r.json());
    falhas = 0;
  } catch (e) { falhas++; semConexao(falhas); }
  setTimeout(consultar, falhas ? Math.min(8000, 1000 * 2 ** Math.min(falhas, 3)) : 200);
}

conectar();

import json
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path
import socket
import threading
import time
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UDP_PORT = 5005
HTTP_PORT = 8080
NODES = ('ESP_A', 'ESP_B', 'ESP_C', 'ESP_D')
TIMEOUT_SECONDS = 5
# Detector exploratorio calibrado para pessoa parada no centro.
DETECTOR_NODE = 'ESP_C'  # Referencia da V4; V5 utiliza todos os nos
DETECTOR_WINDOW = 20
DETECTOR_MIN_SAMPLES = 8
DETECTOR_OCCUPIED_BELOW = -52.5
DETECTOR_EMPTY_ABOVE = -51.5
DATA_DIR = Path(__file__).resolve().parent / 'dados_csi'
DATA_DIR.mkdir(parents=True, exist_ok=True)
CSV_PATH = DATA_DIR / ('smartroom_csi_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.csv')
CSV_COLUMNS = ['timestamp','esp_id','ip','seq','rssi','csi_len','sig_mode','mcs','channel','csi_mean_abs','first_word_invalid','csi_encoding','csi_hex','csi_bytes_recebidos','csi_valido']
lock = threading.Lock()
state = {name: {'last_seen': 0, 'ip': None, 'rssi': None, 'seq': None, 'packets': 0, 'history': deque(maxlen=100), 'detector_rssi': deque(maxlen=DETECTOR_WINDOW), 'samples': deque(maxlen=35), 'last_mcs': None} for name in NODES}

# Arquivos de calibracao coletados no Experimento 03.
CAL_DIR = Path(__file__).resolve().parent / 'experimento_03'
CAL_FILES = {
    'vazio_inicio': 'Quarto vazio.csv',
    'perto_a': 'Próximo à ESP_A.csv',
    'perto_b': 'Próximo à ESP_B.csv',
    'perto_c': 'Próximo à ESP_C.csv',
    'perto_d': 'Próximo à ESP_D.csv',
    'centro': 'Centro do quarto.csv',
    'vazio_fim': 'Quarto vazio novamente.csv',
}
LABELS = {'vazio_inicio':'Vazio', 'vazio_fim':'Vazio', 'perto_a':'Perto A',
          'perto_b':'Perto B','perto_c':'Perto C','perto_d':'Perto D','centro':'Centro'}
calibration = {}
calibration_error = None

def csi_amplitude(raw, invalid=False):
    if len(raw) != 128:
        return None
    signed = [b - 256 if b >= 128 else b for b in raw]
    if invalid:
        signed[:4] = [0] * 4
    amps = [math.hypot(signed[i], signed[i+1]) for i in range(0, 128, 2)]
    nonzero = [x for x in amps if x > 0]
    return sum(nonzero)/len(nonzero) if nonzero else None

def load_calibration():
    global calibration, calibration_error
    result = {}
    try:
        for scenario, filename in CAL_FILES.items():
            path = CAL_DIR / filename
            if not path.is_file():
                raise FileNotFoundError(str(path))
            groups = defaultdict(list)
            with path.open(newline='', encoding='utf-8-sig') as file:
                for row in csv.DictReader(file):
                    try:
                        if row['csi_valido'].lower() != 'true' or row['csi_encoding'] != 'hex_u8':
                            continue
                        raw = bytes.fromhex(row['csi_hex'])
                        amp = csi_amplitude(raw, str(row.get('first_word_invalid','0')).lower() in ('1','true'))
                        if amp is None or row['esp_id'] not in NODES:
                            continue
                        groups[row['esp_id']].append((float(row['rssi']), amp))
                    except (ValueError, TypeError, KeyError):
                        continue
            result[scenario] = {}
            for node in NODES:
                vals = groups[node]
                if len(vals) < 15:
                    raise ValueError(f'{filename}: poucas amostras validas em {node}')
                result[scenario][node] = (
                    statistics.mean(x[0] for x in vals),
                    statistics.mean(x[1] for x in vals))
        calibration = result
        calibration_error = None
    except (OSError, ValueError, KeyError) as exc:
        calibration = {}
        calibration_error = str(exc)

# Calibracao dinamica: nunca aprende automaticamente um estado ocupado como vazio.
BASELINE_SECONDS = 60
BASELINE_MIN_PER_NODE = 60
baseline = None
calibration_run = {'started': None, 'values': {n: [] for n in NODES}}
detection = {'candidate': None, 'since': None, 'status': 'incerto'}

def calibration_progress(now):
    started = calibration_run['started']
    if started is None:
        return None
    return min(100, int(100 * (now-started)/BASELINE_SECONDS))

def finish_calibration(now):
    global baseline
    if calibration_run['started'] is None or now-calibration_run['started'] < BASELINE_SECONDS:
        return
    candidate = {}
    for node in NODES:
        vals = calibration_run['values'][node]
        if len(vals) < BASELINE_MIN_PER_NODE:
            calibration_run['started'] = None
            calibration_run['error'] = f'Amostras insuficientes em {node}: {len(vals)}. Tente novamente.'
            return
        rs = [v[0] for v in vals]
        amps = [v[1] for v in vals]
        candidate[node] = {'rssi': statistics.median(rs), 'amp': statistics.median(amps),
                           'rssi_std': max(1.0, statistics.pstdev(rs)),
                           'amp_std': max(0.25, statistics.pstdev(amps)), 'n': len(vals)}
    baseline = candidate
    calibration_run['started'] = None
    calibration_run['error'] = None
    detection.update(candidate=None, since=None, status='incerto')
    print('Calibracao de vazio concluida:', {n: v['n'] for n,v in candidate.items()}, flush=True)

def presence_status(now):
    finish_calibration(now)
    active = {}
    for node, item in state.items():
        if now-item['last_seen'] < TIMEOUT_SECONDS and len(item['samples']) >= 8:
            samples = list(item['samples'])
            active[node] = {'rssi': statistics.median(x[0] for x in samples),
                            'amp': statistics.median(x[1] for x in samples),
                            'n': len(samples)}
    progress = calibration_progress(now)
    result = {'status':'analisando', 'posicao':None, 'nos_ativos':len(active),
              'calibracao':baseline is not None, 'calibrando':progress is not None,
              'progresso':progress, 'erro_calibracao':calibration_run.get('error'),
              'metodo':'Comparacao com referencia de vazio (RSSI + CSI)',
              'aviso':'Detector exploratorio; nao validado para presenca ou localizacao.',
              'nos':{}, 'candidato':None}
    if progress is not None:
        result['detalhe'] = f'Coletando referencia do quarto vazio: {progress}%'
        return result
    if baseline is None:
        result['detalhe'] = 'Clique em CALIBRAR QUARTO VAZIO com o quarto desocupado.'
        return result
    if len(active) < 3:
        result['detalhe'] = 'Aguardando pelo menos 3 placas com CSI recente.'
        detection.update(candidate=None, since=None, status='incerto')
        return result
    strong, moderate = 0, 0
    for node, v in active.items():
        ref = baseline[node]
        dr = abs(v['rssi']-ref['rssi']) / max(1.5, ref['rssi_std']*2)
        da = abs(v['amp']-ref['amp']) / max(0.45, ref['amp_std']*2)
        # Somente a amplitude agregada CSI e utilizada; ela pode mascarar mudancas
        # entre subportadoras e e sensivel a variacoes de MCS.
        score = max(dr, da*0.7)
        result['nos'][node] = {'rssi':round(v['rssi'],2), 'amp_csi':round(v['amp'],2),
                               'delta_rssi':round(v['rssi']-ref['rssi'],2),
                               'delta_amp':round(v['amp']-ref['amp'],2),
                               'indice_mudanca':round(score,2)}
        if score >= 2.8: strong += 1
        if score >= 1.6: moderate += 1
    # Requer concordancia entre placas; uma unica placa ruidosa nao basta.
    if strong >= 2 or (strong >= 1 and moderate >= 2):
        candidate = 'presenca'
    elif strong == 0 and moderate == 0:
        candidate = 'vazio'
    else:
        candidate = 'incerto'
    if candidate != detection['candidate']:
        detection.update(candidate=candidate, since=now, status='incerto')
    # Apos calibrar, confirma vazio apos 8s; presenca apos 6s.
    required = 6 if candidate == 'presenca' else 8
    if candidate == 'incerto':
        detection['status'] = 'incerto'
    elif now-detection['since'] >= required:
        detection['status'] = candidate
    else:
        detection['status'] = 'incerto'
    result['status'] = detection['status']
    result['candidato'] = candidate
    result['detalhe'] = f'Mudancas fortes: {strong}; moderadas: {moderate}; criterio multino experimental.'
    return result

HTML = r'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SmartRoom V5.1 | Monitoramento</title><style>
:root{color-scheme:dark;font-family:Inter,system-ui,Segoe UI,Arial,sans-serif;background:#090e1a;color:#e8eefb}*{box-sizing:border-box}body{margin:0;padding:28px;max-width:1300px;margin:auto}.top{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}h1{margin:0;font-size:32px;letter-spacing:-1px}p{color:#9aa9c4}.tag{border:1px solid #304360;border-radius:999px;padding:8px 13px;color:#a8c6f7}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-top:26px}.card,.panel{background:#121b2c;border:1px solid #24334b;border-radius:18px;padding:20px}.card h2{font-size:18px;margin:0 0 15px}.status{display:inline-block;width:9px;height:9px;border-radius:50%;background:#64748b;margin-right:7px}.online{background:#34d399;box-shadow:0 0 12px #34d39988}.rssi{font-size:32px;font-weight:750;margin:12px 0}.meta{font-size:13px;color:#9aa9c4;line-height:1.9}.lower{display:grid;grid-template-columns:1fr 1.25fr;gap:16px;margin-top:16px}.room{position:relative;max-width:470px;aspect-ratio:4/3;margin:25px auto 10px;border:2px solid #4a6386;border-radius:10px;background:repeating-linear-gradient(0deg,transparent 0 34px,#ffffff08 35px 36px),repeating-linear-gradient(90deg,transparent 0 34px,#ffffff08 35px 36px)}.pin{position:absolute;background:#1e3a5c;border:1px solid #5575a1;border-radius:10px;padding:8px 10px;font-weight:700;transform:translate(-50%,-50%)}.pin.on{border-color:#34d399;color:#9ef5ce}.a{left:0;top:0}.b{left:100%;top:0}.c{left:0;top:100%}.d{left:100%;top:100%}.center{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);text-align:center;color:#8b9cb7;font-size:13px}.chart{width:100%;height:280px;display:block}svg text{fill:#9aa9c4;font-size:11px}.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:#aab9d1}.legend i{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:5px}.note{font-size:13px;margin-top:14px}.presence{margin-top:18px;border:1px solid #304360;border-radius:18px;padding:20px;background:#121b2c}.presence-title{font-size:13px;color:#9aa9c4}.presence-state{font-size:27px;font-weight:800;margin:9px 0}.presence-detail{font-size:13px;color:#9aa9c4}.presence.presenca{border-color:#34d399}.presence.vazio{border-color:#58a6ff}.presence.incerto{border-color:#fbbf24}@media(max-width:1000px){.grid{grid-template-columns:repeat(2,1fr)}.lower{grid-template-columns:1fr}}@media(max-width:560px){body{padding:16px}.grid{grid-template-columns:1fr}h1{font-size:26px}}
</style></head><body><div class="top"><div><h1>SmartRoom V5.1 <span style="color:#58a6ff">/</span> Live</h1><p>Monitoramento de sinais Wi-Fi · ESP32-S3</p></div><div class="tag" id="server">Conectando ao servidor...</div></div><section class="presence" id="presence-box"><div class="presence-title">Detector multino experimental · referência de quarto vazio</div><div class="presence-state" id="presence-state">ANALISANDO...</div><div class="presence-detail" id="presence-detail">Aguardando dados das quatro ESP32.</div><div style="margin-top:16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap"><button id="calibrate" style="background:#2563eb;color:white;border:0;border-radius:10px;padding:12px 18px;font-weight:700;cursor:pointer">CALIBRAR QUARTO VAZIO (60s)</button><span id="calibration-msg" style="color:#9aa9c4;font-size:13px">Calibração necessária após iniciar o servidor.</span></div></section><section class="grid" id="cards"></section><div class="lower"><section class="panel"><h2>Mapa do ambiente</h2><p>Distribuição planejada das placas · 4 × 3 metros</p><div class="room"><div class="pin a" id="pin-ESP_A">A</div><div class="pin b" id="pin-ESP_B">B</div><div class="pin c" id="pin-ESP_C">C</div><div class="pin d" id="pin-ESP_D">D</div><div class="center">Área de monitoramento<br>Posição estimada (experimental)</div></div><p class="note">As posições dos nós são ilustrativas; não representam uma medição de localização.</p></section><section class="panel"><h2>Histórico de RSSI</h2><p>Últimas 100 amostras recebidas por placa</p><svg id="chart" class="chart" viewBox="0 0 640 280" role="img" aria-label="Gráfico de RSSI"></svg><div id="legend" class="legend"></div><p class="note">RSSI é intensidade de sinal, não posição da pessoa.</p></section></div><p class="note">Dados atualizados a cada segundo. Placa offline após 5 segundos sem receber pacotes.</p><script>
const names=['ESP_A','ESP_B','ESP_C','ESP_D'];const colors=['#58a6ff','#34d399','#fbbf24','#c084fc'];const cards=document.getElementById('cards');cards.innerHTML=names.map(n=>`<article class="card"><h2><span class="status" id="dot-${n}"></span>${n} <small id="status-${n}" style="float:right;color:#9aa9c4;font-weight:400">Offline</small></h2><div class="rssi" id="rssi-${n}">— dBm</div><div class="meta">IP: <span id="ip-${n}">—</span><br>Pacotes: <span id="packets-${n}">0</span><br>Sequência: <span id="seq-${n}">—</span></div></article>`).join('');document.getElementById('legend').innerHTML=names.map((n,i)=>`<span><i style="background:${colors[i]}"></i>${n}</span>`).join('');
function draw(nodes){let s='';for(let y=-90;y<=-30;y+=15){let py=18+(-30-y)/60*230;s+=`<line x1="45" y1="${py}" x2="625" y2="${py}" stroke="#263852"/><text x="5" y="${py+4}">${y}</text>`}for(let i=0;i<names.length;i++){const h=nodes[names[i]].history||[];if(h.length<2)continue;const pts=h.map((v,j)=>{const x=45+(j/99)*580;const y=18+Math.max(0,Math.min(1,(-30-v.rssi)/60))*230;return `${x.toFixed(1)},${y.toFixed(1)}`}).join(' ');s+=`<polyline points="${pts}" fill="none" stroke="${colors[i]}" stroke-width="2.5" stroke-linejoin="round"/>`}document.getElementById('chart').innerHTML=s}
async function refresh(){try{const response=await fetch('/api/status',{cache:'no-store'});if(!response.ok)throw Error('HTTP '+response.status);const nodes=await response.json();for(const n of names){const v=nodes[n];document.getElementById('dot-'+n).classList.toggle('online',v.online);document.getElementById('pin-'+n).classList.toggle('on',v.online);document.getElementById('status-'+n).textContent=v.online?'Online':'Offline';document.getElementById('rssi-'+n).textContent=v.rssi===null?'— dBm':v.rssi+' dBm';document.getElementById('ip-'+n).textContent=v.ip||'—';document.getElementById('packets-'+n).textContent=v.packets;document.getElementById('seq-'+n).textContent=v.seq??'—'}const pr=await fetch('/api/presence',{cache:'no-store'});if(!pr.ok)throw Error('HTTP '+pr.status);const presence=await pr.json();const labels={presenca:'PRESENÇA DETECTADA',vazio:'QUARTO VAZIO',incerto:'SINAL INCERTO',analisando:'ANALISANDO...'};const box=document.getElementById('presence-box');box.className='presence '+presence.status;document.getElementById('presence-state').textContent=labels[presence.status]||'ANALISANDO...';document.getElementById('presence-detail').textContent='Nós ativos: '+presence.nos_ativos+'/4 · '+(presence.detalhe||'')+' · não validado';document.getElementById('calibrate').disabled=presence.calibrando;document.getElementById('calibration-msg').textContent=presence.calibrando?'Calibrando: '+presence.progresso+'% · mantenha o quarto vazio':presence.erro_calibracao?'Falha: '+presence.erro_calibracao:presence.calibracao?'Referência de vazio carregada nesta sessão':'Calibração necessária';draw(nodes);document.getElementById('server').textContent='● Servidor conectado'}catch(e){document.getElementById('server').textContent='● Sem conexão com servidor'}}document.getElementById('calibrate').addEventListener('click',async()=>{if(!confirm('Confirma que o quarto está VAZIO e a porta está fechada pelos próximos 60 segundos?'))return;try{const r=await fetch('/api/calibrate',{method:'POST'});const d=await r.json();if(!r.ok)alert(d.error||'Falha ao iniciar');else refresh()}catch(e){alert('Sem conexão com o servidor')}});refresh();setInterval(refresh,1000);
</script></body></html>'''

def udp_listener():
    csv_file = CSV_PATH.open('w', newline='', encoding='utf-8')
    writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    csv_file.flush()
    print(f'CSV CSI: {CSV_PATH}', flush=True)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', UDP_PORT))
    print(f'UDP escutando na porta {UDP_PORT}', flush=True)
    while True:
        data, address = sock.recvfrom(8192)
        try:
            message = json.loads(data.decode('utf-8'))
            node = message.get('esp_id')
            if node not in state:
                continue
            rssi = int(message['rssi']) if message.get('rssi') is not None else None
            seq = message.get('seq')
            now = time.monotonic()
            csi_hex = message.get('csi', '')
            encoding = message.get('csi_encoding', '')
            csi_valid = False
            csi_size = 0
            if isinstance(csi_hex, str) and encoding == 'hex_u8':
                try:
                    csi_bytes = bytes.fromhex(csi_hex)
                    csi_size = len(csi_bytes)
                    csi_valid = (csi_size > 0 and csi_size <= 512 and
                                 csi_size <= int(message.get('csi_len', 0)))
                except (ValueError, TypeError):
                    pass
            writer.writerow({
                'timestamp': datetime.now().isoformat(timespec='milliseconds'),
                'esp_id': node, 'ip': address[0], 'seq': seq, 'rssi': rssi,
                'csi_len': message.get('csi_len'), 'sig_mode': message.get('sig_mode'),
                'mcs': message.get('mcs'), 'channel': message.get('channel'),
                'csi_mean_abs': message.get('csi_mean_abs'),
                'first_word_invalid': message.get('first_word_invalid'),
                'csi_encoding': encoding, 'csi_hex': csi_hex,
                'csi_bytes_recebidos': csi_size, 'csi_valido': csi_valid
            })
            csv_file.flush()
            with lock:
                entry = state[node]
                entry['last_seen'] = now
                entry['ip'] = address[0]
                entry['rssi'] = rssi
                entry['seq'] = seq
                entry['last_mcs'] = message.get('mcs')
                entry['packets'] += 1
                if csi_valid and rssi is not None:
                    amp = csi_amplitude(csi_bytes, str(message.get('first_word_invalid',0)).lower() in ('1','true'))
                    if amp is not None:
                        entry['samples'].append((rssi, amp))
                        if calibration_run['started'] is not None:
                            calibration_run['values'][node].append((rssi, amp))
                if rssi is not None:
                    entry['history'].append({'rssi': rssi})
                    if node == DETECTOR_NODE and csi_valid:
                        entry['detector_rssi'].append(rssi)
            print(f'[{datetime.now():%H:%M:%S}] {node} | {address[0]} | RSSI {rssi} | seq {seq} | CSI {csi_size} bytes | valido={csi_valid}', flush=True)
        except (UnicodeDecodeError, ValueError, TypeError, KeyError) as exc:
            print(f'Pacote inválido de {address[0]}: {exc}', flush=True)

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != '/api/calibrate':
            self.send_error(404)
            return
        global baseline
        with lock:
            if calibration_run['started'] is not None:
                payload, code = {'error':'Calibracao ja em andamento'}, 409
            elif any(time.monotonic()-state[n]['last_seen'] >= TIMEOUT_SECONDS for n in NODES):
                payload, code = {'error':'As quatro ESP32 precisam estar online'}, 409
            else:
                baseline = None
                calibration_run.update(started=time.monotonic(), values={n:[] for n in NODES}, error=None)
                detection.update(candidate=None, since=None, status='incerto')
                payload, code = {'ok':True, 'seconds':BASELINE_SECONDS}, 200
                print('Iniciada calibracao do quarto vazio por 60 segundos', flush=True)
        raw = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == '/api/presence':
            now = time.monotonic()
            with lock:
                result = presence_status(now)
            payload = json.dumps(result).encode('utf-8')
            content_type = 'application/json; charset=utf-8'
        elif self.path == '/api/status':
            now = time.monotonic()
            with lock:
                result = {name: {'online': (now - item['last_seen'] < TIMEOUT_SECONDS), 'ip': item['ip'], 'rssi': item['rssi'], 'seq': item['seq'], 'packets': item['packets'], 'history': list(item['history'])} for name, item in state.items()}
            payload = json.dumps(result).encode('utf-8')
            content_type = 'application/json; charset=utf-8'
        elif self.path in ('/', '/index.html'):
            payload = HTML.encode('utf-8')
            content_type = 'text/html; charset=utf-8'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        pass

if __name__ == '__main__':
    load_calibration()
    print('Calibracao:', '7 arquivos carregados' if calibration else calibration_error, flush=True)
    threading.Thread(target=udp_listener, daemon=True).start()
    server = ThreadingHTTPServer(('127.0.0.1', HTTP_PORT), Handler)
    print(f'Dashboard: http://localhost:{HTTP_PORT}', flush=True)
    print('V5.1: calibracao de vazio de 60s via dashboard; detector experimental.', flush=True)
    print('Use Ctrl+C para encerrar.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nEncerrando SmartRoom...')
    finally:
        server.server_close()

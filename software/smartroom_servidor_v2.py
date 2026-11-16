import json
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
lock = threading.Lock()
state = {name: {'last_seen': 0, 'ip': None, 'rssi': None, 'seq': None, 'packets': 0, 'history': deque(maxlen=100)} for name in NODES}

HTML = r'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SmartRoom | Monitoramento</title><style>
:root{color-scheme:dark;font-family:Inter,system-ui,Segoe UI,Arial,sans-serif;background:#090e1a;color:#e8eefb}*{box-sizing:border-box}body{margin:0;padding:28px;max-width:1300px;margin:auto}.top{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}h1{margin:0;font-size:32px;letter-spacing:-1px}p{color:#9aa9c4}.tag{border:1px solid #304360;border-radius:999px;padding:8px 13px;color:#a8c6f7}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-top:26px}.card,.panel{background:#121b2c;border:1px solid #24334b;border-radius:18px;padding:20px}.card h2{font-size:18px;margin:0 0 15px}.status{display:inline-block;width:9px;height:9px;border-radius:50%;background:#64748b;margin-right:7px}.online{background:#34d399;box-shadow:0 0 12px #34d39988}.rssi{font-size:32px;font-weight:750;margin:12px 0}.meta{font-size:13px;color:#9aa9c4;line-height:1.9}.lower{display:grid;grid-template-columns:1fr 1.25fr;gap:16px;margin-top:16px}.room{position:relative;max-width:470px;aspect-ratio:4/3;margin:25px auto 10px;border:2px solid #4a6386;border-radius:10px;background:repeating-linear-gradient(0deg,transparent 0 34px,#ffffff08 35px 36px),repeating-linear-gradient(90deg,transparent 0 34px,#ffffff08 35px 36px)}.pin{position:absolute;background:#1e3a5c;border:1px solid #5575a1;border-radius:10px;padding:8px 10px;font-weight:700;transform:translate(-50%,-50%)}.pin.on{border-color:#34d399;color:#9ef5ce}.a{left:0;top:0}.b{left:100%;top:0}.c{left:0;top:100%}.d{left:100%;top:100%}.center{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);text-align:center;color:#8b9cb7;font-size:13px}.chart{width:100%;height:280px;display:block}svg text{fill:#9aa9c4;font-size:11px}.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:#aab9d1}.legend i{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:5px}.note{font-size:13px;margin-top:14px}@media(max-width:1000px){.grid{grid-template-columns:repeat(2,1fr)}.lower{grid-template-columns:1fr}}@media(max-width:560px){body{padding:16px}.grid{grid-template-columns:1fr}h1{font-size:26px}}
</style></head><body><div class="top"><div><h1>SmartRoom <span style="color:#58a6ff">/</span> Live</h1><p>Monitoramento de sinais Wi-Fi · ESP32-S3</p></div><div class="tag" id="server">Conectando ao servidor...</div></div><section class="grid" id="cards"></section><div class="lower"><section class="panel"><h2>Mapa do ambiente</h2><p>Distribuição planejada das placas · 4 × 3 metros</p><div class="room"><div class="pin a" id="pin-ESP_A">A</div><div class="pin b" id="pin-ESP_B">B</div><div class="pin c" id="pin-ESP_C">C</div><div class="pin d" id="pin-ESP_D">D</div><div class="center">Área de monitoramento<br>Localização ainda não calibrada</div></div><p class="note">As posições dos nós são ilustrativas; não representam uma medição de localização.</p></section><section class="panel"><h2>Histórico de RSSI</h2><p>Últimas 100 amostras recebidas por placa</p><svg id="chart" class="chart" viewBox="0 0 640 280" role="img" aria-label="Gráfico de RSSI"></svg><div id="legend" class="legend"></div><p class="note">RSSI é intensidade de sinal, não posição da pessoa.</p></section></div><p class="note">Dados atualizados a cada segundo. Placa offline após 5 segundos sem receber pacotes.</p><script>
const names=['ESP_A','ESP_B','ESP_C','ESP_D'];const colors=['#58a6ff','#34d399','#fbbf24','#c084fc'];const cards=document.getElementById('cards');cards.innerHTML=names.map(n=>`<article class="card"><h2><span class="status" id="dot-${n}"></span>${n} <small id="status-${n}" style="float:right;color:#9aa9c4;font-weight:400">Offline</small></h2><div class="rssi" id="rssi-${n}">— dBm</div><div class="meta">IP: <span id="ip-${n}">—</span><br>Pacotes: <span id="packets-${n}">0</span><br>Sequência: <span id="seq-${n}">—</span></div></article>`).join('');document.getElementById('legend').innerHTML=names.map((n,i)=>`<span><i style="background:${colors[i]}"></i>${n}</span>`).join('');
function draw(nodes){let s='';for(let y=-90;y<=-30;y+=15){let py=18+(-30-y)/60*230;s+=`<line x1="45" y1="${py}" x2="625" y2="${py}" stroke="#263852"/><text x="5" y="${py+4}">${y}</text>`}for(let i=0;i<names.length;i++){const h=nodes[names[i]].history||[];if(h.length<2)continue;const pts=h.map((v,j)=>{const x=45+(j/99)*580;const y=18+Math.max(0,Math.min(1,(-30-v.rssi)/60))*230;return `${x.toFixed(1)},${y.toFixed(1)}`}).join(' ');s+=`<polyline points="${pts}" fill="none" stroke="${colors[i]}" stroke-width="2.5" stroke-linejoin="round"/>`}document.getElementById('chart').innerHTML=s}
async function refresh(){try{const response=await fetch('/api/status',{cache:'no-store'});if(!response.ok)throw Error('HTTP '+response.status);const nodes=await response.json();for(const n of names){const v=nodes[n];document.getElementById('dot-'+n).classList.toggle('online',v.online);document.getElementById('pin-'+n).classList.toggle('on',v.online);document.getElementById('status-'+n).textContent=v.online?'Online':'Offline';document.getElementById('rssi-'+n).textContent=v.rssi===null?'— dBm':v.rssi+' dBm';document.getElementById('ip-'+n).textContent=v.ip||'—';document.getElementById('packets-'+n).textContent=v.packets;document.getElementById('seq-'+n).textContent=v.seq??'—'}draw(nodes);document.getElementById('server').textContent='● Servidor conectado'}catch(e){document.getElementById('server').textContent='● Sem conexão com servidor'}}refresh();setInterval(refresh,1000);
</script></body></html>'''

def udp_listener():
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
            with lock:
                entry = state[node]
                entry['last_seen'] = now
                entry['ip'] = address[0]
                entry['rssi'] = rssi
                entry['seq'] = seq
                entry['packets'] += 1
                if rssi is not None:
                    entry['history'].append({'rssi': rssi})
            print(f'[{datetime.now():%H:%M:%S}] {node} | {address[0]} | RSSI {rssi} | seq {seq}', flush=True)
        except (UnicodeDecodeError, ValueError, TypeError, KeyError) as exc:
            print(f'Pacote inválido de {address[0]}: {exc}', flush=True)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/api/status':
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
    threading.Thread(target=udp_listener, daemon=True).start()
    server = ThreadingHTTPServer(('127.0.0.1', HTTP_PORT), Handler)
    print(f'Dashboard: http://localhost:{HTTP_PORT}', flush=True)
    print('Use Ctrl+C para encerrar.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nEncerrando SmartRoom...')
    finally:
        server.server_close()

"""Painel web e API (HTTP só em localhost).

GET  /                    painel (web/index.html, app.js, style.css)
GET  /api/estado          estado completo (placas, presença, localização, monitor, OTA)
GET  /api/stream          o mesmo estado por Server-Sent Events, 1x por segundo
GET  /api/monitor         só a saúde das placas
GET  /api/placas          mapa MAC → placa
POST /api/calibrar        inicia a calibração do ambiente vazio (alias: /api/presence_v7/calibrate)
POST /api/rotulo          {"rotulo": "..."} marca o CSV rotulado (alias: /api/label com {"label": ...})
POST /api/ota             {"placas": "todas"} atualiza o firmware pelo Wi-Fi
POST /api/sensibilidade   {"valor": -1..1} ajusta o limiar do detector em tempo real
POST /api/teste/iniciar   inicia o teste guiado (rótulos e calibração automáticos); /api/teste/parar interrompe
"""
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import PASTA_WEB

ESTATICOS = {'/': ('index.html', 'text/html'), '/index.html': ('index.html', 'text/html'),
             '/app.js': ('app.js', 'application/javascript'), '/style.css': ('style.css', 'text/css')}
INTERVALO_STREAM_S = 0.1          # 10 atualizações/s (saúde das placas vai 1x/s)


def criar_servidor(sistema, porta, host='127.0.0.1'):
    class Painel(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        # ---------------------------------------------------------- utilidades
        def _enviar(self, corpo: bytes, tipo, codigo=200):
            self.send_response(codigo)
            self.send_header('Content-Type', tipo + '; charset=utf-8')
            self.send_header('Content-Length', str(len(corpo)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(corpo)

        def _json(self, dados, codigo=200):
            self._enviar(json.dumps(dados, ensure_ascii=False).encode('utf-8'), 'application/json', codigo)

        def _corpo_json(self):
            n = int(self.headers.get('Content-Length') or 0)
            if n > 4096:
                raise ValueError('corpo grande demais')
            if n == 0:
                return {}
            dados = json.loads(self.rfile.read(n).decode('utf-8'))
            if not isinstance(dados, dict):
                raise ValueError('JSON deve ser um objeto')
            return dados

        def log_message(self, *args):
            pass

        # ---------------------------------------------------------- GET
        def do_GET(self):
            rota = self.path.split('?', 1)[0]
            if rota in ESTATICOS:
                nome, tipo = ESTATICOS[rota]
                try:
                    self._enviar((PASTA_WEB / nome).read_bytes(), tipo)
                except OSError:
                    self.send_error(500, 'arquivo do painel ausente')
                return
            if rota == '/api/estado':
                return self._json(sistema.estado())
            if rota == '/api/monitor':
                return self._json(sistema.monitor.snapshot(sistema.agora()))
            if rota == '/api/placas':
                return self._json(sistema.registro.mapa())
            if rota == '/api/stream':
                return self._stream()
            self.send_error(404)

        def _stream(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            try:
                n = 0
                while True:
                    dados = json.dumps(sistema.estado(com_monitor=(n % 10 == 0)), ensure_ascii=False)
                    n += 1
                    self.wfile.write(f'data: {dados}\n\n'.encode('utf-8'))
                    self.wfile.flush()
                    time.sleep(INTERVALO_STREAM_S)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass  # o navegador fechou a aba; ele reconecta sozinho se precisar

        # ---------------------------------------------------------- POST
        def do_POST(self):
            rota = self.path.split('?', 1)[0]
            try:
                corpo = self._corpo_json()
            except (ValueError, UnicodeDecodeError) as exc:
                return self._json({'erro': f'requisição inválida: {exc}'}, 400)
            if rota in ('/api/calibrar', '/api/presence_v7/calibrate'):
                ok, erro = sistema.calibrar()
                return self._json({'ok': True, 'segundos': 60} if ok else {'erro': erro, 'error': erro}, 200 if ok else 409)
            if rota in ('/api/rotulo', '/api/label'):
                ok, erro = sistema.rotular(corpo.get('rotulo', corpo.get('label')))
                return self._json({'ok': True} if ok else {'erro': erro, 'error': erro}, 200 if ok else 400)
            if rota == '/api/teste/iniciar':
                ok, erro = sistema.teste.iniciar()
                return self._json({'ok': True} if ok else {'erro': erro}, 200 if ok else 409)
            if rota == '/api/teste/parar':
                sistema.teste.parar()
                return self._json({'ok': True})
            if rota == '/api/sensibilidade':
                ok, erro = sistema.ajustar_sensibilidade(corpo.get('valor'))
                return self._json({'ok': True} if ok else {'erro': erro}, 200 if ok else 400)
            if rota == '/api/ota':
                if sistema.ota is None:
                    return self._json({'erro': 'OTA indisponível neste modo'}, 409)
                ok, msg = sistema.ota.iniciar(corpo.get('placas', 'todas'))
                return self._json({'ok': True, 'mensagem': msg} if ok else {'erro': msg}, 200 if ok else 409)
            self.send_error(404)

    servidor = ThreadingHTTPServer((host, porta), Painel)
    servidor.daemon_threads = True
    return servidor

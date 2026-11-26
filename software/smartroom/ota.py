"""Atualização do firmware das placas pelo Wi-Fi (OTA).

Abre um HTTP temporário em 0.0.0.0:8081 que serve só o firmware compilado e manda a
cada placa v2 online o comando {"smartroom":"ota","url":...}. O servidor HTTP fecha
sozinho depois de 10 minutos.
"""
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import FIRMWARE_BIN, PORTA_OTA
from .rede import enviar_comando, ip_local_para

DURACAO_S = 600
CAMINHO = '/smartroom_node.bin'


class GerenciadorOTA:
    def __init__(self, sistema, binario=FIRMWARE_BIN, porta=PORTA_OTA):
        self.sistema, self.binario, self.porta = sistema, binario, porta
        self._lock = threading.Lock()
        self._http = None
        self._timer = None
        self._mensagem = ''

    def info(self):
        existe = self.binario.is_file()
        arquivo = None
        if existe:
            st = self.binario.stat()
            arquivo = f'{self.binario.name} ({st.st_size // 1024} KB, {datetime.fromtimestamp(st.st_mtime):%d/%m %H:%M})'
        return {'disponivel': existe, 'arquivo': arquivo, 'ativo': self._http is not None, 'mensagem': self._mensagem}

    def iniciar(self, placas='todas'):
        if not self.binario.is_file():
            return False, 'Firmware não compilado. Rode .\\smartroom.ps1 -Firmware -Ota'
        if placas in (None, 'todas'):
            alvos = list(self.sistema.placas)
        elif isinstance(placas, str):
            alvos = [placas]
        else:
            alvos = [str(p) for p in placas]
        online = self.sistema.online()
        prontos, ignorados = [], []
        for placa in alvos:
            ip = self.sistema.ip_da_placa(placa) if placa in self.sistema.placas else None
            if placa not in online or not ip:
                ignorados.append(f'{placa} (offline)')
            elif self.sistema.protocolo_da_placa(placa) != 2:
                ignorados.append(f'{placa} (firmware antigo: grave por USB)')
            else:
                prontos.append((placa, ip))
        if not prontos:
            return False, 'Nenhuma placa pronta para OTA: ' + (', '.join(ignorados) or 'nenhuma placa')
        dados = self.binario.read_bytes()
        with self._lock:
            self._abrir_http(dados)
        for placa, ip in prontos:
            url = f'http://{ip_local_para(ip)}:{self.porta}{CAMINHO}'
            enviar_comando(ip, {'smartroom': 'ota', 'url': url})
            self.sistema.monitor.evento_servidor('info', f'OTA enviado para {placa} ({ip})')
        self._mensagem = (f'Atualizando {", ".join(p for p, _ in prontos)}; elas reiniciam ao terminar.'
                          + (f' Ignoradas: {", ".join(ignorados)}.' if ignorados else ''))
        return True, self._mensagem

    def _abrir_http(self, dados):
        if self._http is None:
            class Arquivo(BaseHTTPRequestHandler):
                def do_GET(h):
                    if h.path != CAMINHO:
                        h.send_error(404)
                        return
                    h.send_response(200)
                    h.send_header('Content-Type', 'application/octet-stream')
                    h.send_header('Content-Length', str(len(Arquivo.dados)))
                    h.end_headers()
                    h.wfile.write(Arquivo.dados)

                def log_message(h, *a):
                    pass
            Arquivo.dados = dados
            self._http = ThreadingHTTPServer(('0.0.0.0', self.porta), Arquivo)
            self._http.daemon_threads = True
            threading.Thread(target=self._http.serve_forever, name='ota-http', daemon=True).start()
        else:
            self._http.RequestHandlerClass.dados = dados
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(DURACAO_S, self.parar)
        self._timer.daemon = True
        self._timer.start()

    def parar(self):
        with self._lock:
            if self._http:
                self._http.shutdown()
                self._http.server_close()
                self._http = None
            self._mensagem = ''

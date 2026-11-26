"""Núcleo do SmartRoom: recebe amostras, mantém o estado e calcula presença/localização.

Não sabe nada de rede nem de HTTP: quem recebe pacotes (rede.py) ou reproduz uma
gravação (replay.py) chama Sistema.receber()/processar_amostra(); o painel lê
Sistema.estado(), recalculado 10x por segundo em Sistema.atualizar() (o monitor e a
gravação em disco rodam 1x por segundo).

Dois detectores rodam juntos, sobre os mesmos dados e a mesma calibração:
- tempo_real (tempo_real.py): principal, CSI multivariado + HMM + localização contínua;
- clássico   (presenca.py): V7.4, mantido para comparação.
"""
import threading
import time
from collections import deque
from datetime import datetime

from . import csi
from .config import ARQUIVO_PLACAS, OFFLINE_APOS_S, PASTA_DADOS, PLACAS, SALA
from .gravacao import CsvSeguro
from .monitor import MonitorPlacas
from .placas import RegistroPlacas
from .presenca import DetectorPresenca, LocalizadorProximidade
from .protocolo import Amostra, PacoteInvalido, Status, ler
from .tempo_real import DetectorTempoReal
from .teste_guiado import TesteGuiado

COLUNAS_CSV = ['timestamp', 'esp_id', 'ip', 'seq', 'rssi', 'csi_len', 'sig_mode', 'mcs', 'channel', 'csi_mean_abs',
               'first_word_invalid', 'csi_encoding', 'csi_hex', 'csi_bytes_recebidos', 'csi_valido',
               'mac', 't_placa_ms', 'protocolo', 'rotulo']
COLUNAS_ROTULADO = ['timestamp', 'esp_id', 'seq', 'rotulo', 'segundos_rotulo', 'rssi', 'mcs', 'channel',
                    'n_pares_validos', 'amplitude_media', 'variacao_espectral', 'mudanca_temporal', 'csi_hex']
ROTULOS = {'nao_marcado', 'vazio', 'vazio_calibracao', 'andando', 'centro', 'perto_a', 'perto_b', 'perto_c', 'perto_d'}


class Relogio:
    """Tempo monotônico; em replay pode andar mais rápido (velocidade > 1)."""

    def __init__(self, velocidade=1.0):
        self.velocidade = float(velocidade)
        self._r0 = time.monotonic()

    def agora(self):
        return self._r0 + (time.monotonic() - self._r0) * self.velocidade


class Sistema:
    def __init__(self, gravar_csv=True, relogio=None, imprimir=print, verbose=False,
                 arquivo_placas=ARQUIVO_PLACAS, pasta_dados=PASTA_DADOS):
        self.relogio = relogio or Relogio()
        self.verbose = verbose
        self.lock = threading.RLock()
        self.monitor = MonitorPlacas(PLACAS, imprimir=imprimir)
        self.registro = RegistroPlacas(
            arquivo_placas, PLACAS,
            ao_atribuir=lambda placa, mac: self.monitor.evento_servidor(
                'aviso', f'placa nova {mac} registrada como {placa} em placas.json; confira se está no canto certo'))
        self.detector = DetectorPresenca(PLACAS, ao_evento=self.monitor.evento_servidor)
        self.localizador = LocalizadorProximidade()
        self.tempo_real = DetectorTempoReal(PLACAS, ao_evento=self.monitor.evento_servidor)
        self._ultimo_lento = 0.0
        self.teste = TesteGuiado(self)
        self.placas = {p: {'ultimo': None, 'ip': None, 'rssi': None, 'seq': None, 'pacotes': 0,
                           'protocolo': None, 'espectros': deque(maxlen=80)} for p in PLACAS}
        self.rotulo = {'rotulo': 'nao_marcado', 'desde': self.relogio.agora()}
        self.replay = None
        self.ota = None                      # GerenciadorOTA (opcional)
        self._cache = None
        carimbo = datetime.now().strftime('%Y%m%d_%H%M%S')
        avisar = self.monitor.evento_servidor
        self.csv_bruto = CsvSeguro(pasta_dados / f'smartroom_csi_{carimbo}.csv', COLUNAS_CSV, 'CSV CSI', avisar, gravar_csv)
        self.csv_rotulado = CsvSeguro(pasta_dados / f'smartroom_v53_rotulado_{carimbo}.csv', COLUNAS_ROTULADO,
                                      'CSV rotulado', avisar, gravar_csv)

    def agora(self):
        return self.relogio.agora()

    # ------------------------------------------------------------ entrada
    def identificar(self, pacote):
        if pacote.mac:
            return self.registro.resolver(pacote.mac)
        return pacote.esp_id if pacote.esp_id in self.placas else None

    def receber(self, data: bytes, ip: str):
        """Pacote cru da rede. Lança PacoteInvalido se não reconhecer."""
        pacote = ler(data)
        placa = self.identificar(pacote)
        if placa is None:
            raise PacoteInvalido('placa desconhecida ou sem ID livre')
        if isinstance(pacote, Status):
            self.monitor.registrar_status_firmware(placa, ip, pacote.dados, self.agora())
        else:
            self.processar_amostra(placa, ip, pacote)

    def processar_amostra(self, placa: str, ip: str, a: Amostra, agora=None):
        agora = self.agora() if agora is None else agora
        valido = a.csi_valido
        amp = csi.amplitude_media(a.csi, a.first_word_invalid) if valido and a.rssi is not None else None
        metricas = None
        with self.lock:
            p = self.placas[placa]
            if valido and len(a.csi) == 128:
                spec = csi.espectro(a.csi, a.first_word_invalid)
                metricas = csi.metricas_espectro(spec, p['espectros'], a.mcs)
                if metricas:
                    p['espectros'].append((a.mcs, spec))
            p.update(ultimo=agora, ip=ip, rssi=a.rssi, seq=a.seq, protocolo=a.versao)
            p['pacotes'] += 1
            self.detector.adicionar(placa, agora, a.rssi, amp)
            if valido:
                self.tempo_real.adicionar(placa, agora, a.csi, a.rssi, a.sig_mode, a.mcs)
            rotulo, desde = self.rotulo['rotulo'], self.rotulo['desde']
        self.monitor.registrar_pacote(placa, ip, a.rssi, a.seq, valido, agora, mac=a.mac)

        carimbo = datetime.now().isoformat(timespec='milliseconds')
        csi_hex = a.csi.hex()
        signed = [b - 256 if b >= 128 else b for b in a.csi]
        self.csv_bruto.escrever({
            'timestamp': carimbo, 'esp_id': placa, 'ip': ip, 'seq': a.seq, 'rssi': a.rssi,
            'csi_len': a.csi_len_declarado, 'sig_mode': a.sig_mode, 'mcs': a.mcs, 'channel': a.canal,
            'csi_mean_abs': round(sum(abs(v) for v in signed) / len(signed), 2) if signed else None,
            'first_word_invalid': int(a.first_word_invalid), 'csi_encoding': 'hex_u8', 'csi_hex': csi_hex,
            'csi_bytes_recebidos': len(a.csi), 'csi_valido': valido, 'mac': a.mac or '',
            't_placa_ms': a.t_placa_ms, 'protocolo': a.versao, 'rotulo': rotulo})
        if metricas:
            self.csv_rotulado.escrever({
                'timestamp': carimbo, 'esp_id': placa, 'seq': a.seq, 'rotulo': rotulo,
                'segundos_rotulo': round(agora - desde, 2), 'rssi': a.rssi, 'mcs': a.mcs, 'channel': a.canal,
                'csi_hex': csi_hex, **metricas})
        if self.verbose:
            print(f'[{datetime.now():%H:%M:%S}] {placa} | {ip} | RSSI {a.rssi} | seq {a.seq} | '
                  f'CSI {len(a.csi)} bytes | valido={valido}', flush=True)

    def registrar_procura(self, mensagem: dict, ip: str):
        placa = None
        if mensagem.get('mac'):
            try:
                placa = self.registro.resolver(str(mensagem['mac']))
            except ValueError:
                placa = None
        elif mensagem.get('esp_id') in self.placas:
            placa = mensagem['esp_id']
        if placa:
            self.monitor.registrar_procura(placa, ip, self.agora())

    # ------------------------------------------------------------ ações
    def online(self, agora=None):
        agora = self.agora() if agora is None else agora
        with self.lock:
            return {n for n, p in self.placas.items() if p['ultimo'] is not None and agora - p['ultimo'] < OFFLINE_APOS_S}

    def calibrar(self):
        agora = self.agora()
        if len(self.online(agora)) < len(self.placas):
            return False, 'As quatro placas precisam estar online'
        with self.lock:
            ok, erro = self.detector.iniciar_calibracao(agora)
            if ok:
                self.tempo_real.iniciar_calibracao(agora)
        if ok:
            self.monitor.evento_servidor('info', 'calibração do ambiente vazio iniciada (60 s)')
        return ok, erro

    def rotular(self, rotulo):
        if rotulo not in ROTULOS:
            return False, 'Rótulo desconhecido'
        with self.lock:
            self.rotulo = {'rotulo': rotulo, 'desde': self.agora()}
        return True, None

    def ajustar_sensibilidade(self, valor):
        """valor em [-1, 1]: positivo = mais sensível (limiar menor)."""
        try:
            v = max(-1.0, min(1.0, float(valor)))
        except (TypeError, ValueError):
            return False, 'Valor inválido'
        with self.lock:
            self.tempo_real.sensibilidade = v
        return True, None

    def ip_da_placa(self, placa):
        with self.lock:
            return self.placas[placa]['ip']

    def protocolo_da_placa(self, placa):
        with self.lock:
            return self.placas[placa]['protocolo']

    # ------------------------------------------------------------ periódico (10x/s) e leitura
    def atualizar(self):
        self.teste.atualizar()
        agora = self.agora()
        online = self.online(agora)
        lento = time.monotonic() - self._ultimo_lento >= 1.0
        if lento:
            self._ultimo_lento = time.monotonic()
            self.monitor.verificar(agora)
        with self.lock:
            rt = self.tempo_real.avaliar(agora, online)
            presenca = self.detector.avaliar(agora, online)
            localizacao = self.localizador.avaliar(presenca, agora)
            placas = {n: {'online': n in online, 'ip': p['ip'], 'rssi': p['rssi'], 'seq': p['seq'],
                          'pacotes': p['pacotes'], 'protocolo': p['protocolo']} for n, p in self.placas.items()}
            self._cache = {'placas': placas, 'tempo_real': rt, 'presenca': presenca, 'localizacao': localizacao,
                           'sensibilidade': self.tempo_real.sensibilidade, 'rotulo': self.rotulo['rotulo'],
                           'teste': self.teste.info()}
        if lento:
            self.csv_bruto.descarregar()
            self.csv_rotulado.descarregar()

    def estado(self, com_monitor=True):
        if self._cache is None:
            self.atualizar()
        with self.lock:
            base = dict(self._cache)
        base['sala'] = SALA
        base['monitor'] = self.monitor.snapshot(self.agora()) if com_monitor else None
        base['ota'] = self.ota.info() if self.ota else None
        base['replay'] = self.replay
        return base

    def fechar(self):
        self.csv_bruto.fechar()
        self.csv_rotulado.fechar()

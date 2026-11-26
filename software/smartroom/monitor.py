"""Monitor de saúde das placas SmartRoom.

Recebe cada pacote (e as mensagens de status do firmware), calcula por placa:
online/offline, qualidade do sinal (RSSI), taxa de pacotes, perda estimada pela
sequência, CSI válido, reinícios e reconexões; e gera eventos/avisos com histerese
(um aviso só aparece depois de a condição durar alguns segundos, e some quando ela
termina). Thread-safe: todos os métodos públicos usam o próprio lock.
"""
import statistics
import threading
import time
from collections import deque
from datetime import datetime

# Limiares (ajuste aqui)
OFFLINE_APOS_S = 5.0          # sem pacotes por este tempo => desconectada
JANELA_S = 10.0               # janela para taxa, perda e CSI válido
JANELA_RSSI_S = 5.0           # janela da mediana do RSSI
CONFIRMAR_AVISO_S = 5.0       # condição precisa durar isto para virar aviso
TAXA_ESPERADA = 10.0          # pacotes/s (firmware envia 1 a cada 10 CSI, ping a 100 Hz)
TAXA_MINIMA_FRACAO = 0.5      # abaixo de 50% da esperada => instável
PERDA_ALTA = 0.25             # 25% de perda estimada
CSI_VALIDO_MINIMO = 0.8       # menos de 80% de CSI válido => aviso
STATUS_FIRMWARE_ATUAL_S = 15  # telemetria do firmware considerada atual

FAIXAS_SINAL = (              # (rssi mínimo, nome, nível)
    (-60, 'Ótimo', 'ok'),
    (-70, 'Bom', 'ok'),
    (-78, 'Fraco', 'aviso'),
    (-999, 'Muito fraco', 'erro'),
)


def classificar_sinal(rssi):
    if rssi is None:
        return None, None
    for minimo, nome, nivel in FAIXAS_SINAL:
        if rssi >= minimo:
            return nome, nivel
    return FAIXAS_SINAL[-1][1], FAIXAS_SINAL[-1][2]


def _novo_estado():
    return {
        'visto': False, 'online': False, 'ip': None, 'mac': None,
        'ultimo': 0.0, 'conectada_desde': None, 'caiu_em': None,
        'pacotes': 0, 'desconexoes': 0, 'reinicios': 0,
        'janela': deque(),          # (t, rssi, seq, csi_valido)
        'ultimo_seq': None,
        'firmware': None, 'firmware_em': None,
        'condicoes': {},            # chave -> {'desde': t, 'ativo': bool}
        'procurou_em': None,
    }


class MonitorPlacas:
    def __init__(self, placas, imprimir=print):
        self._lock = threading.Lock()
        self._placas = tuple(placas)
        self._estado = {p: _novo_estado() for p in self._placas}
        self._eventos = deque(maxlen=200)
        self._imprimir = imprimir
        self._inicio = time.monotonic()
        self._pacotes_total = 0
        self._servidor = {}

    # ------------------------------------------------------------ entrada
    def registrar_pacote(self, placa, ip, rssi, seq, csi_valido, agora=None, mac=None):
        agora = time.monotonic() if agora is None else agora
        with self._lock:
            e = self._estado.get(placa)
            if e is None:
                return
            self._pacotes_total += 1
            if not e['online']:
                if e['visto'] and e['caiu_em'] is not None:
                    self._evento('ok', placa, f'reconectada após {agora - e["caiu_em"]:.0f} s fora ({ip})')
                elif not e['visto']:
                    self._evento('ok', placa, f'conectada ({ip})')
                e['online'] = True
                e['conectada_desde'] = agora
                e['caiu_em'] = None
                e['janela'].clear()  # métricas recomeçam a cada conexão
            elif e['ip'] and ip != e['ip']:
                self._evento('info', placa, f'mudou de IP: {e["ip"]} → {ip}')
            if seq is not None and e['ultimo_seq'] is not None and seq < e['ultimo_seq'] - 50:
                e['reinicios'] += 1
                e['janela'].clear()
                self._evento('aviso', placa, 'reiniciou (sequência voltou ao início)')
            e['visto'] = True
            e['ip'] = ip
            if mac:
                e['mac'] = mac
            e['ultimo'] = agora
            e['pacotes'] += 1
            if seq is not None:
                e['ultimo_seq'] = seq
            e['janela'].append((agora, rssi, seq, bool(csi_valido)))
            self._podar(e, agora)

    def registrar_status_firmware(self, placa, ip, dados, agora=None):
        """Mensagem {"tipo":"status"} enviada pelo firmware a cada poucos segundos."""
        agora = time.monotonic() if agora is None else agora
        with self._lock:
            e = self._estado.get(placa)
            if e is None:
                return
            anterior = e['firmware']
            if anterior:
                if _num(dados.get('uptime_s'), 0) + 5 < _num(anterior.get('uptime_s'), 0):
                    e['reinicios'] += 1
                    motivo = dados.get('motivo_reinicio')
                    self._evento('aviso', placa, 'reiniciou' + (f' (motivo: {motivo})' if motivo else ''))
                elif _num(dados.get('wifi_quedas'), 0) > _num(anterior.get('wifi_quedas'), 0):
                    self._evento('aviso', placa, 'Wi-Fi caiu e reconectou sozinha')
                if dados.get('versao_fw') and anterior.get('versao_fw') and dados['versao_fw'] != anterior['versao_fw']:
                    self._evento('ok', placa, f'firmware atualizado: {anterior["versao_fw"]} → {dados["versao_fw"]}')
                ota_antes, ota_agora = anterior.get('ota_estado'), dados.get('ota_estado')
                if ota_agora != ota_antes and ota_agora in (1, 3):
                    self._evento('info' if ota_agora == 1 else 'erro', placa,
                                 'baixando firmware novo (OTA)' if ota_agora == 1 else 'atualização OTA falhou')
            e['firmware'] = dict(dados)
            e['firmware_em'] = agora
            if ip:
                e['ip'] = ip

    def registrar_procura(self, placa, ip, agora=None):
        agora = time.monotonic() if agora is None else agora
        with self._lock:
            e = self._estado.get(placa)
            if e is None:
                return
            primeira = e['procurou_em'] is None or agora - e['procurou_em'] > 30
            e['procurou_em'] = agora
            if primeira:
                self._evento('info', placa, f'procurou o servidor a partir de {ip}; resposta enviada')

    def evento_servidor(self, nivel, mensagem):
        with self._lock:
            self._evento(nivel, 'servidor', mensagem)

    def definir_info_servidor(self, **info):
        with self._lock:
            self._servidor.update(info)

    # ------------------------------------------------------------ avaliação periódica
    def verificar(self, agora=None):
        """Chamar ~1x/s: detecta quedas e liga/desliga avisos com histerese."""
        agora = time.monotonic() if agora is None else agora
        with self._lock:
            for placa, e in self._estado.items():
                self._podar(e, agora)
                if e['online'] and agora - e['ultimo'] > OFFLINE_APOS_S:
                    e['online'] = False
                    e['caiu_em'] = e['ultimo']
                    e['desconexoes'] += 1
                    e['conectada_desde'] = None
                    for c in e['condicoes'].values():
                        c['ativo'] = False
                        c['desde'] = None
                    self._evento('erro', placa, f'desconectada (sem pacotes há {agora - e["ultimo"]:.0f} s); '
                                               'aguardando reconexão automática')
                if not e['online']:
                    continue
                m = self._metricas(e, agora)
                conectada_ha = agora - (e['conectada_desde'] or agora)
                self._condicao(placa, e, agora, 'sinal',
                               m['sinal_nivel'] in ('aviso', 'erro'),
                               f'sinal {(m["sinal"] or "").lower()} ({m["rssi_mediana"]} dBm); '
                               'aproxime a placa do hotspot ou remova obstáculos',
                               f'sinal normalizado ({m["rssi_mediana"]} dBm)')
                if conectada_ha >= JANELA_S:
                    self._condicao(placa, e, agora, 'taxa',
                                   m['taxa'] < TAXA_ESPERADA * TAXA_MINIMA_FRACAO,
                                   f'instável: {m["taxa"]:.1f} pacotes/s (esperado ~{TAXA_ESPERADA:.0f})',
                                   f'taxa normalizada ({m["taxa"]:.1f} pacotes/s)')
                    self._condicao(placa, e, agora, 'perda',
                                   m['perda'] is not None and m['perda'] > PERDA_ALTA,
                                   f'perda alta de pacotes (~{(m["perda"] or 0) * 100:.0f}%)',
                                   'perda de pacotes normalizada')
                    self._condicao(placa, e, agora, 'csi',
                                   m['csi_valido'] is not None and m['csi_valido'] < CSI_VALIDO_MINIMO,
                                   f'CSI inválido em {(1 - (m["csi_valido"] or 0)) * 100:.0f}% dos pacotes',
                                   'CSI voltou ao normal')

    # ------------------------------------------------------------ saída
    def snapshot(self, agora=None, eventos=40):
        agora = time.monotonic() if agora is None else agora
        with self._lock:
            placas = {}
            for placa, e in self._estado.items():
                self._podar(e, agora)
                m = self._metricas(e, agora)
                avisos = [c['mensagem'] for c in e['condicoes'].values() if c['ativo']]
                if not e['visto']:
                    status, avisos = 'aguardando', ['nunca enviou dados: ligada? no hotspot? firmware atualizado?']
                elif not e['online']:
                    status = 'offline'
                    avisos = [f'sem pacotes há {agora - e["ultimo"]:.0f} s']
                elif any(e['condicoes'][k]['ativo'] and e['condicoes'][k].get('nivel') == 'erro'
                         for k in e['condicoes']):
                    status = 'critico'
                elif avisos:
                    status = 'atencao'
                else:
                    status = 'ok'
                fw = e['firmware'] if e['firmware_em'] and agora - e['firmware_em'] < STATUS_FIRMWARE_ATUAL_S else None
                placas[placa] = {
                    'status': status, 'avisos': avisos, 'online': e['online'], 'ip': e['ip'],
                    'rssi': m['rssi_atual'], 'rssi_mediana': m['rssi_mediana'],
                    'sinal': m['sinal'], 'sinal_nivel': m['sinal_nivel'],
                    'taxa': round(m['taxa'], 1), 'perda': None if m['perda'] is None else round(m['perda'], 3),
                    'csi_valido': None if m['csi_valido'] is None else round(m['csi_valido'], 3),
                    'ultimo_pacote_s': round(agora - e['ultimo'], 1) if e['visto'] else None,
                    'conectada_ha_s': round(agora - e['conectada_desde']) if e['conectada_desde'] else None,
                    'pacotes': e['pacotes'], 'desconexoes': e['desconexoes'], 'reinicios': e['reinicios'],
                    'firmware': fw, 'mac': e['mac'],
                }
            online = sum(1 for p in placas.values() if p['online'])
            return {
                'placas': placas,
                'resumo': {'online': online, 'total': len(placas),
                           'com_aviso': sum(1 for p in placas.values() if p['status'] in ('atencao', 'critico', 'offline'))},
                'eventos': list(self._eventos)[-eventos:][::-1],
                'servidor': dict(self._servidor, ativo_ha_s=round(agora - self._inicio),
                                 pacotes_total=self._pacotes_total),
            }

    def resumo_linha(self, agora=None):
        s = self.snapshot(agora, eventos=0)
        partes = []
        for placa, p in s['placas'].items():
            if p['status'] == 'aguardando':
                partes.append(f'{placa} —')
            elif not p['online']:
                partes.append(f'{placa} OFFLINE')
            else:
                partes.append(f'{placa} {p["rssi_mediana"]}dBm {p["taxa"]:.0f}/s' + (' !' if p['avisos'] else ''))
        return f'[{datetime.now():%H:%M:%S}] {s["resumo"]["online"]}/{s["resumo"]["total"]} online | ' + ' | '.join(partes)

    # ------------------------------------------------------------ internos (com lock)
    def _evento(self, nivel, placa, mensagem):
        item = {'hora': datetime.now().strftime('%H:%M:%S'), 'nivel': nivel, 'placa': placa, 'mensagem': mensagem}
        self._eventos.append(item)
        if self._imprimir:
            marca = {'ok': '[ok]', 'info': '[i] ', 'aviso': '[!] ', 'erro': '[x] '}.get(nivel, '    ')
            try:
                self._imprimir(f'{item["hora"]} {marca} {placa}: {mensagem}')
            except Exception:
                pass

    def _condicao(self, placa, e, agora, chave, ativa, msg_ligar, msg_desligar):
        c = e['condicoes'].setdefault(chave, {'desde': None, 'ativo': False, 'mensagem': msg_ligar})
        if ativa:
            c['mensagem'] = msg_ligar
            if chave == 'sinal':
                c['nivel'] = self._metricas(e, agora)['sinal_nivel']
            if c['desde'] is None:
                c['desde'] = agora
            if not c['ativo'] and agora - c['desde'] >= CONFIRMAR_AVISO_S:
                c['ativo'] = True
                self._evento(c.get('nivel', 'aviso') if chave == 'sinal' else 'aviso', placa, msg_ligar)
        else:
            c['desde'] = None
            if c['ativo']:
                c['ativo'] = False
                self._evento('ok', placa, msg_desligar)

    @staticmethod
    def _podar(e, agora):
        j = e['janela']
        while j and agora - j[0][0] > JANELA_S:
            j.popleft()

    @staticmethod
    def _metricas(e, agora):
        j = e['janela']
        rssis = [r for t, r, _, _ in j if r is not None and agora - t <= JANELA_RSSI_S]
        mediana = round(statistics.median(rssis)) if rssis else None
        nome, nivel = classificar_sinal(mediana)
        atual = j[-1][1] if j else None
        duracao = min(JANELA_S, agora - e['conectada_desde']) if e['conectada_desde'] else JANELA_S
        taxa = len(j) / max(duracao, 1.0)
        perda = None
        seqs = [s for _, _, s, _ in j if s is not None]
        if len(seqs) >= 5:
            passos = [b - a for a, b in zip(seqs, seqs[1:]) if b > a]
            if passos:
                passo = min(passos) or 1
                esperados = (seqs[-1] - seqs[0]) / passo + 1
                if esperados > 0:
                    perda = max(0.0, 1 - len(seqs) / esperados)
        csi = (sum(1 for *_, v in j if v) / len(j)) if j else None
        return {'rssi_atual': atual, 'rssi_mediana': mediana, 'sinal': nome, 'sinal_nivel': nivel,
                'taxa': taxa, 'perda': perda, 'csi_valido': csi}


def _num(v, padrao):
    try:
        return float(v)
    except (TypeError, ValueError):
        return padrao

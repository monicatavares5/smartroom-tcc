"""Replay: reproduz um CSV gravado como se as placas estivessem enviando.

Aceita os CSVs do servidor (smartroom_csi_*.csv e smartroom_v53_rotulado_*.csv).
Respeita o intervalo original entre pacotes, dividido por `velocidade`; o relógio do
Sistema anda na mesma velocidade, então janelas e confirmações continuam coerentes.
"""
import csv
import time
from datetime import datetime
from pathlib import Path

from .protocolo import Amostra


def _int(v):
    try:
        return int(float(v)) if v not in (None, '') else None
    except ValueError:
        return None


def ler_csv(caminho):
    """Gera (segundos_desde_o_inicio, placa, ip, Amostra) em ordem."""
    inicio = None
    with Path(caminho).open(newline='', encoding='utf-8-sig') as f:
        for linha in csv.DictReader(f):
            try:
                t = datetime.fromisoformat(linha['timestamp']).timestamp()
                csi = bytes.fromhex(linha.get('csi_hex') or '')
            except (KeyError, ValueError):
                continue
            inicio = t if inicio is None else inicio
            fwi = str(linha.get('first_word_invalid', '0')).lower() in ('1', 'true')
            a = Amostra(seq=_int(linha.get('seq')), rssi=_int(linha.get('rssi')), csi=csi,
                        mcs=_int(linha.get('mcs')), sig_mode=_int(linha.get('sig_mode')),
                        canal=_int(linha.get('channel')), first_word_invalid=fwi,
                        csi_len_declarado=_int(linha.get('csi_len')) or len(csi),
                        mac=linha.get('mac') or None, versao=_int(linha.get('protocolo')) or 1)
            yield t - inicio, linha.get('esp_id', ''), linha.get('ip') or '0.0.0.0', a


def reproduzir(sistema, caminho, velocidade=1.0, repetir=False, parar=None, inicio=0.0):
    """Envia as amostras ao Sistema no ritmo original / velocidade. Devolve o nº de amostras."""
    total = 0
    sistema.replay = Path(caminho).name
    while True:
        inicio_real = time.monotonic()
        for t, placa, ip, a in ler_csv(caminho):
            if parar is not None and parar.is_set():
                return total
            if t < inicio:
                continue
            t -= inicio
            if placa not in sistema.placas:
                continue
            espera = t / velocidade - (time.monotonic() - inicio_real)
            if espera > 0:
                time.sleep(espera)
            sistema.processar_amostra(placa, ip, a)
            total += 1
        if not repetir:
            return total

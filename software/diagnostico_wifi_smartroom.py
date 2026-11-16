"""Diagnostico de metadados CSI SmartRoom. Python: pyserial, numpy.
Executar com monitor ESP-IDF fechado; Ctrl+C para parar.
"""
import ast
import csv
import os
import time
from collections import Counter
from datetime import datetime
import serial
import numpy as np

PORTA = 'COM13'
BAUDRATE = 921600
PASTA = r'C:\TCC\SmartRoom\data'
# Indices CSV: rate=4, sig_mode=5, mcs=6, bandwidth=7,
# smoothing=8, not_sounding=9, aggregation=10, stbc=11,
# fec_coding=12, sgi=13, channel=16, secondary_channel=17,
# ant=19, rx_format=22, len=23.
INDICES = (4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 16, 17, 19, 22, 23)
NOMES = ('rate','sig_mode','mcs','bandwidth','smoothing','not_sounding',
         'aggregation','stbc','fec_coding','sgi','channel',
         'secondary_channel','ant','rx_format','len')


def parsear(linha):
    if not linha.startswith('CSI_DATA,'):
        return None
    try:
        c = next(csv.reader([linha]))
        if len(c) < 25:
            return None
        vetor = np.asarray(ast.literal_eval(c[24]), dtype=float)
        if vetor.size != 128 or not np.isfinite(vetor).all():
            return None
        return float(c[3]), tuple(c[i] for i in INDICES)
    except (ValueError, TypeError, SyntaxError, IndexError, csv.Error):
        return None


def main():
    os.makedirs(PASTA, exist_ok=True)
    destino = os.path.join(PASTA, 'diagnostico_wifi_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.csv')
    print('Diagnostico CSI -', PORTA, '- 921600 baud')
    print('Nao mexa no roteador ou nas placas durante o teste.')
    print('Arquivo:', destino)
    with serial.Serial(PORTA, BAUDRATE, timeout=0.2) as porta, open(destino, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['segundo','pacotes','rssi_mediano','formato_dominante_pct',
                         'formatos_distintos', *NOMES])
        inicio = time.monotonic()
        segundo = 0
        amostras = []
        try:
            while True:
                linha = porta.readline().decode('utf-8', errors='ignore').strip()
                item = parsear(linha)
                if item is not None:
                    amostras.append(item)
                agora = int(time.monotonic() - inicio)
                while segundo < agora:
                    if amostras:
                        contagem = Counter(m for _, m in amostras)
                        dominante, n = contagem.most_common(1)[0]
                        rssi = float(np.median([r for r, _ in amostras]))
                        percentual = 100*n/len(amostras)
                        writer.writerow([segundo,len(amostras),f'{rssi:.1f}',f'{percentual:.1f}',len(contagem),*dominante])
                        print(f'{segundo:3d}s | {len(amostras):3d} pacotes | RSSI={rssi:5.1f} | formatos={len(contagem)} | dominante={percentual:5.1f}% | rate={dominante[0]} mcs={dominante[2]} canal={dominante[10]}')
                    else:
                        writer.writerow([segundo,0,'','','',*(['']*len(NOMES))])
                        print(f'{segundo:3d}s | 0 pacotes')
                    f.flush()
                    segundo += 1
                    amostras = []
        except KeyboardInterrupt:
            print('\nEncerrado. Arquivo:', destino)


if __name__ == '__main__':
    main()

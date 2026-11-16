"""SmartRoom V5.3.1 - experimento CSI, sem classificacao conclusiva de presenca.
Dependencias: pip install pyserial numpy matplotlib
Feche o monitor ESP-IDF antes de executar. Ctrl+C ou feche o grafico para encerrar.
"""
import ast
import csv
import os
import queue
import threading
import time
from collections import Counter, deque
from datetime import datetime

import matplotlib.pyplot as plt
import numpy as np
import serial

PORTA = 'COM13'
BAUDRATE = 921600
TEMPO_SAIDA = 5
TEMPO_CALIBRACAO = 20
MIN_PACOTES = 10
MIN_JANELAS = 5
MIN_SUBPORTADORAS = 10
MIN_FRACAO_COMUM = .80
PERSISTENCIA = 3
LIBERACAO = 4
HISTORICO = 120
PASTA_EVENTOS = r'C:\TCC\SmartRoom\data'


def parsear(linha):
    if not linha.startswith('CSI_DATA,'):
        return None
    try:
        c = next(csv.reader([linha]))
        if len(c) < 25:
            return None
        # Agrupa por campos de PHY relevantes: rate, sig_mode, mcs, bandwidth,
        # stbc, fec_coding, sgi, channel, secondary_channel, rx_format e len.
        # Nao assume que esta chave corrige todos os efeitos de AGC/firmware.
        campos = (4, 5, 6, 7, 11, 12, 13, 16, 17, 22, 23)
        formato = tuple(c[i].strip() for i in campos)
        rssi = float(c[3])
        v = np.asarray(ast.literal_eval(c[24]), dtype=float)
        if v.size != 128 or not np.isfinite(v).all() or not np.any(v):
            return None
        amplitude = np.hypot(v[::2], v[1::2])
        return (rssi, formato, amplitude)
    except (ValueError, SyntaxError, TypeError, IndexError, csv.Error):
        return None


def resumir(pacotes, formato=None):
    if not pacotes:
        return None
    if formato is None:
        formato = Counter(p[1] for p in pacotes).most_common(1)[0][0]
    selecionados = [p for p in pacotes if p[1] == formato]
    if len(selecionados) < MIN_PACOTES:
        return None
    a = np.stack([p[2] for p in selecionados])
    validos = (a > 0).mean(axis=0) >= .95
    if validos.sum() < MIN_SUBPORTADORAS:
        return None
    # Escala individual por pacote, apenas em indices positivos.
    with np.errstate(invalid='ignore'):
        escalas = np.nanmedian(np.where(a > 0, a, np.nan), axis=1)
    if not np.isfinite(escalas).all() or np.any(escalas <= 0):
        return None
    normal = a / escalas[:, None]
    perfil = np.median(normal, axis=0)
    return {'rssi': float(np.median([p[0] for p in selecionados])),
            'perfil': perfil, 'validos': validos, 'formato': formato,
            'n': len(selecionados)}


def construir_modelo(janelas):
    if len(janelas) < MIN_JANELAS:
        return None
    mascara = np.logical_and.reduce([j['validos'] for j in janelas])
    if mascara.sum() < MIN_SUBPORTADORAS:
        return None
    perfis = np.stack([j['perfil'][mascara] for j in janelas])
    referencia = np.median(perfis, axis=0)
    desvios = np.median(np.abs(perfis - referencia), axis=1)
    centro = float(np.median(desvios))
    dispersao = float(1.4826 * np.median(np.abs(desvios - centro)))
    # Piso para evitar divisao por variabilidade quase zero na calibracao.
    escala = max(.05, dispersao)
    scores = desvios / escala
    limiar = max(3., float(np.quantile(scores, .95) + 1.))
    return {'mascara': mascara, 'referencia': referencia, 'escala': escala,
            'limiar': limiar, 'rssi0': float(np.median([j['rssi'] for j in janelas]))}


def pontuar(janela, modelo):
    mascara = modelo['mascara']
    idx = np.flatnonzero(mascara)
    presentes = janela['validos'][idx]
    if presentes.sum() < MIN_SUBPORTADORAS or presentes.mean() < MIN_FRACAO_COMUM:
        return None, None
    desvio = float(np.median(np.abs(
        janela['perfil'][idx][presentes] - modelo['referencia'][presentes])))
    return desvio / modelo['escala'], desvio


def main():
    print(f'Conectando em {PORTA} ({BAUDRATE} baud)...')
    print('Saia do quarto nos primeiros 5 segundos; deixe vazio ate o fim da calibracao (25 s).')
    try:
        porta = serial.Serial(PORTA, BAUDRATE, timeout=.15)
    except serial.SerialException as exc:
        print('Falha ao abrir serial:', exc)
        return
    fila = queue.Queue(maxsize=4000)
    parar = threading.Event()
    perdas = [0]

    def leitor():
        while not parar.is_set():
            try:
                linha = porta.readline().decode('utf-8', errors='ignore').strip()
                pacote = parsear(linha)
                if pacote is not None:
                    try:
                        fila.put_nowait((time.monotonic(), pacote))
                    except queue.Full:
                        perdas[0] += 1
            except serial.SerialException as exc:
                print('Erro serial:', exc)
                parar.set()

    thread = threading.Thread(target=leitor, daemon=True)
    thread.start()
    os.makedirs(PASTA_EVENTOS, exist_ok=True)
    caminho = os.path.join(PASTA_EVENTOS, 'eventos_v5_3_1_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.csv')
    fig, (ax_r, ax_s) = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    fig.subplots_adjust(top=.84, hspace=.30)
    fig.suptitle('SmartRoom V5.3.1 - analise CSI por configuracao Wi-Fi')
    status = fig.text(.5, .90, 'Aguardando...', ha='center', fontsize=11)
    linha_r, = ax_r.plot([], [], label='RSSI (diagnostico)')
    linha_s, = ax_s.plot([], [], label='Score CSI (sem RSSI)')
    linha_l, = ax_s.plot([], [], 'r--', label='Limiar CSI')
    ax_r.set_ylabel('RSSI (dBm)')
    ax_s.set_ylabel('Score CSI')
    ax_s.set_xlabel('Tempo (s)')
    for ax in (ax_r, ax_s):
        ax.grid(alpha=.3)
        ax.legend(loc='upper left')
    plt.ion()
    plt.show(block=False)

    t0 = time.monotonic()
    segundo = 0
    amostras = []
    referencia_bruta = []
    modelos = {}
    calibrado = False
    modelo = None
    acima = abaixo = 0
    alerta = False
    historico = deque(maxlen=HISTORICO)
    processados = 0
    ultima_tela = 0.

    with open(caminho, 'w', newline='', encoding='utf-8') as arquivo:
        gravador = csv.writer(arquivo)
        gravador.writerow(['segundo', 'horario', 'pacotes_total', 'pacotes_formato',
                           'formatos_distintos', 'rssi_dbm', 'delta_rssi_db',
                           'desvio_csi', 'score_csi', 'limiar', 'estado', 'formato_utilizado'])

        def fechar_segundo(s, pacotes):
            nonlocal calibrado, modelos, modelo, acima, abaixo, alerta
            contagem = Counter(p[1] for p in pacotes)
            if TEMPO_SAIDA <= s < TEMPO_SAIDA + TEMPO_CALIBRACAO:
                referencia_bruta.append(pacotes[:])
            if s >= TEMPO_SAIDA + TEMPO_CALIBRACAO and not calibrado:
                calibrado = True
                total_cal = Counter(p[1] for janela in referencia_bruta for p in janela)
                for formato, _ in total_cal.most_common():
                    janelas = [resumir(j, formato) for j in referencia_bruta]
                    janelas = [j for j in janelas if j is not None]
                    novo = construir_modelo(janelas)
                    if novo is not None:
                        modelos[formato] = novo
                print(f'Calibracao: {len(modelos)} formatos com referencia valida.')
                for formato, m in modelos.items():
                    print(f'  formato={formato} | RSSI={m["rssi0"]:.1f} | limiar={m["limiar"]:.2f}')
            # Seleciona somente um formato QUE TENHA SUA PROPRIA REFERENCIA.
            # Nao compara vetores de formatos diferentes nem adapta a referencia ao alerta.
            candidatos = [(n, fmt) for fmt, n in contagem.items() if fmt in modelos and n >= MIN_PACOTES]
            formato_atual = max(candidatos)[1] if candidatos else None
            janela = resumir(pacotes, formato_atual) if formato_atual else None
            modelo = modelos.get(formato_atual)
            # RSSI geral aparece mesmo quando nenhum formato e comparavel.
            rssi = float(np.median([p[0] for p in pacotes])) if pacotes else float('nan')
            delta_rssi = rssi - modelo['rssi0'] if modelo and np.isfinite(rssi) else float('nan')
            score = desvio = float('nan')
            if s < TEMPO_SAIDA:
                estado = 'SAIA DO QUARTO'
            elif s < TEMPO_SAIDA + TEMPO_CALIBRACAO:
                estado = 'CALIBRANDO QUARTO VAZIO'
            elif not modelos:
                estado = 'INCONCLUSIVO: NENHUM FORMATO CALIBRADO'
            elif janela is None:
                estado = 'INCONCLUSIVO: SEM FORMATO CALIBRADO COM PACOTES SUFICIENTES'
                acima = abaixo = 0
                alerta = False
            else:
                score, desvio = pontuar(janela, modelo)
                if score is None:
                    score = desvio = float('nan')
                    estado = 'INCONCLUSIVO: SUBPORTADORAS INSUFICIENTES'
                    acima = abaixo = 0
                    alerta = False
                elif score > modelo['limiar']:
                    acima += 1
                    abaixo = 0
                    if acima >= PERSISTENCIA:
                        alerta = True
                    estado = 'ALTERACAO CSI PERSISTENTE (PRESENCA NAO CONFIRMADA)' if alerta else 'ALTERACAO CSI EM OBSERVACAO'
                else:
                    abaixo += 1
                    acima = 0
                    if abaixo >= LIBERACAO:
                        alerta = False
                    estado = 'AGUARDANDO NORMALIZACAO' if alerta else 'CSI COMPATIVEL COM REFERENCIA'
            historico.append((s, rssi, score))
            print(f'{s:3d}s | total={len(pacotes):3d} | formato={janela["n"] if janela else 0:3d} '
                  f'| tipos={len(contagem):2d} | RSSI={rssi:6.1f} | CSI={score:6.2f} | {estado}')
            status.set_text(estado)
            gravador.writerow([s, datetime.now().isoformat(timespec='seconds'), len(pacotes),
                               janela['n'] if janela else 0, len(contagem),
                               '' if not np.isfinite(rssi) else f'{rssi:.2f}',
                               '' if not np.isfinite(delta_rssi) else f'{delta_rssi:.2f}',
                               '' if not np.isfinite(desvio) else f'{desvio:.5f}',
                               '' if not np.isfinite(score) else f'{score:.3f}',
                               '' if modelo is None else f'{modelo["limiar"]:.3f}',
                               estado, '' if formato_atual is None else '|'.join(formato_atual)])
            arquivo.flush()

        try:
            while plt.fignum_exists(fig.number) and not parar.is_set():
                for _ in range(500):
                    try:
                        instante, pacote = fila.get_nowait()
                    except queue.Empty:
                        break
                    s = int(instante - t0)
                    while segundo < s:
                        fechar_segundo(segundo, amostras)
                        amostras = []
                        segundo += 1
                    amostras.append(pacote)
                    processados += 1
                agora = int(time.monotonic() - t0)
                if fila.empty():
                    while segundo < agora:
                        fechar_segundo(segundo, amostras)
                        amostras = []
                        segundo += 1
                if time.monotonic() - ultima_tela > .25:
                    if historico:
                        x, r, v = map(list, zip(*historico))
                        linha_r.set_data(x, r)
                        linha_s.set_data(x, v)
                        linha_l.set_data(x, [modelo['limiar'] if modelo else np.nan] * len(x))
                        ax_r.set_xlim(max(0, x[-1]-HISTORICO), max(HISTORICO, x[-1]+2))
                        for ax in (ax_r, ax_s):
                            ax.relim()
                            ax.autoscale_view(scalex=False, scaley=True)
                    fig.canvas.draw_idle()
                    ultima_tela = time.monotonic()
                plt.pause(.02)
        except KeyboardInterrupt:
            print('Encerrado pelo usuario.')
        finally:
            parar.set()
            thread.join(timeout=1)
            porta.close()
            plt.close(fig)
            print(f'Pacotes processados: {processados}; perdas na fila: {perdas[0]}')
            print('Eventos:', caminho)


if __name__ == '__main__':
    main()

"""SmartRoom v3 — monitor experimental de alteracoes CSI, nao detector validado de pessoas.
Requer: pip install pyserial numpy matplotlib
Feche o idf.py monitor antes de executar.
"""
import ast
import csv
import queue
import threading
import time
from collections import deque

import matplotlib.pyplot as plt
import numpy as np
import serial

PORTA = 'COM13'
BAUDRATE = 921600
TEMPO_SAIDA = 5
TEMPO_CALIBRACAO = 15
MIN_PACOTES = 20
MIN_JANELAS_CALIBRACAO = 8
PERSISTENCIA = 3
HISTORICO = 90


def parsear(linha):
    if not linha.startswith('CSI_DATA,'):
        return None
    try:
        c = next(csv.reader([linha]))
        if len(c) < 25:
            return None
        rssi = float(c[3])
        # MCS, SGI e STBC podem variar: nao bloqueamos toda a janela por isso.
        # Mantemos apenas quadros de mesmo modo, largura e comprimento CSI.
        formato = (c[5], c[8], c[23])
        v = np.asarray(ast.literal_eval(c[24]), dtype=float)
        if v.size != 128 or not np.isfinite(v).all() or not np.any(v):
            return None
        return rssi, formato, np.hypot(v[::2], v[1::2])
    except (ValueError, SyntaxError, TypeError, IndexError, csv.Error):
        return None


def resumo(pacotes):
    if len(pacotes) < MIN_PACOTES:
        return None
    # Seleciona o formato predominante na janela, sem exigir MCS identico.
    from collections import Counter
    formato = Counter(p[1] for p in pacotes).most_common(1)[0][0]
    selecionados = [p for p in pacotes if p[1] == formato]
    if len(selecionados) < MIN_PACOTES:
        return None
    a = np.stack([p[2] for p in selecionados])
    validas = np.mean(a > 0, axis=0) >= .9
    if validas.sum() < 10:
        return None
    a = a[:, validas]
    a = a / np.maximum(np.median(a, axis=1, keepdims=True), 1e-6)
    perfil = np.median(a, axis=0)
    return float(np.median([p[0] for p in selecionados])), perfil, validas


def main():
    print(f'Conectando a {PORTA} em {BAUDRATE} baud...')
    print('Saia nos primeiros 5s; deixe o quarto vazio durante a calibracao. Ctrl+C encerra.')
    try:
        porta = serial.Serial(PORTA, BAUDRATE, timeout=.15)
    except serial.SerialException as e:
        print('Falha na porta serial:', e)
        return

    fila = queue.Queue(maxsize=3000)
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
            except serial.SerialException as e:
                print('Erro serial:', e)
                parar.set()

    thread = threading.Thread(target=leitor, daemon=True)
    thread.start()
    fig, (ax_r, ax_s) = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    fig.subplots_adjust(top=.84, hspace=.32)
    fig.suptitle('SmartRoom — CSI em tempo real (experimental)', y=.98)
    estado = fig.text(.5, .90, 'Aguardando pacotes...', ha='center', fontsize=12)
    lr, = ax_r.plot([], [], label='RSSI mediano (dBm)')
    ls, = ax_s.plot([], [], label='Desvio da referencia')
    ll, = ax_s.plot([], [], 'r--', label='Limiar')
    ax_r.set_ylabel('RSSI (dBm)')
    ax_s.set_ylabel('Pontuacao')
    ax_s.set_xlabel('Tempo (s)')
    for ax in (ax_r, ax_s):
        ax.grid(alpha=.3)
        ax.legend(loc='upper left')
    plt.ion()
    plt.show(block=False)

    t0 = time.monotonic()
    segundo_atual = 0
    amostras = []
    referencia = []
    modelo = None
    consecutivos = 0
    historico = deque(maxlen=HISTORICO)
    processados = 0
    ultima_tela = 0

    def fechar_segundo(s, pacotes):
        nonlocal modelo, consecutivos
        dado = resumo(pacotes)
        rssi = dado[0] if dado else np.nan
        score = np.nan
        if s < TEMPO_SAIDA:
            msg = 'SAIA DO QUARTO'
        elif s < TEMPO_SAIDA + TEMPO_CALIBRACAO:
            if dado is not None:
                referencia.append(dado)
            msg = f'CALIBRANDO: {len(referencia)} janelas validas'
        else:
            if modelo is None:
                if len(referencia) < MIN_JANELAS_CALIBRACAO:
                    msg = 'CALIBRACAO PENDENTE — REINICIE COM QUARTO VAZIO'
                else:
                    # Mantem apenas a mascara de subportadoras mais comum na calibracao.
                    from collections import Counter
                    chave = Counter(tuple(x[2]) for x in referencia).most_common(1)[0][0]
                    base = [x for x in referencia if tuple(x[2]) == chave]
                    if len(base) < MIN_JANELAS_CALIBRACAO:
                        msg = 'CALIBRACAO PENDENTE — FORMATOS CSI INCONSISTENTES'
                    else:
                        rs = np.array([x[0] for x in base])
                        ps = np.stack([x[1] for x in base])
                        r0 = float(np.median(rs))
                        p0 = np.median(ps, axis=0)
                        dr = np.abs(rs-r0)
                        dp = np.median(np.abs(ps-p0), axis=1)
                        escala_r = max(1., 1.4826*np.median(np.abs(rs-r0)))
                        escala_p = max(.025, 1.4826*np.median(np.abs(dp-np.median(dp))))
                        scores_base = .5*dr/escala_r + .5*dp/escala_p
                        limiar = max(3., float(np.max(scores_base)+1.))
                        modelo = (r0, p0, escala_r, escala_p, limiar, chave)
                        print(f'Calibrado: {len(base)} janelas, RSSI={r0:.1f}, limiar={limiar:.2f}')
                        msg = 'CALIBRADO — MONITORANDO'
            else:
                msg = 'MONITORANDO'
            if modelo is not None:
                if dado is None:
                    msg = 'SINAL INSUFICIENTE'
                    consecutivos = 0
                elif tuple(dado[2]) != modelo[5]:
                    msg = 'FORMATO CSI INCOMPATIVEL — SEM DECISAO'
                    consecutivos = 0
                else:
                    r0, p0, sr, sp, limiar, _ = modelo
                    dr = abs(dado[0]-r0)/sr
                    dp = float(np.median(np.abs(dado[1]-p0)))/sp
                    score = .5*dr + .5*dp
                    consecutivos = consecutivos+1 if score > limiar else 0
                    msg = ('POSSIVEL ALTERACAO' if consecutivos >= PERSISTENCIA else 'SEM ALTERACAO') + f' | score={score:.2f}'
        historico.append((s, rssi, score))
        print(f'{s:3d}s | {len(pacotes):3d} pacotes | {msg}')
        estado.set_text(msg)

    try:
        while plt.fignum_exists(fig.number) and not parar.is_set():
            for _ in range(500):
                try:
                    instante, pacote = fila.get_nowait()
                except queue.Empty:
                    break
                s = int(instante-t0)
                while segundo_atual < s:
                    fechar_segundo(segundo_atual, amostras)
                    amostras = []
                    segundo_atual += 1
                amostras.append(pacote)
                processados += 1
            agora = int(time.monotonic()-t0)
            if fila.empty():
                while segundo_atual < agora:
                    fechar_segundo(segundo_atual, amostras)
                    amostras = []
                    segundo_atual += 1
            if time.monotonic()-ultima_tela > .25:
                if historico:
                    x, r, v = map(list, zip(*historico))
                    lr.set_data(x, r)
                    ls.set_data(x, v)
                    ll.set_data(x, [modelo[4] if modelo else np.nan]*len(x))
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


if __name__ == '__main__':
    main()

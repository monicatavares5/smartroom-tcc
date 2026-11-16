"""SmartRoom - monitor experimental ao vivo (ESP32-S3 csi_recv_router).

Calibre com o ambiente vazio. Indicacao de ALTERACAO, nao prova de pessoa.
Feche o idf.py monitor antes de abrir a porta serial.
"""
import ast
import csv
import time
import threading
import queue
from collections import deque

import matplotlib.pyplot as plt
import numpy as np
import serial

PORTA = 'COM13'                 # ajuste se o Windows mudar a COM
BAUDRATE = 921600
ESPERA_SAIDA = 5               # tempo para sair do quarto antes da calibracao
CALIBRACAO = 15                # segundos de ambiente vazio
PERSISTENCIA = 3               # segundos consecutivos acima do limiar
HISTORICO = 90                 # segundos exibidos
MIN_PACOTES_SEGUNDO = 20


def interpretar(linha):
    if not linha.startswith('CSI_DATA,'):
        return None
    try:
        c = next(csv.reader([linha]))
        if len(c) < 25 or c[0] != 'CSI_DATA':
            return None
        rssi = float(c[3])
        cfg = tuple(c[i] for i in (4, 5, 6, 7, 13, 16, 17, 21))
        vetor = np.asarray(ast.literal_eval(c[24]), dtype=float)
        if vetor.size != 128 or not np.all(np.isfinite(vetor)) or not np.any(vetor):
            return None
        amp = np.hypot(vetor[::2], vetor[1::2])
        return rssi, cfg, amp
    except (ValueError, SyntaxError, TypeError, IndexError, csv.Error):
        return None


def mediana_abs(x):
    return np.median(np.abs(x - np.median(x)))


def main():
    print(f'Conectando a {PORTA} em {BAUDRATE} baud...')
    print('Deixe o ambiente VAZIO durante a calibracao. Ctrl+C para encerrar.')
    try:
        porta = serial.Serial(PORTA, BAUDRATE, timeout=0.15)
    except serial.SerialException as exc:
        print('Nao foi possivel abrir a porta:', exc)
        return

    fig, (ax_r, ax_s) = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    fig.suptitle('SmartRoom | Monitor CSI em tempo real (experimental)')
    lr, = ax_r.plot([], [], label='RSSI (dBm)')
    ls, = ax_s.plot([], [], label='Desvio da referencia')
    lim, = ax_s.plot([], [], 'r--', label='Limiar calibrado')
    ax_r.set_ylabel('RSSI (dBm)')
    ax_s.set_ylabel('Pontuacao')
    ax_s.set_xlabel('Tempo (s)')
    for ax in (ax_r, ax_s):
        ax.grid(alpha=.25)
        ax.legend(loc='upper left')
    status = fig.text(.5, .965, 'Iniciando...', ha='center', fontsize=12)
    plt.ion()
    plt.show(block=False)

    t0 = time.monotonic()
    bucket = -1
    amostras = []
    ref = []
    cfg_base = None
    validas = None
    r0 = sh0 = d0 = r_scale = d_scale = limiar = None
    streak = 0
    xs, rssis, scores = deque(maxlen=HISTORICO), deque(maxlen=HISTORICO), deque(maxlen=HISTORICO)
    n_total = 0
    fila = queue.Queue(maxsize=2000)
    parar = threading.Event()
    perdas_fila = [0]

    def ler_serial():
        while not parar.is_set():
            try:
                linha = porta.readline().decode('utf-8', errors='ignore').strip()
                if not linha:
                    continue
                dado = interpretar(linha)
                if dado is not None:
                    try:
                        fila.put_nowait((time.monotonic(), dado))
                    except queue.Full:
                        perdas_fila[0] += 1
            except serial.SerialException as exc:
                print('Erro na leitura serial:', exc)
                parar.set()
                break

    leitor = threading.Thread(target=ler_serial, daemon=True)
    leitor.start()

    def processar_segundo(seg, pacotes):
        nonlocal cfg_base, validas, r0, sh0, d0, r_scale, d_scale, limiar, streak
        if len(pacotes) < MIN_PACOTES_SEGUNDO:
            return 'SINAL INSUFICIENTE'
        # Durante a calibracao escolhemos a configuracao mais comum.
        if cfg_base is None:
            from collections import Counter
            cfg_base = Counter(p[1] for p in pacotes).most_common(1)[0][0]
        filtrados = [p for p in pacotes if p[1] == cfg_base]
        if len(filtrados) < MIN_PACOTES_SEGUNDO:
            return 'CONFIGURACAO WIFI MUDOU'
        amps = np.stack([p[2] for p in filtrados])
        rssi = float(np.median([p[0] for p in filtrados]))
        if validas is None:
            validas = np.mean(amps > 0, axis=0) > .9
            if validas.sum() < 10:
                validas = None
                return 'CSI INSUFICIENTE'
        amps = amps[:, validas]
        shape = amps / np.maximum(np.median(amps, axis=1, keepdims=True), 1e-6)
        perfil = np.median(shape, axis=0)
        if seg < ESPERA_SAIDA:
            return 'SAIA DO AMBIENTE'
        if seg < ESPERA_SAIDA + CALIBRACAO:
            ref.append((rssi, perfil))
            return f'CALIBRANDO {seg - ESPERA_SAIDA + 1}/{CALIBRACAO}s'
        if r0 is None:
            if len(ref) < max(8, CALIBRACAO // 2):
                return 'CALIBRACAO INSUFICIENTE: REINICIE'
            r_ref = np.array([v[0] for v in ref])
            sh_ref = np.stack([v[1] for v in ref])
            r0 = float(np.median(r_ref))
            sh0 = np.median(sh_ref, axis=0)
            dist_ref = np.median(np.abs(sh_ref - sh0), axis=1)
            d0 = float(np.median(dist_ref))
            r_scale = max(1., 1.4826 * mediana_abs(r_ref))
            d_scale = max(.025, 1.4826 * mediana_abs(dist_ref))
            base_score = .5 * np.abs(r_ref - r0) / r_scale + .5 * np.maximum(0, dist_ref - d0) / d_scale
            limiar = max(3., float(np.max(base_score) + 1.))
            print(f'Calibrado: RSSI={r0:.1f} dBm, limiar={limiar:.2f}, {len(ref)} janelas')
        dist = float(np.median(np.abs(perfil - sh0)))
        score = .5 * abs(rssi - r0) / r_scale + .5 * max(0, dist - d0) / d_scale
        streak = streak + 1 if score > limiar else 0
        xs.append(seg)
        rssis.append(rssi)
        scores.append(score)
        return ('POSSIVEL ALTERACAO' if streak >= PERSISTENCIA else 'SEM ALTERACAO') + f' | score={score:.2f}'

    ultimo_desenho = 0.0
    try:
        while plt.fignum_exists(fig.number) and not parar.is_set():
            # A thread serial continua lendo enquanto a interface desenha.
            # Limitamos o processamento por ciclo para nao congelar a janela.
            for _ in range(500):
                try:
                    instante, dado = fila.get_nowait()
                except queue.Empty:
                    break
                segundo = int(instante - t0)
                if bucket == -1:
                    bucket = segundo
                if segundo != bucket:
                    mensagem = processar_segundo(bucket, amostras)
                    print(f'{bucket:3d}s | {len(amostras):3d} pacotes | {mensagem}')
                    status.set_text(mensagem)
                    amostras = []
                    bucket = segundo
                amostras.append(dado)
                n_total += 1

            # Se nao chegou dado, ainda assim encerra a janela de 1 segundo.
            segundo_atual = int(time.monotonic() - t0)
            if bucket >= 0 and segundo_atual > bucket and fila.empty():
                mensagem = processar_segundo(bucket, amostras)
                print(f'{bucket:3d}s | {len(amostras):3d} pacotes | {mensagem}')
                status.set_text(mensagem)
                amostras = []
                bucket = segundo_atual

            if time.monotonic() - ultimo_desenho >= 0.25:
                if xs:
                    x = list(xs)
                    lr.set_data(x, list(rssis))
                    ls.set_data(x, list(scores))
                    lim.set_data(x, [limiar] * len(x))
                    ax_r.set_xlim(max(0, x[-1] - HISTORICO), max(HISTORICO, x[-1] + 2))
                    ax_r.relim()
                    ax_r.autoscale_view(scalex=False, scaley=True)
                    ax_s.relim()
                    ax_s.autoscale_view(scalex=False, scaley=True)
                fig.canvas.draw_idle()
                ultimo_desenho = time.monotonic()
            plt.pause(.02)
    except KeyboardInterrupt:
        print('Encerrado pelo usuario.')
    finally:
        parar.set()
        leitor.join(timeout=1.0)
        porta.close()
        print(f'Total de pacotes processados: {n_total}; perdas por fila cheia: {perdas_fila[0]}')
        plt.ioff()
        plt.close(fig)


if __name__ == '__main__':
    main()

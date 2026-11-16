"""SmartRoom V5.2 — detector experimental de alteracoes CSI (nao identifica pessoas com certeza).
Requer: pip install pyserial numpy matplotlib. Feche o monitor ESP-IDF.
"""
import ast
import csv
import os
import queue
import threading
import time
from collections import deque, Counter
from datetime import datetime

import matplotlib.pyplot as plt
import numpy as np
import serial

PORTA = 'COM13'
BAUDRATE = 921600
TEMPO_SAIDA = 5
TEMPO_CALIBRACAO = 15
MIN_PACOTES = 20
MIN_JANELAS = 8
MIN_SUBPORTADORAS = 10
PERSISTENCIA = 3
ESTABILIDADE_JANELAS = 8
LIMIAR_MUDANCA = 0.12  # distancia mediana entre perfis normalizados consecutivos
LIBERACAO = 4
HISTORICO = 90
PASTA_EVENTOS = r'C:\TCC\SmartRoom\data'


def parsear(linha):
    if not linha.startswith('CSI_DATA,'):
        return None
    try:
        c = next(csv.reader([linha]))
        if len(c) < 25:
            return None
        rssi = float(c[3])
        # Preserva modo e largura: formatos fisicamente diferentes nao sao comparaveis.
        formato = (c[5], c[8], c[23])
        v = np.asarray(ast.literal_eval(c[24]), dtype=float)
        if v.size != 128 or not np.isfinite(v).all() or not np.any(v):
            return None
        return rssi, formato, np.hypot(v[::2], v[1::2])
    except (ValueError, SyntaxError, TypeError, IndexError, csv.Error):
        return None


def resumir(pacotes):
    if len(pacotes) < MIN_PACOTES:
        return None
    formato = Counter(p[1] for p in pacotes).most_common(1)[0][0]
    sel = [p for p in pacotes if p[1] == formato]
    if len(sel) < MIN_PACOTES:
        return None
    a = np.stack([p[2] for p in sel])
    # Mantem vetor de 64 posicoes fixas; nao comprime a mascara por janela.
    valido = (a > 0).mean(axis=0) >= .90
    if valido.sum() < MIN_SUBPORTADORAS:
        return None
    # Zeros representam subportadoras ausentes: ignorar NaNs na mediana.
    with np.errstate(all='ignore'):
        escalas = np.nanmedian(np.where(a > 0, a, np.nan), axis=1)
    if not np.isfinite(escalas).all() or np.any(escalas <= 0):
        return None
    normal = a / escalas[:, None]
    perfil = np.median(normal, axis=0)
    return float(np.median([p[0] for p in sel])), perfil, valido, formato


def criar_modelo(referencia):
    if len(referencia) < MIN_JANELAS:
        return None
    # Escolhe um formato de radio predominante na calibracao; nao mistura formatos.
    formato = Counter(d[3] for d in referencia).most_common(1)[0][0]
    base = [d for d in referencia if d[3] == formato]
    if len(base) < MIN_JANELAS:
        return None
    # Mesmas subportadoras fisicas em todas as janelas da referencia.
    mascara = np.logical_and.reduce([d[2] for d in base])
    if mascara.sum() < MIN_SUBPORTADORAS:
        return None
    rs = np.array([d[0] for d in base])
    ps = np.stack([d[1][mascara] for d in base])
    r0 = float(np.median(rs))
    p0 = np.median(ps, axis=0)
    sr = max(1., float(1.4826 * np.median(np.abs(rs-r0))))
    desvios = np.median(np.abs(ps-p0), axis=1)
    sp = max(.025, float(1.4826 * np.median(np.abs(desvios-np.median(desvios)))))
    scores = .5*np.abs(rs-r0)/sr + .5*desvios/sp
    limiar = max(3., float(np.quantile(scores, .95) + 1.))
    return dict(formato=formato, mascara=mascara, r0=r0, p0=p0, sr=sr, sp=sp, limiar=limiar)


def pontuar(dado, modelo):
    if dado[3] != modelo['formato']:
        return None, 'FORMATO RADIO DIFERENTE'
    mascara = modelo['mascara']
    comuns = mascara & dado[2]
    if comuns.sum() < MIN_SUBPORTADORAS or comuns.sum() < .75 * mascara.sum():
        return None, 'SUBPORTADORAS INSUFICIENTES'
    # Seleciona os mesmos indices no vetor original, sem deslocar as frequencias.
    idx = np.flatnonzero(mascara)
    presentes = dado[2][idx]
    delta = np.abs(dado[1][idx][presentes] - modelo['p0'][presentes])
    dr = abs(dado[0]-modelo['r0'])/modelo['sr']
    dp = float(np.median(delta))/modelo['sp']
    return .5*dr + .5*dp, None


def main():
    print(f'Conectando a {PORTA} em {BAUDRATE} baud...')
    print('Saia nos primeiros 5 s. Deixe o quarto vazio durante a calibracao.')
    try:
        porta = serial.Serial(PORTA, BAUDRATE, timeout=.15)
    except serial.SerialException as exc:
        print('Erro ao abrir porta:', exc)
        return
    fila = queue.Queue(maxsize=3000)
    parar = threading.Event()
    perdas = [0]

    def leitor():
        while not parar.is_set():
            try:
                pacote = parsear(porta.readline().decode('utf-8', errors='ignore').strip())
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
    fig, (ax_r, ax_s) = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    fig.subplots_adjust(top=.84, hspace=.32)
    fig.suptitle('SmartRoom V5.2 — CSI em tempo real (experimental)', y=.98)
    texto = fig.text(.5, .90, 'Aguardando dados...', ha='center', fontsize=12)
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

    os.makedirs(PASTA_EVENTOS, exist_ok=True)
    caminho = os.path.join(PASTA_EVENTOS, 'eventos_v5_2_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.csv')
    print('Eventos:', caminho)
    t0 = time.monotonic()
    segundo = 0
    amostras = []
    referencia = []
    modelo = None
    acima = 0
    abaixo = 0
    em_alerta = False
    historico = deque(maxlen=HISTORICO)
    processados = 0
    ultima_tela = 0
    perfil_anterior = None
    formato_anterior = None
    estavel = 0
    ultima_mudanca = np.nan

    with open(caminho, 'w', newline='', encoding='utf-8') as arquivo:
        gravador = csv.writer(arquivo)
        gravador.writerow(['segundo','horario','pacotes','rssi_dbm','score','limiar','estado','motivo','mudanca_perfil','segundos_estaveis'])

        def fechar_segundo(s, pacotes):
            nonlocal modelo, acima, abaixo, em_alerta, perfil_anterior, formato_anterior, estavel, ultima_mudanca
            dado = resumir(pacotes)
            rssi = dado[0] if dado else np.nan
            score = np.nan
            motivo = ''
            if s < TEMPO_SAIDA:
                estado = 'SAIA DO QUARTO'
            elif s < TEMPO_SAIDA + TEMPO_CALIBRACAO:
                if dado is not None:
                    referencia.append(dado)
                estado = f'CALIBRANDO: {len(referencia)} janelas validas'
            else:
                if modelo is None:
                    modelo = criar_modelo(referencia)
                    if modelo is not None:
                        print(f"Calibrado: RSSI={modelo['r0']:.1f}, limiar={modelo['limiar']:.2f}, "
                              f"subportadoras={modelo['mascara'].sum()}")
                if modelo is None:
                    estado = 'SINAL INCONCLUSIVO: CALIBRACAO INSUFICIENTE'
                elif dado is None:
                    estado = 'SINAL INCONCLUSIVO: PACOTES INSUFICIENTES'
                    motivo = 'PACOTES INSUFICIENTES'
                    acima = abaixo = 0
                else:
                    valor, motivo_erro = pontuar(dado, modelo)
                    if motivo_erro:
                        estado = 'SINAL INCONCLUSIVO: ' + motivo_erro
                        motivo = motivo_erro
                        acima = abaixo = 0
                        perfil_anterior = None
                        estavel = 0
                    else:
                        score = valor
                        # Distancia entre perfis sucessivos, nas mesmas subportadoras.
                        # Mede dinamica, nao prova presenca nem ausencia de pessoas.
                        if perfil_anterior is not None and formato_anterior == dado[3]:
                            indices = modelo['mascara'] & dado[2] & perfil_anterior[1]
                            if indices.sum() >= MIN_SUBPORTADORAS:
                                ultima_mudanca = float(np.median(np.abs(
                                    dado[1][indices] - perfil_anterior[0][indices])))
                            else:
                                ultima_mudanca = np.nan
                        else:
                            ultima_mudanca = np.nan
                        perfil_anterior = (dado[1].copy(), dado[2].copy())
                        formato_anterior = dado[3]
                        if np.isfinite(ultima_mudanca) and ultima_mudanca <= LIMIAR_MUDANCA:
                            estavel += 1
                        else:
                            estavel = 0
                        if score > modelo['limiar']:
                            acima += 1
                            abaixo = 0
                            if acima >= PERSISTENCIA:
                                em_alerta = True
                            if estavel >= ESTABILIDADE_JANELAS:
                                estado = 'ALTERACAO PERSISTENTE ESTAVEL - PRESENCA INDETERMINADA'
                            else:
                                estado = 'POSSIVEL ALTERACAO RECENTE' if em_alerta else 'ALTERACAO EM OBSERVACAO'
                        else:
                            abaixo += 1
                            acima = 0
                            if abaixo >= LIBERACAO:
                                em_alerta = False
                            estado = ('AGUARDANDO RETORNO A REFERENCIA' if em_alerta
                                      else 'AMBIENTE COMPATIVEL COM REFERENCIA')
                        estado += f' | score={score:.2f} | mudanca={ultima_mudanca:.3f}'
            historico.append((s, rssi, score))
            print(f'{s:3d}s | {len(pacotes):3d} pacotes | {estado}')
            texto.set_text(estado)
            gravador.writerow([s, datetime.now().isoformat(timespec='seconds'), len(pacotes),
                               '' if np.isnan(rssi) else f'{rssi:.2f}',
                               '' if np.isnan(score) else f'{score:.3f}',
                               '' if modelo is None else f"{modelo['limiar']:.3f}", estado, motivo,
                               '' if not np.isfinite(ultima_mudanca) else f'{ultima_mudanca:.4f}', estavel])
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
                        lr.set_data(x, r)
                        ls.set_data(x, v)
                        ll.set_data(x, [modelo['limiar'] if modelo else np.nan] * len(x))
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

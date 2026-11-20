"""SmartRoom: detector experimental de alteracao de ambiente via CSI.

Uso: python detectar_presenca.py [caminho_do_csv]
Referencia: segundos 5 a 18 (quarto presumivelmente vazio).
Nao equivale a deteccao validada de presenca humana.
"""
import ast
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

PASTA = Path(__file__).resolve().parent / 'dados_serial'
REFERENCIA_INICIO = 5
REFERENCIA_FIM = 18
JANELA_SEGUNDOS = 1


def carregar():
    if len(sys.argv) > 1:
        caminho = Path(sys.argv[1])
    else:
        arquivos = sorted(PASTA.glob('csi_*.csv'), key=lambda p: p.stat().st_mtime)
        if not arquivos:
            raise FileNotFoundError('Nenhum arquivo csi_*.csv encontrado em ' + str(PASTA))
        caminho = arquivos[-1]
    df = pd.read_csv(caminho, low_memory=False)
    colunas = {'timestamp_pc', 'rssi', 'csi_data'}
    if not colunas.issubset(df.columns):
        raise ValueError(f'CSV precisa conter {colunas}. Encontradas: {list(df.columns)}')
    tempos = pd.to_datetime(df['timestamp_pc'], errors='coerce')
    t = (tempos - tempos.iloc[0]).dt.total_seconds().to_numpy()
    linhas = []
    for i, item in enumerate(df['csi_data']):
        try:
            v = np.asarray(ast.literal_eval(item), dtype=float)
            if v.size == 128 and np.all(np.isfinite(v)) and np.any(v):
                linhas.append((i, v))
        except (ValueError, SyntaxError, TypeError):
            continue
    if not linhas:
        raise ValueError('Nenhum CSI valido no CSV.')
    idx = np.array([i for i, _ in linhas])
    raw = np.stack([v for _, v in linhas])
    amplitude = np.hypot(raw[:, ::2], raw[:, 1::2])
    rssi = pd.to_numeric(df['rssi'], errors='coerce').to_numpy()[idx]
    return caminho, t[idx], rssi, amplitude


def executar():
    caminho, tempo, rssi, amp = carregar()
    ref_pkt = (tempo >= REFERENCIA_INICIO) & (tempo < REFERENCIA_FIM)
    if ref_pkt.sum() < 100:
        raise ValueError('Poucos pacotes no periodo de referencia (5-18 s).')
    # Apenas subportadoras presentes na referencia, evitando regioes nulas.
    validas = (amp[ref_pkt] > 0).mean(axis=0) > 0.9
    if validas.sum() < 10:
        raise ValueError('Poucas subportadoras validas na referencia.')
    amp = amp[:, validas]
    # Normalizacao por pacote reduz mudancas globais de ganho.
    shape = amp / np.maximum(np.median(amp, axis=1, keepdims=True), 1e-6)
    # Agrega por segundo para reduzir variacao entre pacotes.
    segundos = np.floor(tempo).astype(int)
    janelas = []
    for s in np.unique(segundos):
        m = (segundos == s) & np.isfinite(rssi)
        if m.sum() < 20:
            continue
        janelas.append((s, np.median(rssi[m]), np.median(shape[m], axis=0)))
    if len(janelas) < 20:
        raise ValueError('Poucas janelas de 1 segundo validas.')
    x = np.array([v[0] for v in janelas])
    rs = np.array([v[1] for v in janelas])
    shapes = np.stack([v[2] for v in janelas])
    base = (x >= REFERENCIA_INICIO) & (x < REFERENCIA_FIM)
    if base.sum() < 8:
        raise ValueError('Referencia insuficiente em janelas de 1 s.')
    r0 = np.median(rs[base])
    shape0 = np.median(shapes[base], axis=0)
    # Distancia entre perfis espectrais; robusta a poucos tons aberrantes.
    dist = np.median(np.abs(shapes - shape0), axis=1)
    # Escalas de referencia com pisos para evitar divisao por zero.
    r_scale = max(1.0, 1.4826 * np.median(np.abs(rs[base] - r0)))
    d0 = np.median(dist[base])
    d_scale = max(0.025, 1.4826 * np.median(np.abs(dist[base] - d0)))
    score_r = np.abs(rs - r0) / r_scale
    score_c = np.maximum(0, dist - d0) / d_scale
    # Soma: alteracoes de RSSI e/ou formato CSI elevam a pontuacao.
    score = 0.5 * score_r + 0.5 * score_c
    # Limite obtido SOMENTE da referencia vazia; nao dos dados ocupados.
    limiar = max(3.0, float(np.max(score[base]) + 1.0))
    suspeita = score > limiar
    # Persistencia minima de 3 janelas consecutivas.
    persistente = np.zeros(len(x), dtype=bool)
    for i in range(2, len(x)):
        if np.all(suspeita[i-2:i+1]) and np.all(np.diff(x[i-2:i+1]) == 1):
            persistente[i-2:i+1] = True
    # Mantem marcacao de toda sequencia com pelo menos 3 segundos.
    print(f'Arquivo: {caminho.name}')
    print(f'Pacotes validos: {len(tempo)}; subportadoras utilizadas: {validas.sum()}')
    print(f'Referencia de quarto vazio: {REFERENCIA_INICIO}-{REFERENCIA_FIM} s')
    print(f'RSSI de referencia: {r0:.2f} dBm; limiar: {limiar:.2f}')
    print('OBS: possivel alteracao do ambiente NAO comprova presenca humana.')
    for ini, fim, nome in [(0, 20, '0-20 s'), (20, 40, '20-40 s'), (40, 60, '40-60 s')]:
        m = (x >= ini) & (x < fim)
        if m.any():
            print(f'{nome}: RSSI mediano {np.median(rs[m]):.1f} dBm; '
                  f'score mediano {np.median(score[m]):.2f}; '
                  f'{persistente[m].sum()} s sinalizados')
    fig, axs = plt.subplots(3, 1, figsize=(13, 9), sharex=True)
    fig.suptitle(f'SmartRoom - Presenca estatica (experimental)\n{caminho.name}')
    axs[0].plot(x, rs, marker='.', label='RSSI mediano por segundo')
    axs[0].axhline(r0, color='gray', ls='--', label='Referencia vazia')
    axs[0].set_ylabel('RSSI (dBm)')
    axs[0].legend()
    axs[1].plot(x, score, marker='.', label='Desvio do ambiente vazio')
    axs[1].axhline(limiar, color='red', ls='--', label='Limiar')
    axs[1].set_ylabel('Pontuacao')
    axs[1].legend()
    axs[2].step(x, persistente.astype(int), where='mid')
    axs[2].set_yticks([0, 1], ['Normal', 'Alteracao'])
    axs[2].set_xlabel('Tempo (s)')
    for ax in axs:
        ax.axvspan(REFERENCIA_INICIO, REFERENCIA_FIM, color='green', alpha=0.10)
        ax.grid(alpha=0.2)
    fig.tight_layout()
    saida = caminho.with_name(caminho.stem + '_presenca.png')
    fig.savefig(saida, dpi=150)
    print('Grafico salvo em:', saida)
    plt.show()


if __name__ == '__main__':
    executar()

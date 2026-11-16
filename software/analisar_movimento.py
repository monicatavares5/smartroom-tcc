"""SmartRoom: analise experimental de movimento a partir de CSV Wi-Fi CSI."""
import ast
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

PASTA = Path(r'C:\TCC\SmartRoom\data')
ARQUIVO = None  # Ex.: r'C:\TCC\SmartRoom\data\csi_20261030_135436.csv'
SEGUNDOS_BASELINE = 15
JANELA_SEGUNDOS = 1.0
FATOR_LIMIAR = 3.0


def arquivo_entrada():
    if ARQUIVO:
        return Path(ARQUIVO)
    arquivos = list(PASTA.glob('csi_*.csv'))
    if not arquivos:
        raise FileNotFoundError(f'Nenhum CSV encontrado em {PASTA}')
    return max(arquivos, key=lambda p: p.stat().st_mtime)


def main():
    arquivo = arquivo_entrada()
    df = pd.read_csv(arquivo, low_memory=False)
    obrigatorias = ['timestamp_pc', 'csi_data', 'rssi']
    faltando = [c for c in obrigatorias if c not in df.columns]
    if faltando:
        raise ValueError(f'Colunas ausentes: {faltando}')
    t0 = pd.to_datetime(df['timestamp_pc'], errors='coerce')
    vetores, tempos, rssis, configs = [], [], [], []
    campos = ['mac', 'sig_mode', 'mcs', 'bandwidth', 'stbc', 'sgi', 'rx_format', 'csi_length']
    for idx, linha in df.iterrows():
        if pd.isna(t0.iloc[idx]):
            continue
        try:
            raw = np.asarray(ast.literal_eval(linha['csi_data']), dtype=float)
            if len(raw) != 128 or not np.all(np.isfinite(raw)) or not np.any(raw):
                continue
            amplitude = np.hypot(raw[0::2], raw[1::2])
            vetores.append(amplitude)
            tempos.append((t0.iloc[idx] - t0.iloc[0]).total_seconds())
            rssis.append(float(linha['rssi']))
            configs.append(tuple(str(linha[c]) for c in campos if c in df.columns))
        except (ValueError, SyntaxError, TypeError, KeyError):
            continue
    if len(vetores) < 100:
        raise ValueError('Poucos pacotes CSI validos.')
    a = np.stack(vetores)
    t = np.asarray(tempos)
    rssi = np.asarray(rssis)
    # Mantem configuracoes como tuplas Python

    # Usa somente subportadoras ativas em pelo menos 95% dos pacotes.
    mascara = (a > 0).mean(axis=0) >= .95
    if not np.any(mascara):
        raise ValueError('Nao ha subportadoras ativas suficientes.')
    a = a[:, mascara]

    # Diferenca relativa para reduzir efeito de mudancas na escala do sinal.
    # Pares de configuracoes diferentes nao sao comparados.
    diferenca = np.mean(
        np.abs(np.diff(a, axis=0)) / np.maximum((a[1:] + a[:-1]) / 2, 1),
        axis=1
    )
    mesma_config = np.array([configs[i] == configs[i-1] for i in range(1, len(configs))])
    diferenca[~mesma_config] = np.nan

    # Agrega em janelas de 1 segundo pela mediana (robusta a picos isolados).
    bins = np.floor(t[1:] / JANELA_SEGUNDOS).astype(int)
    serie = pd.DataFrame({'bin': bins, 'variacao': diferenca})
    agregado = serie.groupby('bin')['variacao'].median().dropna()
    x = (agregado.index.to_numpy() + .5) * JANELA_SEGUNDOS
    y = agregado.to_numpy()
    base = y[x < SEGUNDOS_BASELINE]
    if len(base) < 5:
        raise ValueError('Periodo inicial insuficiente para calibracao.')
    mediana = np.median(base)
    mad = np.median(np.abs(base - mediana))
    limiar = mediana + FATOR_LIMIAR * 1.4826 * mad
    # Evita limiar zero para baseline constante.
    limiar = max(limiar, mediana * 1.2, 1e-6)
    alerta = y > limiar

    print(f'Arquivo: {arquivo.name}')
    print(f'Pacotes validos: {len(a)}')
    print(f'Subportadoras selecionadas: {a.shape[1]}')
    print(f'Pares descartados por mudanca de configuracao: {(~mesma_config).sum()}')
    print(f'Baseline (mediana): {mediana:.4f}')
    print(f'Limiar experimental: {limiar:.4f}')
    for inicio, fim in [(0, 20), (20, 40), (40, 60)]:
        s = (x >= inicio) & (x < fim)
        if np.any(s):
            print(f'{inicio:02d}-{fim:02d}s: indice medio={np.mean(y[s]):.4f}; '
                  f'janelas acima do limiar={np.sum(alerta[s])}/{np.sum(s)}')

    fig, axs = plt.subplots(3, 1, figsize=(14, 10), sharex=True)
    fig.suptitle(f'SmartRoom - Detector experimental CSI\n{arquivo.name}')
    axs[0].plot(t, rssi, lw=.7)
    axs[0].set_ylabel('RSSI (dBm)')
    axs[0].set_title('Intensidade do sinal')
    axs[1].plot(x, y, marker='.', label='Variacao relativa (mediana por 1s)')
    axs[1].axhline(limiar, color='red', ls='--', label='Limiar experimental')
    axs[1].axvspan(0, SEGUNDOS_BASELINE, alpha=.12, color='green', label='Calibracao')
    axs[1].set_ylabel('Variacao relativa')
    axs[1].set_title('Variacao CSI com filtro de configuracao')
    axs[1].legend(loc='upper right')
    axs[2].step(x, alerta.astype(int), where='mid', label='Alerta experimental')
    axs[2].set_yticks([0, 1], ['Normal', 'Alerta'])
    axs[2].set_ylim(-.2, 1.2)
    axs[2].set_ylabel('Estado')
    axs[2].set_xlabel('Tempo (segundos)')
    axs[2].set_title('Janelas acima do limiar (nao equivale a presenca confirmada)')
    for ax in axs:
        ax.grid(alpha=.25)
        ax.axvline(20, color='gray', ls=':', alpha=.6)
        ax.axvline(40, color='gray', ls=':', alpha=.6)
    plt.tight_layout()
    saida = arquivo.with_name(arquivo.stem + '_movimento.png')
    fig.savefig(saida, dpi=150, bbox_inches='tight')
    print(f'Grafico salvo: {saida}')
    plt.show()


if __name__ == '__main__':
    main()

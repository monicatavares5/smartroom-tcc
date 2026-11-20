
import ast
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


# CONFIGURACOES
PASTA_DADOS = Path(__file__).resolve().parent / "dados_serial"
ARQUIVO_CSV = None  # None = usar o CSV mais recente
TAMANHO_CSI = 128


def encontrar_csv():
    if ARQUIVO_CSV:
        arquivo = Path(ARQUIVO_CSV)
        if not arquivo.exists():
            raise FileNotFoundError(f"Arquivo nao encontrado: {arquivo}")
        return arquivo

    arquivos = sorted(
        PASTA_DADOS.glob("csi_*.csv"),
        key=lambda p: p.stat().st_mtime
    )

    if not arquivos:
        raise FileNotFoundError("Nenhum CSV encontrado na pasta data.")

    return arquivos[-1]


def localizar_coluna(df, nomes):
    mapa = {str(c).strip().lower(): c for c in df.columns}
    for nome in nomes:
        if nome.lower() in mapa:
            return mapa[nome.lower()]
    return None


def converter_csi(valor):
    try:
        if isinstance(valor, str):
            valor = ast.literal_eval(valor.strip())

        vetor = np.asarray(valor, dtype=float)

        if vetor.ndim != 1 or len(vetor) != TAMANHO_CSI:
            return None

        if not np.all(np.isfinite(vetor)):
            return None

        if np.all(vetor == 0):
            return None

        return vetor

    except (ValueError, SyntaxError, TypeError):
        return None


def analisar():
    arquivo = encontrar_csv()
    print(f"\nAnalisando: {arquivo.name}")

    df = pd.read_csv(arquivo, low_memory=False)

    coluna_csi = localizar_coluna(
        df, ["data", "csi_data", "csi", "csi_raw", "csi_values"]
    )
    coluna_rssi = localizar_coluna(df, ["rssi"])
    coluna_tempo = localizar_coluna(
        df, ["timestamp", "timestamp_pc", "timestamp_host",
             "time", "tempo", "pc_timestamp", "datetime"]
    )
    coluna_local_ts = localizar_coluna(df, ["local_timestamp"])

    if coluna_csi is None:
        raise ValueError(
            "Nao encontrei a coluna CSI no CSV.\n"
            f"Colunas disponiveis: {list(df.columns)}"
        )

    vetores = []
    indices = []

    for indice, valor in enumerate(df[coluna_csi]):
        vetor = converter_csi(valor)
        if vetor is not None:
            vetores.append(vetor)
            indices.append(indice)

    if len(vetores) < 2:
        raise ValueError("Nao existem pacotes CSI validos suficientes.")

    matriz = np.stack(vetores)
    df_valido = df.iloc[indices].reset_index(drop=True)

    # Cada par de bytes representa I e Q.
    # Amplitude = sqrt(I^2 + Q^2).
    i = matriz[:, 0::2]
    q = matriz[:, 1::2]
    amplitudes = np.sqrt(i**2 + q**2)

    # Descarta subportadoras que sao zero em todos os pacotes.
    subportadoras_ativas = np.any(amplitudes > 0, axis=0)
    amplitudes = amplitudes[:, subportadoras_ativas]

    # Tempo: tenta timestamp do PC, depois timestamp do ESP32.
    # Se nenhum estiver disponivel, usa taxa nominal de 100 Hz.
    tempo = None

    if coluna_tempo is not None:
        serie = df_valido[coluna_tempo]

        numerico = pd.to_numeric(serie, errors="coerce")
        if numerico.notna().all():
            valores = numerico.to_numpy(dtype=float)
            escala = 1000.0 if np.median(np.diff(valores)) > 1 else 1.0
            candidato = (valores - valores[0]) / escala
            if np.all(np.diff(candidato) >= 0):
                tempo = candidato
        else:
            datas = pd.to_datetime(serie, errors="coerce")
            if datas.notna().all():
                tempo = (
                    (datas - datas.iloc[0]).dt.total_seconds().to_numpy()
                )

    if tempo is None and coluna_local_ts is not None:
        valores = pd.to_numeric(
            df_valido[coluna_local_ts], errors="coerce"
        ).to_numpy(dtype=float)

        if np.all(np.isfinite(valores)):
            candidato = (valores - valores[0]) / 1_000_000
            if np.all(np.diff(candidato) >= 0):
                tempo = candidato

    if tempo is None:
        tempo = np.arange(len(amplitudes)) / 100.0
        print("Aviso: tempo estimado usando 100 pacotes/s.")

    # Diferenca media absoluta entre amplitudes consecutivas.
    variacao = np.mean(
        np.abs(np.diff(amplitudes, axis=0)), axis=1
    )
    tempo_variacao = tempo[1:]

    # Suavizacao para facilitar a visualizacao.
    janela = 100
    variacao_suave = (
        pd.Series(variacao)
        .rolling(window=janela, min_periods=1, center=True)
        .mean()
        .to_numpy()
    )

    print(f"Pacotes validos: {len(matriz)}")
    print(f"Subportadoras ativas: {amplitudes.shape[1]}")
    print(f"Duracao aproximada: {tempo[-1]:.1f} s")
    print(f"Variacao media CSI: {np.mean(variacao):.3f}")

    fig, eixos = plt.subplots(
        3, 1, figsize=(14, 10), sharex=True,
        gridspec_kw={"height_ratios": [1, 1.4, 1.6]}
    )

    fig.suptitle(
        f"SmartRoom - Analise Wi-Fi CSI\n{arquivo.name}",
        fontsize=15
    )

    # Grafico 1: RSSI
    if coluna_rssi is not None:
        rssi = pd.to_numeric(
            df_valido[coluna_rssi], errors="coerce"
        )
        eixos[0].plot(tempo, rssi, linewidth=0.8)
        eixos[0].set_ylabel("RSSI (dBm)")
        eixos[0].set_title("Intensidade do sinal Wi-Fi")
    else:
        eixos[0].text(
            0.5, 0.5, "Coluna RSSI nao encontrada",
            ha="center", va="center",
            transform=eixos[0].transAxes
        )

    eixos[0].grid(alpha=0.3)

    # Grafico 2: variacao CSI
    eixos[1].plot(
        tempo_variacao, variacao,
        alpha=0.25, linewidth=0.6,
        label="Variacao por pacote"
    )
    eixos[1].plot(
        tempo_variacao, variacao_suave,
        linewidth=1.7, label="Media movel (100 pacotes)"
    )
    eixos[1].set_ylabel("Variacao media")
    eixos[1].set_title("Variacao das amplitudes CSI")
    eixos[1].legend()
    eixos[1].grid(alpha=0.3)

    # Grafico 3: mapa de calor
    eixos[2].imshow(
        amplitudes.T,
        aspect="auto",
        origin="lower",
        interpolation="nearest",
        extent=[
            tempo[0], tempo[-1],
            0, amplitudes.shape[1]
        ]
    )
    eixos[2].set_title("Mapa de calor das amplitudes CSI")
    eixos[2].set_ylabel("Subportadora ativa")
    eixos[2].set_xlabel("Tempo (segundos)")

    plt.tight_layout()

    saida = arquivo.with_name(
        arquivo.stem + "_analise.png"
    )
    fig.savefig(saida, dpi=150, bbox_inches="tight")

    print(f"Grafico salvo em: {saida}")
    plt.show()


if __name__ == "__main__":
    analisar()

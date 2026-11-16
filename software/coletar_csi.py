
import ast
import csv
import time
from datetime import datetime
from pathlib import Path

import serial

# SMARTROOM - COLETOR CSI
PORTA = "COM13"
BAUDRATE = 921600
DURACAO = 60
TAMANHO_ESPERADO = 128

PASTA = Path(r"C:\TCC\SmartRoom\data")
PASTA.mkdir(parents=True, exist_ok=True)

arquivo = PASTA / f"csi_{datetime.now():%Y%m%d_%H%M%S}.csv"

CAMPOS = [
    "seq", "mac", "rssi", "rate", "sig_mode", "mcs",
    "bandwidth", "smoothing", "not_sounding",
    "aggregation", "stbc", "fec_coding", "sgi",
    "noise_floor", "ampdu_cnt", "channel",
    "secondary_channel", "local_timestamp", "ant",
    "sig_len", "rx_format", "csi_length",
    "first_word_invalid"
]

COLUNAS = (
    ["timestamp_pc"]
    + CAMPOS
    + ["csi_data", "csi_valido", "motivo_invalido"]
)

total = 0
validos = 0
zerados = 0
tamanho_incorreto = 0
ignoradas = 0

print("\nSMARTROOM - COLETA CSI")
print(f"Porta: {PORTA}")
print(f"Arquivo: {arquivo}")
print(f"Duracao: {DURACAO} segundos\n")

try:
    with serial.Serial(PORTA, BAUDRATE, timeout=0.5) as esp:
        # Aguarda a estabilizacao da conexao serial
        time.sleep(2)
        esp.reset_input_buffer()

        with arquivo.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=COLUNAS)
            writer.writeheader()

            inicio = time.monotonic()

            while time.monotonic() - inicio < DURACAO:
                linha = esp.readline().decode(
                    "utf-8", errors="replace"
                ).strip()

                if not linha.startswith("CSI_DATA,"):
                    continue

                try:
                    partes = next(csv.reader([linha]))

                    # 1 campo de tipo + 23 metadados + vetor CSI
                    if len(partes) != 25:
                        ignoradas += 1
                        continue

                    dados = {}

                    for indice, nome in enumerate(CAMPOS, start=1):
                        if nome == "mac":
                            dados[nome] = partes[indice]
                        else:
                            dados[nome] = int(partes[indice])

                    csi = ast.literal_eval(partes[24])

                    if not isinstance(csi, list) or not all(
                        type(valor) is int and -32768 <= valor <= 32767
                        for valor in csi
                    ):
                        ignoradas += 1
                        continue

                    motivo = ""

                    if len(csi) != TAMANHO_ESPERADO:
                        motivo = "tamanho_incorreto"
                        tamanho_incorreto += 1
                    elif all(valor == 0 for valor in csi):
                        motivo = "vetor_zerado"
                        zerados += 1
                    else:
                        validos += 1

                    dados["timestamp_pc"] = datetime.now().isoformat()
                    dados["csi_data"] = str(csi)
                    dados["csi_valido"] = int(not motivo)
                    dados["motivo_invalido"] = motivo

                    writer.writerow(dados)
                    total += 1

                    if total % 500 == 0:
                        print(
                            f"{total} pacotes | "
                            f"validos: {validos} | "
                            f"zerados: {zerados}"
                        )

                except (
                    ValueError, SyntaxError, IndexError,
                    TypeError, csv.Error
                ):
                    ignoradas += 1

except (serial.SerialException, OSError) as erro:
    print(f"ERRO: {erro}")

print("\n--- RESUMO ---")
print(f"Pacotes registrados: {total}")
print(f"CSI validos: {validos}")
print(f"Vetores zerados: {zerados}")
print(f"Tamanho incorreto: {tamanho_incorreto}")
print(f"Linhas ignoradas: {ignoradas}")
print(f"Arquivo: {arquivo}")

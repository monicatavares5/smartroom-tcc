
import socket
import json
from datetime import datetime

HOST = "0.0.0.0"
PORT = 5005

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((HOST, PORT))

print("=" * 45)
print("SMARTROOM - SERVIDOR DE RECEPCAO")
print(f"Porta UDP: {PORT}")
print("Aguardando dados das ESP32...")
print("=" * 45)

while True:
    data, address = sock.recvfrom(8192)

    try:
        mensagem = json.loads(data.decode("utf-8"))

        print(
            f"[{datetime.now():%H:%M:%S}] "
            f"ESP: {mensagem.get('esp_id', '?')} | "
            f"IP: {address[0]} | "
            f"RSSI: {mensagem.get('rssi', '?')} | "
            f"Pacote: {mensagem.get('seq', '?')}"
        )

    except (ValueError, UnicodeDecodeError):
        print(f"Pacote recebido de {address}: {data[:100]!r}")

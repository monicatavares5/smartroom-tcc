"""Configurações do SmartRoom (portas, caminhos e placas)."""
from pathlib import Path

PASTA_SOFTWARE = Path(__file__).resolve().parent.parent
PASTA_PROJETO = PASTA_SOFTWARE.parent
PASTA_DADOS = PASTA_SOFTWARE / 'dados'
PASTA_WEB = Path(__file__).resolve().parent / 'web'
ARQUIVO_PLACAS = PASTA_SOFTWARE / 'placas.json'
FIRMWARE_BIN = PASTA_PROJETO / 'firmware' / 'smartroom_node' / 'build' / 'smartroom_node.bin'

PORTA_DADOS = 5005        # UDP: CSI e status das placas
PORTA_DESCOBERTA = 5006   # UDP: anúncio/procura e comando de OTA
PORTA_HTTP = 8080         # painel (só localhost)
PORTA_OTA = 8081          # HTTP temporário para as placas baixarem o firmware

PLACAS = ('ESP_A', 'ESP_B', 'ESP_C', 'ESP_D')

# Geometria da sala em metros (origem no canto da ESP_A, x para a ESP_B, y para a ESP_C).
# Ajuste se mudar a montagem: o mapa do painel e a localização usam estes valores.
SALA = {
    'largura': 2.5,            # A → B
    'profundidade': 3.5,       # A → C
    'placas': {'ESP_A': (0.0, 0.0), 'ESP_B': (2.5, 0.0), 'ESP_C': (0.0, 3.5), 'ESP_D': (2.5, 3.5)},
    'roteador': (1.25, 1.75),  # iPhone no centro
    'raio_centro_m': 0.5,      # a menos disto do iPhone a região é "centro"; senão, a placa mais próxima
}
OFFLINE_APOS_S = 5.0      # placa sem pacotes por este tempo é considerada offline

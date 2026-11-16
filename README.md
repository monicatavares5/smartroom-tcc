# SmartRoom — detecção de presença com CSI Wi-Fi (TCC)

Sistema experimental que usa placas ESP32-S3 para coletar CSI (Channel State Information) e RSSI do Wi-Fi e, a partir dessas medições, detectar presença e estimar a localização de uma pessoa em um ambiente de cerca de 4 × 3 metros. Quatro nós (ESP_A, ESP_B, ESP_C e ESP_D) enviam os dados por UDP para um servidor em Python, que exibe um painel no navegador.

## Estrutura

```
firmware/smartroom_node/   Firmware dos nós ESP32 (ESP-IDF), baseado no exemplo csi_recv_router do esp-csi
software/                  Servidor, painel web, scripts de coleta e análise (Python)
software/dados_csi/        Coletas CSI rotuladas e análises
software/experimento_03/   Coletas por posição (centro, perto de cada ESP, quarto vazio)
data/                      Coletas e gráficos dos primeiros testes
```

## Firmware

Requer o [ESP-IDF](https://docs.espressif.com/projects/esp-idf/) instalado (variável `IDF_PATH` configurada).

```bash
cd firmware/smartroom_node
idf.py set-target esp32s3
idf.py menuconfig      # Example Connection Configuration → SSID e senha do Wi-Fi
idf.py build flash monitor
```

O arquivo `sdkconfig` não é versionado porque guarda o nome e a senha da rede Wi-Fi; ele é gerado pelo `menuconfig` a partir de `sdkconfig.defaults`. A pasta `historico/` guarda versões anteriores do `app_main.c`.

## Servidor e painel

```bash
cd software
python -m venv venv
venv\Scripts\activate            # Windows  (Linux/macOS: source venv/bin/activate)
pip install -r requirements.txt
python smartroom_servidor_entrega_final_localizacao_ajustada.py
```

O servidor escuta os nós na porta UDP 5005 e o painel fica em http://localhost:8080.

Os arquivos `smartroom_servidor_v2.py` a `v7_7` e `presenca_tempo_real*.py` são as versões intermediárias desenvolvidas ao longo do trabalho.

## Base

O firmware parte do projeto [espressif/esp-csi](https://github.com/espressif/esp-csi) (licença Apache-2.0).

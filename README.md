# SmartRoom — detecção de presença com CSI Wi-Fi (TCC)

Sistema experimental que usa placas ESP32-S3 para coletar CSI (Channel State Information) e RSSI do Wi-Fi e, a partir dessas medições, detectar presença e estimar a localização de uma pessoa em um ambiente de cerca de 4 × 3 metros. Quatro nós (ESP_A, ESP_B, ESP_C e ESP_D) enviam os dados por UDP para um servidor em Python, que exibe um painel no navegador.

## Estrutura

```
firmware/smartroom_node/   Firmware dos nós ESP32 (ESP-IDF), baseado no exemplo csi_recv_router do esp-csi
software/smartroom_servidor.py   Servidor UDP + painel web (Python)
software/calibracao/       Coletas por posição (quarto vazio, perto de cada ESP, centro)
software/dados/            CSVs gerados pelo servidor a cada execução
software/analise/          Scripts de análise offline, coletas e gráficos dos primeiros testes
```

## Firmware

Requer o [ESP-IDF](https://docs.espressif.com/projects/esp-idf/) instalado (variável `IDF_PATH` configurada).

```bash
cd firmware/smartroom_node
idf.py set-target esp32s3
idf.py menuconfig      # Example Connection Configuration → SSID e senha do Wi-Fi
idf.py build flash monitor
```

O arquivo `sdkconfig` não é versionado porque guarda o nome e a senha da rede Wi-Fi; ele é gerado pelo `menuconfig` a partir de `sdkconfig.defaults`.

## Servidor e painel

```bash
cd software
python -m venv venv
venv\Scripts\activate            # Windows  (Linux/macOS: source venv/bin/activate)
pip install -r requirements.txt
python smartroom_servidor.py
```

O servidor escuta os nós na porta UDP 5005 e o painel fica em http://localhost:8080.

As versões intermediárias desenvolvidas ao longo do trabalho foram removidas do diretório e continuam disponíveis no histórico do git (commit `3213293`).

## Base

O firmware parte do projeto [espressif/esp-csi](https://github.com/espressif/esp-csi) (licença Apache-2.0).

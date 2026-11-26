# SmartRoom — detecção de presença com CSI Wi-Fi (TCC)

Sistema experimental que usa placas ESP32-S3 para coletar CSI (Channel State Information) e RSSI do Wi-Fi e, a partir dessas medições, detectar presença e estimar a região de uma pessoa em um ambiente de cerca de 4 × 3 metros. Quatro placas (ESP_A, ESP_B, ESP_C e ESP_D) enviam os dados por UDP para um servidor em Python, que exibe um painel no navegador.

## Início rápido (Windows)

```powershell
.\requisitos.ps1                 # instala/configura o que falta (Python, venv, pacotes, firewall); -Firmware inclui o ESP-IDF
.\smartroom.ps1                  # confere os requisitos, inicia o servidor e abre o painel
.\smartroom.ps1 -Verificar       # diagnóstico: mostra quais placas estão enviando
.\smartroom.ps1 -Firmware        # compila uma vez e grava as 4 placas por USB (primeira vez)
.\smartroom.ps1 -Firmware -Ota   # atualizações seguintes, pelo Wi-Fi
.\smartroom.ps1 -Replay software\dados\smartroom_csi_20261102_174709.csv   # painel com uma gravação, sem placas
```

Também dá para dar duplo clique em `smartroom.cmd`.

- **Sem configuração de rede**: as placas encontram o servidor sozinhas (descoberta por broadcast UDP).
- **Um firmware para as 4 placas**: cada uma é identificada pelo MAC (`software/placas.json`).
- **Monitoramento em tempo real**: o painel mostra conexão, sinal, pacotes/s, perda, quedas, reinícios e versão do firmware de cada placa, com avisos e histórico de eventos. Placas e servidor se recuperam sozinhos de falhas.
- **Atualização pelo Wi-Fi (OTA)** com volta automática à versão anterior se o firmware novo não funcionar.
- **Replay e testes**: qualquer gravação pode ser reproduzida no painel; `software/tests` tem testes automatizados (`python -m pytest`).

## Estrutura

```
docs/protocolo.md          Protocolo placa ↔ servidor (v2, binário)
firmware/smartroom_node/   Firmware ESP-IDF v6.0.3, baseado no exemplo csi_recv_router do esp-csi
software/smartroom/        Servidor, painel web, detector de presença, monitor das placas
software/tests/            Testes automatizados
software/dados/            CSVs gravados a cada execução
software/calibracao/       Coletas por posição (análise offline)
software/analise/          Scripts de análise offline e dados dos primeiros testes
```

Detalhes técnicos em [CLAUDE.md](CLAUDE.md).

## Base

O firmware parte do projeto [espressif/esp-csi](https://github.com/espressif/esp-csi) (licença Apache-2.0).

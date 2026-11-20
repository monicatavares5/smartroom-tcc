# CLAUDE.md — SmartRoom (TCC)

Detecção de presença e estimativa de região de uma pessoa num quarto de ~4 × 3 m usando CSI + RSSI de Wi-Fi coletados por 4 placas **ESP32-S3** (ESP_A, ESP_B, ESP_C, ESP_D). As placas mandam pacotes JSON por **UDP** para um servidor Python que serve um **painel web** (HTML/JS embutido no próprio `.py`). Projeto de TCC: código experimental, em português, sem testes automatizados.

## Estrutura

```
firmware/smartroom_node/          Firmware dos 4 nós (projeto ESP-IDF)
  main/app_main.c                   código do nó (CSI → JSON → UDP)
  sdkconfig.defaults, CMakeLists.txt, main/idf_component.yml, dependencies.lock
software/
  smartroom_servidor.py           ★ Servidor UDP + HTTP + painel web (HTML/JS inline na string COLLECT_HTML)
  requirements.txt
  modelo_localizacao.joblib       modelo ML experimental (só /api/location_ml; painel não usa)
  coletar_localizacao.py          cliente que rotula coletas via POST /api/label no servidor rodando
  calibracao/                     CSVs por posição (vazio_inicio, perto_esp_a..d, centro, vazio_fim) lidos pelo servidor
  dados/                          CSVs gerados pelo servidor a cada execução (não é código)
  analise/                        scripts offline (não fazem parte do sistema em tempo real)
    analisar_calibracao.py          compara os CSVs de calibracao/ → relatorio_calibracao.csv
    analisar_espectro.py            análise espectral de um smartroom_v53_rotulado_*.csv (argumento)
    comparar_vazio_centro.py, comparar_5_testes.py   usam testes_presenca/
    analisar_csi_serial.py, analisar_movimento.py, detectar_presenca.py   usam dados_serial/ (formato serial antigo, coluna csi_data)
    dados_serial/                   coletas e gráficos dos primeiros testes via USB (30/10)
    testes_presenca/                5 coletas vazio/centro
```

Não há frontend separado (sem Node/npm): o HTML/CSS/JS está inline no servidor. Nenhum script importa outro; todos usam caminhos relativos ao próprio arquivo.

Versões intermediárias do servidor, scripts serial antigos e `firmware/smartroom_node/historico/` foram removidos; estão no commit `3213293` (`git show 3213293:<caminho>`). Lá os nomes antigos eram: `smartroom_servidor_entrega_final_localizacao_ajustada.py`, `experimento_03/`, `dados_csi/`, `data/`.

## Arquitetura

```
ESP32-S3 (x4)                         PC (Windows)
 conecta no hotspot Wi-Fi  ──UDP:5005──>  servidor Python
 faz ping 100 Hz no gateway               ├─ thread UDP: parse JSON, grava CSV, atualiza estado
 captura CSI das respostas                └─ HTTP 127.0.0.1:8080: painel + API JSON
 envia JSON {esp_id, seq, rssi, mcs, channel, csi(hex), ...}
```

- **Firmware**: baseado no exemplo `csi_recv_router` do [esp-csi](https://github.com/espressif/esp-csi). Conecta via `example_connect()` (SSID/senha do `menuconfig`), pinga o gateway a 100 Hz (`CONFIG_SEND_FREQUENCY`) para gerar tráfego, e a callback de CSI enfileira amostras que a task `smartroom_udp_task` envia como JSON. Constantes em `app_main.c`:
  - `SMARTROOM_NODE_ID` — `"ESP_A"`…`"ESP_D"` (**muda por placa**; hoje está `"ESP_D"`)
  - `SMARTROOM_SERVER_IP` — `"172.20.10.10"` (IP do PC na rede do hotspot do iPhone)
  - `SMARTROOM_SERVER_PORT` — `5005`
- **Servidor** (`software/smartroom_servidor.py`):
  - `udp_listener()` — recebe pacotes; ignora `esp_id` fora de `NODES`; grava `dados/smartroom_csi_<timestamp>.csv` (bruto) e `dados/smartroom_v53_rotulado_<timestamp>.csv` (métricas espectrais).
  - `presence_v7()` — **detector de presença usado no painel**. Exige calibração manual de 60 s com o quarto vazio (botão no painel → `POST /api/presence_v7/calibrate`). Compara RSSI/amplitude CSI de janelas de 8 s com a linha de base; presença se índice ≥ 1.0 por 5 s, vazio se < 0.6 por 8 s (histerese).
  - `location_proximity()` — **localização usada no painel**: heurística "placa com índice dominante" → `perto_a/b/c/d`, senão `centro`. Não é triangulação.
  - Outros detectores antigos continuam no arquivo mas o painel não usa: `presence_status` (`/api/presence`), `v6_estimate` (`/api/location`), `location_ml` (`/api/location_ml`, modelo `.joblib`).
- **Endpoints usados pelo painel**: `GET /`, `GET /api/status`, `GET /api/presence_v7`, `GET /api/localizacao`, `POST /api/presence_v7/calibrate`.

## Como rodar o backend + frontend

Requer Python 3.10+.

```powershell
cd software
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
pip install scikit-learn   # opcional: só para o modelo .joblib (location_ml), não usado pelo painel final
python smartroom_servidor.py
```

- Painel: http://localhost:8080 (só no próprio PC — o HTTP faz bind em `127.0.0.1`; para abrir de outro aparelho, trocar para `'0.0.0.0'` no `ThreadingHTTPServer(...)` no fim do arquivo).
- O terminal imprime uma linha por pacote recebido (`ESP_A | IP | RSSI | seq | CSI 128 bytes | valido=True`). Se nada aparece, o problema é rede/firewall (ver abaixo).
- Uso: esperar as 4 placas ficarem **Online** → sair do quarto → **Calibrar ambiente vazio · 60 s** → entrar no quarto.
- Ctrl+C encerra.

## Como conectar as placas (Wi-Fi) ao computador

A rede usada no TCC é o **hotspot de um iPhone** (sub-rede `172.20.10.0/28`, gateway `172.20.10.1`). PC e as 4 ESP32 precisam estar na **mesma rede**.

1. **Ligar o hotspot** no iPhone (ativar "Maximizar compatibilidade" para usar 2,4 GHz — o ESP32-S3 não conecta em 5 GHz).
2. **Conectar o PC** ao hotspot e ver o IP:
   ```powershell
   ipconfig
   ```
   O firmware envia para `172.20.10.10`. Duas opções:
   - Fixar o IP do PC: Configurações → Rede → Wi-Fi → (rede do hotspot) → Atribuição de IP → Manual: IP `172.20.10.10`, máscara `255.255.255.240`, gateway/DNS `172.20.10.1`; **ou**
   - Alterar `SMARTROOM_SERVER_IP` em `app_main.c` para o IP que o PC recebeu e regravar as 4 placas.
3. **Liberar o firewall do Windows** para UDP 5005 (PowerShell como administrador; o hotspot costuma ser classificado como rede "Pública"):
   ```powershell
   New-NetFirewallRule -DisplayName "SmartRoom UDP 5005" -Direction Inbound -Protocol UDP -LocalPort 5005 -Action Allow -Profile Any
   ```
4. **Gravar o firmware** em cada placa (ESP-IDF instalado, `IDF_PATH` configurado; rodar no "ESP-IDF PowerShell/CMD"):
   ```powershell
   cd firmware\smartroom_node
   idf.py set-target esp32s3
   idf.py menuconfig          # Example Connection Configuration → WiFi SSID / WiFi Password do hotspot
   ```
   Para **cada** placa: editar `SMARTROOM_NODE_ID` em `main/app_main.c` (`ESP_A`, `ESP_B`, `ESP_C`, `ESP_D`), plugar no USB e:
   ```powershell
   idf.py -p COMx build flash monitor
   ```
   No monitor deve aparecer `got ip:172.20.10.x, gw: 172.20.10.1`. Saia do monitor com `Ctrl+]`.
5. **Posicionar** as placas nos cantos do quarto: A = superior esquerdo, B = superior direito, C = inferior esquerdo, D = inferior direito (é assim que o mapa do painel desenha). Alimentar por USB/fonte.
6. Rodar o servidor e conferir as 4 placas Online no painel.

`sdkconfig` não é versionado (contém SSID/senha); é gerado a partir de `sdkconfig.defaults` pelo `menuconfig`. Nunca commitar.

## Pegadinhas conhecidas

- `--treino` aponta por padrão para `dados/smartroom_csi_20261101_191222.csv`, que não existe mais (a pasta onde estava foi removida; está no commit `3213293`). O servidor só avisa `Arquivo nao encontrado`. Só afeta `/api/location` (V6), não usado pelo painel.
- `requirements.txt` não tem `scikit-learn`; sem ele o `.joblib` não carrega (só afeta `/api/location_ml`).
- A rota `/coleta` serve o mesmo painel principal (a coleta guiada da V7.7 foi removida nesta versão).
- Calibração e coleta exigem as **4** placas online (pacote nos últimos 5 s).
- Mudou de rede (outro roteador)? Ajuste `SMARTROOM_SERVER_IP` e regrave; o CSI vem das respostas de ping do gateway, então o hotspot/roteador também deve ficar fixo durante calibração e uso.

## Convenções

- Código, comentários, UI e commits em português.
- Nova versão do servidor: prefira editar o arquivo final em vez de criar mais um `smartroom_servidor_vX.py`.
- Não commitar `venv/`, `build/`, `managed_components/`, `sdkconfig`.

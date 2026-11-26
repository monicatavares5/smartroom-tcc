# CLAUDE.md — SmartRoom (TCC)

Detecção de presença e estimativa de região de uma pessoa num quarto de ~4 × 3 m usando CSI + RSSI de Wi-Fi coletados por 4 placas **ESP32-S3**. As placas mandam pacotes binários por **UDP** para um servidor Python (biblioteca padrão + numpy) que serve um **painel web** atualizado 10x por segundo. Projeto de TCC: código, comentários, UI e commits em português.

## Estrutura

```
requisitos.ps1                    instala/configura o ambiente (o "requirements" do projeto)
smartroom.ps1 / smartroom.cmd     roda o servidor, diagnostica, grava/atualiza o firmware, replay
docs/protocolo.md                 ★ contrato do protocolo v2 placa ↔ servidor (firmware e servidor seguem este arquivo)
docs/resultados_teste_guiado_20261120.md   métricas do teste guiado (base dos parâmetros atuais; usar no TCC)
firmware/smartroom_node/          projeto ESP-IDF v6.0.3 (um único firmware para as 4 placas)
  main/app_main.c                   inicialização e ligação dos módulos
  main/sr_protocolo.h               structs dos pacotes v2 (espelho de docs/protocolo.md)
  main/sr_csi.c/.h                  CSI, callback, fila, ping ao gateway
  main/sr_rede.c/.h                 descoberta do servidor e envio dos pacotes
  main/sr_saude.c/.h                eventos de Wi-Fi, watchdog/reinícios, pacote STATUS
  main/sr_ota.c/.h                  atualização pelo Wi-Fi
  main/Kconfig.projbuild            opções do menu "SmartRoom" (menuconfig)
  sdkconfig.defaults                flash 4 MB, tabela de partição com 2 OTA, rollback, HTTP OTA, CSI
software/
  smartroom_servidor.py             entrada fina (python smartroom_servidor.py --help)
  placas.json                       MAC → ESP_A…ESP_D (gerado pela gravação; editável)
  requirements.txt                  numpy (servidor) + pacotes da análise offline e dos testes
  smartroom/                        pacote do servidor
    __main__.py                       CLI e montagem (threads supervisionadas)
    config.py                         portas, caminhos, IDs das placas
    protocolo.py                      leitura/montagem de pacotes v2 (binário) e v1 (JSON legado)
    placas.py                         RegistroPlacas (MAC → ID, atribui ID livre a MAC novo e salva)
    nucleo.py                         Sistema: estado, processamento das amostras, CSV, detectores (10x/s)
    tempo_real.py                     ★ DetectorTempoReal (CSI multivariado + HMM) e LocalizadorRTI (grade bayesiana + Kalman)
    csi.py                            amplitude/espectro do CSI
    teste_guiado.py                   TesteGuiado: sequência cronometrada (~6,5 min) que fala as instruções no painel, calibra e marca os rótulos
    presenca.py                       DetectorPresenca (V7.4) e LocalizadorProximidade — "clássico", mantido para comparação
    monitor.py                        MonitorPlacas: saúde, avisos com histerese, eventos
    rede.py                           receptor UDP, descoberta, supervisão de threads
    ota.py                            GerenciadorOTA (HTTP temporário 8081 + comando às placas)
    replay.py                         reproduz CSV gravado como se fossem as placas
    gravacao.py                       CsvSeguro (escrita em lote, tolera arquivo bloqueado)
    web.py + web/                     HTTP do painel (index.html, app.js, style.css) e API
  tests/                            pytest (protocolo, presença, monitor, sistema com rede/HTTP/replay)
  calibracao/                       CSVs por posição (usados só pela análise offline)
  dados/                            CSVs gravados a cada execução (não é código)
  analise/                          scripts offline (avaliar_tempo_real.py = métricas de um teste rotulado), dados antigos
```

Versões antigas (servidores v2…v7_7, monolito `smartroom_servidor.py` de ~1200 linhas, firmware com IP fixo) estão no histórico do git.

## Arquitetura

```
ESP32-S3 (x4, mesmo firmware)                 PC (Windows)
 conecta no hotspot                            servidor (threads supervisionadas: reiniciam se falharem)
 procura ──broadcast UDP 5006──>               ├─ descoberta: anuncia {"smartroom":"servidor","porta":5005} 1x/s em cada interface
        <──anúncio / resposta──                │   e responde às procuras
 ping 20 Hz no gateway → CSI                   ├─ receptor UDP 5005 → Sistema.receber() → CSV + detector + monitor
 envia 1 de cada 2 CSI (binário) ──UDP 5005──> ├─ laço 10 Hz: detectores e localização (monitor e CSV 1 Hz)
 STATUS a cada 5 s ──UDP 5005──>               ├─ OTA: HTTP 0.0.0.0:8081 temporário + comando UDP às placas
 OTA: baixa de http://PC:8081 <──comando──     └─ HTTP 127.0.0.1:8080: painel + /api/stream (SSE 10x/s)
```

- **Protocolo**: ver `docs/protocolo.md`. Cabeçalho de 12 bytes com o **MAC** (identidade da placa); CSI tipo 1; STATUS tipo 2 (uptime, quedas de Wi-Fi, erros de envio, heap, RSSI do AP, motivo do reinício, estado do OTA, versão do firmware). O servidor também aceita o JSON v1 das placas antigas. Ao mudar o protocolo, mude `docs/protocolo.md`, `main/sr_protocolo.h` e `smartroom/protocolo.py` juntos (há testes de tamanho das structs).
- **Identidade**: `software/placas.json` (MAC → ID). A gravação por USB registra o MAC lido pelo esptool. MAC desconhecido recebe o primeiro ID livre (e um aviso no painel para conferir o canto).
- **Firmware**: descoberta sem IP fixo; reconexão de Wi-Fi a cada 10 s e reinício após 120 s sem Wi-Fi; reinício se ficar 30 s sem CSI; recria o socket após erros seguidos; rollback de OTA (o firmware novo só se marca válido depois de achar o servidor). Opções no `menuconfig` → "SmartRoom".
- **Servidor**: `Sistema` (nucleo.py) não depende de rede/HTTP — rede.py e replay.py alimentam; web.py lê `Sistema.estado()` (cache recalculado 10x/s em `Sistema.atualizar()`; o monitor e o descarregar do CSV rodam 1x/s). Relógio injetável (`Relogio`) para replay acelerado e testes. O CSV bruto tem a coluna `rotulo` (marcada no painel em "Teste rotulado").
- **Detector em tempo real** (tempo_real.py, o que o painel mostra; detalhes na docstring do módulo): por placa, forma do espectro `x = ln(|H|+0,5) − média` nas 51 subportadoras úteis do LLTF (remove o ganho automático); resíduo contra a média do quarto vazio por `sig_mode`; numa janela de 1 s, três escores z padronizados pela calibração — movimento (ln da variância temporal), forma (ln da distância de Mahalanobis com covariância Ledoit–Wolf) e RSSI. Escore global S = máximo. Decisão por HMM de 2 estados em log-odds (LLR assimétrico, limiar τ = quantil 95% de S no vazio + 1, limitado a [2,5; 6]; média do vazio = mediana por subportadora; descarta os 10 s iniciais da calibração; `sensibilidade` ∈ [−1, 1] desloca τ em até 1,5); presença se P ≥ 0,85, vazio se P ≤ 0,15. Linha de base se adapta devagar só após 30 s de vazio confiante. **Ambiente alterado**: forma > τ em ≥ 3 placas com movimento médio < 1 por 20 s → aviso "recalibre" (não muda a decisão). KAPPA_SOBE/DESCE = 1,5/1,0.
- **Localização contínua** (LocalizadorRTI): modelo de tomografia de rádio — cada enlace iPhone→placa é sensível perto do seu segmento (gaussiana de σ = 0,70 m); posterior (κ = 12) numa grade de 5 cm comparando o vetor de evidências normalizado; média/covariância vão para um Kalman 2-D de velocidade constante. Geometria da sala em `config.SALA` (2,5 × 3,5 m, iPhone no centro). Região: `centro` se a < 0,5 m do iPhone, senão `perto_` da placa mais próxima.
- **Clássico** (presenca.py, V7.4): RSSI/amplitude média em janelas de 8 s, presença ≥ 1,0 por 5 s, vazio < 0,6 por 8 s; roda em paralelo só para comparação (aparece numa linha do painel e no avaliador).
- **Monitor** (monitor.py): status por placa (`ok`/`atencao`/`critico`/`offline`/`aguardando`), sinal pela mediana do RSSI (Ótimo ≥ −60, Bom ≥ −70, Fraco ≥ −78), pacotes/s, perda pela `seq`, CSI válido, quedas, reinícios, versão do firmware e OTA. Avisos só depois de 5 s (histerese). Limiares no topo do arquivo.
- **API**: `GET /api/estado`, `GET /api/stream` (SSE 10x/s; `monitor` vem só 1 a cada 10 mensagens), `GET /api/monitor`, `GET /api/placas`, `POST /api/calibrar` (calibra os dois detectores), `POST /api/rotulo` (alias `/api/label`), `POST /api/sensibilidade`, `POST /api/teste/iniciar`, `POST /api/teste/parar`, `POST /api/ota`.

## Comandos

```powershell
.\requisitos.ps1                 # instala o que falta: Python (winget), venv, pacotes, firewall (UDP 5005/5006, TCP 8081)
.\requisitos.ps1 -Firmware       # + ESP-IDF v6.0.3 (winget Espressif.EIM-CLI + eim install), ~8 GB
.\requisitos.ps1 -Verificar      # só relata (saída 1 se falta algo)
.\smartroom.ps1                  # confere requisitos (chama requisitos.ps1 se faltar), inicia servidor e abre o painel
.\smartroom.ps1 -Verificar       # diagnóstico: requisitos, rede e 8 s escutando as placas (--diagnostico)
.\smartroom.ps1 -Firmware        # compila 1x e grava as 4 placas por USB, registrando os MACs
.\smartroom.ps1 -Firmware -Ota   # compila e atualiza pelo Wi-Fi as placas online (servidor aberto)
.\smartroom.ps1 -Replay software\dados\smartroom_csi_20261120_213225.csv -Velocidade 3   # painel sem placas

# Avaliação de um teste rotulado (dentro de software/, com o venv):
python analise\avaliar_tempo_real.py dados\smartroom_csi_<data>.csv [--buscar]   # acerto, falsos alarmes, atraso, erro de posição; --buscar acha os melhores parâmetros
```

Servidor direto (dentro de `software/`, com o venv): `python smartroom_servidor.py [--verbose] [--resumo S] [--diagnostico S] [--replay CSV --velocidade N --inicio S --repetir] [--sem-csv] [--abrir]`.

Testes: `cd software; venv\Scripts\python -m pytest -q` (34 testes, ~6 s; usam portas livres e não tocam em `dados/` nem em `placas.json`; `test_tempo_real.py` gera CSI sintético com quarto vazio, pessoa parada e andando).

ESP-IDF: ativar com o script do EIM (`C:\Espressif\tools\Microsoft.v6.0.3.PowerShell_profile.ps1`, caminho em `C:\Espressif\tools\eim_idf.json` → `activationScript`); o `export.ps1` do esp-idf **não** ativa o `idf.py` nessa instalação. `requisitos.ps1 -CaminhoEspIdf` devolve o script certo.

## Pegadinhas

- `sdkconfig` (local, contém SSID/senha, fora do git) tem prioridade sobre `sdkconfig.defaults`. O `smartroom.ps1 -Firmware` recria o `sdkconfig` quando o defaults é mais novo, preservando o Wi-Fi. Ao testar builds, compile numa cópia fora do repositório.
- Placas com firmware antigo (JSON, IP fixo `172.20.10.10`) não participam da descoberta nem do OTA: grave por USB uma vez.
- A primeira gravação v2 muda a tabela de partições (2 OTA, flash 4 MB): precisa ser por USB.
- A descoberta depende de broadcast no hotspot. Se o anúncio não chegar, a placa acha o servidor pela procura dela (precisa da regra UDP 5006).
- "Presença" logo depois da calibração com o quarto supostamente vazio quase sempre é a própria pessoa voltando ao notebook (21/11: mudança real nos enlaces A/B/C 5 s após o fim da calibração, mesma MCS, canal e RSSI estáveis). Notebook fora do quarto ou use o teste guiado. Perfil do vazio por MCS (`POR_MCS`) foi testado e não ajudou — desligado.
- Calibração exige as 4 placas online. O hotspot deve ficar fixo durante calibração e uso (o CSI vem das respostas de ping do gateway).
- Os `.ps1` são UTF-8 **com BOM** (acentos no Windows PowerShell 5.1) — manter ao editar.

## Convenções

- Português em código, UI e commits. Trabalhar direto na `main`; commit só quando a usuária pedir.
- Nada de criar `smartroom_servidor_vX.py`: edite o pacote `smartroom/` e rode os testes.
- Resultados atuais (teste guiado de 20/11, trecho válido até 623 s): acurácia balanceada 88,8% (clássico 76,2%), pessoa detectada 94,4%, vazio 83,1%, região 81,5%, erro de posição 0,50 m; B é a placa fraca. Ao mudar parâmetros, rode `python analise\avaliar_tempo_real.py dados\smartroom_csi_20261120_232804.csv --ate 623` e compare.
- Avaliação confiável exige rótulos confiáveis: use o **teste guiado** (painel → "Teste rotulado"); o avaliador calibra no trecho `vazio_calibracao`. No teste manual de 20/11 (`dados/smartroom_csi_20261120_222103.csv`, calibração ~3410 s) o trecho "vazio" parece ter tido gente no quarto (a pessoa clicava no notebook), então não serve como verdade.
- Ao mexer no detector, valide também com dados reais: replay (`--inicio` pula a montagem) e `analise/avaliar_tempo_real.py` num teste rotulado. A gravação `dados/smartroom_csi_20261120_213225.csv` não tem rótulos e tem trechos de montagem (mesas sendo mexidas antes de ~1015 s e depois de ~1540 s).
- Não commitar `venv/`, `build/`, `managed_components/`, `sdkconfig`, `eim_config.toml`.

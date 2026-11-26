# Protocolo SmartRoom v2 (placas ↔ servidor)

Contrato entre o firmware (`firmware/smartroom_node`) e o servidor (`software/smartroom`).
Os dois lados devem seguir este arquivo. O servidor também aceita o protocolo v1 (JSON) das placas antigas.

## Portas

| Porta | Protocolo | Sentido | Uso |
|---|---|---|---|
| 5005 | UDP | placa → PC | dados (CSI) e status, binário v2 |
| 5006 | UDP | ambos | descoberta (JSON) e comando de OTA |
| 8081 | TCP/HTTP | placa → PC | download do firmware na atualização OTA (só aberto durante a atualização) |

Todos os inteiros são **little-endian**. Structs empacotadas (sem padding).

## Identidade da placa

O firmware é **o mesmo para as 4 placas**. A placa se identifica pelo **MAC da interface Wi-Fi STA** (`esp_read_mac(..., ESP_MAC_WIFI_STA)`).
O servidor traduz MAC → `ESP_A`…`ESP_D` usando `software/placas.json` (gravado pelo script de gravação; MAC desconhecido recebe o primeiro ID livre e é salvo).

## Cabeçalho (12 bytes, todo pacote na porta 5005)

| Offset | Tipo | Campo | Valor |
|---|---|---|---|
| 0 | char[2] | magic | `"SR"` |
| 2 | u8 | versao | `2` |
| 3 | u8 | tipo | `1` = CSI, `2` = STATUS |
| 4 | u8[6] | mac | MAC STA |
| 10 | u16 | tamanho | bytes do payload que vem depois do cabeçalho |

## Tipo 1 — CSI (payload 16 + n bytes)

| Offset* | Tipo | Campo |
|---|---|---|
| 0 | u32 | seq — contador de callbacks de CSI desde o boot (inclui os não enviados) |
| 4 | u32 | t_ms — ms desde o boot (`esp_timer_get_time()/1000`) |
| 8 | i8 | rssi |
| 9 | u8 | sig_mode |
| 10 | u8 | mcs |
| 11 | u8 | canal |
| 12 | u8 | first_word_invalid (0/1) |
| 13 | u8 | reservado (0) |
| 14 | u16 | csi_len — bytes de CSI que seguem (≤ 384) |
| 16 | i8[csi_len] | csi — buffer cru do `wifi_csi_info_t.buf` (pares imag, real) |

\* offset relativo ao início do payload (byte 12 do pacote).

## Tipo 2 — STATUS (payload 44 bytes, a cada 5 s)

| Offset* | Tipo | Campo |
|---|---|---|
| 0 | u32 | uptime_s |
| 4 | u32 | wifi_quedas |
| 8 | u32 | erros_envio |
| 12 | u32 | heap_livre |
| 16 | i8 | rssi_ap |
| 17 | u8 | motivo_reinicio — 0 outro, 1 energia, 2 software, 3 erro_fatal, 4 watchdog, 5 queda_de_tensao, 6 deep_sleep, 7 pino_reset |
| 18 | u8 | ota_estado — 0 nenhum, 1 baixando, 2 concluído (reiniciando), 3 falhou |
| 19 | u8 | reservado (0) |
| 20 | char[24] | versao_fw — `esp_app_get_description()->version`, terminado/preenchido com `\0` |

## Descoberta (porta 5006, JSON compacto, sem espaços)

- Servidor → broadcast a cada 1 s e resposta unicast às procuras:
  `{"smartroom":"servidor","porta":5005}`
  O firmware reconhece pelo texto exato `"smartroom":"servidor"` e usa o **IP de origem** como servidor.
- Placa → broadcast enquanto não conhece o servidor (e 1 vez/s se ficar 10 s sem ouvir o anúncio, ou após reconectar o Wi-Fi):
  `{"smartroom":"procura","mac":"aa:bb:cc:dd:ee:ff","fw":"2.0.0"}`

## Atualização OTA (porta 5006)

1. O servidor abre um HTTP temporário em `0.0.0.0:8081` servindo apenas `/smartroom_node.bin`.
2. O servidor envia **unicast** para o IP da placa, porta 5006:
   `{"smartroom":"ota","url":"http://<ip-do-pc>:8081/smartroom_node.bin"}`
3. A placa só aceita o comando se o IP de origem for o servidor descoberto. Baixa com `esp_https_ota` (HTTP permitido), informa `ota_estado` no STATUS e reinicia.
4. Rollback: o novo firmware só se marca como válido (`esp_ota_mark_app_valid_cancel_rollback`) depois de encontrar o servidor. Se reiniciar antes disso, o bootloader volta para a versão anterior.

## Protocolo v1 (legado, só leitura no servidor)

Pacote JSON por UDP 5005: `{"esp_id":"ESP_A","seq":N,"rssi":R,"csi_len":L,"sig_mode":S,"mcs":M,"channel":C,"csi_mean_abs":F,"first_word_invalid":0,"csi_encoding":"hex_u8","csi":"<hex>"}` e status `{"esp_id":"ESP_A","tipo":"status",...}`. A procura v1 traz `"esp_id"` em vez de `"mac"`.

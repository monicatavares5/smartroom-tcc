/* Captura de CSI do roteador: callback curto + fila + ping que gera o trafego. */
#pragma once

#include <stdbool.h>
#include "freertos/FreeRTOS.h"
#include "sr_protocolo.h"

/* Cria a fila e liga o CSI filtrado pelo BSSID do AP (Wi-Fi ja conectado). */
void sr_csi_iniciar(void);

/* Inicia o ping ao gateway em CONFIG_SMARTROOM_PING_HZ. */
void sr_csi_iniciar_ping(void);

/* Retira a proxima amostra da fila (false se esgotar a espera). */
bool sr_csi_receber(sr_csi_amostra_t *amostra, TickType_t espera);

/* Tick do ultimo callback de CSI (0 se ainda nao houve nenhum). */
TickType_t sr_csi_ultimo_tick(void);

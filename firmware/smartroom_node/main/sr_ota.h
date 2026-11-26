/* Atualizacao OTA por Wi-Fi (comando JSON na porta de descoberta) e rollback. */
#pragma once

#include <stdint.h>

/* Trata {"smartroom":"ota","url":"http://..."}; quem chama ja validou a origem. */
void sr_ota_comando(const char *json);

/* Estado atual (SR_OTA_*), informado no STATUS. */
uint8_t sr_ota_estado(void);

/* Marca o firmware como valido (cancela o rollback). Chamar ao achar o servidor. */
void sr_ota_confirmar_firmware(void);

/*
 * SmartRoom - no de captura de CSI (ESP32-S3).
 * Baseado no exemplo csi_recv_router do esp-csi (Espressif, Apache-2.0).
 * O mesmo firmware serve para as 4 placas: a identidade e o MAC STA.
 * Protocolo com o servidor: docs/protocolo.md (v2 binario).
 *
 * Modulos:
 *   sr_csi   - CSI do roteador, fila e ping
 *   sr_rede  - descoberta do servidor e envio dos pacotes
 *   sr_saude - eventos de Wi-Fi, reinicios automaticos e STATUS
 *   sr_ota   - atualizacao por Wi-Fi e rollback
 */
#include "esp_event.h"
#include "esp_netif.h"
#include "nvs_flash.h"
#include "protocol_examples_common.h"

#include "sr_csi.h"
#include "sr_rede.h"
#include "sr_saude.h"

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        /* ex.: tabela de particoes mudou; apaga e recomeca */
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    sr_saude_registrar_eventos();

    /* Com CONFIG_EXAMPLE_WIFI_CONN_MAX_RETRY alto (sdkconfig.defaults), espera o hotspot
     * aparecer em vez de desistir. */
    ESP_ERROR_CHECK(example_connect());

    sr_csi_iniciar();
    sr_rede_iniciar();
    sr_csi_iniciar_ping();
    sr_saude_iniciar();
}

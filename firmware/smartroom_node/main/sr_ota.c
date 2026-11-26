/*
 * OTA: baixa o firmware com esp_https_ota (HTTP permitido no sdkconfig) numa tarefa
 * propria e reinicia. Com rollback ligado, o firmware novo so vira valido depois de
 * encontrar o servidor; se reiniciar antes, o bootloader volta para a versao anterior.
 */
#include <stdbool.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_http_client.h"
#include "esp_https_ota.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_system.h"

#include "sr_ota.h"
#include "sr_protocolo.h"
#include "sr_saude.h"

#define SR_OTA_URL_MAX 192

static const char *TAG = "sr_ota";

static volatile uint8_t s_estado = SR_OTA_NENHUM;
static char s_url[SR_OTA_URL_MAX];

uint8_t sr_ota_estado(void)
{
    return s_estado;
}

static void sr_ota_task(void *arg)
{
    ESP_LOGI(TAG, "Baixando firmware de %s", s_url);
    esp_http_client_config_t http = {
        .url = s_url,
        .timeout_ms = 15000,
        .keep_alive_enable = true,
    };
    esp_https_ota_config_t cfg = {
        .http_config = &http,
    };
    esp_err_t err = esp_https_ota(&cfg);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "OTA concluida; reiniciando");
        s_estado = SR_OTA_CONCLUIDO;
        sr_saude_pedir_status();
        vTaskDelay(pdMS_TO_TICKS(2000));  /* da tempo do STATUS sair */
        esp_restart();
    }
    ESP_LOGE(TAG, "OTA falhou: %s", esp_err_to_name(err));
    s_estado = SR_OTA_FALHOU;
    sr_saude_pedir_status();
    vTaskDelete(NULL);
}

void sr_ota_comando(const char *json)
{
    if (s_estado == SR_OTA_BAIXANDO || s_estado == SR_OTA_CONCLUIDO) {
        ESP_LOGW(TAG, "OTA ja em andamento; comando ignorado");
        return;
    }
    const char *p = strstr(json, "\"url\":\"");
    if (!p) {
        ESP_LOGW(TAG, "Comando de OTA sem url");
        return;
    }
    p += 7;
    const char *fim = strchr(p, '"');
    size_t len = fim ? (size_t)(fim - p) : 0;
    if (len == 0 || len >= sizeof(s_url) || strncmp(p, "http://", 7) != 0) {
        ESP_LOGW(TAG, "Comando de OTA com url invalida");
        return;
    }
    memcpy(s_url, p, len);
    s_url[len] = 0;

    s_estado = SR_OTA_BAIXANDO;
    if (xTaskCreate(sr_ota_task, "sr_ota", 8192, NULL, 5, NULL) != pdPASS) {
        ESP_LOGE(TAG, "Sem memoria para a tarefa de OTA");
        s_estado = SR_OTA_FALHOU;
    }
    sr_saude_pedir_status();
}

void sr_ota_confirmar_firmware(void)
{
    static bool feito = false;
    if (feito) {
        return;
    }
    feito = true;
    esp_ota_img_states_t estado;
    const esp_partition_t *rodando = esp_ota_get_running_partition();
    if (esp_ota_get_state_partition(rodando, &estado) == ESP_OK &&
        estado == ESP_OTA_IMG_PENDING_VERIFY) {
        esp_err_t err = esp_ota_mark_app_valid_cancel_rollback();
        ESP_LOGI(TAG, "Firmware novo confirmado (rollback cancelado): %s", esp_err_to_name(err));
    }
}

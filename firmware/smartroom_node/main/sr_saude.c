/*
 * Reconexao e recuperacao automaticas:
 *  - Wi-Fi caiu: o handler do exemplo tenta reconectar; alem disso, a cada
 *    CONFIG_SMARTROOM_WIFI_RETRY_MS chama esp_wifi_connect(); apos
 *    CONFIG_SMARTROOM_WIFI_RESTART_MS sem Wi-Fi, reinicia.
 *  - Wi-Fi conectado mas sem CSI ha CONFIG_SMARTROOM_CSI_STALL_RESTART_MS: reinicia.
 * Telemetria: pacote STATUS (tipo 2) a cada CONFIG_SMARTROOM_STATUS_PERIOD_MS.
 */
#include <stdbool.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_app_desc.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"

#include "sr_csi.h"
#include "sr_ota.h"
#include "sr_protocolo.h"
#include "sr_rede.h"
#include "sr_saude.h"

static const char *TAG = "sr_saude";

static volatile bool s_wifi_ok = false;
static volatile uint32_t s_wifi_quedas = 0;
static volatile TickType_t s_wifi_caiu_em = 0;
static volatile TickType_t s_wifi_voltou_em = 0;
static TaskHandle_t s_tarefa = NULL;

static void sr_saude_eventos(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        if (s_wifi_ok) {
            s_wifi_quedas++;
            s_wifi_caiu_em = xTaskGetTickCount();
            ESP_LOGW(TAG, "Wi-Fi caiu (queda %lu); tentando reconectar", (unsigned long)s_wifi_quedas);
        }
        s_wifi_ok = false;
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        if (!s_wifi_ok) {
            ESP_LOGI(TAG, "Wi-Fi conectado; procurando o servidor");
        }
        s_wifi_voltou_em = xTaskGetTickCount();  /* da tempo para o CSI voltar */
        s_wifi_ok = true;
        sr_rede_forcar_procura();                /* o IP do servidor pode ter mudado */
    }
}

void sr_saude_registrar_eventos(void)
{
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, WIFI_EVENT_STA_DISCONNECTED,
                                               sr_saude_eventos, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                               sr_saude_eventos, NULL));
}

void sr_saude_pedir_status(void)
{
    if (s_tarefa) {
        xTaskNotifyGive(s_tarefa);
    }
}

static uint8_t sr_saude_motivo_reinicio(void)
{
    switch (esp_reset_reason()) {
    case ESP_RST_POWERON:   return SR_MOTIVO_ENERGIA;
    case ESP_RST_SW:        return SR_MOTIVO_SOFTWARE;
    case ESP_RST_PANIC:     return SR_MOTIVO_ERRO_FATAL;
    case ESP_RST_INT_WDT:
    case ESP_RST_TASK_WDT:
    case ESP_RST_WDT:       return SR_MOTIVO_WATCHDOG;
    case ESP_RST_BROWNOUT:  return SR_MOTIVO_QUEDA_DE_TENSAO;
    case ESP_RST_DEEPSLEEP: return SR_MOTIVO_DEEP_SLEEP;
    case ESP_RST_EXT:       return SR_MOTIVO_PINO_RESET;
    default:                return SR_MOTIVO_OUTRO;
    }
}

static void sr_saude_enviar_status(uint8_t motivo)
{
    sr_pacote_status_t pkt;
    memset(&pkt, 0, sizeof(pkt));
    sr_rede_cabecalho(&pkt.cab, SR_TIPO_STATUS, sizeof(sr_status_t));

    int8_t rssi_ap = 0;
    wifi_ap_record_t ap = {0};
    if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
        rssi_ap = ap.rssi;
    }
    pkt.status.uptime_s = (uint32_t)(esp_timer_get_time() / 1000000);
    pkt.status.wifi_quedas = s_wifi_quedas;
    pkt.status.erros_envio = sr_rede_erros_envio();
    pkt.status.heap_livre = esp_get_free_heap_size();
    pkt.status.rssi_ap = rssi_ap;
    pkt.status.motivo_reinicio = motivo;
    pkt.status.ota_estado = sr_ota_estado();
    pkt.status.reservado = 0;
    const char *versao = esp_app_get_description()->version;
    memcpy(pkt.status.versao_fw, versao, strnlen(versao, SR_VERSAO_FW_LEN - 1));  /* resto ja e '\0' */

    sr_rede_enviar(&pkt, sizeof(pkt));
}

static void sr_saude_task(void *arg)
{
    const uint8_t motivo = sr_saude_motivo_reinicio();
    TickType_t ultimo_status = 0;
    TickType_t ultima_tentativa = 0;

    while (true) {
        /* acorda a cada 1 s ou quando pedirem um STATUS */
        bool pedido = ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(1000)) > 0;
        TickType_t agora = xTaskGetTickCount();

        if (!s_wifi_ok) {
            if (agora - s_wifi_caiu_em > pdMS_TO_TICKS(CONFIG_SMARTROOM_WIFI_RESTART_MS)) {
                ESP_LOGE(TAG, "Sem Wi-Fi ha %d s; reiniciando a placa",
                         CONFIG_SMARTROOM_WIFI_RESTART_MS / 1000);
                esp_restart();
            }
            if (agora - ultima_tentativa > pdMS_TO_TICKS(CONFIG_SMARTROOM_WIFI_RETRY_MS)) {
                ultima_tentativa = agora;
                esp_wifi_connect();  /* garante novas tentativas mesmo se o exemplo desistir */
            }
            continue;
        }

        /* Sem CSI desde antes da ultima conexao e ha mais que o limite (nao durante a OTA) */
        TickType_t limite = pdMS_TO_TICKS(CONFIG_SMARTROOM_CSI_STALL_RESTART_MS);
        if (agora - sr_csi_ultimo_tick() > limite && agora - s_wifi_voltou_em > limite &&
            sr_ota_estado() != SR_OTA_BAIXANDO) {
            ESP_LOGE(TAG, "Wi-Fi conectado mas sem CSI ha %d s; reiniciando a placa",
                     CONFIG_SMARTROOM_CSI_STALL_RESTART_MS / 1000);
            esp_restart();
        }

        if (!sr_rede_servidor_conhecido()) {
            continue;
        }
        if (!pedido && agora - ultimo_status < pdMS_TO_TICKS(CONFIG_SMARTROOM_STATUS_PERIOD_MS)) {
            continue;
        }
        ultimo_status = agora;
        sr_saude_enviar_status(motivo);
    }
}

void sr_saude_iniciar(void)
{
    xTaskCreate(sr_saude_task, "sr_saude", 4096, NULL, 2, &s_tarefa);
}

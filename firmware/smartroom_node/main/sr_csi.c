/*
 * Captura de CSI (baseado no csi_recv_router do esp-csi).
 * O callback so filtra pelo AP, conta o seq e copia para a fila; o envio fica em sr_rede.
 */
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "ping/ping_sock.h"
#include "esp_csi_gain_ctrl.h"

#include "sr_csi.h"

#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61
#define CSI_FORCE_LLTF 0
#endif
#define CONFIG_FORCE_GAIN 0

#if CONFIG_IDF_TARGET_ESP32S3 || CONFIG_IDF_TARGET_ESP32C3 || CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C6 || CONFIG_IDF_TARGET_ESP32C61
#define CONFIG_GAIN_CONTROL 1
#endif

#define SR_CSI_FILA_TAM 64

static const char *TAG = "sr_csi";

static QueueHandle_t s_fila;
static volatile TickType_t s_ultimo_csi = 0;
static uint32_t s_seq = 0;  /* so o callback escreve */
static wifi_ap_record_t s_ap;

#if CONFIG_GAIN_CONTROL
/* Coleta os ganhos dos 100 primeiros pacotes e calcula a linha de base. */
static void sr_csi_ganho(const wifi_pkt_rx_ctrl_t *rx_ctrl, uint32_t n)
{
    uint8_t agc_gain = 0;
    int8_t fft_gain = 0;
    esp_csi_gain_ctrl_get_rx_gain(rx_ctrl, &agc_gain, &fft_gain);
    if (n <= 100) {
        esp_csi_gain_ctrl_record_rx_gain(agc_gain, fft_gain);
    }
    if (n == 100) {
        uint8_t agc_base = 0;
        int8_t fft_base = 0;
        esp_csi_gain_ctrl_get_rx_gain_baseline(&agc_base, &fft_base);
#if CONFIG_FORCE_GAIN
        esp_csi_gain_ctrl_set_rx_force_gain(agc_base, fft_base);
        ESP_LOGI(TAG, "fft_force %d, agc_force %d", fft_base, agc_base);
#endif
    }
}
#endif

static void sr_csi_cb(void *ctx, wifi_csi_info_t *info)
{
    if (!info || !info->buf || memcmp(info->mac, ctx, 6)) {
        return;  /* so CSI do AP */
    }
    const wifi_pkt_rx_ctrl_t *rx_ctrl = &info->rx_ctrl;

    s_ultimo_csi = xTaskGetTickCount();
    uint32_t seq = ++s_seq;

#if CONFIG_GAIN_CONTROL
    sr_csi_ganho(rx_ctrl, seq);
#endif

    if (seq % CONFIG_SMARTROOM_ENVIAR_A_CADA) {
        return;
    }
    uint16_t n = info->len > SR_CSI_MAX ? SR_CSI_MAX : info->len;
    sr_csi_amostra_t a;
    a.info.seq = seq;
    a.info.t_ms = (uint32_t)(esp_timer_get_time() / 1000);
    a.info.rssi = (int8_t)rx_ctrl->rssi;
    a.info.sig_mode = rx_ctrl->sig_mode;
    a.info.mcs = rx_ctrl->mcs;
    a.info.canal = rx_ctrl->channel;
    a.info.first_word_invalid = info->first_word_invalid ? 1 : 0;
    a.info.reservado = 0;
    a.info.csi_len = n;
    memcpy(a.csi, info->buf, n);
    xQueueSend(s_fila, &a, 0);  /* fila cheia: descarta */
}

void sr_csi_iniciar(void)
{
    s_fila = xQueueCreate(SR_CSI_FILA_TAM, sizeof(sr_csi_amostra_t));
    ESP_ERROR_CHECK(s_fila ? ESP_OK : ESP_ERR_NO_MEM);

    /* Para compatibilidade com roteadores, so as subportadoras LLTF. */
#if CONFIG_IDF_TARGET_ESP32C5 || CONFIG_IDF_TARGET_ESP32C61
    wifi_csi_config_t csi_config = {
        .enable                   = true,
        .acquire_csi_legacy       = true,
        .acquire_csi_force_lltf   = CSI_FORCE_LLTF,
        .acquire_csi_ht20         = true,
        .acquire_csi_ht40         = true,
        .acquire_csi_vht          = false,
        .acquire_csi_su           = false,
        .acquire_csi_mu           = false,
        .acquire_csi_dcm          = false,
        .acquire_csi_beamformed   = false,
        .acquire_csi_he_stbc_mode = 2,
        .val_scale_cfg            = 0,
        .dump_ack_en              = false,
        .reserved                 = false
    };
#elif CONFIG_IDF_TARGET_ESP32C6
    wifi_csi_config_t csi_config = {
        .enable                 = true,
        .acquire_csi_legacy     = true,
        .acquire_csi_ht20       = true,
        .acquire_csi_ht40       = true,
        .acquire_csi_su         = false,
        .acquire_csi_mu         = false,
        .acquire_csi_dcm        = false,
        .acquire_csi_beamformed = false,
        .acquire_csi_he_stbc    = 2,
        .val_scale_cfg          = false,
        .dump_ack_en            = false,
        .reserved               = false
    };
#else
    wifi_csi_config_t csi_config = {
        .lltf_en           = true,
        .htltf_en          = false,
        .stbc_htltf2_en    = false,
        .ltf_merge_en      = true,
        .channel_filter_en = true,
        .manu_scale        = true,
        .shift             = true,
    };
#endif
    ESP_ERROR_CHECK(esp_wifi_sta_get_ap_info(&s_ap));
    ESP_ERROR_CHECK(esp_wifi_set_csi_config(&csi_config));
    ESP_ERROR_CHECK(esp_wifi_set_csi_rx_cb(sr_csi_cb, s_ap.bssid));
    ESP_ERROR_CHECK(esp_wifi_set_csi(true));
}

void sr_csi_iniciar_ping(void)
{
    esp_ping_config_t ping_config = ESP_PING_DEFAULT_CONFIG();
    ping_config.count           = 0;  /* sem fim */
    ping_config.interval_ms     = 1000 / CONFIG_SMARTROOM_PING_HZ;
    ping_config.task_stack_size = 3072;
    ping_config.data_size       = 1;

    esp_netif_ip_info_t local_ip;
    esp_netif_get_ip_info(esp_netif_get_handle_from_ifkey("WIFI_STA_DEF"), &local_ip);
    ESP_LOGI(TAG, "ip " IPSTR ", gw " IPSTR ", ping %d Hz, envia 1 a cada %d CSI",
             IP2STR(&local_ip.ip), IP2STR(&local_ip.gw),
             CONFIG_SMARTROOM_PING_HZ, CONFIG_SMARTROOM_ENVIAR_A_CADA);
    ping_config.target_addr.u_addr.ip4.addr = ip4_addr_get_u32(&local_ip.gw);
    ping_config.target_addr.type = ESP_IPADDR_TYPE_V4;

    esp_ping_callbacks_t cbs = { 0 };
    esp_ping_handle_t ping = NULL;
    ESP_ERROR_CHECK(esp_ping_new_session(&ping_config, &cbs, &ping));
    ESP_ERROR_CHECK(esp_ping_start(ping));
}

bool sr_csi_receber(sr_csi_amostra_t *amostra, TickType_t espera)
{
    return xQueueReceive(s_fila, amostra, espera) == pdTRUE;
}

TickType_t sr_csi_ultimo_tick(void)
{
    return s_ultimo_csi;
}

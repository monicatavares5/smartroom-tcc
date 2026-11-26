/*
 * Descoberta do servidor e envio dos pacotes binarios v2.
 * O servidor anuncia {"smartroom":"servidor","porta":5005} por broadcast na porta de
 * descoberta; a placa usa o IP de origem como destino. Sem servidor (ou sem anuncio ha
 * CONFIG_SMARTROOM_SERVER_STALE_MS, ou apos reconectar) a placa envia a procura por broadcast.
 * O mesmo socket recebe o comando de OTA, aceito so do servidor descoberto.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_app_desc.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"

#include "sr_csi.h"
#include "sr_ota.h"
#include "sr_rede.h"

#define SR_ERROS_PARA_RECRIAR 100  /* erros seguidos de envio antes de recriar o socket */

static const char *TAG = "sr_rede";

static uint8_t s_mac[6];

/* Endereco do servidor, em ordem de rede; 0 = desconhecido */
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
static uint32_t s_servidor_ip = 0;
static uint16_t s_servidor_porta = 0;
static volatile bool s_forcar_procura = false;

/* Socket de envio, compartilhado entre CSI e STATUS */
static SemaphoreHandle_t s_envio_mutex;
static int s_sock = -1;
static int s_erros_seguidos = 0;
static volatile uint32_t s_erros_envio = 0;

static void sr_rede_servidor(uint32_t *ip, uint16_t *porta)
{
    taskENTER_CRITICAL(&s_mux);
    *ip = s_servidor_ip;
    *porta = s_servidor_porta;
    taskEXIT_CRITICAL(&s_mux);
}

static void sr_rede_definir_servidor(uint32_t ip, uint16_t porta)
{
    taskENTER_CRITICAL(&s_mux);
    s_servidor_ip = ip;
    s_servidor_porta = porta;
    taskEXIT_CRITICAL(&s_mux);
}

const uint8_t *sr_rede_mac(void)
{
    return s_mac;
}

bool sr_rede_servidor_conhecido(void)
{
    uint32_t ip;
    uint16_t porta;
    sr_rede_servidor(&ip, &porta);
    return ip != 0;
}

void sr_rede_forcar_procura(void)
{
    s_forcar_procura = true;
}

uint32_t sr_rede_erros_envio(void)
{
    return s_erros_envio;
}

void sr_rede_cabecalho(sr_cabecalho_t *cab, uint8_t tipo, uint16_t tamanho)
{
    cab->magic[0] = SR_MAGIC_0;
    cab->magic[1] = SR_MAGIC_1;
    cab->versao = SR_PROTO_VERSAO;
    cab->tipo = tipo;
    memcpy(cab->mac, s_mac, sizeof(cab->mac));
    cab->tamanho = tamanho;
}

bool sr_rede_enviar(const void *pacote, size_t len)
{
    struct sockaddr_in dest = { .sin_family = AF_INET };
    uint32_t ip;
    uint16_t porta;
    sr_rede_servidor(&ip, &porta);
    if (ip == 0) {
        return false;  /* servidor ainda nao encontrado */
    }
    dest.sin_addr.s_addr = ip;
    dest.sin_port = porta;

    xSemaphoreTake(s_envio_mutex, portMAX_DELAY);
    if (s_sock < 0) {
        s_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    }
    int r = -1;
    int err = 0;
    if (s_sock >= 0) {
        r = sendto(s_sock, pacote, len, 0, (struct sockaddr *)&dest, sizeof(dest));
        err = errno;
    }
    if (r < 0) {
        s_erros_envio++;
        if (++s_erros_seguidos >= SR_ERROS_PARA_RECRIAR) {
            ESP_LOGW(TAG, "%d erros seguidos no envio (errno %d); recriando socket",
                     s_erros_seguidos, err);
            if (s_sock >= 0) {
                close(s_sock);
            }
            s_sock = -1;  /* recria no proximo envio */
            s_erros_seguidos = 0;
        }
    } else {
        s_erros_seguidos = 0;
    }
    xSemaphoreGive(s_envio_mutex);

    if (r < 0 && err == ENOMEM) {
        vTaskDelay(pdMS_TO_TICKS(5));  /* buffers cheios: da um respiro */
    }
    return r >= 0;
}

/* Le as amostras da fila de CSI e envia como pacotes tipo 1. */
static void sr_rede_dados_task(void *arg)
{
    static sr_pacote_csi_t pkt;
    while (true) {
        if (!sr_csi_receber(&pkt.corpo, portMAX_DELAY)) {
            continue;
        }
        uint16_t tamanho = sizeof(sr_csi_info_t) + pkt.corpo.info.csi_len;
        sr_rede_cabecalho(&pkt.cab, SR_TIPO_CSI, tamanho);
        sr_rede_enviar(&pkt, sizeof(sr_cabecalho_t) + tamanho);
    }
}

static void sr_rede_descoberta_task(void *arg)
{
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (sock < 0) {
        ESP_LOGE(TAG, "Descoberta: socket falhou: errno %d", errno);
        vTaskDelete(NULL);
        return;
    }
    int yes = 1;
    setsockopt(sock, SOL_SOCKET, SO_BROADCAST, &yes, sizeof(yes));
    struct sockaddr_in local = {
        .sin_family = AF_INET,
        .sin_port = htons(CONFIG_SMARTROOM_DISCOVERY_PORT),
        .sin_addr.s_addr = htonl(INADDR_ANY),
    };
    if (bind(sock, (struct sockaddr *)&local, sizeof(local)) < 0) {
        ESP_LOGE(TAG, "Descoberta: bind na porta %d falhou: errno %d",
                 CONFIG_SMARTROOM_DISCOVERY_PORT, errno);
        close(sock);
        vTaskDelete(NULL);
        return;
    }
    struct timeval tv = { .tv_sec = 1, .tv_usec = 0 };
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    struct sockaddr_in bcast = {
        .sin_family = AF_INET,
        .sin_port = htons(CONFIG_SMARTROOM_DISCOVERY_PORT),
        .sin_addr.s_addr = htonl(INADDR_BROADCAST),
    };

    /* JSON compacto, sem espacos */
    char procura[112];
    int procura_len = snprintf(procura, sizeof(procura),
        "{\"smartroom\":\"procura\",\"mac\":\"%02x:%02x:%02x:%02x:%02x:%02x\",\"fw\":\"%s\"}",
        s_mac[0], s_mac[1], s_mac[2], s_mac[3], s_mac[4], s_mac[5],
        esp_app_get_description()->version);
    if (procura_len < 0 || procura_len >= (int)sizeof(procura)) {
        procura_len = (int)strlen(procura);
    }

    char buf[256];
    TickType_t ultimo_anuncio = 0;
    TickType_t ultima_procura = 0;
    bool primeira_vez = true;

    while (true) {
        if (s_forcar_procura) {
            s_forcar_procura = false;
            ultimo_anuncio = 0;
        }
        bool velho = !sr_rede_servidor_conhecido() || ultimo_anuncio == 0 ||
                     (xTaskGetTickCount() - ultimo_anuncio) > pdMS_TO_TICKS(CONFIG_SMARTROOM_SERVER_STALE_MS);
        /* no maximo 1 procura/s (as procuras das outras placas tambem acordam o recvfrom) */
        if (velho && (ultima_procura == 0 || xTaskGetTickCount() - ultima_procura >= pdMS_TO_TICKS(1000))) {
            ultima_procura = xTaskGetTickCount();
            sendto(sock, procura, procura_len, 0, (struct sockaddr *)&bcast, sizeof(bcast));
        }

        struct sockaddr_in from = {0};
        socklen_t from_len = sizeof(from);
        int n = recvfrom(sock, buf, sizeof(buf) - 1, 0, (struct sockaddr *)&from, &from_len);
        if (n <= 0) {
            continue;
        }
        buf[n] = 0;

        uint32_t ip;
        uint16_t porta;
        sr_rede_servidor(&ip, &porta);

        if (strstr(buf, "\"smartroom\":\"ota\"")) {
            if (ip != 0 && from.sin_addr.s_addr == ip) {
                sr_ota_comando(buf);
            } else {
                ESP_LOGW(TAG, "Comando de OTA ignorado: nao veio do servidor descoberto");
            }
            continue;
        }
        if (!strstr(buf, "\"smartroom\":\"servidor\"")) {
            continue;  /* ignora as proprias procuras e as de outras placas */
        }
        int nova_porta = CONFIG_SMARTROOM_DATA_PORT;
        const char *p = strstr(buf, "\"porta\":");
        if (p) {
            int lida = atoi(p + 8);
            if (lida > 0 && lida < 65536) {
                nova_porta = lida;
            }
        }
        if (from.sin_addr.s_addr != ip || htons(nova_porta) != porta) {
            char txt[16];
            inet_ntoa_r(from.sin_addr, txt, sizeof(txt));
            ESP_LOGI(TAG, "Servidor SmartRoom encontrado em %s:%d", txt, nova_porta);
        }
        sr_rede_definir_servidor(from.sin_addr.s_addr, htons(nova_porta));
        ultimo_anuncio = xTaskGetTickCount();
        if (primeira_vez) {
            primeira_vez = false;
            sr_ota_confirmar_firmware();  /* cancela o rollback */
        }
    }
}

void sr_rede_iniciar(void)
{
    ESP_ERROR_CHECK(esp_read_mac(s_mac, ESP_MAC_WIFI_STA));
    ESP_LOGI(TAG, "MAC " MACSTR ", firmware %s", MAC2STR(s_mac), esp_app_get_description()->version);
    s_envio_mutex = xSemaphoreCreateMutex();
    ESP_ERROR_CHECK(s_envio_mutex ? ESP_OK : ESP_ERR_NO_MEM);
    xTaskCreate(sr_rede_dados_task, "sr_dados", 4096, NULL, 4, NULL);
    xTaskCreate(sr_rede_descoberta_task, "sr_descoberta", 4096, NULL, 3, NULL);
}

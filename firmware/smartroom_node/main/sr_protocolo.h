/*
 * Protocolo SmartRoom v2 (placas -> servidor). Espelha docs/protocolo.md.
 * Todos os inteiros sao little-endian; structs empacotadas (sem padding).
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

_Static_assert(__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__, "o protocolo v2 e little-endian");

#define SR_MAGIC_0          'S'
#define SR_MAGIC_1          'R'
#define SR_PROTO_VERSAO     2

#define SR_TIPO_CSI         1
#define SR_TIPO_STATUS      2

#define SR_CSI_MAX          384  /* bytes de CSI por pacote (limite do protocolo) */
#define SR_VERSAO_FW_LEN    24

/* motivo_reinicio do STATUS */
enum {
    SR_MOTIVO_OUTRO = 0,
    SR_MOTIVO_ENERGIA = 1,
    SR_MOTIVO_SOFTWARE = 2,
    SR_MOTIVO_ERRO_FATAL = 3,
    SR_MOTIVO_WATCHDOG = 4,
    SR_MOTIVO_QUEDA_DE_TENSAO = 5,
    SR_MOTIVO_DEEP_SLEEP = 6,
    SR_MOTIVO_PINO_RESET = 7,
};

/* ota_estado do STATUS */
enum {
    SR_OTA_NENHUM = 0,
    SR_OTA_BAIXANDO = 1,
    SR_OTA_CONCLUIDO = 2,  /* reiniciando */
    SR_OTA_FALHOU = 3,
};

/* Cabecalho de todo pacote na porta de dados (12 bytes) */
typedef struct __attribute__((packed)) {
    char     magic[2];   /* "SR" */
    uint8_t  versao;     /* SR_PROTO_VERSAO */
    uint8_t  tipo;       /* SR_TIPO_* */
    uint8_t  mac[6];     /* MAC da interface STA */
    uint16_t tamanho;    /* bytes de payload apos o cabecalho */
} sr_cabecalho_t;

/* Tipo 1 - CSI: parte fixa do payload (16 bytes) */
typedef struct __attribute__((packed)) {
    uint32_t seq;                /* callbacks de CSI desde o boot (inclui os nao enviados) */
    uint32_t t_ms;               /* ms desde o boot */
    int8_t   rssi;
    uint8_t  sig_mode;
    uint8_t  mcs;
    uint8_t  canal;
    uint8_t  first_word_invalid; /* 0/1 */
    uint8_t  reservado;          /* 0 */
    uint16_t csi_len;            /* bytes de CSI que seguem (<= SR_CSI_MAX) */
} sr_csi_info_t;

/* Payload CSI completo; so 16 + csi_len bytes vao para a rede */
typedef struct __attribute__((packed)) {
    sr_csi_info_t info;
    int8_t        csi[SR_CSI_MAX];  /* wifi_csi_info_t.buf cru (pares imag, real) */
} sr_csi_amostra_t;

typedef struct __attribute__((packed)) {
    sr_cabecalho_t   cab;
    sr_csi_amostra_t corpo;
} sr_pacote_csi_t;

/* Tipo 2 - STATUS (44 bytes) */
typedef struct __attribute__((packed)) {
    uint32_t uptime_s;
    uint32_t wifi_quedas;
    uint32_t erros_envio;
    uint32_t heap_livre;
    int8_t   rssi_ap;
    uint8_t  motivo_reinicio;  /* SR_MOTIVO_* */
    uint8_t  ota_estado;       /* SR_OTA_* */
    uint8_t  reservado;        /* 0 */
    char     versao_fw[SR_VERSAO_FW_LEN];  /* terminado/preenchido com '\0' */
} sr_status_t;

typedef struct __attribute__((packed)) {
    sr_cabecalho_t cab;
    sr_status_t    status;
} sr_pacote_status_t;

/* Conferencia do layout contra docs/protocolo.md */
_Static_assert(sizeof(sr_cabecalho_t) == 12, "cabecalho deve ter 12 bytes");
_Static_assert(offsetof(sr_cabecalho_t, mac) == 4, "mac no offset 4");
_Static_assert(offsetof(sr_cabecalho_t, tamanho) == 10, "tamanho no offset 10");
_Static_assert(sizeof(sr_csi_info_t) == 16, "payload CSI fixo deve ter 16 bytes");
_Static_assert(offsetof(sr_csi_info_t, rssi) == 8, "rssi no offset 8");
_Static_assert(offsetof(sr_csi_info_t, csi_len) == 14, "csi_len no offset 14");
_Static_assert(offsetof(sr_csi_amostra_t, csi) == 16, "csi no offset 16");
_Static_assert(offsetof(sr_pacote_csi_t, corpo) == 12, "payload CSI apos o cabecalho");
_Static_assert(sizeof(sr_status_t) == 44, "payload STATUS deve ter 44 bytes");
_Static_assert(offsetof(sr_status_t, rssi_ap) == 16, "rssi_ap no offset 16");
_Static_assert(offsetof(sr_status_t, versao_fw) == 20, "versao_fw no offset 20");
_Static_assert(sizeof(sr_pacote_status_t) == 56, "pacote STATUS deve ter 56 bytes");

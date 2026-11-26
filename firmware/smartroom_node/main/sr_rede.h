/* Rede: descoberta do servidor (porta 5006), endereco do servidor e envio binario (porta 5005). */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "sr_protocolo.h"

/* Le o MAC STA e inicia as tarefas de descoberta e de envio de CSI. */
void sr_rede_iniciar(void);

/* MAC STA (6 bytes), identidade da placa. */
const uint8_t *sr_rede_mac(void);

bool sr_rede_servidor_conhecido(void);

/* Pede nova procura do servidor (ex.: apos reconectar o Wi-Fi). */
void sr_rede_forcar_procura(void);

/* Total de falhas de envio desde o boot. */
uint32_t sr_rede_erros_envio(void);

/* Preenche o cabecalho v2 com o MAC da placa. */
void sr_rede_cabecalho(sr_cabecalho_t *cab, uint8_t tipo, uint16_t tamanho);

/* Envia um pacote ao servidor (thread-safe). false se nao enviou. */
bool sr_rede_enviar(const void *pacote, size_t len);

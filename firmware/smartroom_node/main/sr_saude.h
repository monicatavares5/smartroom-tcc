/* Saude da placa: eventos de Wi-Fi, reinicios automaticos e pacote STATUS. */
#pragma once

/* Registra os handlers de Wi-Fi/IP. Chamar antes do example_connect(). */
void sr_saude_registrar_eventos(void);

/* Inicia a tarefa de monitoramento (reconexao, watchdogs e STATUS). */
void sr_saude_iniciar(void);

/* Envia um STATUS assim que possivel (ex.: mudanca do estado de OTA). */
void sr_saude_pedir_status(void);

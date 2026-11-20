"""Coleta rotulada de localizacao para SmartRoom V7.4, sem alterar o servidor."""
import json
import time
import urllib.request
from datetime import datetime

BASE = 'http://localhost:8080'
STAGES = [('vazio', 'QUARTO VAZIO'), ('perto_a','PERTO DA ESP_A'),
          ('perto_b','PERTO DA ESP_B'), ('perto_c','PERTO DA ESP_C'),
          ('perto_d','PERTO DA ESP_D'), ('centro','CENTRO'),
          ('vazio','QUARTO VAZIO NOVAMENTE')]

def request(path, data=None):
    body = None if data is None else json.dumps(data).encode('utf-8')
    req = urllib.request.Request(BASE + path, data=body,
                                 headers={'Content-Type':'application/json'} if body else {},
                                 method='POST' if body else 'GET')
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.load(resp)

def label(value):
    return request('/api/label', {'label':value})

def run():
    print('SMARTROOM - COLETA DE CSI ROTULADO')
    print('Mantenha as 4 ESP32, iPhone e notebook fixos. Nao reinicie o servidor durante as rodadas.')
    print('Cada etapa: 15s de acomodacao (descartados na analise) + 90s de coleta.')
    print('O CSV rotulado e salvo automaticamente pelo servidor na pasta dados.')
    request('/api/status')
    try:
        for round_no in (1, 2):
            print(f'\n========== RODADA {round_no}/2 ==========')
            for value, name in STAGES:
                input(f'\nVa para {name}, fique parado e pressione ENTER para iniciar... ')
                label(value)
                print('Acomodacao: 15 segundos')
                time.sleep(15)
                print('Coletando: 90 segundos. Permaneca parado.')
                for left in range(90, 0, -10):
                    time.sleep(min(10,left))
                    print(f'  {max(0,left-10)}s restantes')
                label('nao_marcado')
                print(f'Concluido: {name} (rodada {round_no})')
    finally:
        try:
            label('nao_marcado')
        except Exception as exc:
            print('Aviso: nao foi possivel limpar rotulo:', exc)
    print('\nFIM. Envie os dois arquivos smartroom_v53_rotulado_*.csv gerados durante as rodadas,')
    print('ou o arquivo unico gerado nesta execucao (se nao reiniciou o servidor).')

if __name__ == '__main__':
    try:
        run()
    except KeyboardInterrupt:
        print('\nColeta interrompida.')

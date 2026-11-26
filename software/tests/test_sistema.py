"""Sistema completo: rede UDP de verdade (portas livres), registro de MAC, replay e painel HTTP."""
import csv
import json
import socket
import threading
import time
import urllib.request
from datetime import datetime, timedelta

from smartroom import protocolo
from smartroom.nucleo import COLUNAS_CSV, Relogio, Sistema
from smartroom.rede import ANUNCIO, descoberta, receptor
from smartroom.replay import reproduzir
from smartroom.web import criar_servidor

from conftest import csi_aleatorio, porta_livre


def esperar(cond, timeout=3.0):
    fim = time.monotonic() + timeout
    while time.monotonic() < fim:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_mac_novo_recebe_id_livre_e_e_salvo(sistema, tmp_path):
    sistema.receber(protocolo.montar_csi('aa:bb:cc:dd:ee:01', 2, -55, csi_aleatorio()), '10.0.0.5')
    sistema.receber(protocolo.montar_csi('aa:bb:cc:dd:ee:02', 2, -60, csi_aleatorio()), '10.0.0.6')
    assert sistema.placas['ESP_A']['pacotes'] == 1 and sistema.placas['ESP_B']['ip'] == '10.0.0.6'
    salvo = json.loads((tmp_path / 'placas.json').read_text())
    assert salvo == {'aa:bb:cc:dd:ee:01': 'ESP_A', 'aa:bb:cc:dd:ee:02': 'ESP_B'}


def test_placas_json_existente_define_posicao(tmp_path, relogio):
    (tmp_path / 'placas.json').write_text(json.dumps({'aa:bb:cc:dd:ee:09': 'ESP_D'}))
    s = Sistema(gravar_csv=False, relogio=relogio, imprimir=None, arquivo_placas=tmp_path / 'placas.json', pasta_dados=tmp_path)
    s.receber(protocolo.montar_csi('aa:bb:cc:dd:ee:09', 2, -55, csi_aleatorio()), '10.0.0.9')
    assert s.placas['ESP_D']['pacotes'] == 1 and s.placas['ESP_A']['pacotes'] == 0


def test_status_v2_chega_ao_monitor(sistema):
    sistema.receber(protocolo.montar_csi('aa:bb:cc:dd:ee:01', 2, -55, csi_aleatorio()), '10.0.0.5')
    sistema.receber(protocolo.montar_status('aa:bb:cc:dd:ee:01', uptime_s=50, versao_fw='2.0.0'), '10.0.0.5')
    fw = sistema.monitor.snapshot(sistema.agora())['placas']['ESP_A']['firmware']
    assert fw['versao_fw'] == '2.0.0' and fw['uptime_s'] == 50


def test_receptor_e_descoberta_pela_rede(sistema):
    p_dados, p_desc = porta_livre(), porta_livre()
    threading.Thread(target=receptor, args=(sistema, p_dados), daemon=True).start()
    threading.Thread(target=descoberta, args=(sistema, p_desc), daemon=True).start()
    time.sleep(0.3)
    placa = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    placa.settimeout(2)
    placa.sendto(b'{"smartroom":"procura","mac":"aa:bb:cc:dd:ee:03","fw":"2.0.0"}', ('127.0.0.1', p_desc))
    resposta, _ = placa.recvfrom(256)
    assert resposta == ANUNCIO == b'{"smartroom":"servidor","porta":5005}'
    placa.sendto(protocolo.montar_csi('aa:bb:cc:dd:ee:03', 2, -58, csi_aleatorio()), ('127.0.0.1', p_dados))
    placa.sendto(b'lixo que nao derruba o receptor', ('127.0.0.1', p_dados))
    placa.sendto(protocolo.montar_csi('aa:bb:cc:dd:ee:03', 4, -58, csi_aleatorio()), ('127.0.0.1', p_dados))
    assert esperar(lambda: sistema.placas['ESP_A']['pacotes'] == 2)


def gravar_csv_sintetico(caminho, segundos=12, hz=10):
    t0 = datetime(2026, 11, 20, 12, 0, 0)
    with caminho.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=COLUNAS_CSV)
        w.writeheader()
        for i in range(segundos * hz):
            for n, placa in enumerate(('ESP_A', 'ESP_B', 'ESP_C', 'ESP_D')):
                w.writerow({'timestamp': (t0 + timedelta(seconds=i / hz)).isoformat(timespec='milliseconds'),
                            'esp_id': placa, 'ip': f'10.0.0.{n + 2}', 'seq': i * 2, 'rssi': -50 - n, 'csi_len': 128,
                            'mcs': 7, 'channel': 6, 'first_word_invalid': 0, 'csi_hex': csi_aleatorio(semente=i).hex(),
                            'csi_valido': True})


def test_replay_alimenta_o_sistema(tmp_path):
    caminho = tmp_path / 'gravacao.csv'
    gravar_csv_sintetico(caminho)
    s = Sistema(gravar_csv=False, relogio=Relogio(50), imprimir=None,
                arquivo_placas=tmp_path / 'placas.json', pasta_dados=tmp_path)
    total = reproduzir(s, caminho, velocidade=50)
    assert total == 12 * 10 * 4
    assert all(s.placas[p]['pacotes'] == 120 for p in s.placas)
    s.atualizar()
    est = s.estado()
    assert est['replay'] == 'gravacao.csv' and all(v['online'] for v in est['placas'].values())


def test_painel_http(sistema):
    porta = porta_livre(socket.SOCK_STREAM)
    servidor = criar_servidor(sistema, porta)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{porta}'
    try:
        html = urllib.request.urlopen(base + '/').read().decode()
        assert 'Saúde das placas' in html and '/app.js' in html
        assert b'EventSource' in urllib.request.urlopen(base + '/app.js').read()
        estado = json.loads(urllib.request.urlopen(base + '/api/estado').read())
        assert set(estado) >= {'placas', 'presenca', 'localizacao', 'monitor', 'ota', 'replay'}

        req = urllib.request.Request(base + '/api/label', data=b'{"label":"centro"}', method='POST',
                                     headers={'Content-Type': 'application/json'})
        assert json.loads(urllib.request.urlopen(req).read())['ok'] and sistema.rotulo['rotulo'] == 'centro'

        req = urllib.request.Request(base + '/api/calibrar', data=b'{}', method='POST')
        try:
            urllib.request.urlopen(req)
            assert False, 'calibrar sem placas deveria falhar'
        except urllib.error.HTTPError as e:
            assert e.code == 409

        with urllib.request.urlopen(base + '/api/stream', timeout=3) as r:
            linha = r.readline().decode()
        assert linha.startswith('data: ') and 'monitor' in json.loads(linha[6:])
    finally:
        servidor.shutdown()
        servidor.server_close()


def test_teste_guiado_marca_rotulos_e_calibra(sistema, relogio):
    from smartroom.teste_guiado import DURACAO_TOTAL_S, ETAPAS
    macs = [f'aa:bb:cc:dd:ee:0{i}' for i in range(1, 5)]

    def pulso(seq):
        for m in macs:
            sistema.receber(protocolo.montar_csi(m, seq, -55, csi_aleatorio()), '10.0.0.5')

    assert not sistema.teste.iniciar()[0]            # sem as 4 placas online
    pulso(2)
    assert sistema.teste.iniciar()[0]
    assert sistema.rotulo['rotulo'] == 'nao_marcado'
    relogio.t += ETAPAS[0][1] + 0.5
    pulso(4)
    sistema.atualizar()
    assert sistema.rotulo['rotulo'] == 'vazio_calibracao'
    assert sistema.tempo_real.status == 'calibrando' and sistema.estado()['teste']['etapa'] == 2
    relogio.t += DURACAO_TOTAL_S
    sistema.atualizar()
    info = sistema.estado()['teste']
    assert not info['ativo'] and 'concluído' in info['mensagem']
    assert sistema.rotulo['rotulo'] == 'nao_marcado'

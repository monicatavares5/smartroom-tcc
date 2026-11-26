from smartroom.monitor import MonitorPlacas


def test_conecta_cai_e_reconecta():
    m = MonitorPlacas(['ESP_A', 'ESP_B'], imprimir=None)
    t, seq = 1000.0, 0
    for _ in range(150):
        t += 0.1
        seq += 2
        m.registrar_pacote('ESP_A', '10.0.0.2', -55, seq, True, t, mac='aa:bb:cc:dd:ee:01')
        if seq % 20 == 0:
            m.verificar(t)
    s = m.snapshot(t)['placas']
    assert s['ESP_A']['status'] == 'ok' and s['ESP_A']['sinal'] == 'Ótimo'
    assert s['ESP_A']['mac'] == 'aa:bb:cc:dd:ee:01'
    assert s['ESP_B']['status'] == 'aguardando'

    for _ in range(7):
        t += 1
        m.verificar(t)
    assert m.snapshot(t)['placas']['ESP_A']['status'] == 'offline'
    m.registrar_pacote('ESP_A', '10.0.0.2', -55, seq + 2, True, t)
    snap = m.snapshot(t)
    assert snap['placas']['ESP_A']['online'] and snap['placas']['ESP_A']['desconexoes'] == 1
    assert any('reconectada' in e['mensagem'] for e in snap['eventos'])


def test_sinal_fraco_vira_aviso_so_depois_de_5s():
    m = MonitorPlacas(['ESP_A'], imprimir=None)
    t = 0.0
    for i in range(40):           # 4 s
        t += 0.1
        m.registrar_pacote('ESP_A', 'ip', -80, i * 2, True, t)
        m.verificar(t)
    assert m.snapshot(t)['placas']['ESP_A']['avisos'] == []
    for i in range(40, 80):       # mais 4 s
        t += 0.1
        m.registrar_pacote('ESP_A', 'ip', -80, i * 2, True, t)
        m.verificar(t)
    p = m.snapshot(t)['placas']['ESP_A']
    assert p['status'] == 'critico' and 'muito fraco' in p['avisos'][0]


def test_eventos_de_firmware():
    m = MonitorPlacas(['ESP_A'], imprimir=None)
    m.registrar_status_firmware('ESP_A', 'ip', {'uptime_s': 100, 'versao_fw': '2.0.0', 'ota_estado': 0}, 1)
    m.registrar_status_firmware('ESP_A', 'ip', {'uptime_s': 105, 'versao_fw': '2.0.0', 'ota_estado': 1}, 2)
    m.registrar_status_firmware('ESP_A', 'ip', {'uptime_s': 3, 'versao_fw': '2.0.1', 'ota_estado': 0}, 3)
    msgs = [e['mensagem'] for e in m.snapshot(3)['eventos']]
    assert any('baixando firmware' in x for x in msgs)
    assert any('reiniciou' in x for x in msgs)
    assert any('2.0.0 → 2.0.1' in x for x in msgs)

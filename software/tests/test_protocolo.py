import json

import pytest

from smartroom import protocolo as p

MAC = 'aa:bb:cc:dd:ee:01'


def test_csi_v2_ida_e_volta():
    csi = bytes(range(128))
    a = p.ler(p.montar_csi(MAC, seq=42, rssi=-61, csi=csi, t_ms=1234, mcs=7, canal=6, fwi=1))
    assert isinstance(a, p.Amostra)
    assert (a.mac, a.seq, a.rssi, a.mcs, a.canal, a.t_placa_ms, a.versao) == (MAC, 42, -61, 7, 6, 1234, 2)
    assert a.csi == csi and a.first_word_invalid and a.csi_valido


def test_cabecalho_tem_12_bytes_e_status_44():
    assert p.CABECALHO.size == 12
    assert p.CSI.size == 16
    assert p.STATUS.size == 44


def test_status_v2():
    s = p.ler(p.montar_status(MAC, uptime_s=90, wifi_quedas=2, erros_envio=3, heap_livre=150000,
                              rssi_ap=-55, motivo=4, ota_estado=1, versao_fw='2.0.1'))
    assert isinstance(s, p.Status) and s.mac == MAC
    assert s.dados == {'uptime_s': 90, 'wifi_quedas': 2, 'erros_envio': 3, 'heap_livre': 150000, 'rssi_ap': -55,
                       'motivo_reinicio': 'watchdog', 'ota_estado': 1, 'versao_fw': '2.0.1'}


def test_v1_json_legado():
    msg = {'esp_id': 'ESP_B', 'seq': 10, 'rssi': -50, 'csi_len': 128, 'mcs': 3, 'channel': 6,
           'first_word_invalid': 0, 'csi_encoding': 'hex_u8', 'csi': '01' * 128}
    a = p.ler(json.dumps(msg).encode())
    assert a.esp_id == 'ESP_B' and a.versao == 1 and len(a.csi) == 128 and a.csi_valido
    st = p.ler(json.dumps({'esp_id': 'ESP_B', 'tipo': 'status', 'uptime_s': 5}).encode())
    assert isinstance(st, p.Status) and st.dados['uptime_s'] == 5


@pytest.mark.parametrize('dado', [b'', b'xx', b'SR\x02\x01', b'SR\x09\x01' + bytes(8), b'{"sem":"id"}', b'{quebrado'])
def test_pacotes_invalidos(dado):
    with pytest.raises(p.PacoteInvalido):
        p.ler(dado)


def test_payload_truncado():
    pacote = p.montar_csi(MAC, 1, -50, bytes(128))
    with pytest.raises(p.PacoteInvalido):
        p.ler(pacote[:-10])

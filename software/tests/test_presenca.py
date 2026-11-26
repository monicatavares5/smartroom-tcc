"""Detector de presença e localização com dados sintéticos e relógio controlado."""
from smartroom.presenca import DetectorPresenca, LocalizadorProximidade

PLACAS = ('ESP_A', 'ESP_B', 'ESP_C', 'ESP_D')


def simular(det, t0, segundos, rssi, loc=None, hz=10, amp=10.0):
    """Alimenta amostras e avalia 1x por segundo, como o servidor faz. Devolve (t, presença, localização)."""
    r = l = None
    t = t0
    for i in range(int(segundos * hz)):
        t = t0 + i / hz
        for p in PLACAS:
            det.adicionar(p, t, rssi(p, i), amp)
        if i % hz == hz - 1:
            r = det.avaliar(t, set(PLACAS))
            if loc:
                l = loc.avaliar(r, t)
    return t, r, l


def test_sem_calibracao():
    det = DetectorPresenca(PLACAS)
    assert det.avaliar(0, set(PLACAS))['status'] == 'sem_calibracao'


def test_calibra_detecta_vazio_presenca_e_localiza():
    det, loc = DetectorPresenca(PLACAS), LocalizadorProximidade()
    vazio = lambda p, i: -50 + (i % 2)
    assert det.iniciar_calibracao(0.0)[0]
    t, r, _ = simular(det, 0.0, 61, vazio)
    assert r['calibracao'] and det.linha_base is not None

    t, r, _ = simular(det, t, 12, vazio)
    assert r['status'] == 'vazio'

    perto_c = lambda p, i: -60 if p == 'ESP_C' else -50 + (i % 2)   # pessoa perto da ESP_C
    t, r, l = simular(det, t, 15, perto_c, loc)
    assert r['status'] == 'presenca'
    assert r['nos']['ESP_C']['indice'] > max(r['nos'][p]['indice'] for p in ('ESP_A', 'ESP_B', 'ESP_D'))
    assert l['regiao'] == 'perto_c'

    t, r, l = simular(det, t, 20, vazio, loc)    # pessoa saiu
    assert r['status'] == 'vazio' and l['regiao'] is None


def test_calibracao_falha_sem_amostras():
    det = DetectorPresenca(PLACAS)
    det.iniciar_calibracao(0.0)
    r = det.avaliar(61.0, set(PLACAS))
    assert r['status'] == 'sem_calibracao' and 'insuficientes' in r['detail']


def test_incerto_com_menos_de_3_placas():
    det = DetectorPresenca(PLACAS)
    det.iniciar_calibracao(0.0)
    t, _, _ = simular(det, 0.0, 61, lambda p, i: -50)
    assert det.avaliar(t, {'ESP_A', 'ESP_B'})['status'] == 'incerto'

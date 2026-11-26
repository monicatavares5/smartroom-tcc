"""Detector em tempo real com CSI sintético: quarto vazio, pessoa parada perto de uma placa e andando."""
import numpy as np

from smartroom import tempo_real as tr
from smartroom.config import SALA
from smartroom.tempo_real import DetectorTempoReal, LocalizadorRTI, forma_espectro, ledoit_wolf

PLACAS = ('ESP_A', 'ESP_B', 'ESP_C', 'ESP_D')
HZ = 15


class Quarto:
    """Gera CSI LLTF (128 bytes) com uma resposta em frequência fixa por placa + ruído."""

    def __init__(self, semente=1):
        self.rng = np.random.default_rng(semente)
        k = np.arange(64)
        self.base = {p: 14 + 5 * np.sin(k / (3 + i) + i) for i, p in enumerate(PLACAS)}
        self.nulas = np.r_[0, 27:38]

    def csi(self, placa, perturbacao=0.0, movimento=0.0):
        amp = self.base[placa].copy()
        if perturbacao:   # pessoa parada: muda o multipercurso (forma do espectro)
            amp *= 1 + perturbacao * np.cos(np.arange(64) / 2.3)
        amp *= 1 + self.rng.normal(0, 0.03 + movimento, 64)   # ruído + agitação
        amp *= self.rng.uniform(0.9, 1.1)                       # ganho automático (deve ser ignorado)
        amp = np.clip(amp, 0, 120)
        amp[self.nulas] = 0
        fase = self.rng.uniform(0, 2 * np.pi, 64)
        iq = np.empty(128)
        iq[0::2], iq[1::2] = amp * np.sin(fase), amp * np.cos(fase)
        return np.clip(np.round(iq), -127, 127).astype(np.int8).tobytes()


def simular(det, quarto, t0, segundos, efeito=lambda p: (0.0, 0.0)):
    resultado, t = None, t0
    for i in range(int(segundos * HZ)):
        t = t0 + i / HZ
        for p in PLACAS:
            pert, mov = efeito(p)
            det.adicionar(p, t, quarto.csi(p, pert, mov), -50 - 3 * pert * 10, 1)
        if i % 2 == 0:        # ~7 avaliações/s
            resultado = det.avaliar(t, set(PLACAS))
    return t, resultado


def calibrado(semente=1):
    det, quarto = DetectorTempoReal(PLACAS), Quarto(semente)
    assert det.iniciar_calibracao(0.0)[0]
    t, r = simular(det, quarto, 0.0, tr.CALIBRACAO_S + 1)
    assert r['calibracao'] and det.tau is not None
    return det, quarto, t


def test_forma_ignora_ganho():
    q = Quarto()
    a = q.csi('ESP_A')
    b = (np.frombuffer(a, dtype=np.int8).astype(int) // 2).astype(np.int8).tobytes()
    xa, xb = forma_espectro(a), forma_espectro(b)
    assert np.corrcoef(xa, xb)[0, 1] > 0.95          # mesma forma, apesar do ganho 2x
    assert abs(xa.mean()) < 1e-9 and abs(xb.mean()) < 1e-9
    assert forma_espectro(bytes(10)) is None


def test_ledoit_wolf_e_invertivel():
    X = np.random.default_rng(0).normal(size=(40, 51))     # menos amostras que dimensões
    S = ledoit_wolf(X)
    assert np.all(np.linalg.eigvalsh(S) > 0)


def test_quarto_vazio_fica_vazio():
    det, quarto, t = calibrado()
    t, r = simular(det, quarto, t, 30)
    assert r['status'] == 'vazio' and r['probabilidade'] < 0.15


def test_pessoa_parada_perto_da_c_e_detectada_rapido_e_localizada():
    det, quarto, t = calibrado()
    t, _ = simular(det, quarto, t, 5)
    perto_c = lambda p: (0.25, 0.01) if p == 'ESP_C' else (0.02, 0.0)
    estados = []
    t_ini = t
    for _ in range(30):                     # 3 s em fatias de 0,1 s
        t, r = simular(det, quarto, t, 0.1 + 1 / HZ, perto_c)
        estados.append((t - t_ini, r['status']))
    detectou = next(s for s, st in estados if st == 'presenca')
    assert detectou < 2.0, f'demorou {detectou:.1f} s'
    t, r = simular(det, quarto, t, 3, perto_c)
    assert r['status'] == 'presenca'
    assert r['placas']['ESP_C']['escore'] == max(v['escore'] for v in r['placas'].values())
    pos = r['posicao']
    assert pos['x'] < 1.25 and pos['y'] > 1.75, pos          # quadrante da C (inferior esquerdo)

    t, r = simular(det, quarto, t, 8)        # saiu
    assert r['status'] == 'vazio' and r['posicao'] is None


def test_andando_e_detectado():
    det, quarto, t = calibrado(semente=3)
    t, r = simular(det, quarto, t, 3, lambda p: (0.05, 0.12))
    assert r['status'] == 'presenca'


def test_sensibilidade_baixa_o_limiar():
    det, quarto, t = calibrado()
    t, r0 = simular(det, quarto, t, 2)
    det.sensibilidade = 1.0
    t, r1 = simular(det, quarto, t, 2)
    assert r1['limiar'] < r0['limiar']


def test_localizador_aponta_para_a_placa_com_mais_evidencia():
    loc = LocalizadorRTI(PLACAS)
    for k in range(10):
        pos = loc.estimar({'ESP_A': {'escore': 2.0}, 'ESP_B': {'escore': 9.0},
                           'ESP_C': {'escore': 1.0}, 'ESP_D': {'escore': 2.0}}, k * 0.1)
    bx, by = SALA['placas']['ESP_B']
    assert pos['x'] > SALA['largura'] / 2 and pos['y'] < SALA['profundidade'] / 2, pos
    assert pos['regiao'] in ('perto_b', 'centro')


def test_avisa_quando_o_ambiente_muda_sem_ninguem():
    eventos = []
    det, quarto, t = calibrado()
    det._ao_evento = lambda nivel, msg: eventos.append(msg)
    t, r = simular(det, quarto, t, 5)
    assert not r['ambiente_alterado']
    # iPhone mexido: a forma de TODOS os enlaces muda e fica parada, sem movimento
    t, r = simular(det, quarto, t, 40, lambda p: (0.3, 0.0))
    assert r['ambiente_alterado'] and 'recalibre' in r['detail']
    assert any('ambiente parece ter mudado' in m for m in eventos)


def test_pessoa_perto_de_uma_placa_nao_e_confundida_com_ambiente_alterado():
    det, quarto, t = calibrado()
    t, r = simular(det, quarto, t, 40, lambda p: (0.25, 0.01) if p == 'ESP_C' else (0.0, 0.0))
    assert r['status'] == 'presenca' and not r['ambiente_alterado']

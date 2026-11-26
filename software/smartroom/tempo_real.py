"""Detector de presença em tempo real e localização contínua (v3).

Ideia geral, por placa b (o enlace iPhone → placa b):

1. Forma do espectro. Do CSI (LLTF, 64 subportadoras) ficam 51 úteis. Usa-se
   x = ln(|H_k| + 0,5) − média_k, o que remove o ganho automático do rádio (multiplicativo →
   aditivo no log → some ao centrar) e deixa só o "desenho" do multipercurso.
   Resíduo r = x − μ_b,modo, em que μ é a forma média do quarto vazio (por sig_mode).

2. Três evidências numa janela curta W = 1 s, cada uma padronizada pela calibração
   (distribuição das mesmas estatísticas em janelas do quarto vazio):
   - movimento   zM: m = (1/K)·Σ_k Var_t(r_k)  → z de ln m           (pessoa andando, respirando)
   - forma       zS: q = r̄ᵀ Σ⁻¹ r̄ (Mahalanobis, Σ com encolhimento de Ledoit–Wolf) → z robusto de ln q
                                                                     (pessoa parada mudando o multipercurso)
   - RSSI        zR: |RSSĪ − RSSI₀| / σ_R
   Escore da placa s_b = max(zM, zS, zR); escore global S = max_b s_b.

3. Decisão bayesiana (HMM de 2 estados, forma log-odds), a cada avaliação:
   logit p_t = clamp( logit(prior_t) + LLR(S_t) ),  prior_t = A·p_{t−1},
   LLR(S) = κ⁺·(S − τ) se S > τ, senão κ⁻·(S − τ)  (sobe rápido, desce mais devagar).
   τ = quantil 95% de S no quarto vazio + 1, limitado a [2,5; 6] (robusto a transientes na
   calibração). Presença se p ≥ 0,85; vazio se p ≤ 0,15 (histerese).

4. Localização contínua (modelo de tomografia de rádio, RTI): uma pessoa perturba mais o
   enlace cujo segmento iPhone→placa passa perto dela: f_b(q) = exp(−d(q, seg_b)²/2σ²).
   Evidência e_b = max(0, s_b − 1). Em uma grade de 5 cm: P(q|e) ∝ exp(−κ_L‖ê − f̂(q)‖²)
   (vetores normalizados). A média e a covariância da posterior entram num filtro de Kalman
   2-D de velocidade constante, que suaviza a trajetória.

5. Linha de base adaptativa: só depois de 30 s seguidos de "vazio" confiante, μ e RSSI₀
   seguem derivas lentas (constante de tempo ~3 min). Nunca aprende com presença.
"""
import math
from collections import deque

import numpy as np

SUBPORTADORAS = np.r_[2:27, 38:64]     # LLTF: 0 = DC, 27–37 = guarda, 1 pode ser inválida
JANELA_S = 1.0
PASSO_CAL_S = 0.5
MIN_PACOTES_JANELA = 6
CALIBRACAO_S = 60.0
DESCARTE_INICIAL_S = 10.0             # tempo para a pessoa sair do quarto depois de clicar
P_PRESENCA, P_VAZIO = 0.85, 0.15
TRANSICAO = 0.02                       # prob. de trocar de estado por avaliação (prior do HMM)
KAPPA_SOBE, KAPPA_DESCE = 1.5, 1.0     # ajustados no teste guiado de 20/11
# Ambiente alterado: escore de forma acima do limiar em ≥ 3 placas, sem movimento, por este tempo
# (ex.: iPhone ou mesa mexidos). Só avisa para recalibrar; não muda a decisão.
AMBIENTE_PLACAS, AMBIENTE_MOV_MAX, AMBIENTE_S = 3, 1.0, 20.0
# Perfil do quarto vazio separado por (sig_mode, MCS): o iPhone muda a taxa (MCS) sozinho e o
# formato do CSI depende um pouco dela; grupos com < 30 amostras usam o perfil do sig_mode.
POR_MCS = False   # testado no teste guiado de 20/11: não melhorou (87,2% x 87,4%) e dobrou o atraso


def chave_modo(sig_mode, mcs=None):
    """Código inteiro do grupo: sig_mode·100 + MCS (99 = MCS ignorado)."""
    sm = int(sig_mode or 0)
    return sm * 100 + (int(mcs) if POR_MCS and mcs is not None else 99)


def _perfil(base, modo):
    return base['mu'].get(modo) if modo in base['mu'] else base['mu_sm'].get(modo // 100, base['mu_geral'])
LOGIT_MAX = 8.0
ADAPTA_APOS_S = 30.0
ADAPTA_TAU_S = 180.0


def forma_espectro(csi: bytes):
    """Vetor de 51 posições: ln(|H| + 0,5) centrado. None se o CSI não for LLTF de 128 bytes."""
    if len(csi) != 128:
        return None
    b = np.frombuffer(csi, dtype=np.int8).astype(np.float64)
    amp = np.hypot(b[0::2], b[1::2])[SUBPORTADORAS]
    if not amp.any():
        return None
    x = np.log(amp + 0.5)
    return x - x.mean()


def ledoit_wolf(X):
    """Covariância com encolhimento ótimo de Ledoit–Wolf para a identidade escalada."""
    n, p = X.shape
    Xc = X - X.mean(0)
    S = Xc.T @ Xc / n
    mu = np.trace(S) / p
    F = mu * np.eye(p)
    d2 = np.sum((S - F) ** 2)
    b2 = sum(np.sum((np.outer(x, x) - S) ** 2) for x in Xc) / n ** 2
    alpha = 0.0 if d2 == 0 else min(1.0, b2 / d2)
    return alpha * F + (1 - alpha) * S


def _robusto(valores, piso):
    v = np.asarray(valores, dtype=float)
    med = float(np.median(v))
    mad = float(np.median(np.abs(v - med))) * 1.4826
    return med, max(mad, piso)


class _Placa:
    def __init__(self):
        self.buf = deque()                 # (t, x, rssi, modo)
        self.base = None                   # dict da calibração


class DetectorTempoReal:
    def __init__(self, placas, ao_evento=None):
        self.placas = tuple(placas)
        self._p = {b: _Placa() for b in self.placas}
        self._cal_inicio = None
        self._cal_amostras = None
        self._cal_erro = None
        self.calibrado_em = None
        self.tau = None
        self.sensibilidade = 0.0          # [-1, 1]; +1 baixa o limiar em 1,5 desvio
        self.logit = -LOGIT_MAX
        self.status = 'sem_calibracao'
        self._vazio_desde = None
        self._ultimo = None
        self._ao_evento = ao_evento
        self.localizador = LocalizadorRTI(self.placas)

    # ------------------------------------------------------------ entrada
    def adicionar(self, placa, agora, csi, rssi, sig_mode, mcs=None):
        p = self._p.get(placa)
        if p is None or rssi is None:
            return
        x = forma_espectro(csi)
        if x is None:
            return
        modo = chave_modo(sig_mode, mcs)
        p.buf.append((agora, x, float(rssi), modo))
        while p.buf and agora - p.buf[0][0] > JANELA_S:
            p.buf.popleft()
        if self._cal_amostras is not None and agora - self._cal_inicio >= DESCARTE_INICIAL_S:
            self._cal_amostras[placa].append((agora, x, float(rssi), modo))
        if self.status == 'vazio' and p.base and self._vazio_desde is not None \
                and agora - self._vazio_desde > ADAPTA_APOS_S and self._ultimo is not None \
                and self._ultimo['escore'] < self._ultimo['limiar']:
            self._adaptar(p.base, x, float(rssi), modo)

    # ------------------------------------------------------------ calibração
    def iniciar_calibracao(self, agora):
        if self._cal_inicio is not None:
            return False, 'Calibração já iniciada'
        self._cal_inicio = agora
        self._cal_amostras = {b: [] for b in self.placas}
        self._cal_erro = None
        self.status = 'calibrando'
        self._ambiente = {'mov_ema': 0.0, 'desde': None, 'alterado': False, 't': None}
        return True, None

    def _verificar_ambiente(self, placas, tau, agora):
        """Mudança estática em quase todos os enlaces e nenhum movimento = o ambiente mudou."""
        a = getattr(self, '_ambiente', None)
        if a is None:
            a = self._ambiente = {'mov_ema': 0.0, 'desde': None, 'alterado': False, 't': None}
        dt = 0.1 if a['t'] is None else max(0.0, min(1.0, agora - a['t']))
        a['t'] = agora
        mov = sum(v['movimento'] for v in placas.values()) / len(placas)   # média: robusta a picos isolados
        a['mov_ema'] += (1 - math.exp(-dt / 5.0)) * (mov - a['mov_ema'])        # média móvel de ~5 s
        formas = sum(1 for v in placas.values() if v['forma'] > tau)
        if formas >= AMBIENTE_PLACAS and a['mov_ema'] < AMBIENTE_MOV_MAX:
            a['desde'] = a['desde'] if a['desde'] is not None else agora
            if not a['alterado'] and agora - a['desde'] >= AMBIENTE_S:
                a['alterado'] = True
                if self._ao_evento:
                    self._ao_evento('aviso', 'o ambiente parece ter mudado (iPhone, mesas ou porta mexidos?): '
                                             'todos os enlaces mudaram e não há movimento. Se o quarto estiver vazio, recalibre.')
        else:
            a['desde'] = None
            if a['alterado'] and formas < 2:
                a['alterado'] = False
        return a['alterado']

    def _concluir_calibracao(self):
        bases, series = {}, {}
        for b, amostras in self._cal_amostras.items():
            if len(amostras) < 200:
                self._cal_erro = f'Amostras insuficientes para {b} ({len(amostras)})'
                break
            t = np.array([a[0] for a in amostras])
            X = np.stack([a[1] for a in amostras])
            rssi = np.array([a[2] for a in amostras])
            modos = np.array([a[3] for a in amostras])
            # mediana por subportadora: robusta a transientes (ex.: alguém saindo do quarto)
            mu = {m: np.median(X[modos == m], axis=0) for m in np.unique(modos) if (modos == m).sum() >= 30}
            mu_geral = np.median(X, axis=0)
            sms = modos // 100
            mu_sm = {s: np.median(X[sms == s], axis=0) for s in np.unique(sms) if (sms == s).sum() >= 30}
            base0 = {'mu': mu, 'mu_sm': mu_sm, 'mu_geral': mu_geral}
            R = X - np.stack([_perfil(base0, m) for m in modos])
            sigma = ledoit_wolf(R)
            base = {'mu': mu, 'mu_sm': mu_sm, 'mu_geral': mu_geral, 'sigma_inv': np.linalg.inv(sigma),
                    'rssi0': float(np.median(rssi))}
            # estatísticas das janelas do quarto vazio
            lnm, q, rj = [], [], []
            ts = np.arange(t[0] + JANELA_S, t[-1], PASSO_CAL_S)
            for fim in ts:
                sel = (t > fim - JANELA_S) & (t <= fim)
                if sel.sum() < MIN_PACOTES_JANELA:
                    continue
                m, qq, rr = self._estatisticas(R[sel], rssi[sel], base)
                lnm.append(math.log(m))
                q.append(math.log(qq + 1e-9))
                rj.append(rr)
            if len(lnm) < 40:
                self._cal_erro = f'Janelas insuficientes para {b}'
                break
            base['lnm'] = _robusto(lnm, 0.10)
            base['q'] = _robusto(q, 0.15)
            r_med, r_mad = _robusto(rj, 0.5)
            base['rssi_sigma'] = max(1.0, r_mad)
            base['rssi0'] = r_med
            bases[b] = base
            series[b] = (ts[:len(lnm)], lnm, q, rj)
        else:
            # τ: quantil alto do escore global no quarto vazio (janelas alinhadas no tempo)
            globais = []
            n = min(len(s[1]) for s in series.values())
            for i in range(n):
                globais.append(max(self._escore(bases[b], series[b][1][i], series[b][2][i], series[b][3][i], log_q=True)[0]
                                   for b in self.placas))
            self.tau = float(np.clip(np.quantile(globais, 0.95) + 1.0, 2.5, 6.0))
            for b in self.placas:
                self._p[b].base = bases[b]
            self.calibrado_em = self._cal_inicio
            self.logit = -LOGIT_MAX
            self.status = 'vazio'
            self._vazio_desde = None
            if self._ao_evento:
                self._ao_evento('ok', f'calibração (tempo real) concluída; limiar {self.tau:.2f}')
        if self._cal_erro:
            self.status = 'sem_calibracao' if not any(p.base for p in self._p.values()) else self.status
            if self._ao_evento:
                self._ao_evento('erro', 'calibração (tempo real) falhou: ' + self._cal_erro)
        self._cal_inicio = None
        self._cal_amostras = None

    # ------------------------------------------------------------ estatística
    @staticmethod
    def _estatisticas(R, rssi, base):
        m = float(np.mean(np.var(R, axis=0))) + 1e-6
        rb = R.mean(0)
        q = float(rb @ base['sigma_inv'] @ rb)
        return m, q, float(np.mean(rssi))

    @staticmethod
    def _escore(base, lnm, q, rssi_media, log_q=False):
        zM = (lnm - base['lnm'][0]) / base['lnm'][1]
        zS = ((q if log_q else math.log(q + 1e-9)) - base['q'][0]) / base['q'][1]
        zR = abs(rssi_media - base['rssi0']) / base['rssi_sigma']
        return max(zM, zS, zR), zM, zS, zR

    def _adaptar(self, base, x, rssi, modo):
        alfa = 1.0 / (ADAPTA_TAU_S * 15.0)          # ~15 pacotes/s
        mu = base['mu'].get(modo)
        if mu is not None:
            mu += alfa * (x - mu)
        base['rssi0'] += alfa * (rssi - base['rssi0'])

    # ------------------------------------------------------------ avaliação (10x/s)
    def avaliar(self, agora, online):
        if self._cal_inicio is not None:
            if agora - self._cal_inicio >= CALIBRACAO_S:
                self._concluir_calibracao()
            else:
                return {'status': 'calibrando', 'progresso': round(100 * (agora - self._cal_inicio) / CALIBRACAO_S),
                        'calibracao': False, 'placas': {}, 'posicao': None}
        if self.tau is None:
            return {'status': 'sem_calibracao', 'calibracao': False, 'placas': {}, 'posicao': None,
                    'detail': self._cal_erro or 'Calibre com o quarto vazio'}

        placas = {}
        for b in self.placas:
            p = self._p[b]
            if b not in online or not p.base:
                continue
            janela = [a for a in p.buf if agora - a[0] <= JANELA_S]
            if len(janela) < MIN_PACOTES_JANELA:
                continue
            X = np.stack([a[1] for a in janela])
            R = X - np.stack([_perfil(p.base, a[3]) for a in janela])
            m, q, rssi_media = self._estatisticas(R, np.array([a[2] for a in janela]), p.base)
            s, zM, zS, zR = self._escore(p.base, math.log(m), q, rssi_media)
            placas[b] = {'escore': round(s, 2), 'movimento': round(zM, 2), 'forma': round(zS, 2),
                         'rssi': round(zR, 2), 'pacotes': len(janela)}

        if len(placas) < 2:
            self._ultimo = None
            return {'status': 'incerto', 'calibracao': True, 'placas': placas, 'posicao': None,
                    'probabilidade': None, 'detail': 'Aguardando CSI de pelo menos 2 placas'}

        S = max(v['escore'] for v in placas.values())
        tau = max(1.5, self.tau - 1.5 * self.sensibilidade)
        # HMM 2 estados em log-odds: predição (transição) + atualização (evidência)
        p_ant = 1 / (1 + math.exp(-self.logit))
        prior = p_ant * (1 - TRANSICAO) + (1 - p_ant) * TRANSICAO
        llr = (KAPPA_SOBE if S > tau else KAPPA_DESCE) * (S - tau)
        self.logit = max(-LOGIT_MAX, min(LOGIT_MAX, math.log(prior / (1 - prior)) + llr))
        prob = 1 / (1 + math.exp(-self.logit))
        anterior = self.status
        if prob >= P_PRESENCA:
            self.status = 'presenca'
        elif prob <= P_VAZIO:
            self.status = 'vazio'
        elif self.status not in ('presenca', 'vazio'):
            self.status = 'incerto'
        if self.status == 'vazio' and anterior != 'vazio':
            self._vazio_desde = agora
        elif self.status != 'vazio':
            self._vazio_desde = None

        posicao = self.localizador.estimar(placas, agora) if self.status == 'presenca' else self.localizador.reiniciar()
        alterado = self._verificar_ambiente(placas, tau, agora)
        self._ultimo = {'escore': S, 'limiar': tau}
        detalhe = f'P(presença) = {prob:.0%} · escore {S:.1f} / limiar {tau:.1f}'
        if alterado:
            detalhe = 'Ambiente alterado desde a calibração (iPhone/mesas mexidos?) — recalibre com o quarto vazio. ' + detalhe
        return {'status': self.status, 'calibracao': True, 'probabilidade': round(prob, 3),
                'escore': round(S, 2), 'limiar': round(tau, 2), 'placas': placas, 'posicao': posicao,
                'ambiente_alterado': alterado, 'detail': detalhe}


# ================================================================ localização
def _dist_segmento(P, a, b):
    """Distância de cada ponto de P (N×2) ao segmento a–b."""
    ab = b - a
    t = np.clip(((P - a) @ ab) / (ab @ ab), 0.0, 1.0)
    return np.linalg.norm(P - (a + t[:, None] * ab), axis=1)


class LocalizadorRTI:
    """Grade bayesiana sobre a sala + filtro de Kalman 2-D (velocidade constante)."""

    # σ e κ ajustados no teste guiado de 20/11 (erro médio 0,65 → 0,52 m; A, C e D 100% certas)
    def __init__(self, placas, sala=None, sigma_m=0.7, kappa=12.0, passo_m=0.05):
        from .config import SALA
        self.sala = sala or SALA
        self.placas = tuple(placas)
        w, h = self.sala['largura'], self.sala['profundidade']
        xs, ys = np.arange(0, w + 1e-9, passo_m), np.arange(0, h + 1e-9, passo_m)
        gx, gy = np.meshgrid(xs, ys)
        self.grade = np.c_[gx.ravel(), gy.ravel()]
        ap = np.array(self.sala['roteador'])
        F = np.stack([np.exp(-_dist_segmento(self.grade, ap, np.array(self.sala['placas'][b])) ** 2 / (2 * sigma_m ** 2))
                      for b in self.placas], axis=1) + 0.02
        self.F = F / np.linalg.norm(F, axis=1, keepdims=True)
        self.kappa = kappa
        self.x = None          # estado [x, y, vx, vy]
        self.P = None
        self.t = None

    def reiniciar(self):
        self.x = self.P = self.t = None
        return None

    def estimar(self, placas, agora):
        e = np.array([max(0.0, placas[b]['escore'] - 1.0) if b in placas else 0.0 for b in self.placas])
        mascara = np.array([b in placas for b in self.placas])
        if e.sum() <= 1e-6:
            z, Rm = np.array(self.sala['roteador'], dtype=float), np.eye(2) * 0.6 ** 2
        else:
            e_hat = e / np.linalg.norm(e)
            Fm = self.F * mascara
            Fm = Fm / (np.linalg.norm(Fm, axis=1, keepdims=True) + 1e-12)
            logp = -self.kappa * np.sum((Fm - e_hat) ** 2, axis=1)
            w = np.exp(logp - logp.max())
            w /= w.sum()
            z = w @ self.grade
            d = self.grade - z
            Rm = (d * w[:, None]).T @ d + np.eye(2) * 0.05 ** 2
        self._kalman(z, Rm, agora)
        x, y = float(self.x[0]), float(self.x[1])
        sx, sy = float(math.sqrt(self.P[0, 0])), float(math.sqrt(self.P[1, 1]))
        return {'x': round(x, 2), 'y': round(y, 2), 'incerteza_m': round(max(sx, sy), 2),
                'regiao': self.regiao(x, y), 'evidencia': {b: round(float(v), 2) for b, v in zip(self.placas, e)}}

    def _kalman(self, z, Rm, agora, acel=1.5):
        if self.x is None:
            self.x = np.r_[z, 0.0, 0.0]
            self.P = np.diag([Rm[0, 0], Rm[1, 1], 1.0, 1.0])
            self.t = agora
            return
        dt = max(1e-3, min(1.0, agora - self.t))
        self.t = agora
        Fk = np.eye(4)
        Fk[0, 2] = Fk[1, 3] = dt
        G = np.array([[dt * dt / 2, 0], [0, dt * dt / 2], [dt, 0], [0, dt]])
        Q = G @ G.T * acel ** 2
        self.x = Fk @ self.x
        self.P = Fk @ self.P @ Fk.T + Q
        H = np.eye(2, 4)
        S = H @ self.P @ H.T + Rm
        K = self.P @ H.T @ np.linalg.inv(S)
        self.x = self.x + K @ (z - H @ self.x)
        self.P = (np.eye(4) - K @ H) @ self.P
        w, h = self.sala['largura'], self.sala['profundidade']
        self.x[0] = min(max(self.x[0], 0.0), w)
        self.x[1] = min(max(self.x[1], 0.0), h)

    def regiao(self, x, y):
        """Centro se estiver a menos de raio_centro_m do iPhone; senão, a placa mais próxima."""
        q = np.array([x, y])
        if np.linalg.norm(q - np.array(self.sala['roteador'])) < self.sala.get('raio_centro_m', 0.5):
            return 'centro'
        perto = {b: np.linalg.norm(q - np.array(pos)) for b, pos in self.sala['placas'].items()}
        return 'perto_' + min(perto, key=perto.get)[-1].lower()

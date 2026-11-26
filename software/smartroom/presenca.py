"""Detector de presença (V7.4) e localização por proximidade.

Mesma lógica das versões anteriores, agora em classes sem estado global:
- calibração explícita de 60 s com o quarto vazio (nunca aprende ocupação sozinha);
- índice por placa = max(desvio de RSSI, 0,5 × desvio de amplitude CSI, aumento da variabilidade);
- presença se algum índice ≥ 1,0 por 5 s; vazio se todos < 0,6 por 8 s (histerese);
- localização: placa com índice dominante → perto_x; sem destaque → centro.
"""
import statistics
from collections import deque
from datetime import datetime

JANELA_S = 8.0
CALIBRACAO_S = 60
MIN_AMOSTRAS_JANELA = 12
LIMIAR_PRESENCA = 1.0
LIMIAR_VAZIO = 0.6
CONFIRMA_PRESENCA_S = 5
CONFIRMA_VAZIO_S = 8

PERTO_INDICE = 1.25
PERTO_MARGEM = 0.20
LOCAL_ESTAVEL_S = 2.0


def percentil(valores, p):
    v = sorted(valores)
    if not v:
        return 0.0
    i = (len(v) - 1) * p
    lo = int(i)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (i - lo)


class DetectorPresenca:
    def __init__(self, placas, ao_evento=None):
        self.placas = tuple(placas)
        self.amostras = {p: deque(maxlen=1800) for p in self.placas}
        self.linha_base = None
        self.calibracao = {'inicio': None, 'erro': None, 'concluida_em': None}
        self.decisao = {'candidato': None, 'desde': None, 'status': 'incerto', 'ultima_mudanca': None}
        self._ao_evento = ao_evento

    def adicionar(self, placa, agora, rssi, amplitude):
        if placa in self.amostras and rssi is not None and amplitude is not None:
            self.amostras[placa].append((agora, rssi, amplitude))

    def iniciar_calibracao(self, agora):
        if self.calibracao['inicio'] is not None:
            return False, 'Calibração já iniciada'
        self.linha_base = None
        self.calibracao.update(inicio=agora, erro=None, concluida_em=None)
        self.decisao.update(candidato=None, desde=None, status='incerto', ultima_mudanca=None)
        return True, None

    def _janela(self, placa, agora, inicio=None, fim=None):
        ini = agora - JANELA_S if inicio is None else inicio
        fim = agora if fim is None else fim
        vals = [(r, a) for t, r, a in self.amostras[placa] if ini <= t <= fim]
        if len(vals) < MIN_AMOSTRAS_JANELA:
            return None
        rr = [x[0] for x in vals]
        return {'n': len(vals), 'rssi': statistics.median(rr), 'rssi_std': statistics.pstdev(rr),
                'amp': statistics.median(x[1] for x in vals)}

    def _concluir_calibracao(self, agora):
        inicio = self.calibracao['inicio']
        refs = {}
        for placa in self.placas:
            # dez janelas de 5 s, ignorando os 10 s iniciais
            janelas = [w for k in range(10)
                       if (w := self._janela(placa, agora, inicio + 10 + k * 5, inicio + 15 + k * 5))]
            if len(janelas) < 8:
                self.calibracao['erro'] = 'Amostras insuficientes para ' + placa
                break
            med = statistics.median(w['rssi'] for w in janelas)
            amp = statistics.median(w['amp'] for w in janelas)
            ruido = statistics.median(w['rssi_std'] for w in janelas)
            refs[placa] = {
                'rssi': med, 'amp': amp, 'ruido': ruido,
                'lim_rssi': max(3.0, percentil([abs(w['rssi'] - med) for w in janelas], .95) * 2.0 + 1.0),
                'lim_amp': max(.75, percentil([abs(w['amp'] - amp) for w in janelas], .95) * 2.0 + .3),
                'lim_std': max(1.5, ruido * 2.5 + .5),
            }
        else:
            self.linha_base = refs
            self.calibracao['erro'] = None
            self.calibracao['concluida_em'] = datetime.now().strftime('%H:%M:%S')
            self.decisao.update(candidato=None, desde=None, status='incerto', ultima_mudanca=None)
            if self._ao_evento:
                self._ao_evento('ok', 'calibração do ambiente vazio concluída')
        if self.calibracao['erro'] and self._ao_evento:
            self._ao_evento('erro', 'calibração falhou: ' + self.calibracao['erro'])
        self.calibracao['inicio'] = None

    def avaliar(self, agora, online):
        """online: conjunto das placas com pacotes recentes."""
        inicio = self.calibracao['inicio']
        if inicio is not None and agora - inicio >= CALIBRACAO_S:
            self._concluir_calibracao(agora)
        if self.calibracao['inicio'] is not None:
            return {'status': 'calibrando', 'progresso': min(100, round((agora - inicio) * 100 / CALIBRACAO_S)),
                    'calibracao': False, 'detail': 'Calibrando: mantenha o quarto vazio. Aguarde a confirmação.'}
        if self.linha_base is None:
            return {'status': 'sem_calibracao', 'calibracao': False, 'calibrado_em': self.calibracao['concluida_em'],
                    'detail': self.calibracao['erro'] or 'Calibre com o quarto vazio'}

        nos = {}
        for placa in self.placas:
            if placa not in online:
                continue
            w = self._janela(placa, agora)
            if w is None:
                continue
            ref = self.linha_base[placa]
            indice = max(abs(w['rssi'] - ref['rssi']) / ref['lim_rssi'],
                         .5 * abs(w['amp'] - ref['amp']) / ref['lim_amp'],   # CSI é auxiliar
                         max(0.0, w['rssi_std'] - ref['ruido']) / ref['lim_std'])
            nos[placa] = {'rssi': round(w['rssi'], 2), 'rssi_vazio': round(ref['rssi'], 2),
                          'desvio_rssi': round(w['rssi'] - ref['rssi'], 2),
                          'variabilidade_rssi': round(w['rssi_std'], 2), 'variabilidade_vazio': round(ref['ruido'], 2),
                          'indice': round(indice, 2), 'amostras': w['n']}
        base = {'calibracao': True, 'calibrado_em': self.calibracao['concluida_em'], 'nos': nos}
        if len(nos) < 3:
            self.decisao.update(candidato=None, desde=None, status='incerto', ultima_mudanca=None)
            return dict(base, status='incerto', detail='Aguardando CSI de pelo menos 3 placas')

        maior = max(v['indice'] for v in nos.values())
        candidato = 'presenca' if maior >= LIMIAR_PRESENCA else ('vazio' if maior < LIMIAR_VAZIO else 'incerto')
        d = self.decisao
        if candidato != d['candidato']:
            d.update(candidato=candidato, desde=agora)
        exigido = CONFIRMA_PRESENCA_S if candidato == 'presenca' else CONFIRMA_VAZIO_S
        if candidato != 'incerto' and agora - d['desde'] >= exigido:
            if d['status'] != candidato:
                d['ultima_mudanca'] = datetime.now().strftime('%H:%M:%S')
            d['status'] = candidato
        transicao = candidato != d['status']
        fortes = sum(1 for v in nos.values() if v['indice'] >= 1.5)
        moderadas = sum(1 for v in nos.values() if v['indice'] >= 1.0)
        return dict(base, status=d['status'], candidato=candidato, nos_ativos=len(nos),
                    em_transicao=bool(d['status'] in ('vazio', 'presenca') and transicao),
                    confirmacao_restante_s=round(max(0, exigido - (agora - d['desde'])), 1)
                    if candidato != 'incerto' and transicao else None,
                    ultima_mudanca=d['ultima_mudanca'],
                    detail=f'Mudanças fortes: {fortes}; moderadas: {moderadas}; maior índice: {maior:.2f}. '
                           'Presença ≥1,0 por 5 s; vazio <0,6 por 8 s. Detector experimental.')


class LocalizadorProximidade:
    """Heurística de demonstração: não é triangulação nem medição de distância."""

    def __init__(self):
        self.estado = {'candidato': None, 'desde': None, 'confirmado': None}

    def avaliar(self, presenca, agora):
        if presenca['status'] != 'presenca':
            self.estado.update(candidato=None, desde=None, confirmado=None)
            return {'status': presenca['status'], 'regiao': None, 'nos': {}, 'criterio': 'Aguardando presença confirmada'}
        nos = presenca.get('nos', {})
        if len(nos) < 3:
            self.estado.update(candidato=None, desde=None, confirmado=None)
            return {'status': 'incerto', 'regiao': None, 'nos': nos, 'criterio': 'Aguardando sinais de 3 placas'}
        ranking = sorted(nos.items(), key=lambda kv: kv[1]['indice'], reverse=True)
        lider, topo = ranking[0]
        segundo = ranking[1][1]['indice'] if len(ranking) > 1 else 0
        dominante = topo['indice'] >= PERTO_INDICE and topo['indice'] - segundo >= PERTO_MARGEM
        candidato = 'perto_' + lider[-1].lower() if dominante else 'centro'
        if candidato != self.estado['candidato']:
            self.estado.update(candidato=candidato, desde=agora)
        if agora - self.estado['desde'] >= LOCAL_ESTAVEL_S:
            self.estado['confirmado'] = candidato
        regiao = self.estado['confirmado']
        return {'status': 'estimado' if regiao else 'estabilizando', 'regiao': regiao, 'nos': nos,
                'principal': lider, 'indice_principal': topo['indice'],
                'diferenca_indices': round(topo['indice'] - segundo, 2),
                'ganho_rssi_db': round(topo['desvio_rssi'], 2),
                'criterio': 'Maior índice de alteração com destaque sobre as demais unidades; centro sem destaque',
                'limiares': {'indice': PERTO_INDICE, 'margem': PERTO_MARGEM}, 'heuristico': True}

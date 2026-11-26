"""Teste guiado: sequência cronometrada que marca os rótulos e calibra sozinha.

O painel fala cada instrução em voz alta; a pessoa só segue a voz, sem tocar no
notebook. O CSV bruto recebe o rótulo de cada etapa, e o analise/avaliar_tempo_real.py
calibra no trecho "vazio_calibracao" e mede o resto.
"""

# (rótulo, segundos, instrução, ação)
ETAPAS = [
    ('nao_marcado', 20, 'Saia do quarto e fique do lado de fora. A calibração começa em 20 segundos.', None),
    ('vazio_calibracao', 70, 'Calibrando com o quarto vazio. Continue fora do quarto.', 'calibrar'),
    ('vazio', 30, 'Continue fora do quarto.', None),
    ('andando', 40, 'Entre e ande devagar por todo o quarto.', None),
    ('andando', 8, 'Vá até a placa A.', None),
    ('perto_a', 30, 'Fique parado perto da placa A.', None),
    ('andando', 8, 'Vá até a placa B.', None),
    ('perto_b', 30, 'Fique parado perto da placa B.', None),
    ('andando', 8, 'Vá até a placa C.', None),
    ('perto_c', 30, 'Fique parado perto da placa C.', None),
    ('andando', 8, 'Vá até a placa D.', None),
    ('perto_d', 30, 'Fique parado perto da placa D.', None),
    ('andando', 8, 'Vá até o centro, perto do iPhone, sem encostar nele.', None),
    ('centro', 30, 'Fique parado no centro, a um passo do iPhone, sem encostar nele nem na mesa.', None),
    ('vazio', 40, 'Saia do quarto e fique do lado de fora.', None),
]
DURACAO_TOTAL_S = sum(e[1] for e in ETAPAS)


class TesteGuiado:
    def __init__(self, sistema):
        self.sistema = sistema
        self.inicio = None
        self.indice = -1
        self.mensagem_final = None

    @property
    def ativo(self):
        return self.inicio is not None

    def iniciar(self):
        if self.ativo:
            return False, 'O teste já está em andamento'
        if len(self.sistema.online()) < len(self.sistema.placas):
            return False, 'As quatro placas precisam estar online'
        self.inicio = self.sistema.agora()
        self.indice = -1
        self.mensagem_final = None
        self.sistema.monitor.evento_servidor('info', f'teste guiado iniciado ({DURACAO_TOTAL_S // 60} min {DURACAO_TOTAL_S % 60} s)')
        self.atualizar()
        return True, None

    def parar(self, motivo='Teste interrompido.'):
        if self.ativo:
            self.inicio = None
            self.indice = -1
            self.mensagem_final = motivo
            self.sistema.rotular('nao_marcado')
            self.sistema.monitor.evento_servidor('aviso' if 'interromp' in motivo.lower() else 'ok', 'teste guiado: ' + motivo)

    def atualizar(self):
        """Chamado 10x/s pelo Sistema: troca de etapa, rótulo e dispara a calibração."""
        if not self.ativo:
            return
        decorrido = self.sistema.agora() - self.inicio
        acumulado = 0
        for i, (rotulo, dur, _, acao) in enumerate(ETAPAS):
            if decorrido < acumulado + dur:
                if i != self.indice:
                    self.indice = i
                    self.sistema.rotular(rotulo)
                    if acao == 'calibrar':
                        ok, erro = self.sistema.calibrar()
                        if not ok:
                            self.parar(f'Teste interrompido: calibração falhou ({erro}).')
                return
            acumulado += dur
        self.parar('Teste concluído. Rode o avaliador no CSV desta sessão.')

    def info(self):
        if not self.ativo:
            return {'ativo': False, 'mensagem': self.mensagem_final, 'duracao_total_s': DURACAO_TOTAL_S}
        decorrido = self.sistema.agora() - self.inicio
        acumulado = sum(e[1] for e in ETAPAS[:self.indice])
        rotulo, dur, instrucao, _ = ETAPAS[self.indice]
        return {'ativo': True, 'etapa': self.indice + 1, 'total': len(ETAPAS), 'instrucao': instrucao,
                'rotulo': rotulo, 'restante_s': round(max(0.0, acumulado + dur - decorrido), 1),
                'restante_total_s': round(max(0.0, DURACAO_TOTAL_S - decorrido)),
                'proxima': ETAPAS[self.indice + 1][2] if self.indice + 1 < len(ETAPAS) else 'Fim do teste.'}

import random
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from smartroom.nucleo import Relogio, Sistema  # noqa: E402


class RelogioManual(Relogio):
    """Relógio controlado pelo teste."""

    def __init__(self, inicio=1000.0):
        super().__init__()
        self.t = inicio

    def agora(self):
        return self.t


@pytest.fixture
def relogio():
    return RelogioManual()


@pytest.fixture
def sistema(tmp_path, relogio):
    s = Sistema(gravar_csv=False, relogio=relogio, imprimir=None,
                arquivo_placas=tmp_path / 'placas.json', pasta_dados=tmp_path)
    yield s
    s.fechar()


def csi_aleatorio(amplitude=10, semente=None):
    rnd = random.Random(semente)
    return bytes((rnd.randint(-amplitude, amplitude) or 1) & 0xFF for _ in range(128))


def porta_livre(tipo=socket.SOCK_DGRAM):
    s = socket.socket(socket.AF_INET, tipo)
    s.bind(('127.0.0.1', 0))
    porta = s.getsockname()[1]
    s.close()
    return porta

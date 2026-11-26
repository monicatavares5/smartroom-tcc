"""Registro MAC → ID da placa (ESP_A…ESP_D), salvo em software/placas.json.

O firmware é o mesmo nas 4 placas; quem diz qual é qual é este arquivo. O script de
gravação registra o MAC de cada placa gravada. Um MAC desconhecido recebe o primeiro
ID livre (e é salvo), com um aviso para conferir a posição.
"""
import json
import re
import threading
from pathlib import Path

_MAC = re.compile(r'([0-9a-f]{2}:){5}[0-9a-f]{2}')


class RegistroPlacas:
    def __init__(self, arquivo: Path, ids, ao_atribuir=None):
        self._arquivo = Path(arquivo)
        self._ids = tuple(ids)
        self._lock = threading.Lock()
        self._ao_atribuir = ao_atribuir
        self._mapa = self._carregar()

    def _carregar(self):
        try:
            dados = json.loads(self._arquivo.read_text(encoding='utf-8'))
            return {str(k).lower(): v for k, v in dados.items() if v in self._ids}
        except (OSError, ValueError):
            return {}

    def _salvar(self):
        try:
            self._arquivo.write_text(json.dumps(self._mapa, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        except OSError:
            pass

    def resolver(self, mac: str):
        """ID da placa para este MAC (atribui um livre se for novo). None se todos ocupados."""
        mac = str(mac).lower()
        if not _MAC.fullmatch(mac):
            return None
        with self._lock:
            if mac in self._mapa:
                return self._mapa[mac]
            livres = [i for i in self._ids if i not in self._mapa.values()]
            if not livres:
                return None
            self._mapa[mac] = livres[0]
            self._salvar()
            novo = livres[0]
        if self._ao_atribuir:
            self._ao_atribuir(novo, mac)
        return novo

    def mac_de(self, placa: str):
        with self._lock:
            return next((m for m, p in self._mapa.items() if p == placa), None)

    def mapa(self):
        with self._lock:
            return dict(self._mapa)

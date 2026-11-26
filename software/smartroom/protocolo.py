"""Leitura e montagem dos pacotes das placas (ver docs/protocolo.md).

v2: binário little-endian com cabeçalho de 12 bytes (identifica a placa pelo MAC).
v1: JSON legado (identifica a placa por esp_id).
"""
import json
import struct
from dataclasses import dataclass, field
from typing import Optional

MAGIC = b'SR'
VERSAO = 2
TIPO_CSI = 1
TIPO_STATUS = 2

CABECALHO = struct.Struct('<2sBB6sH')            # magic, versao, tipo, mac, tamanho
CSI = struct.Struct('<IIbBBBBBH')                 # seq, t_ms, rssi, sig_mode, mcs, canal, fwi, reservado, csi_len
STATUS = struct.Struct('<IIIIbBBB24s')            # uptime, quedas, erros, heap, rssi_ap, motivo, ota, reservado, versao
MAX_CSI = 384

MOTIVOS_REINICIO = {0: 'outro', 1: 'energia', 2: 'software', 3: 'erro_fatal', 4: 'watchdog',
                    5: 'queda_de_tensao', 6: 'deep_sleep', 7: 'pino_reset'}


class PacoteInvalido(ValueError):
    pass


@dataclass
class Amostra:
    """Uma amostra de CSI de uma placa."""
    seq: Optional[int]
    rssi: Optional[int]
    csi: bytes = b''                 # bytes crus (int8 armazenado como u8)
    mcs: Optional[int] = None
    sig_mode: Optional[int] = None
    canal: Optional[int] = None
    first_word_invalid: bool = False
    t_placa_ms: Optional[int] = None
    csi_len_declarado: Optional[int] = None
    mac: Optional[str] = None        # v2
    esp_id: Optional[str] = None     # v1
    versao: int = 2

    @property
    def csi_valido(self) -> bool:
        n = len(self.csi)
        declarado = self.csi_len_declarado if self.csi_len_declarado is not None else n
        return 0 < n <= 512 and n <= declarado


@dataclass
class Status:
    """Telemetria periódica do firmware."""
    dados: dict = field(default_factory=dict)
    mac: Optional[str] = None
    esp_id: Optional[str] = None


def mac_texto(mac: bytes) -> str:
    return ':'.join(f'{b:02x}' for b in mac)


def mac_bytes(texto: str) -> bytes:
    return bytes(int(p, 16) for p in texto.split(':'))


def ler(data: bytes):
    """Devolve Amostra ou Status. Lança PacoteInvalido se não reconhecer."""
    if data[:2] == MAGIC:
        return _ler_v2(data)
    if data[:1] == b'{':
        return _ler_v1(data)
    raise PacoteInvalido('formato desconhecido')


def _ler_v2(data):
    if len(data) < CABECALHO.size:
        raise PacoteInvalido('cabeçalho curto')
    _, versao, tipo, mac, tamanho = CABECALHO.unpack_from(data)
    if versao != VERSAO:
        raise PacoteInvalido(f'versão {versao} não suportada')
    corpo = data[CABECALHO.size:CABECALHO.size + tamanho]
    if len(corpo) != tamanho:
        raise PacoteInvalido('payload truncado')
    mac_t = mac_texto(mac)
    if tipo == TIPO_CSI:
        if tamanho < CSI.size:
            raise PacoteInvalido('CSI curto')
        seq, t_ms, rssi, sig_mode, mcs, canal, fwi, _, csi_len = CSI.unpack_from(corpo)
        csi = corpo[CSI.size:CSI.size + csi_len]
        if len(csi) != csi_len or csi_len > MAX_CSI:
            raise PacoteInvalido('tamanho do CSI inconsistente')
        return Amostra(seq=seq, rssi=rssi, csi=bytes(csi), mcs=mcs, sig_mode=sig_mode, canal=canal,
                       first_word_invalid=bool(fwi), t_placa_ms=t_ms, csi_len_declarado=csi_len,
                       mac=mac_t, versao=2)
    if tipo == TIPO_STATUS:
        if tamanho < STATUS.size:
            raise PacoteInvalido('STATUS curto')
        up, quedas, erros, heap, rssi_ap, motivo, ota, _, versao_fw = STATUS.unpack_from(corpo)
        return Status(mac=mac_t, dados={
            'uptime_s': up, 'wifi_quedas': quedas, 'erros_envio': erros, 'heap_livre': heap,
            'rssi_ap': rssi_ap, 'motivo_reinicio': MOTIVOS_REINICIO.get(motivo, 'outro'),
            'ota_estado': ota, 'versao_fw': versao_fw.split(b'\0', 1)[0].decode('ascii', 'replace'),
        })
    raise PacoteInvalido(f'tipo {tipo} desconhecido')


def _ler_v1(data):
    try:
        m = json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, ValueError) as exc:
        raise PacoteInvalido('JSON inválido') from exc
    if not isinstance(m, dict) or 'esp_id' not in m:
        raise PacoteInvalido('JSON sem esp_id')
    esp_id = str(m['esp_id'])
    if m.get('tipo') == 'status':
        return Status(esp_id=esp_id, dados={k: v for k, v in m.items() if k not in ('esp_id', 'tipo')})
    csi = b''
    if m.get('csi_encoding') == 'hex_u8' and isinstance(m.get('csi'), str):
        try:
            csi = bytes.fromhex(m['csi'])
        except ValueError:
            csi = b''
    def inteiro(chave):
        try:
            return int(m[chave]) if m.get(chave) is not None else None
        except (TypeError, ValueError):
            return None
    return Amostra(seq=inteiro('seq'), rssi=inteiro('rssi'), csi=csi, mcs=inteiro('mcs'),
                   sig_mode=inteiro('sig_mode'), canal=inteiro('channel'),
                   first_word_invalid=str(m.get('first_word_invalid', 0)).lower() in ('1', 'true'),
                   csi_len_declarado=inteiro('csi_len') if m.get('csi_len') is not None else 0,
                   esp_id=esp_id, versao=1)


# ------------------------------------------------------------------ montagem (testes, replay, simulador)
def montar_csi(mac: str, seq: int, rssi: int, csi: bytes, *, t_ms=0, sig_mode=1, mcs=0, canal=6, fwi=0) -> bytes:
    corpo = CSI.pack(seq, t_ms, rssi, sig_mode, mcs, canal, fwi, 0, len(csi)) + csi
    return CABECALHO.pack(MAGIC, VERSAO, TIPO_CSI, mac_bytes(mac), len(corpo)) + corpo


def montar_status(mac: str, *, uptime_s=0, wifi_quedas=0, erros_envio=0, heap_livre=0, rssi_ap=0,
                  motivo=1, ota_estado=0, versao_fw='2.0.0') -> bytes:
    corpo = STATUS.pack(uptime_s, wifi_quedas, erros_envio, heap_livre, rssi_ap, motivo, ota_estado, 0,
                        versao_fw.encode('ascii')[:24])
    return CABECALHO.pack(MAGIC, VERSAO, TIPO_STATUS, mac_bytes(mac), len(corpo)) + corpo

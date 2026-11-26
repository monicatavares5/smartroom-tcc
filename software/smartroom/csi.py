"""Características extraídas do CSI.

Hipótese de decodificação (a mesma usada desde a V5): 128 bytes = 64 pares (imag, real)
em int8. Quando first_word_invalid, as primeiras amostras são descartadas.
"""
import math
import statistics


def _pares(raw: bytes):
    signed = [b - 256 if b >= 128 else b for b in raw]
    return [math.hypot(signed[i], signed[i + 1]) for i in range(0, len(signed) - 1, 2)]


def amplitude_media(raw: bytes, first_word_invalid=False):
    """Média das amplitudes não nulas (usada pelo detector de presença)."""
    if len(raw) != 128:
        return None
    if first_word_invalid:
        raw = bytes(4) + raw[4:]
    amps = [a for a in _pares(raw) if a > 0]
    return sum(amps) / len(amps) if amps else None


def espectro(raw: bytes, first_word_invalid=False):
    """Amplitude por subportadora (None = inválida/nula)."""
    if len(raw) != 128:
        return None
    amps = _pares(raw)
    if first_word_invalid:
        amps[:2] = [None, None]
    return [None if a is None or a == 0 else round(a, 4) for a in amps]


def metricas_espectro(spec, historico, mcs):
    """Métricas do espectro; mudança temporal compara com o último pacote do mesmo MCS."""
    bons = [v for v in spec if v is not None]
    if len(bons) < 16:
        return None
    anterior = next((sp for m, sp in reversed(historico) if m == mcs), None)
    mudancas = [abs(a - b) for a, b in zip(spec, anterior or []) if a is not None and b is not None]
    temporal = statistics.mean(mudancas) if mudancas else None
    return {'amplitude_media': round(statistics.mean(bons), 4),
            'variacao_espectral': round(statistics.pstdev(bons), 4),
            'mudanca_temporal': round(temporal, 4) if temporal is not None else None,
            'n_pares_validos': len(bons)}

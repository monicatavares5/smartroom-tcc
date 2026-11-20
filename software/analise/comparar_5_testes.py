import csv
import math
import statistics
from collections import defaultdict, Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent / 'testes_presenca'
TESTES = [
    ('01', 'VAZIO ', 'teste_01_quarto_vazio.csv'),
    ('02', 'CENTRO', 'teste_02_pessoa_centro.csv'),
    ('03', 'VAZIO ', 'teste_03_quarto_vazio.csv'),
    ('04', 'CENTRO', 'teste_04_pessoa_centro.csv'),
    ('05', 'VAZIO ', 'teste_05_quarto_vazio.csv'),
]

def mean(x):
    return statistics.mean(x) if x else float('nan')

def sd(x):
    return statistics.pstdev(x) if len(x) > 1 else float('nan')

def fmt(x):
    return f'{x:.2f}' if math.isfinite(x) else 'n/d'

def load(path):
    out = defaultdict(list)
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            try:
                if row.get('csi_valido', '').strip().lower() not in ('true', '1'):
                    continue
                raw = bytes.fromhex(row['csi_hex'])
                if len(raw) != int(row['csi_len']) or len(raw) != int(row['csi_bytes_recebidos']) or len(raw) % 2:
                    continue
                signed = [v if v < 128 else v - 256 for v in raw]
                amps = [math.hypot(signed[i], signed[i + 1]) for i in range(0, len(signed), 2)]
                out[row['esp_id']].append({
                    'rssi': float(row['rssi']),
                    'amp': mean(amps),
                    'format': (len(raw), row.get('sig_mode', ''), row.get('mcs', ''), row.get('channel', '')),
                })
            except (ValueError, TypeError, KeyError, OverflowError):
                continue
    return out

def main():
    missing = [name for _, _, name in TESTES if not (BASE / name).is_file()]
    if missing:
        raise SystemExit('Arquivos ausentes em ' + str(BASE) + ':\n' + '\n'.join(missing))
    all_data = [(number, scenario, load(BASE / name)) for number, scenario, name in TESTES]
    esps = sorted(set().union(*(set(d) for _, _, d in all_data)))
    print('SMARTROOM | 5 SESSOES | ANALISE EXPLORATORIA')
    print('RSSI em dBm; amplitudes CSI em unidades arbitrarias. Nao comprova deteccao.')
    for esp in esps:
        print('\n' + '=' * 75)
        print(esp)
        print('Teste | Cenario | Amostras | RSSI media (DP) | Amplitude media (DP) | Formato dominante (n)')
        for number, scenario, data in all_data:
            samples = data.get(esp, [])
            rssis = [r['rssi'] for r in samples]
            amps = [r['amp'] for r in samples]
            dominant = Counter(r['format'] for r in samples).most_common(1)
            form = f'{dominant[0][0]} ({dominant[0][1]}/{len(samples)})' if dominant else 'n/d'
            print(f'{number:>5} | {scenario:>7} | {len(samples):>8} | '
                  f'{fmt(mean(rssis)):>6} ({fmt(sd(rssis)):>5}) | '
                  f'{fmt(mean(amps)):>7} ({fmt(sd(amps)):>5}) | {form}')
        empty = [mean([r['rssi'] for r in data.get(esp, [])]) for _, scenario, data in all_data if scenario.strip() == 'VAZIO']
        center = [mean([r['rssi'] for r in data.get(esp, [])]) for _, scenario, data in all_data if scenario.strip() == 'CENTRO']
        if all(math.isfinite(v) for v in empty + center):
            print(f'Medias das sessoes: vazio={fmt(mean(empty))} dBm | centro={fmt(mean(center))} dBm | diferenca={fmt(mean(center)-mean(empty))} dB')
            print(f'Retorno no teste 05 vs teste 01: {fmt(empty[-1]-empty[0])} dB')
    print('\nATENCAO: Mudancas de MCS, variacoes entre sessoes e geometria do roteador podem confundir resultados.')
    print('Para validar, repita em outros dias e teste dados de sessoes independentes.')

if __name__ == '__main__':
    main()

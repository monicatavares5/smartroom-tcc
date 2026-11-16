import csv, math, statistics, collections
from pathlib import Path

BASE = Path(__file__).resolve().parent / 'dados_csi'
ARQUIVOS = {'vazio': BASE/'teste_01_quarto_vazio.csv', 'centro': BASE/'teste_02_pessoa_centro.csv'}

def media(xs): return statistics.mean(xs) if xs else float('nan')
def desvio(xs): return statistics.pstdev(xs) if len(xs)>1 else float('nan')
def fmt(x): return f'{x:.3f}' if math.isfinite(x) else 'n/d'

def ler(path):
    dados=collections.defaultdict(list)
    with path.open(newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            try:
                if row.get('csi_valido','').lower() not in ('true','1'): continue
                raw=bytes.fromhex(row['csi_hex'])
                if not raw or len(raw)!=int(row['csi_bytes_recebidos']): continue
                if len(raw)!=int(row['csi_len']): continue
                vals=[b if b<128 else b-256 for b in raw]
                # CSI intercalado: pares real/imag; amplitude por subportadora.
                amps=[math.hypot(vals[i], vals[i+1]) for i in range(0,len(vals)-1,2)]
                if not amps: continue
                dados[row['esp_id']].append({'rssi':float(row['rssi']), 'amp':media(amps), 'amp_sd':desvio(amps), 'len':len(raw), 'sig_mode':row.get('sig_mode'), 'mcs':row.get('mcs'), 'channel':row.get('channel')})
            except (ValueError, KeyError, TypeError): continue
    return dados

def main():
    for nome,p in ARQUIVOS.items():
        if not p.exists(): raise SystemExit(f'Arquivo não encontrado: {p}\nColoque este script em C:\\TCC\\SmartRoom\\software')
    resultados={nome:ler(p) for nome,p in ARQUIVOS.items()}
    print('SMARTROOM — COMPARAÇÃO EXPLORATÓRIA (não é um detector validado)')
    print('Os dados de cada cenário foram coletados em sessões distintas.')
    for esp in sorted(set().union(*(set(d) for d in resultados.values()))):
        print(f'\n{esp}')
        for c in ('vazio','centro'):
            amostras=resultados[c].get(esp,[])
            rssi=[a['rssi'] for a in amostras]; amp=[a['amp'] for a in amostras]
            modos=collections.Counter((a['len'],a['sig_mode'],a['mcs'],a['channel']) for a in amostras)
            print(f'  {c:6s}: n={len(amostras):5d} | RSSI média={fmt(media(rssi))} dBm, dp={fmt(desvio(rssi))} | amplitude média={fmt(media(amp))}, dp temporal={fmt(desvio(amp))}')
            if modos: print(f'          formato predominante (len,sig_mode,mcs,canal): {modos.most_common(1)[0][0]} ({modos.most_common(1)[0][1]}/{len(amostras)})')
        a=resultados['vazio'].get(esp,[]); b=resultados['centro'].get(esp,[])
        if a and b:
            print(f"  diferença centro-vazio: RSSI={fmt(media([x['rssi'] for x in b])-media([x['rssi'] for x in a]))} dB | amplitude={fmt(media([x['amp'] for x in b])-media([x['amp'] for x in a]))}")
    print('\nATENÇÃO: diferenças entre sessões não provam presença. É necessário repetir coletas, controlar condições e avaliar em dados independentes.')

if __name__=='__main__': main()

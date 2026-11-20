"""SmartRoom V5: analise exploratoria de CSI por posicao (sem treino/validacao)."""
import csv, math, statistics as st
from collections import defaultdict, Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / 'calibracao'
ARQUIVOS = [
 ('Vazio inicial', 'vazio_inicio.csv'),
 ('Perto A', 'perto_esp_a.csv'),
 ('Perto B', 'perto_esp_b.csv'),
 ('Perto C', 'perto_esp_c.csv'),
 ('Perto D', 'perto_esp_d.csv'),
 ('Centro', 'centro.csv'),
 ('Vazio final', 'vazio_fim.csv'),
]
NODES = ('ESP_A','ESP_B','ESP_C','ESP_D')
OUT = Path(__file__).resolve().parent / 'relatorio_calibracao.csv'

def mean(xs): return sum(xs)/len(xs) if xs else float('nan')
def std(xs): return st.pstdev(xs) if len(xs)>1 else 0.0

def ler(p):
    groups=defaultdict(list)
    with p.open(newline='',encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            try:
                if str(r['csi_valido']).lower()!='true' or r['csi_encoding']!='hex_u8': continue
                raw=bytes.fromhex(r['csi_hex'])
                if len(raw)!=128: continue
                vals=[v-256 if v>=128 else v for v in raw]
                # ESP CSI: pares I/Q assinados. Primeira palavra pode ser invalida.
                if str(r.get('first_word_invalid','0')) in ('1','True','true'):
                    vals[:4]=[0]*4
                amps=[math.hypot(vals[i],vals[i+1]) for i in range(0,128,2)]
                # Eliminar subportadoras sem energia (zeros/padding); nao confundir com CSI real.
                valid=[v for v in amps if v>0]
                groups[r['esp_id']].append({
                    'rssi':float(r['rssi']), 'mcs':r['mcs'], 'sig':r['sig_mode'],
                    'channel':r['channel'], 'amps':amps,
                    'amp_media':mean(valid) if valid else 0,
                    'energia':mean([v*v for v in valid]) if valid else 0,
                })
            except (ValueError,KeyError,IndexError,TypeError): continue
    return groups

def resumo(rows):
    if not rows: return None
    mcs=Counter((r['sig'],r['mcs'],r['channel']) for r in rows)
    am=[r['amp_media'] for r in rows]
    # Variacao temporal por subportadora, ignorando as que sao zero em quase todas as amostras
    cols=list(zip(*(r['amps'] for r in rows)))
    variacoes=[std([x for x in col if x>0]) for col in cols if sum(x>0 for x in col)>=0.8*len(col)]
    return dict(n=len(rows),rssi=mean([r['rssi'] for r in rows]),rssi_dp=std([r['rssi'] for r in rows]),
                amp=mean(am),amp_dp=std(am),energia=mean([r['energia'] for r in rows]),
                var_sub=mean(variacoes),mcs=mcs.most_common(1)[0][0],mcs_frac=mcs.most_common(1)[0][1]/len(rows))

def main():
    dados={}
    for nome,arq in ARQUIVOS:
        path=BASE/arq
        if not path.exists():
            print('FALTANDO:',path)
            print('Coloque este script na mesma pasta dos 7 CSVs.');return
        dados[nome]=ler(path)
    base={n:resumo(dados['Vazio inicial'][n]) for n in NODES}
    print('SMARTROOM V5 | CSI POR POSICAO | ANALISE EXPLORATORIA')
    print('CSI: magnitude de pares I/Q int8; unidades arbitrarias. Sem classificador validado.')
    resultado=[]
    for node in NODES:
        print('\n'+node)
        print('Cenario         n   RSSI    dRSSI   Amp CSI  dAmp   DP temporal  Var subport.  MCS dom. (%)')
        for nome,_ in ARQUIVOS:
            r=resumo(dados[nome][node]); b=base[node]
            if not r: continue
            dr=r['rssi']-b['rssi']; da=r['amp']-b['amp']
            print(f"{nome:13} {r['n']:4} {r['rssi']:7.2f} {dr:+7.2f} {r['amp']:8.2f} {da:+6.2f} {r['amp_dp']:11.2f} {r['var_sub']:12.2f}  {r['mcs'][1]:>3} ({r['mcs_frac']*100:.0f}%)")
            resultado.append(dict(esp=node,cenario=nome,amostras=r['n'],rssi_medio=round(r['rssi'],4),delta_rssi=round(dr,4),amplitude_csi=round(r['amp'],4),delta_amplitude=round(da,4),dp_temporal_amplitude=round(r['amp_dp'],4),variacao_subportadoras=round(r['var_sub'],4),mcs_dominante=r['mcs'][1],percentual_mcs=round(100*r['mcs_frac'],1)))
    with OUT.open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=list(resultado[0]));w.writeheader();w.writerows(resultado)
    print('\nRelatorio salvo em:',OUT)
    print('ATENCAO: delta entre sessoes pode refletir MCS, trafego Wi-Fi e deriva temporal.')
    print('Nao e possivel medir acuracia ou localizar pessoas com apenas uma sessao por posicao.')

if __name__=='__main__':main()

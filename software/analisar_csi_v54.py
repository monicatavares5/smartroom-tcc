"""SmartRoom V5.4: analise exploratoria offline, SEM classificador validado.
Uso: py analisar_csi_v54.py caminho_do_csv
Dependencias: py -m pip install pandas numpy matplotlib
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

INPUT = Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).with_name('smartroom_v53_rotulado_20261031_221048.csv')
OUT = INPUT.parent / ('analise_v54_' + INPUT.stem)
OUT.mkdir(exist_ok=True)


def spectrum(h):
    try:
        b=bytes.fromhex(h)
        if len(b)!=128: return np.full(64,np.nan)
        z=np.frombuffer(b,dtype=np.int8).astype(float).reshape(-1,2)
        a=np.linalg.norm(z,axis=1)
        a[a==0]=np.nan
        # Sem flag first_word_invalid no CSV V5.3; nao pressupor indices fisicos.
        return a
    except (TypeError,ValueError):
        return np.full(64,np.nan)


def main():
    d=pd.read_csv(INPUT)
    required={'timestamp','esp_id','rotulo','rssi','mcs','csi_hex'}
    if not required.issubset(d): raise ValueError('Colunas ausentes: '+str(required-set(d)))
    d['timestamp']=pd.to_datetime(d['timestamp'])
    d=d.sort_values('timestamp').reset_index(drop=True)
    # Segmentos contiguos, evitando misturar os dois periodos de quarto vazio.
    d['segmento']=d['rotulo'].ne(d['rotulo'].shift()).cumsum()
    seg=d.groupby('segmento').agg(rotulo=('rotulo','first'),inicio=('timestamp','min'),fim=('timestamp','max'),amostras=('rotulo','size'))
    seg['duracao_s']=(seg['fim']-seg['inicio']).dt.total_seconds()
    seg.to_csv(OUT/'segmentos.csv',encoding='utf-8-sig')
    d['segundo']=(d['timestamp']-d['timestamp'].min()).dt.total_seconds()
    spectra=np.stack([spectrum(v) for v in d['csi_hex']])
    d['amp_recalculada']=np.nanmean(spectra,axis=1)
    d['var_espectral_recalculada']=np.nanstd(spectra,axis=1)
    # Resumo por segmento/no/MCS, sem misturar MCS.
    summary=d.groupby(['segmento','rotulo','esp_id','mcs']).agg(n=('rssi','size'),rssi_medio=('rssi','mean'),rssi_dp=('rssi','std'),amplitude_media=('amp_recalculada','mean'),variacao_espectral=('var_espectral_recalculada','mean')).reset_index()
    summary.to_csv(OUT/'resumo_por_mcs.csv',index=False,encoding='utf-8-sig')
    # Tabela com comparacao estritamente no mesmo MCS e >= 15 amostras por classe.
    comparisons=[]
    for node in sorted(d['esp_id'].unique()):
        for mcs in sorted(d.loc[d.esp_id==node,'mcs'].unique()):
            idx0=np.where((d.esp_id==node)&(d.rotulo=='vazio')&(d.mcs==mcs))[0]
            idx1=np.where((d.esp_id==node)&(d.rotulo=='centro')&(d.mcs==mcs))[0]
            if min(len(idx0),len(idx1))<15: continue
            a=spectra[idx0];b=spectra[idx1]
            med0=np.nanmedian(a,axis=0);med1=np.nanmedian(b,axis=0)
            valid=np.isfinite(med0)&np.isfinite(med1)
            comparisons.append(dict(esp_id=node,mcs=mcs,n_vazio=len(idx0),n_centro=len(idx1),
                delta_rssi=d.loc[idx1,'rssi'].mean()-d.loc[idx0,'rssi'].mean(),
                delta_amp_mediana=float(np.nanmedian(med1[valid]-med0[valid])),
                distancia_espectral_media=float(np.nanmean(abs(med1[valid]-med0[valid]))),
                pares_comparaveis=int(valid.sum())))
            fig,ax=plt.subplots(figsize=(11,4))
            ax.plot(range(64),med0,label=f'Vazio (n={len(idx0)})')
            ax.plot(range(64),med1,label=f'Centro (n={len(idx1)})')
            ax.set(title=f'{node} | MCS {mcs} | Espectro mediano por rotulo',xlabel='Indice do par I/Q (nao mapeado fisicamente)',ylabel='Magnitude CSI (unidades arbitrarias)')
            ax.legend();fig.tight_layout();fig.savefig(OUT/f'espectro_{node}_mcs{mcs}.png',dpi=150);plt.close(fig)
    pd.DataFrame(comparisons).to_csv(OUT/'comparacao_mesmo_mcs.csv',index=False,encoding='utf-8-sig')
    fig,axs=plt.subplots(4,1,figsize=(13,10),sharex=True)
    for ax,node in zip(axs,sorted(d.esp_id.unique())):
        sub=d[d.esp_id==node]
        ax.scatter(sub['segundo'],sub['rssi'],s=7,alpha=.55)
        ax.set_ylabel(f'{node}\nRSSI (dBm)')
        for seg_id,r in seg.iterrows():
            if r.rotulo=='nao_marcado':continue
            x0=(r.inicio-d.timestamp.min()).total_seconds();x1=(r.fim-d.timestamp.min()).total_seconds()
            ax.axvspan(x0,x1,alpha=.10 if r.rotulo=='vazio' else .20)
            if node=='ESP_A':ax.text((x0+x1)/2,ax.get_ylim()[1],f'{r.rotulo} {seg_id}',ha='center',va='top',fontsize=8)
    axs[-1].set_xlabel('Segundos desde o inicio')
    fig.suptitle('RSSI por ESP32 e periodos rotulados (faixas: vazio/centro)')
    fig.tight_layout();fig.savefig(OUT/'linha_do_tempo_rssi.png',dpi=150);plt.close(fig)
    # Serie temporal da mudanca CSI entre pacotes consecutivos do mesmo MCS.
    d['mudanca_csi_mesmo_mcs']=np.nan
    for (node,mcs),g in d.groupby(['esp_id','mcs']):
        ix=g.index.to_numpy()
        for prev,cur in zip(ix[:-1],ix[1:]):
            if d.loc[cur,'segundo']-d.loc[prev,'segundo']>5:continue
            x=spectra[prev];y=spectra[cur];valid=np.isfinite(x)&np.isfinite(y)
            if valid.sum()>=16:d.loc[cur,'mudanca_csi_mesmo_mcs']=np.mean(np.abs(x[valid]-y[valid]))
    d.drop(columns=['csi_hex']).to_csv(OUT/'medicoes_com_metricas.csv',index=False,encoding='utf-8-sig')
    print('SMARTROOM V5.4 | ANALISE EXPLORATORIA, NAO VALIDACAO')
    print('\nSegmentos:');print(seg.to_string())
    print('\nComparacoes no mesmo MCS (minimo 15 amostras por classe):')
    print(pd.DataFrame(comparisons).to_string(index=False) if comparisons else 'Nenhuma comparacao com amostras suficientes')
    print('\nArquivos gerados em:',OUT)
    print('IMPORTANTE: apenas um ciclo ocupado; nao inferir acuracia, nem causalidade.')

if __name__=='__main__':main()

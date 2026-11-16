import json
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path
import socket
import threading
import time
from collections import deque, Counter

# Modelo opcional de localizacao. O detector de presenca nao depende dele.
try:
    import joblib
    import pandas as pd
except ImportError:
    joblib = None
    pd = None
import argparse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UDP_PORT = 5005
HTTP_PORT = 8080
NODES = ('ESP_A', 'ESP_B', 'ESP_C', 'ESP_D')
TIMEOUT_SECONDS = 5
# Detector exploratorio calibrado para pessoa parada no centro.
DETECTOR_NODE = 'ESP_C'  # Referencia da V4; V5 utiliza todos os nos
DETECTOR_WINDOW = 20
DETECTOR_MIN_SAMPLES = 8
DETECTOR_OCCUPIED_BELOW = -52.5
DETECTOR_EMPTY_ABOVE = -51.5
DATA_DIR = Path(__file__).resolve().parent / 'dados_csi'
DATA_DIR.mkdir(parents=True, exist_ok=True)
CSV_PATH = DATA_DIR / ('smartroom_csi_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.csv')
CSV_COLUMNS = ['timestamp','esp_id','ip','seq','rssi','csi_len','sig_mode','mcs','channel','csi_mean_abs','first_word_invalid','csi_encoding','csi_hex','csi_bytes_recebidos','csi_valido']
lock = threading.Lock()
state = {name: {'last_seen': 0, 'ip': None, 'rssi': None, 'seq': None, 'packets': 0, 'history': deque(maxlen=100), 'detector_rssi': deque(maxlen=DETECTOR_WINDOW), 'samples': deque(maxlen=35), 'last_mcs': None, 'spectra': deque(maxlen=80), 'localization': deque(maxlen=240)} for name in NODES}

# Arquivos de calibracao coletados no Experimento 03.
CAL_DIR = Path(__file__).resolve().parent / 'experimento_03'
CAL_FILES = {
    'vazio_inicio': 'Quarto vazio.csv',
    'perto_a': 'Próximo à ESP_A.csv',
    'perto_b': 'Próximo à ESP_B.csv',
    'perto_c': 'Próximo à ESP_C.csv',
    'perto_d': 'Próximo à ESP_D.csv',
    'centro': 'Centro do quarto.csv',
    'vazio_fim': 'Quarto vazio novamente.csv',
}
LABELS = {'vazio_inicio':'Vazio', 'vazio_fim':'Vazio', 'perto_a':'Perto A',
          'perto_b':'Perto B','perto_c':'Perto C','perto_d':'Perto D','centro':'Centro'}
calibration = {}
calibration_error = None

def csi_amplitude(raw, invalid=False):
    if len(raw) != 128:
        return None
    signed = [b - 256 if b >= 128 else b for b in raw]
    if invalid:
        signed[:4] = [0] * 4
    amps = [math.hypot(signed[i], signed[i+1]) for i in range(0, 128, 2)]
    nonzero = [x for x in amps if x > 0]
    return sum(nonzero)/len(nonzero) if nonzero else None

# Analise CSI exploratoria: 128 bytes = 64 pares I/Q, conforme hipotese
# de decodificacao usada no V5. Esta ordem deve ser validada com o firmware.
# As quatro primeiras amostras sao descartadas quando first_word_invalid.
SPECTRAL_DIR = Path(__file__).resolve().parent / 'dados_csi'
SPECTRAL_CSV = SPECTRAL_DIR / ('smartroom_v53_rotulado_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.csv')
LABEL_OPTIONS = {'nao_marcado','vazio','centro','perto_a','perto_b','perto_c','perto_d'}
label_state = {'label':'nao_marcado','since':time.monotonic()}
SPECTRAL_COLUMNS = ['timestamp','esp_id','seq','rotulo','segundos_rotulo','rssi','mcs','channel','n_pares_validos','amplitude_media','variacao_espectral','mudanca_temporal','csi_hex']

def csi_spectrum(raw, invalid=False):
    if len(raw) != 128:
        return None
    signed = [v-256 if v>=128 else v for v in raw]
    spectrum = [math.hypot(signed[i],signed[i+1]) for i in range(0,128,2)]
    if invalid:
        spectrum[:2] = [None,None]
    # Descartar zeros; podem ser subportadoras nulas ou sem informacao.
    return [None if x is None or x==0 else round(x,4) for x in spectrum]

def spectrum_metrics(spectrum, history, mcs):
    good=[v for v in spectrum if v is not None]
    if len(good)<16:
        return None
    mean=statistics.mean(good)
    spread=statistics.pstdev(good)
    # Compara apenas pacotes do mesmo MCS e subportadoras validas.
    previous=next((sp for old_mcs,sp in reversed(history) if old_mcs==mcs),None)
    changes=[]
    if previous is not None:
        for a,b in zip(spectrum,previous):
            if a is not None and b is not None:
                changes.append(abs(a-b))
    temporal=statistics.mean(changes) if changes else None
    return {'amplitude_media':round(mean,4),'variacao_espectral':round(spread,4),
            'mudanca_temporal':round(temporal,4) if temporal is not None else None,
            'n_pares_validos':len(good)}

def spectrum_dashboard():
    result={}
    for node,entry in state.items():
        recent=list(entry['spectra'])
        if not recent:
            result[node]={'mcs':None,'n':0,'amplitudes':[], 'variabilidade':[], 'mudanca_temporal':None}
            continue
        counts=Counter(x[0] for x in recent)
        dominant=counts.most_common(1)[0][0]
        spectra=[x[1] for x in recent if x[0]==dominant]
        means=[]
        stds=[]
        for j in range(64):
            values=[sp[j] for sp in spectra if sp[j] is not None]
            means.append(round(statistics.mean(values),2) if values else None)
            stds.append(round(statistics.pstdev(values),2) if len(values)>1 else None)
        diffs=[]
        for first,second in zip(spectra,spectra[1:]):
            diffs.extend(abs(a-b) for a,b in zip(first,second) if a is not None and b is not None)
        result[node]={'mcs':dominant,'n':len(spectra),'amplitudes':means,
                      'variabilidade':stds,
                      'mudanca_temporal':round(statistics.mean(diffs),2) if diffs else None}
    return result

def load_calibration():
    global calibration, calibration_error
    result = {}
    try:
        for scenario, filename in CAL_FILES.items():
            path = CAL_DIR / filename
            if not path.is_file():
                raise FileNotFoundError(str(path))
            groups = defaultdict(list)
            with path.open(newline='', encoding='utf-8-sig') as file:
                for row in csv.DictReader(file):
                    try:
                        if row['csi_valido'].lower() != 'true' or row['csi_encoding'] != 'hex_u8':
                            continue
                        raw = bytes.fromhex(row['csi_hex'])
                        amp = csi_amplitude(raw, str(row.get('first_word_invalid','0')).lower() in ('1','true'))
                        if amp is None or row['esp_id'] not in NODES:
                            continue
                        groups[row['esp_id']].append((float(row['rssi']), amp))
                    except (ValueError, TypeError, KeyError):
                        continue
            result[scenario] = {}
            for node in NODES:
                vals = groups[node]
                if len(vals) < 15:
                    raise ValueError(f'{filename}: poucas amostras validas em {node}')
                result[scenario][node] = (
                    statistics.mean(x[0] for x in vals),
                    statistics.mean(x[1] for x in vals))
        calibration = result
        calibration_error = None
    except (OSError, ValueError, KeyError) as exc:
        calibration = {}
        calibration_error = str(exc)

# Calibracao dinamica: nunca aprende automaticamente um estado ocupado como vazio.
BASELINE_SECONDS = 60
BASELINE_MIN_PER_NODE = 60
baseline = None
calibration_run = {'started': None, 'values': {n: [] for n in NODES}}
detection = {'candidate': None, 'since': None, 'status': 'incerto'}

def calibration_progress(now):
    started = calibration_run['started']
    if started is None:
        return None
    return min(100, int(100 * (now-started)/BASELINE_SECONDS))

def percentile(values, p):
    vals = sorted(values)
    if not vals:
        return 0.0
    index = (len(vals)-1)*p
    low = int(index)
    high = min(low+1,len(vals)-1)
    return vals[low]+(vals[high]-vals[low])*(index-low)

def finish_calibration(now):
    global baseline
    if calibration_run['started'] is None or now-calibration_run['started'] < BASELINE_SECONDS:
        return
    candidate = {}
    for node in NODES:
        vals = calibration_run['values'][node]
        if len(vals) < BASELINE_MIN_PER_NODE:
            calibration_run['started'] = None
            calibration_run['error'] = f'Amostras insuficientes em {node}: {len(vals)}.'
            return
        # Janelas de 5 segundos capturam variacao natural do sinal vazio.
        buckets = defaultdict(list)
        for elapsed, rssi, amp, mcs in vals:
            buckets[int(elapsed//5)].append((rssi,amp,mcs))
        windows = [v for _,v in sorted(buckets.items()) if len(v)>=3]
        if len(windows)<8:
            calibration_run['started'] = None
            calibration_run['error'] = f'Poucas janelas validas em {node}.'
            return
        med_r = [statistics.median(x[0] for x in w) for w in windows]
        med_a = [statistics.median(x[1] for x in w) for w in windows]
        r0 = statistics.median(med_r)
        a0 = statistics.median(med_a)
        # Faixas derivadas das janelas vazias, com pisos para evitar
        # limiares artificiais causados por sinal quase constante.
        threshold_r = max(2.5, percentile([abs(x-r0) for x in med_r],0.95)*1.6+0.7)
        threshold_a = max(0.45, percentile([abs(x-a0) for x in med_a],0.95)*1.6+0.15)
        mcs_counts = defaultdict(int)
        for _,_,_,mcs in vals:
            mcs_counts[str(mcs)]+=1
        main_mcs = max(mcs_counts,key=mcs_counts.get)
        candidate[node] = {'rssi':r0,'amp':a0,'rssi_limit':threshold_r,
                           'amp_limit':threshold_a,'mcs':main_mcs,
                           'n':len(vals),'windows':len(windows)}
    baseline = candidate
    calibration_run.update(started=None,error=None)
    detection.update(candidate=None,since=None,status='incerto')
    print('Calibracao V5.2 concluida:', {n:(round(v['rssi_limit'],2),round(v['amp_limit'],2)) for n,v in candidate.items()},flush=True)

def presence_status(now):
    finish_calibration(now)
    active = {}
    for node,item in state.items():
        if now-item['last_seen']<TIMEOUT_SECONDS and len(item['samples'])>=8:
            samples=list(item['samples'])
            active[node]={'rssi':statistics.median(x[0] for x in samples),
                          'amp':statistics.median(x[1] for x in samples),
                          'n':len(samples),'mcs':str(item['last_mcs'])}
    progress=calibration_progress(now)
    result={'status':'analisando','posicao':None,'nos_ativos':len(active),
            'calibracao':baseline is not None,'calibrando':progress is not None,
            'progresso':progress,'erro_calibracao':calibration_run.get('error'),
            'metodo':'V5.3: variabilidade do vazio, RSSI e CSI',
            'aviso':'Heuristica exploratoria, sem acuracia validada.',
            'nos':{},'candidato':None}
    if progress is not None:
        result['detalhe']=f'Calibrando quarto vazio: {progress}%'
        return result
    if baseline is None:
        result['detalhe']='Calibre com o quarto vazio e a porta fechada.'
        return result
    if len(active)<3:
        result['detalhe']='Aguardando CSI recente de pelo menos 3 placas.'
        detection.update(candidate=None,since=None,status='incerto')
        return result
    strong=0
    moderate=0
    for node,v in active.items():
        ref=baseline[node]
        delta_r=v['rssi']-ref['rssi']
        delta_a=v['amp']-ref['amp']
        ratio_r=abs(delta_r)/ref['rssi_limit']
        ratio_a=abs(delta_a)/ref['amp_limit']
        mcs_changed=v['mcs']!=ref['mcs']
        # Amplitude CSI agregada e sensivel a MCS. Quando MCS muda,
        # exibe o desvio mas nao usa amplitude como evidencia principal.
        csi_weight=0.25 if mcs_changed else 0.65
        score=max(ratio_r,ratio_a*csi_weight)
        if score>=1.7: strong+=1
        if score>=1.0: moderate+=1
        result['nos'][node]={'rssi':round(v['rssi'],2),'amp_csi':round(v['amp'],2),
                             'delta_rssi':round(delta_r,2),'delta_amp':round(delta_a,2),
                             'limite_rssi':round(ref['rssi_limit'],2),
                             'limite_amp':round(ref['amp_limit'],2),
                             'indice_mudanca':round(score,2),
                             'mcs_atual':v['mcs'],'mcs_referencia':ref['mcs'],
                             'mcs_mudou':mcs_changed,'amostras':v['n']}
    # Dois nos precisam concordar; um unico enlace nao confirma presenca.
    if strong>=2 or (strong>=1 and moderate>=2):
        candidate='presenca'
    elif strong==0 and moderate==0:
        candidate='vazio'
    else:
        candidate='incerto'
    if candidate!=detection['candidate']:
        detection.update(candidate=candidate,since=now,status='incerto')
    required=8 if candidate=='presenca' else 6
    if candidate=='incerto':
        detection['status']='incerto'
    elif now-detection['since']>=required:
        detection['status']=candidate
    else:
        detection['status']='incerto'
    result.update(status=detection['status'],candidato=candidate,
                  detalhe=f'Mudancas fortes: {strong}; moderadas: {moderate}; janela de confirmacao: {required}s.')
    return result

# V6 - classificador exploratorio de 5 regioes, treinado com coleta rotulada por tempo.
# Um unico ciclo nao permite estimar acuracia. Distancias sao medidas relativas,
# nao probabilidades. Abster-se quando evidencia insuficiente.
V6_REGIONS = ('vazio','perto_a','perto_b','perto_c','perto_d','centro','vazio_final')
V6_COORDS = {'perto_a':(0.12,0.12),'perto_b':(0.88,0.12),
             'perto_c':(0.12,0.88),'perto_d':(0.88,0.88),'centro':(0.5,0.5)}
V6_MODEL = {}
V6_MODEL_INFO = {'ready':False,'detail':'Modelo nao carregado'}
V6_MIN_NODES = 3

def v6_features(raw, invalid, rssi):
    sp=csi_spectrum(raw, invalid)
    if sp is None: return None
    # Normalizacao relativa reduz variacao global de ganho; sem interpretar
    # indices como subportadoras fisicas verificadas.
    vals=[v for v in sp if v is not None]
    if len(vals)<32: return None
    med=statistics.median(vals)
    if med<=0: return None
    bins=[]
    for i in range(0,64,8):
        block=[v for v in sp[i:i+8] if v is not None]
        bins.append(statistics.median(block)/med if len(block)>=3 else None)
    return (float(rssi), bins)

def v6_median_features(samples):
    if len(samples)<5: return None
    r=statistics.median(x[0] for x in samples)
    b=[]
    for j in range(8):
        values=[x[1][j] for x in samples if x[1][j] is not None]
        b.append(statistics.median(values) if len(values)>=4 else None)
    return (r,b)

def v6_distance(x,y):
    # RSSI e forma espectral: escala empirica, sem precisao validada.
    r=abs(x[0]-y[0])/4.0
    pairs=[abs(a-b) for a,b in zip(x[1],y[1]) if a is not None and b is not None]
    if len(pairs)<5: return None
    return 0.65*r+0.35*(statistics.mean(pairs)/0.35)

def v6_load_model(csv_path):
    global V6_MODEL, V6_MODEL_INFO
    from datetime import timedelta
    if not csv_path.is_file():
        V6_MODEL_INFO={'ready':False,'detail':f'Arquivo nao encontrado: {csv_path}'}
        return
    groups=defaultdict(list)
    count=0
    first=None
    with csv_path.open(newline='',encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            try:
                ts=datetime.fromisoformat(row['timestamp'])
                if first is None:first=ts
                elapsed=(ts-first).total_seconds()
                minute=int(elapsed//60)
                if minute not in range(7) or elapsed%60<15:continue
                if row['esp_id'] not in NODES or row['csi_valido'].lower()!='true':continue
                raw=bytes.fromhex(row['csi_hex'])
                feat=v6_features(raw,row.get('first_word_invalid','0') in ('1','True','true'),float(row['rssi']))
                if feat is None:continue
                label=V6_REGIONS[minute]
                mcs=str(row['mcs'])
                groups[(label,row['esp_id'],mcs)].append(feat)
                count+=1
            except (ValueError,KeyError,TypeError):continue
    model={}
    for (label,node,mcs),vals in groups.items():
        if len(vals)>=12:
            model[(label,node,mcs)]=v6_median_features(vals)
    V6_MODEL=model
    labels=set(k[0] for k in model)
    V6_MODEL_INFO={'ready':len(labels)>=6,'detail':f'{len(model)} perfis (no/MCS), {count} amostras, {len(labels)} cenarios; avaliacao independente pendente', 'source':csv_path.name}
    print('Modelo V6:',V6_MODEL_INFO,flush=True)

def v6_estimate(now):
    if not V6_MODEL_INFO['ready']:
        return {'status':'sem_modelo','detail':V6_MODEL_INFO['detail'],'region':None}
    current={}
    diagnostic={}
    for node,item in state.items():
        age=round(now-item['last_seen'],2) if item['last_seen'] else None
        recent=[x for x in item['localization'] if now-x[0]<=8]
        counts=Counter(x[1] for x in recent)
        diagnostic[node]={'online':bool(age is not None and age<TIMEOUT_SECONDS),
                          'idade_ultimo_pacote_s':age,'csi_ultimos_8s':len(recent),
                          'amostras_por_mcs':dict(counts)}
        if age is None or age>=TIMEOUT_SECONDS:continue
        # Preferir MCS com amostras suficientes e maior cobertura no treino.
        candidates=[]
        for mcs,n in counts.items():
            if n<5:continue
            vals=[x[2] for x in recent if x[1]==mcs]
            feat=v6_median_features(vals)
            if feat is None:continue
            coverage=sum((region,node,mcs) in V6_MODEL for region in V6_REGIONS)
            candidates.append((coverage,n,mcs,feat))
        if candidates:
            coverage,n,mcs,feat=max(candidates,key=lambda x:(x[0],x[1]))
            current[node]=(mcs,feat)
            diagnostic[node].update(mcs_selecionado=mcs,cobertura_regioes=coverage)
    if len(current)<V6_MIN_NODES:
        return {'status':'incerto','detail':'CSI insuficiente: menos de 3 nos com >=5 amostras do mesmo MCS nos ultimos 8s',
                'region':None,'nos_com_csi':list(current),'diagnostico':diagnostic}
    # Comparacao justa: todas as regioes devem usar EXATAMENTE os mesmos nos.
    # Caso contrario, a classe com poucos nos pode parecer artificialmente melhor.
    comparable=[node for node,(mcs,feat) in current.items()
                if all((region,node,mcs) in V6_MODEL for region in V6_REGIONS)]
    if len(comparable)<3:
        return {'status':'incerto','region':None,
                'detail':f'Apenas {len(comparable)} nos comparaveis entre todas as regioes/MCS; necessaria nova coleta',
                'nos_comparaveis':comparable,'ranking':ranking_debug,'diagnostico':diagnostic}
    scores={}
    for region in V6_REGIONS:
        distances=[]
        for node in comparable:
            mcs,feat=current[node]
            d=v6_distance(feat,V6_MODEL[(region,node,mcs)])
            if d is not None:distances.append(d)
        if len(distances)==len(comparable):scores[region]=statistics.mean(distances)
    if len(scores)!=len(V6_REGIONS):
        return {'status':'incerto','region':None,'detail':'Caracteristicas CSI insuficientes para comparacao justa','diagnostico':diagnostic}
    # Combinar os dois periodos vazios, sem forcar uma posicao.
    ranking=sorted(scores.items(),key=lambda x:x[1])
    ranking_debug=[{"regiao":k,"distancia":round(v,4)} for k,v in ranking]
    best,best_d=ranking[0]
    other=next((v for k,v in ranking[1:] if not (best.startswith('vazio') and k.startswith('vazio'))),None)
    margin=(other-best_d) if other is not None else 0
    if margin<0.25 or best_d>1.6:
        return {'status':'incerto','region':None,'distance':round(best_d,3),'margin':round(margin,3),
                'detail':'Padrao ambiguo ou distante da coleta de referencia; verificar nova coleta', 'nos_comparaveis':comparable,'ranking':ranking_debug,'diagnostico':diagnostic}
    if best.startswith('vazio'):
        return {'status':'vazio','region':None,'distance':round(best_d,3),'margin':round(margin,3),'detail':'Semelhante a referencia vazia (experimental)', 'nos_comparaveis':comparable,'ranking':ranking_debug,'diagnostico':diagnostic}
    return {'status':'estimativa','region':best,'xy':V6_COORDS[best],
            'distance':round(best_d,3),'margin':round(margin,3),
            'detail':'Estimativa exploratoria; requer validacao em nova sessao', 'nos_comparaveis':comparable,'ranking':ranking_debug,'diagnostico':diagnostic}


# V7: detector de presenca independente do classificador de localizacao.
# Calibracao vazia explicita; nao aprende ocupacao automaticamente.
PRESENCE_WINDOW = 8.0
presence_samples = {n: deque(maxlen=1800) for n in NODES}
LOCATION_MODEL_PATH = Path(__file__).resolve().parent / 'smartroom_modelo_localizacao_ocupado_experimental.joblib'
location_model = None
location_error = None
if joblib is None or pd is None:
    location_error = 'Instale as dependencias: py -m pip install pandas scikit-learn joblib'
elif not LOCATION_MODEL_PATH.exists():
    location_error = 'Modelo nao encontrado na pasta software'
else:
    try:
        location_model = joblib.load(LOCATION_MODEL_PATH)  # Carregar somente arquivo confiavel.
    except Exception as exc:
        location_error = 'Erro ao carregar modelo: ' + str(exc)
location_samples = {n: deque(maxlen=800) for n in NODES}
LOCATION_NAMES = {'perto_a':'Perto da ESP_A','perto_b':'Perto da ESP_B',
                  'perto_c':'Perto da ESP_C','perto_d':'Perto da ESP_D','centro':'Centro'}

def location_ml(now):
    pres = presence_v7(now)
    if pres['status'] != 'presenca':
        return {'status':pres['status'], 'regiao':None, 'confianca':None,
                'detail':'Localizacao suspensa: presenca nao confirmada.'}
    if location_model is None:
        return {'status':'indisponivel','regiao':None,'confianca':None,'detail':location_error}
    feat = {}
    for node in NODES:
        rows = [v for v in location_samples[node] if now-v[0] <= 5.0]
        if len(rows) < 4:
            return {'status':'aguardando','regiao':None,'confianca':None,
                    'detail':'Aguardando pelo menos 4 pacotes recentes de cada ESP32.'}
        for key in ('rssi','amplitude_media','variacao_espectral','n_pares_validos'):
            vals = [r[1][key] for r in rows if r[1].get(key) is not None]
            if not vals:
                return {'status':'aguardando','regiao':None,'confianca':None,'detail':'Caracteristicas CSI insuficientes.'}
            feat[f'{node}_{key}_med'] = statistics.median(vals)
            feat[f'{node}_{key}_std'] = statistics.stdev(vals) if len(vals)>1 else 0.0
        feat[f'{node}_mcs_med'] = statistics.median([r[1]['mcs'] for r in rows])
        feat[f'{node}_n'] = len(rows)
    try:
        cols = location_model['colunas']
        frame = pd.DataFrame([{c:feat.get(c,location_model['medianas'].get(c,0)) for c in cols}],columns=cols)
        frame = frame.fillna(pd.Series(location_model['medianas'])).fillna(0)
        model = location_model['modelo']
        prob = model.predict_proba(frame)[0]
        # Diagnostico de distribuicao: treino vs tempo real, sem alterar a decisao.
        train_medians = location_model['medianas']
        drift = []
        for node in NODES:
            for metric in ('rssi', 'amplitude_media', 'variacao_espectral', 'n_pares_validos', 'mcs'):
                suffix = '_mcs_med' if metric == 'mcs' else '_' + metric + '_med'
                key = node + suffix
                if key in feat and key in train_medians:
                    expected = float(train_medians[key])
                    actual = float(feat[key])
                    drift.append({'caracteristica':key,'atual':round(actual,3),'mediana_treino':round(expected,3),'diferenca':round(actual-expected,3)})
        ranking = sorted(({'regiao':str(c),'probabilidade':round(float(v),3)} for c,v in zip(model.classes_,prob)),key=lambda v:-v['probabilidade'])

        best = int(prob.argmax())
        label = str(model.classes_[best])
        confidence = float(prob[best])
        return {'status':'estimado' if confidence>=0.55 else 'incerto',
                'regiao':label if confidence>=0.55 else None,
                'melhor_palpite':label,'confianca':round(confidence,3),
                'detail':'Probabilidade do modelo, nao precisao comprovada. Janela de 5s.', 'ranking':ranking, 'diagnostico':drift}
    except Exception as exc:
        return {'status':'erro','regiao':None,'confianca':None,'detail':str(exc)}

presence_cal = {'started': None, 'error': None, 'completed_at': None}
presence_baseline = None
presence_decision = {'candidate': None, 'since': None, 'status': 'incerto', 'last_change': None}

def presence_window(node, now, start=None, end=None):
    vals = [(r, a) for t, r, a in presence_samples[node]
            if (start if start is not None else now-PRESENCE_WINDOW) <= t <= (end if end is not None else now)]
    if len(vals) < 12:
        return None
    rr = [x[0] for x in vals]
    aa = [x[1] for x in vals]
    return {'n': len(vals), 'rssi': statistics.median(rr),
            'rssi_std': statistics.pstdev(rr), 'amp': statistics.median(aa)}

def presence_v7(now):
    global presence_baseline
    started = presence_cal['started']
    progress = None
    if started is not None:
        progress = min(100, round((now-started)*100/60))
        if now-started >= 60:
            refs = {}
            for node in NODES:
                windows = []
                # Dez janelas de cinco segundos, evitando os primeiros dez segundos.
                for k in range(10):
                    w = presence_window(node, now, started+10+k*5, started+15+k*5)
                    if w is not None: windows.append(w)
                if len(windows) < 8:
                    presence_cal['error'] = 'Amostras insuficientes para ' + node
                    break
                med = statistics.median(w['rssi'] for w in windows)
                amp = statistics.median(w['amp'] for w in windows)
                noise = statistics.median(w['rssi_std'] for w in windows)
                # Variabilidade da linha de base define os limiares, com pisos conservadores.
                dev = [abs(w['rssi']-med) for w in windows]
                ampdev = [abs(w['amp']-amp) for w in windows]
                refs[node] = {'rssi': med, 'amp': amp, 'noise': noise,
                              'rssi_limit': max(3.0, percentile(dev, .95)*2.0+1.0),
                              'amp_limit': max(.75, percentile(ampdev, .95)*2.0+.3),
                              'std_limit': max(1.5, noise*2.5+.5)}
            else:
                presence_baseline = refs
                presence_cal['error'] = None
                presence_cal['completed_at'] = datetime.now().strftime('%H:%M:%S')
                print('Calibracao de presenca V7 concluida as '+presence_cal['completed_at'], flush=True)
                presence_decision.update(candidate=None, since=None, status='incerto', last_change=None)
            presence_cal['started'] = None
            progress = None
    if presence_cal['started'] is not None:
        return {'status':'calibrando','progresso':progress,'calibracao':False,'detail':'Calibrando: mantenha o quarto vazio. Aguarde a confirmacao.'}
    if presence_baseline is None:
        return {'status':'sem_calibracao','detail':presence_cal['error'] or 'Calibre com o quarto vazio', 'calibracao':False,'calibrado_em':presence_cal['completed_at']}
    detail = {}
    strong = 0
    moderate = 0
    for node in NODES:
        if now-state[node]['last_seen'] >= TIMEOUT_SECONDS: continue
        w = presence_window(node, now)
        if w is None: continue
        ref = presence_baseline[node]
        dr = abs(w['rssi']-ref['rssi'])/ref['rssi_limit']
        da = abs(w['amp']-ref['amp'])/ref['amp_limit']
        ds = max(0.0, w['rssi_std']-ref['noise'])/ref['std_limit']
        # A amplitude CSI e auxiliar; nao deve sozinha confirmar presenca.
        score = max(dr, .5*da, ds)
        detail[node] = {'rssi':round(w['rssi'],2),'rssi_vazio':round(ref['rssi'],2),
                        'desvio_rssi':round(w['rssi']-ref['rssi'],2),
                        'variabilidade_rssi':round(w['rssi_std'],2),
                        'variabilidade_vazio':round(ref['noise'],2),
                        'indice':round(score,2),'amostras':w['n']}
        if score >= 1.5: strong += 1
        if score >= 1.0: moderate += 1
    if len(detail) < 3:
        presence_decision.update(candidate=None,since=None,status='incerto',last_change=None)
        return {'status':'incerto','detail':'Aguardando CSI de pelo menos 3 placas', 'nos':detail,'calibracao':True,'calibrado_em':presence_cal['completed_at']}
    # V7.4: um no com indice >= 1.0 e suficiente para presenca candidata.
    # Regra exploratoria: sensivel a falsos positivos por interferencia.
    max_score = max((v['indice'] for v in detail.values()), default=0.0)
    candidate = 'presenca' if max_score >= 1.0 else ('vazio' if max_score < 0.6 else 'incerto')
    # V7.4: histerese temporal. Oscilacoes intermediarias preservam o
    # ultimo estado CONFIRMADO, sem inventar uma nova classificacao.
    # Uma mudanca so e confirmada apos estabilidade continua do candidato.
    if candidate != presence_decision['candidate']:
        presence_decision.update(candidate=candidate, since=now)
    required = 5 if candidate == 'presenca' else 8
    if candidate != 'incerto' and now-presence_decision['since'] >= required:
        if presence_decision['status'] != candidate:
            presence_decision['last_change'] = datetime.now().strftime('%H:%M:%S')
        presence_decision['status'] = candidate
    # No primeiro ciclo, ate haver evidencia confirmada, continua incerto.
    stable = presence_decision['status'] in ('vazio', 'presenca')
    transition = (candidate != presence_decision['status'])
    return {'status':presence_decision['status'],'candidato':candidate,
            'em_transicao':bool(stable and transition),
            'confirmacao_restante_s':round(max(0, required-(now-presence_decision['since'])),1) if candidate!='incerto' and transition else None,
            'ultima_mudanca':presence_decision['last_change'],
            'calibracao':True,'calibrado_em':presence_cal['completed_at'],'nos_ativos':len(detail),'nos':detail,
            'detail':f'Mudancas fortes: {strong}; moderadas: {moderate}; maior indice: {max_score:.2f}. V7.4: presenca >=1.0 por 5s; vazio <0.6 por 8s; histerese ativa. Detector experimental, nao validado.'}



# Localizacao heuristica de demonstracao: nao e triangulacao nem medicao de distancia.
# A deteccao de presenca permanece INALTERADA.
NEAR_SCORE = 1.8
NEAR_MARGIN = 0.65
NEAR_RSSI_GAIN = 2.0  # dB acima da referencia do quarto vazio
LOC_STABLE_SECONDS = 3.0
loc_hold = {'candidate':None,'since':None,'confirmed':None}

def location_proximity(now):
    presence = presence_v7(now)
    status = presence['status']
    if status != 'presenca':
        loc_hold.update(candidate=None, since=None, confirmed=None)
        return {'status':status,'regiao':None,'nos':{},'criterio':'Aguardando presenca confirmada'}
    nodes = presence.get('nos', {})
    if len(nodes) < 3:
        loc_hold.update(candidate=None, since=None, confirmed=None)
        return {'status':'incerto','regiao':None,'nos':nodes,'criterio':'Aguardando sinais de 3 placas'}
    ranked = sorted(nodes.items(),key=lambda kv:kv[1]['indice'],reverse=True)
    lead, top = ranked[0]
    second = ranked[1][1]['indice'] if len(ranked)>1 else 0
    gain = top['desvio_rssi']
    dominant = top['indice'] >= NEAR_SCORE and top['indice']-second >= NEAR_MARGIN and gain >= NEAR_RSSI_GAIN
    candidate = 'perto_'+lead[-1].lower() if dominant else 'centro'
    if candidate != loc_hold['candidate']:
        loc_hold.update(candidate=candidate,since=now)
    if now-loc_hold['since'] >= LOC_STABLE_SECONDS:
        loc_hold['confirmed']=candidate
    region = loc_hold['confirmed']
    return {'status':'estimado' if region else 'estabilizando','regiao':region,
            'nos':nodes,'principal':lead,'indice_principal':top['indice'],
            'diferenca_indices':round(top['indice']-second,2),
            'ganho_rssi_db':round(gain,2), 'criterio':'Regra de indice dominante e aumento de RSSI; centro por exclusao',
            'limiares':{'indice':NEAR_SCORE,'margem':NEAR_MARGIN,'ganho_rssi_db':NEAR_RSSI_GAIN},
            'heuristico':True}

# V7.7 - Coleta guiada, independente do detector de presenca.
COLLECT_STEPS = [('vazio','Quarto vazio'),('perto_a','Perto da ESP_A'),('perto_b','Perto da ESP_B'),('perto_c','Perto da ESP_C'),('perto_d','Perto da ESP_D'),('centro','Centro'),('centro','Centro novamente'),('perto_d','Perto da ESP_D novamente'),('perto_b','Perto da ESP_B novamente'),('perto_a','Perto da ESP_A novamente'),('perto_c','Perto da ESP_C novamente')]
collect = {'index':0,'phase':'idle','started':None,'file':None,'path':None,'writer':None,'packets':0,'step_packets':0,'completed':False}

def collect_tick(now):
    if collect['phase'] == 'prepare' and now-collect['started'] >= 10:
        collect.update(phase='record',started=now,step_packets=0)
    elif collect['phase'] == 'record' and now-collect['started'] >= 60:
        collect.update(phase='ready',started=None)
        if collect['file']:
            collect['file'].flush()

def collect_info(now):
    collect_tick(now)
    idx=collect['index']
    label,title=COLLECT_STEPS[idx] if idx<len(COLLECT_STEPS) else ('','Concluido')
    phase=collect['phase']
    duration=10 if phase=='prepare' else 60
    remaining=max(0,round(duration-(now-collect['started']),1)) if phase in ('prepare','record') else 0
    return {'etapa':idx+1,'total':len(COLLECT_STEPS),'posicao':title,'rotulo':label,'fase':phase,'restante_s':remaining,'pacotes_etapa':collect['step_packets'],'pacotes_total':collect['packets'],'arquivo':collect['path'].name if collect['path'] else None,'concluido':collect['completed']}

def collect_action(now):
    collect_tick(now)
    if collect['phase'] in ('prepare','record'):
        return False,'Aguarde o cronometro terminar.'
    if collect['completed']:
        return False,'Coleta ja concluida. Reinicie o servidor para nova coleta.'
    if any(now-state[n]['last_seen']>=TIMEOUT_SECONDS for n in NODES):
        return False,'As quatro ESP32 precisam estar online.'
    if collect['phase']=='idle':
        path=DATA_DIR/('smartroom_v77_validacao_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.csv')
        f=path.open('w',newline='',encoding='utf-8')
        w=csv.DictWriter(f,fieldnames=SPECTRAL_COLUMNS)
        w.writeheader();f.flush()
        collect.update(file=f,path=path,writer=w,index=0,packets=0)
    elif collect['phase']=='ready':
        collect['index']+=1
    if collect['index']>=len(COLLECT_STEPS):
        collect.update(phase='done',completed=True)
        collect['file'].flush();collect['file'].close();collect['file']=None
    else:
        collect.update(phase='prepare',started=now,step_packets=0)
    return True,None

COLLECT_HTML = r'''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SmartRoom | Monitoramento Inteligente</title>
<style>
:root{color-scheme:dark;--bg:#080e1a;--panel:#111d30;--border:#26374d;--muted:#94a7bf;--text:#eff6ff;--cyan:#4fd6e5;--green:#36dda6;--amber:#f9bf68}*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 80% 0,#12314c 0,transparent 45%),var(--bg);color:var(--text);font:15px/1.5 Inter,Segoe UI,Arial,sans-serif;min-height:100vh}.wrap{max-width:1320px;margin:auto;padding:32px 28px 44px}header{display:flex;justify-content:space-between;align-items:center;gap:18px;margin-bottom:27px;flex-wrap:wrap}.brand{display:flex;align-items:center;gap:15px}.logo{display:grid;place-items:center;width:54px;height:54px;border-radius:16px;background:linear-gradient(135deg,#0d7fa6,#26cba8);font-size:28px;box-shadow:0 12px 35px #00d9c82b}h1{font-size:31px;letter-spacing:-1.1px;margin:0}header p{margin:0;color:var(--muted)}.online-tag{border:1px solid var(--border);border-radius:30px;padding:9px 16px;color:var(--muted);background:#111c2b}.online-tag.good{color:var(--green);border-color:#21634f}.hero{display:grid;grid-template-columns:1fr 1fr;gap:17px;margin-bottom:17px}.panel,.node{background:linear-gradient(145deg,#142238,#0e1828);border:1px solid var(--border);border-radius:20px;box-shadow:0 10px 36px #0002}.panel{padding:24px}.eyebrow{text-transform:uppercase;letter-spacing:2px;color:var(--muted);font-size:11px;font-weight:800;margin:0 0 12px}.presence{font-size:29px;font-weight:800;letter-spacing:-.6px;display:flex;align-items:center;gap:12px}.pulse{width:13px;height:13px;background:#74839b;border-radius:50%;box-shadow:0 0 0 7px #74839b22}.pulse.yes{background:var(--green);box-shadow:0 0 0 7px #36dda622}.pulse.no{background:#65a8ff;box-shadow:0 0 0 7px #65a8ff22}.sub{color:var(--muted);font-size:13px;margin:12px 0 0}.action{margin-top:19px;background:#156b83;color:white;border:1px solid #35a2b8;padding:11px 17px;border-radius:11px;font-weight:700;cursor:pointer}.action:hover{background:#1b829d}.action:disabled{opacity:.5;cursor:default}.zone{font-size:30px;font-weight:800;color:var(--cyan);margin:2px 0 5px}.layout{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,.95fr);gap:17px}.map-panel{min-height:520px}.section-title{font-size:18px;font-weight:750;margin:0}.room{width:min(100%,560px);aspect-ratio:4/3;position:relative;margin:42px auto 25px;border:2px solid #46728d;border-radius:16px;background:linear-gradient(#182d40aa,#0c1b2ccc),repeating-linear-gradient(0deg,transparent 0 39px,#ffffff0a 40px 41px),repeating-linear-gradient(90deg,transparent 0 39px,#ffffff0a 40px 41px);box-shadow:inset 0 0 70px #0007}.room:before{content:'4,0 m';position:absolute;top:-30px;left:50%;transform:translateX(-50%);color:var(--muted);font-size:12px}.room:after{content:'3,0 m';position:absolute;right:-43px;top:50%;transform:rotate(90deg);color:var(--muted);font-size:12px}.pin{position:absolute;transform:translate(-50%,-50%);display:grid;place-items:center;width:56px;height:46px;border-radius:12px;background:#17304a;border:1px solid #41617b;color:#afc9dc;font-weight:800;z-index:3}.pin.live{border-color:var(--green);color:var(--green)}.pin.hot{border-color:var(--amber);background:#694621;color:#ffe1a1;box-shadow:0 0 28px #f9bf6866}.a{left:0;top:0}.b{left:100%;top:0}.c{left:0;top:100%}.d{left:100%;top:100%}.person{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);display:none;z-index:2}.person .dot{width:25px;height:25px;border:5px solid #d8fff4;background:#21d69a;border-radius:50%;box-shadow:0 0 0 13px #22d69a25,0 0 36px #22d69a88;margin:auto}.person span{display:block;margin-top:17px;padding:5px 10px;background:#082b29e8;border:1px solid #2c806a;border-radius:7px;white-space:nowrap;font-weight:700;font-size:12px}.map-caption{font-size:12px;color:var(--muted);text-align:center}.nodes{display:grid;grid-template-columns:1fr 1fr;gap:13px;margin-top:17px}.node{padding:19px}.node-top{display:flex;justify-content:space-between;align-items:center;gap:8px;font-weight:750}.status{font-size:11px;color:var(--muted)}.rssi{font-size:31px;font-weight:800;margin:12px 0 4px}.rssi small{font-size:14px;font-weight:500;color:var(--muted)}.meter{height:7px;background:#273b50;border-radius:9px;overflow:hidden;margin:14px 0 8px}.fill{height:100%;background:linear-gradient(90deg,#1b90a5,#35e1b3);width:0%;transition:width .5s}.score{color:var(--muted);font-size:12px}.footer{margin-top:22px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;color:#738aa5;font-size:12px}.notice{color:#a6b9cc;font-size:12px;margin-top:16px;border-top:1px solid var(--border);padding-top:13px}@media(max-width:900px){.hero,.layout{grid-template-columns:1fr}}@media(max-width:500px){.wrap{padding:18px 14px}.panel{padding:18px}.nodes{gap:9px}.node{padding:14px}.presence,.zone{font-size:23px}.rssi{font-size:25px}.room{margin:42px auto 22px;width:90%}.room:after{right:-38px}}
</style></head><body><div class="wrap"><header><div class="brand"><div class="logo">⌁</div><div><h1>SmartRoom</h1><p>Monitoramento inteligente de presença e localização por Wi-Fi CSI</p></div></div><div class="online-tag" id="connection">● Conectando...</div></header>
<div class="hero"><section class="panel"><div class="eyebrow">Monitoramento de presença</div><div class="presence"><i class="pulse" id="pulse"></i><span id="presence">Aguardando dados</span></div><p class="sub" id="calibration">Calibração necessária para iniciar a análise</p><button class="action" id="calibrate" onclick="calibrate()">Calibrar ambiente vazio · 60 s</button></section><section class="panel"><div class="eyebrow">Região estimada</div><div class="zone" id="zone">Aguardando</div><p class="sub" id="zone-detail">Localização por comparação de sinais das quatro placas.</p><p class="sub" id="update">Atualização automática em tempo real</p></section></div>
<div class="layout"><section class="panel map-panel"><h2 class="section-title">Mapa do ambiente</h2><p class="sub">Distribuição das quatro unidades ESP32-S3</p><div class="room"><div class="pin a" id="pin-ESP_A">A</div><div class="pin b" id="pin-ESP_B">B</div><div class="pin c" id="pin-ESP_C">C</div><div class="pin d" id="pin-ESP_D">D</div><div class="person" id="person"><div class="dot"></div><span id="person-label">Presença</span></div></div><div class="map-caption">Posição aproximada por região • Não representa coordenadas exatas</div></section>
<section class="panel"><h2 class="section-title">Sinais das unidades</h2><p class="sub">Intensidade recebida e índice de alteração em relação ao ambiente vazio</p><div class="nodes" id="nodes"></div><p class="notice">A estimativa de região usa uma regra experimental: índice dominante acompanhado de aumento de RSSI. Sem um sinal dominante, a presença é representada no centro. Reflexões e interferências podem alterar o resultado.</p></section></div><div class="footer"><span>SmartRoom · Sistema de detecção de presença com ESP32-S3</span><span>Atualização a cada segundo</span></div></div>
<script>
const names=['ESP_A','ESP_B','ESP_C','ESP_D'];const labels={ESP_A:'Unidade A',ESP_B:'Unidade B',ESP_C:'Unidade C',ESP_D:'Unidade D'};const pos={perto_a:[17,19],perto_b:[83,19],perto_c:[17,81],perto_d:[83,81],centro:[50,50]};const zoneNames={perto_a:'Próximo à unidade A',perto_b:'Próximo à unidade B',perto_c:'Próximo à unidade C',perto_d:'Próximo à unidade D',centro:'Centro do ambiente'};
document.getElementById('nodes').innerHTML=names.map(n=>`<div class="node"><div class="node-top"><span>${labels[n]}</span><span class="status" id="state-${n}">Offline</span></div><div class="rssi" id="rssi-${n}">— <small>dBm</small></div><div class="meter"><div class="fill" id="fill-${n}"></div></div><div class="score" id="score-${n}">Índice: —</div></div>`).join('');
async function calibrate(){if(!confirm('O ambiente está vazio e continuará vazio por 60 segundos?'))return;try{const r=await fetch('/api/presence_v7/calibrate',{method:'POST'});const v=await r.json();if(!r.ok)alert(v.error||'Falha na calibração');}catch(e){alert('Falha de conexão: '+e)}}
async function refresh(){try{const [r1,r2,r3]=await Promise.all([fetch('/api/status',{cache:'no-store'}),fetch('/api/presence_v7',{cache:'no-store'}),fetch('/api/localizacao',{cache:'no-store'})]);if(!r1.ok||!r2.ok||!r3.ok)throw Error('Servidor indisponível');const [nodes,p,l]=await Promise.all([r1.json(),r2.json(),r3.json()]);const conn=document.getElementById('connection');conn.textContent='● Sistema conectado';conn.classList.add('good');const namesPresence={presenca:'Presença detectada',vazio:'Ambiente vazio',incerto:'Analisando sinais',calibrando:'Calibrando ambiente',sem_calibracao:'Calibração necessária'};document.getElementById('presence').textContent=namesPresence[p.status]||'Analisando';document.getElementById('pulse').className='pulse '+(p.status==='presenca'?'yes':p.status==='vazio'?'no':'');document.getElementById('calibration').textContent=p.status==='calibrando'?'Calibração em andamento: '+(p.progresso||0)+'%':p.calibracao?'Ambiente calibrado · Detecção ativa':'Calibre com o quarto vazio para iniciar';document.getElementById('calibrate').disabled=p.status==='calibrando';document.getElementById('calibrate').textContent=p.status==='calibrando'?'Calibrando...':p.calibracao?'Refazer calibração · 60 s':'Calibrar ambiente vazio · 60 s';
for(const n of names){const v=nodes[n]||{};document.getElementById('state-'+n).textContent=v.online?'● Online':'○ Offline';document.getElementById('state-'+n).style.color=v.online?'#36dda6':'#94a7bf';document.getElementById('rssi-'+n).innerHTML=(v.rssi==null?'—':v.rssi)+' <small>dBm</small>';const metric=(l.nos||{})[n]||(p.nos||{})[n];const score=metric?.indice;document.getElementById('score-'+n).textContent='Índice: '+(score==null?'—':Number(score).toFixed(2))+(metric?.desvio_rssi==null?'':' · Δ RSSI '+(metric.desvio_rssi>0?'+':'')+metric.desvio_rssi+' dB');document.getElementById('fill-'+n).style.width=score==null?'0%':Math.min(100,Math.max(0,score/4*100))+'%';document.getElementById('pin-'+n).className='pin '+n.slice(-1).toLowerCase()+(v.online?' live':'')+(l.status==='estimado'&&l.regiao==='perto_'+n.slice(-1).toLowerCase()?' hot':'');}
const person=document.getElementById('person');if(p.status==='presenca'&&l.regiao&&pos[l.regiao]){const xy=pos[l.regiao];person.style.display='block';person.style.left=xy[0]+'%';person.style.top=xy[1]+'%';document.getElementById('person-label').textContent=zoneNames[l.regiao];document.getElementById('zone').textContent=zoneNames[l.regiao];document.getElementById('zone-detail').textContent=l.regiao==='centro'?'Presença confirmada sem unidade dominante':'Unidade com índice dominante e aumento do RSSI';}else{person.style.display='none';document.getElementById('zone').textContent=p.status==='vazio'?'Sem presença':p.status==='presenca'?'Estabilizando leitura':'Aguardando';document.getElementById('zone-detail').textContent=p.status==='presenca'?'Analisando os sinais das quatro unidades':'A estimativa será exibida após a detecção de presença';}document.getElementById('update').textContent='Última leitura: '+new Date().toLocaleTimeString('pt-BR');}catch(e){const c=document.getElementById('connection');c.textContent='● Sem conexão';c.classList.remove('good');document.getElementById('zone-detail').textContent='Verifique se o servidor está em execução';}}
refresh();setInterval(refresh,1000);
</script></body></html>'''

def udp_listener():
    label_file = SPECTRAL_CSV.open('w', newline='', encoding='utf-8')
    label_writer = csv.DictWriter(label_file, fieldnames=SPECTRAL_COLUMNS)
    label_writer.writeheader()
    label_file.flush()
    print(f'CSV rotulado V5.3: {SPECTRAL_CSV}',flush=True)
    csv_file = CSV_PATH.open('w', newline='', encoding='utf-8')
    writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    csv_file.flush()
    print(f'CSV CSI: {CSV_PATH}', flush=True)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', UDP_PORT))
    print(f'UDP escutando na porta {UDP_PORT}', flush=True)
    while True:
        data, address = sock.recvfrom(8192)
        try:
            message = json.loads(data.decode('utf-8'))
            node = message.get('esp_id')
            if node not in state:
                continue
            rssi = int(message['rssi']) if message.get('rssi') is not None else None
            seq = message.get('seq')
            now = time.monotonic()
            csi_hex = message.get('csi', '')
            encoding = message.get('csi_encoding', '')
            csi_valid = False
            csi_size = 0
            if isinstance(csi_hex, str) and encoding == 'hex_u8':
                try:
                    csi_bytes = bytes.fromhex(csi_hex)
                    csi_size = len(csi_bytes)
                    csi_valid = (csi_size > 0 and csi_size <= 512 and
                                 csi_size <= int(message.get('csi_len', 0)))
                except (ValueError, TypeError):
                    pass
            metrics = None
            spectrum = None
            if csi_valid and csi_size == 128:
                spectrum = csi_spectrum(csi_bytes, str(message.get('first_word_invalid',0)).lower() in ('1','true'))
            writer.writerow({
                'timestamp': datetime.now().isoformat(timespec='milliseconds'),
                'esp_id': node, 'ip': address[0], 'seq': seq, 'rssi': rssi,
                'csi_len': message.get('csi_len'), 'sig_mode': message.get('sig_mode'),
                'mcs': message.get('mcs'), 'channel': message.get('channel'),
                'csi_mean_abs': message.get('csi_mean_abs'),
                'first_word_invalid': message.get('first_word_invalid'),
                'csi_encoding': encoding, 'csi_hex': csi_hex,
                'csi_bytes_recebidos': csi_size, 'csi_valido': csi_valid
            })
            csv_file.flush()
            with lock:
                entry = state[node]
                if spectrum is not None:
                    metrics = spectrum_metrics(spectrum,entry['spectra'],str(message.get('mcs')))
                    if metrics:
                        if rssi is not None:
                            try:
                                mcs_numeric = float(message.get('mcs'))
                                location_samples[node].append((now, {'rssi':float(rssi), 'amplitude_media':metrics['amplitude_media'], 'variacao_espectral':metrics['variacao_espectral'], 'n_pares_validos':metrics['n_pares_validos'], 'mcs':mcs_numeric}))
                            except (TypeError,ValueError):
                                pass
                        entry['spectra'].append((str(message.get('mcs')),spectrum))
                        feat=v6_features(csi_bytes,str(message.get('first_word_invalid',0)).lower() in ('1','true'),rssi) if rssi is not None else None
                        if feat:entry['localization'].append((now,str(message.get('mcs')),feat))
                entry['last_seen'] = now
                entry['ip'] = address[0]
                entry['rssi'] = rssi
                entry['seq'] = seq
                entry['last_mcs'] = message.get('mcs')
                entry['packets'] += 1
                if csi_valid and rssi is not None:
                    amp = csi_amplitude(csi_bytes, str(message.get('first_word_invalid',0)).lower() in ('1','true'))
                    if amp is not None:
                        entry['samples'].append((rssi, amp))
                        presence_samples[node].append((now, rssi, amp))
                        if calibration_run['started'] is not None:
                            calibration_run['values'][node].append((now-calibration_run['started'], rssi, amp, message.get('mcs')))
                if rssi is not None:
                    entry['history'].append({'rssi': rssi})
                    if node == DETECTOR_NODE and csi_valid:
                        entry['detector_rssi'].append(rssi)
                if metrics is not None:
                    label_writer.writerow({'timestamp':datetime.now().isoformat(timespec='milliseconds'),
                        'esp_id':node,'seq':seq,'rotulo':label_state['label'],
                        'segundos_rotulo':round(now-label_state['since'],2),
                        'rssi':rssi,'mcs':message.get('mcs'),'channel':message.get('channel'),
                        'csi_hex':csi_hex,**metrics})
                    label_file.flush()
                    collect_tick(now)
                    if collect['phase']=='record' and collect['writer'] is not None:
                        row={'timestamp':datetime.now().isoformat(timespec='milliseconds'),
                             'esp_id':node,'seq':seq,'rotulo':COLLECT_STEPS[collect['index']][0],
                             'segundos_rotulo':round(now-collect['started'],2),
                             'rssi':rssi,'mcs':message.get('mcs'),'channel':message.get('channel'),
                             'csi_hex':csi_hex,**metrics}
                        collect['writer'].writerow(row)
                        collect['packets']+=1
                        collect['step_packets']+=1
                        if collect['packets']%100==0:collect['file'].flush()
            print(f'[{datetime.now():%H:%M:%S}] {node} | {address[0]} | RSSI {rssi} | seq {seq} | CSI {csi_size} bytes | valido={csi_valid}', flush=True)
        except (UnicodeDecodeError, ValueError, TypeError, KeyError) as exc:
            print(f'Pacote inválido de {address[0]}: {exc}', flush=True)

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/api/collect/next':
            with lock:
                ok,error=collect_action(time.monotonic())
                result=collect_info(time.monotonic()) if ok else {'error':error}
            self.send_json(result,200 if ok else 409)
            return
        if self.path == '/api/presence_v7/calibrate':
            with lock:
                now=time.monotonic()
                if any(now-state[n]['last_seen'] >= TIMEOUT_SECONDS for n in NODES):
                    payload, code={'error':'As quatro placas precisam estar online'},409
                elif presence_cal['started'] is not None:
                    payload, code={'error':'Calibracao ja iniciada'},409
                else:
                    global presence_baseline
                    presence_baseline=None
                    presence_cal.update(started=now,error=None,completed_at=None)
                    presence_decision.update(candidate=None,since=None,status='incerto',last_change=None)
                    payload, code={'ok':True,'seconds':60},200
            self.send_json(payload,code)
            return

        if self.path == '/api/label':
            try:
                length=int(self.headers.get('Content-Length','0'))
                if length>512 or length<1:
                    raise ValueError('Corpo invalido')
                data=json.loads(self.rfile.read(length).decode('utf-8'))
                chosen=data.get('label')
                if chosen not in LABEL_OPTIONS:
                    raise ValueError('Rotulo desconhecido')
                with lock:
                    label_state.update(label=chosen,since=time.monotonic())
                payload,code={'ok':True,'label':chosen},200
            except (ValueError,TypeError,UnicodeDecodeError,json.JSONDecodeError) as exc:
                payload,code={'error':str(exc)},400
            self.send_json(payload,code)
            return
        if self.path != '/api/calibrate':
            self.send_error(404)
            return
        global baseline
        with lock:
            if calibration_run['started'] is not None:
                payload, code = {'error':'Calibracao ja em andamento'}, 409
            elif any(time.monotonic()-state[n]['last_seen'] >= TIMEOUT_SECONDS for n in NODES):
                payload, code = {'error':'As quatro ESP32 precisam estar online'}, 409
            else:
                baseline = None
                calibration_run.update(started=time.monotonic(), values={n:[] for n in NODES}, error=None)
                detection.update(candidate=None, since=None, status='incerto')
                payload, code = {'ok':True, 'seconds':BASELINE_SECONDS}, 200
                print('Iniciada calibracao do quarto vazio por 60 segundos', flush=True)
        self.send_json(payload,code)

    def send_json(self,payload,code=200):
        raw = json.dumps(payload).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == '/api/collect/status':
            with lock:
                result=collect_info(time.monotonic())
            self.send_json(result)
            return
        if self.path in ('/coleta','/coleta/'):
            raw=COLLECT_HTML.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers();self.wfile.write(raw)
            return
        if self.path == '/api/spectrum':
            with lock:
                result={'spectra':spectrum_dashboard(),'rotulo':label_state['label'],
                        'segundos':int(time.monotonic()-label_state['since'])}
            payload=json.dumps(result).encode('utf-8')
            content_type='application/json; charset=utf-8'
        elif self.path == '/api/presence':
            now = time.monotonic()
            with lock:
                result = presence_status(now)
            payload = json.dumps(result).encode('utf-8')
            content_type = 'application/json; charset=utf-8'
        elif self.path == '/api/presence_v7':
            with lock:
                result=presence_v7(time.monotonic())
            payload=json.dumps(result).encode('utf-8')
            content_type='application/json; charset=utf-8'
        elif self.path == '/api/localizacao':
            with lock:
                result=location_proximity(time.monotonic())
            payload=json.dumps(result).encode('utf-8')
            content_type='application/json; charset=utf-8'
        elif self.path == '/api/location_ml':
            with lock:
                result=location_ml(time.monotonic())
            payload=json.dumps(result).encode('utf-8')
            content_type='application/json; charset=utf-8'
        elif self.path == '/api/location':
            with lock:
                result=v6_estimate(time.monotonic())
            payload=json.dumps(result).encode('utf-8')
            content_type='application/json; charset=utf-8'
        elif self.path == '/api/status':
            now = time.monotonic()
            with lock:
                result = {name: {'online': (now - item['last_seen'] < TIMEOUT_SECONDS), 'ip': item['ip'], 'rssi': item['rssi'], 'seq': item['seq'], 'packets': item['packets'], 'history': list(item['history'])} for name, item in state.items()}
            payload = json.dumps(result).encode('utf-8')
            content_type = 'application/json; charset=utf-8'
        elif self.path in ('/', '/index.html'):
            payload = COLLECT_HTML.encode('utf-8')
            content_type = 'text/html; charset=utf-8'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        pass

if __name__ == '__main__':
    load_calibration()
    parser=argparse.ArgumentParser(description='SmartRoom - painel de entrega e localizacao heuristica')
    parser.add_argument('--treino',default=str(Path(__file__).resolve().parent/'dados_csi'/'smartroom_csi_20261101_191222.csv'))
    args=parser.parse_args()
    v6_load_model(Path(args.treino))
    print('Calibracao:', '7 arquivos carregados' if calibration else calibration_error, flush=True)
    threading.Thread(target=udp_listener, daemon=True).start()
    server = ThreadingHTTPServer(('127.0.0.1', HTTP_PORT), Handler)
    print(f'Dashboard: http://localhost:{HTTP_PORT}', flush=True)
    print('SmartRoom: presenca V7.4 preservada; localizacao por regra experimental de indice e RSSI.', flush=True)
    print('Use Ctrl+C para encerrar.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nEncerrando SmartRoom...')
    finally:
        server.server_close()

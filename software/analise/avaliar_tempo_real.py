"""Avalia o detector em tempo real (e o clássico) numa gravação ROTULADA.

Grave um teste com o painel aberto marcando o que acontece em "Teste rotulado"
(quarto vazio ≥ 70 s no começo, depois andando, parado perto de cada placa, centro…).
O CSV bruto (software/dados/smartroom_csi_*.csv) recebe a coluna "rotulo".

Uso (dentro de software/, com o venv):
    python analise/avaliar_tempo_real.py dados/smartroom_csi_AAAAMMDD_HHMMSS.csv
    python analise/avaliar_tempo_real.py <csv> --buscar      # procura os melhores parâmetros

Métricas:
- acerto por rótulo (fração do tempo com a decisão certa, ignorando 2 s após cada troca de rótulo);
- taxa de falso alarme no quarto vazio e de detecção com pessoa;
- atraso até detectar (vazio → pessoa) e até liberar (pessoa → vazio);
- região certa e erro médio de posição (m) nos rótulos "parado perto de X"/"centro".
"""
import argparse
import csv
import itertools
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from smartroom import tempo_real as tr  # noqa: E402
from smartroom.config import PLACAS, SALA  # noqa: E402
from smartroom.presenca import DetectorPresenca  # noqa: E402
from smartroom import csi as csi_mod  # noqa: E402

IGNORA_APOS_TROCA_S = 2.0
PASSO_S = 0.1


def carregar(caminho):
    linhas, t0 = [], None
    with open(caminho, newline='', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            try:
                t = datetime.fromisoformat(r['timestamp']).timestamp()
                c = bytes.fromhex(r['csi_hex'])
                rssi = int(float(r['rssi']))
            except (KeyError, ValueError):
                continue
            t0 = t if t0 is None else t0
            mcs = r.get('mcs')
            mcs = int(float(mcs)) if mcs not in (None, '') else None
            linhas.append((t - t0, r['esp_id'], c, rssi, (int(float(r.get('sig_mode') or 0)), mcs),
                           r.get('rotulo') or 'nao_marcado', str(r.get('first_word_invalid', '0')) in ('1', 'True', 'true')))
    return linhas


def posicao_do_rotulo(rotulo):
    if rotulo == 'centro':
        return np.array(SALA['roteador'], dtype=float)
    if rotulo.startswith('perto_'):
        placa = 'ESP_' + rotulo[-1].upper()
        canto = np.array(SALA['placas'][placa], dtype=float)
        centro = np.array([SALA['largura'] / 2, SALA['profundidade'] / 2])
        return canto + 0.6 * (centro - canto) / np.linalg.norm(centro - canto)   # ~0,6 m para dentro
    return None


def rodar(linhas, cal_inicio, sens=0.0, kappa_sobe=None, kappa_desce=None, transicao=None, classico=True):
    salvos = (tr.KAPPA_SOBE, tr.KAPPA_DESCE, tr.TRANSICAO)
    tr.KAPPA_SOBE = kappa_sobe or tr.KAPPA_SOBE
    tr.KAPPA_DESCE = kappa_desce or tr.KAPPA_DESCE
    tr.TRANSICAO = transicao or tr.TRANSICAO
    try:
        det = tr.DetectorTempoReal(PLACAS)
        det.sensibilidade = sens
        cla = DetectorPresenca(PLACAS) if classico else None
        ultimo, prox, cal, saida = {}, 0.0, False, []
        for t, placa, c, rssi, modo, rot, fwi in linhas:
            det.adicionar(placa, t, c, rssi, *modo)
            if cla:
                cla.adicionar(placa, t, rssi, csi_mod.amplitude_media(c, fwi))
            ultimo[placa] = t
            if not cal and t >= cal_inicio:
                det.iniciar_calibracao(t)
                if cla:
                    cla.iniciar_calibracao(t)
                cal = True
            if t >= prox:
                prox = t + PASSO_S
                online = {p for p, u in ultimo.items() if t - u < 5}
                r = det.avaliar(t, online)
                rc = cla.avaliar(t, online)['status'] if cla else None
                saida.append((t, rot, r['status'], r.get('posicao'), rc))
        return saida
    finally:
        tr.KAPPA_SOBE, tr.KAPPA_DESCE, tr.TRANSICAO = salvos


def metricas(saida, cal_fim, indice_status=2):
    trocas, rot_ant, desde = [], None, 0.0
    por_rotulo, erros_pos, regiao_ok, regiao_n = {}, [], 0, 0
    for item in saida:
        t, rot = item[0], item[1]
        st = item[indice_status]
        if rot != rot_ant:
            trocas.append((t, rot_ant, rot))
            rot_ant, desde = rot, t
        if t < cal_fim or rot in ('nao_marcado', 'vazio_calibracao') or st in ('calibrando', 'sem_calibracao') or t - desde < IGNORA_APOS_TROCA_S:
            continue
        certo = (st == 'vazio') if rot == 'vazio' else (st == 'presenca')
        a = por_rotulo.setdefault(rot, [0, 0])
        a[0] += certo
        a[1] += 1
        pos = item[3] if indice_status == 2 else None
        alvo = posicao_do_rotulo(rot)
        if pos and alvo is not None and st == 'presenca':
            erros_pos.append(float(np.linalg.norm(np.array([pos['x'], pos['y']]) - alvo)))
            regiao_n += 1
            regiao_ok += pos['regiao'] == rot
    # atrasos
    atrasos_det, atrasos_lib = [], []
    for t0, antes, depois in trocas:
        if t0 < cal_fim or antes is None or {'nao_marcado', 'vazio_calibracao'} & {antes, depois}:
            continue
        alvo = 'presenca' if (antes == 'vazio' and depois != 'vazio') else ('vazio' if depois == 'vazio' else None)
        if not alvo:
            continue
        t_ok = next((it[0] for it in saida if it[0] >= t0 and it[indice_status] == alvo), None)
        if t_ok is not None and t_ok - t0 < 30:
            (atrasos_det if alvo == 'presenca' else atrasos_lib).append(t_ok - t0)
    vazio = por_rotulo.get('vazio', [0, 0])
    pessoa = [sum(v[0] for k, v in por_rotulo.items() if k != 'vazio'), sum(v[1] for k, v in por_rotulo.items() if k != 'vazio')]
    esp = vazio[0] / vazio[1] if vazio[1] else float('nan')
    sen = pessoa[0] / pessoa[1] if pessoa[1] else float('nan')
    return {'por_rotulo': {k: v[0] / v[1] for k, v in por_rotulo.items() if v[1]},
            'especificidade': esp, 'sensibilidade': sen, 'acuracia_balanceada': (esp + sen) / 2,
            'atraso_deteccao_s': float(np.median(atrasos_det)) if atrasos_det else None,
            'atraso_liberacao_s': float(np.median(atrasos_lib)) if atrasos_lib else None,
            'regiao_certa': regiao_ok / regiao_n if regiao_n else None,
            'erro_posicao_m': float(np.mean(erros_pos)) if erros_pos else None}


def imprimir(nome, m):
    print(f'\n== {nome} ==')
    print(f"  acurácia balanceada: {m['acuracia_balanceada']:.1%}   (quarto vazio certo: {m['especificidade']:.1%} · pessoa detectada: {m['sensibilidade']:.1%})")
    for k, v in sorted(m['por_rotulo'].items()):
        print(f'    {k:12s} {v:6.1%}')
    if m['atraso_deteccao_s'] is not None:
        print(f"  atraso mediano para detectar: {m['atraso_deteccao_s']:.1f} s")
    if m['atraso_liberacao_s'] is not None:
        print(f"  atraso mediano para liberar:  {m['atraso_liberacao_s']:.1f} s")
    if m['regiao_certa'] is not None:
        print(f"  região certa: {m['regiao_certa']:.1%} · erro médio de posição: {m['erro_posicao_m']:.2f} m")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('csv')
    ap.add_argument('--calibrar-em', type=float, help='segundo em que começa a calibração (padrão: 1º trecho "vazio")')
    ap.add_argument('--buscar', action='store_true', help='busca em grade os melhores parâmetros')
    ap.add_argument('--ate', type=float, help='avalia só até este segundo (ex.: antes de alguém mexer no iPhone)')
    ap.add_argument('--sem-rotulos', default='', help='rótulos a ignorar, separados por vírgula')
    a = ap.parse_args()
    linhas = carregar(a.csv)
    if a.ate:
        linhas = [l for l in linhas if l[0] <= a.ate]
    ignorar = {r for r in a.sem_rotulos.split(',') if r}
    if ignorar:
        linhas = [l[:5] + ('nao_marcado' if l[5] in ignorar else l[5],) + l[6:] for l in linhas]
    if not linhas:
        raise SystemExit('CSV vazio ou sem as colunas esperadas')
    cal = a.calibrar_em
    if cal is None:
        cal = next((t for t, *_, rot, _ in linhas if rot == 'vazio_calibracao'), None)   # teste guiado
        if cal is None:
            cal = next((t for t, *_, rot, _ in linhas if rot == 'vazio'), None)
            if cal is None:
                raise SystemExit('Sem trecho "vazio" rotulado; use --calibrar-em S')
            cal += 3.0
    cal_fim = cal + tr.CALIBRACAO_S
    print(f'{len(linhas)} pacotes, {linhas[-1][0]:.0f} s; calibração em {cal:.0f}–{cal_fim:.0f} s')
    saida = rodar(linhas, cal)
    imprimir('Detector em tempo real (v3)', metricas(saida, cal_fim))
    imprimir('Detector clássico (V7.4)', metricas(saida, cal_fim, indice_status=4))
    if a.buscar:
        melhores = []
        for sens, ks, kd, tz in itertools.product((-0.5, 0.0, 0.5, 1.0), (1.0, 1.5, 2.5), (0.3, 0.6, 1.0), (0.01, 0.02, 0.05)):
            m = metricas(rodar(linhas, cal, sens, ks, kd, tz, classico=False), cal_fim)
            melhores.append((m['acuracia_balanceada'], sens, ks, kd, tz, m))
        melhores.sort(key=lambda x: -x[0])
        print('\n== Melhores parâmetros (acurácia balanceada) ==')
        for acc, sens, ks, kd, tz, m in melhores[:5]:
            print(f'  {acc:.1%}  sensibilidade={sens:+.1f}  KAPPA_SOBE={ks}  KAPPA_DESCE={kd}  TRANSICAO={tz}'
                  f"  atraso={m['atraso_deteccao_s']}/{m['atraso_liberacao_s']} s")


if __name__ == '__main__':
    main()

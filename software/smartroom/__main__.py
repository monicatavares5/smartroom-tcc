"""Ponto de entrada: python -m smartroom [opções]  (ou python smartroom_servidor.py)."""
import argparse
import threading
import time
import webbrowser
from pathlib import Path

from . import __version__
from .config import PLACAS, PORTA_DADOS, PORTA_DESCOBERTA, PORTA_HTTP
from .nucleo import Relogio, Sistema
from .ota import GerenciadorOTA
from .rede import descoberta, ips_locais, receptor, supervisionar
from .replay import reproduzir
from .web import criar_servidor


def laco_periodico(sistema, resumo_s):
    """10x/s: detectores e localização (monitor e disco 1x/s dentro do Sistema); resumo no terminal."""
    proximo_resumo = time.monotonic() + resumo_s if resumo_s else None
    while True:
        sistema.atualizar()
        if proximo_resumo and time.monotonic() >= proximo_resumo:
            print(sistema.monitor.resumo_linha(sistema.agora()), flush=True)
            proximo_resumo = time.monotonic() + resumo_s
        time.sleep(0.1)


def diagnostico(sistema, segundos):
    """Escuta as placas por alguns segundos e imprime um relatório. 0 se as 4 enviaram."""
    avisar = sistema.monitor.evento_servidor
    supervisionar('descoberta', descoberta, avisar, sistema)
    supervisionar('receptor', receptor, avisar, sistema)
    fim = time.monotonic() + segundos
    while time.monotonic() < fim:
        sistema.monitor.verificar(sistema.agora())
        time.sleep(0.5)
    snap = sistema.monitor.snapshot(sistema.agora())
    print('', flush=True)
    for placa, info in snap['placas'].items():
        if info['status'] == 'aguardando':
            print(f'  [!]  {placa}: nenhum pacote', flush=True)
            continue
        fw = (info.get('firmware') or {}).get('versao_fw')
        linha = (f'{placa}: {info["pacotes"]} pacotes de {info["ip"]}, sinal {info["sinal"] or "?"} '
                 f'({info["rssi_mediana"]} dBm), {info["taxa"]:.1f} pacotes/s' + (f', firmware {fw}' if fw else ''))
        marca = '  [ok]' if info['status'] == 'ok' else '  [!] '
        print(f'{marca} {linha}' + (' — ' + '; '.join(info['avisos']) if info['avisos'] else ''), flush=True)
    if not snap['resumo']['online']:
        print('       Confira: placas ligadas e com o firmware novo, hotspot em 2,4 GHz e regras do firewall.', flush=True)
    return 0 if snap['resumo']['online'] == len(PLACAS) else 1


def abrir_painel(servidor_http):
    for _ in range(3):
        try:
            return servidor_http()
        except OSError as exc:
            print(f'Porta do painel ocupada ({exc}). Outro SmartRoom já está aberto? Tentando de novo...', flush=True)
            time.sleep(2)
    raise SystemExit('Não foi possível abrir o painel. Feche a outra instância e tente de novo.')


def main(argv=None):
    ap = argparse.ArgumentParser(prog='smartroom', description=f'SmartRoom {__version__} - servidor, painel e monitor das placas')
    ap.add_argument('--verbose', action='store_true', help='uma linha por pacote recebido')
    ap.add_argument('--resumo', type=int, default=10, metavar='S', help='resumo das placas no terminal a cada S s (0 desliga)')
    ap.add_argument('--diagnostico', type=int, metavar='S', help='só escuta as placas por S s, imprime um relatório e sai')
    ap.add_argument('--replay', type=Path, metavar='CSV', help='reproduz um CSV gravado em vez de ouvir as placas')
    ap.add_argument('--velocidade', type=float, default=1.0, help='velocidade do replay (ex.: 5 = 5x mais rápido)')
    ap.add_argument('--repetir', action='store_true', help='repete o replay em loop')
    ap.add_argument('--inicio', type=float, default=0.0, metavar='S', help='começa o replay S segundos depois do início da gravação')
    ap.add_argument('--sem-csv', action='store_true', help='não grava CSV em software/dados')
    ap.add_argument('--porta-http', type=int, default=PORTA_HTTP)
    ap.add_argument('--abrir', action='store_true', help='abre o painel no navegador')
    ap.add_argument('--treino', help=argparse.SUPPRESS)  # aceito por compatibilidade; não é mais usado
    args = ap.parse_args(argv)

    if args.diagnostico:
        sistema = Sistema(gravar_csv=False)
        return diagnostico(sistema, args.diagnostico)

    em_replay = args.replay is not None
    if em_replay and not args.replay.is_file():
        raise SystemExit(f'Arquivo não encontrado: {args.replay}')
    sistema = Sistema(gravar_csv=not (args.sem_csv or em_replay), verbose=args.verbose,
                      relogio=Relogio(args.velocidade if em_replay else 1.0))
    sistema.monitor.definir_info_servidor(porta_dados=PORTA_DADOS, porta_descoberta=PORTA_DESCOBERTA, ips=ips_locais())
    avisar = sistema.monitor.evento_servidor

    if em_replay:
        print(f'Replay de {args.replay.name} ({args.velocidade:g}x{", em loop" if args.repetir else ""})', flush=True)
        supervisionar('replay', lambda: print(f'Replay terminou: {reproduzir(sistema, args.replay, args.velocidade, args.repetir, inicio=args.inicio)} amostras', flush=True), avisar)
    else:
        sistema.ota = GerenciadorOTA(sistema)
        supervisionar('receptor', receptor, avisar, sistema)
        supervisionar('descoberta', descoberta, avisar, sistema)
    supervisionar('periodico', laco_periodico, avisar, sistema, args.resumo)

    servidor = abrir_painel(lambda: criar_servidor(sistema, args.porta_http))
    url = f'http://localhost:{args.porta_http}'
    print(f'Painel: {url}', flush=True)
    print('Monitor das placas ativo: avisos aparecem aqui e no painel. Ctrl+C encerra.', flush=True)
    if args.abrir:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print('\nEncerrando SmartRoom...', flush=True)
    finally:
        servidor.server_close()
        if sistema.ota:
            sistema.ota.parar()
        sistema.fechar()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

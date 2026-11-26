"""Rede: receptor UDP dos dados, descoberta automática e tarefas supervisionadas."""
import json
import socket
import threading
import time

from .config import PORTA_DADOS, PORTA_DESCOBERTA
from .protocolo import PacoteInvalido

ANUNCIO = json.dumps({'smartroom': 'servidor', 'porta': PORTA_DADOS}, separators=(',', ':')).encode()


def supervisionar(nome, alvo, avisar, *args):
    """Roda `alvo` numa thread e a reinicia (com espera crescente) se lançar exceção."""
    def executar():
        tentativa = 0
        while True:
            inicio = time.monotonic()
            try:
                alvo(*args)
                return
            except Exception as exc:  # noqa: BLE001 - tarefa de fundo nunca pode morrer calada
                tentativa = 0 if time.monotonic() - inicio > 60 else tentativa + 1
                espera = min(30, 2 ** min(tentativa, 5))
                avisar('erro', f'tarefa "{nome}" falhou ({type(exc).__name__}: {exc}); reiniciando em {espera} s')
                time.sleep(espera)
    t = threading.Thread(target=executar, name=nome, daemon=True)
    t.start()
    return t


def abrir_udp(porta, avisar, timeout=1.0):
    """Abre a porta UDP; se estiver ocupada, tenta de novo com espera crescente."""
    tentativa = 0
    while True:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.bind(('0.0.0.0', porta))
            s.settimeout(timeout)
            if tentativa:
                avisar('ok', f'porta UDP {porta} aberta')
            return s
        except OSError as exc:
            s.close()
            tentativa += 1
            espera = min(30, 2 ** min(tentativa, 5))
            avisar('erro', f'porta UDP {porta} indisponível ({exc}); nova tentativa em {espera} s')
            time.sleep(espera)


def ips_locais():
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith(('127.', '169.254.')))


def ip_local_para(destino):
    """IP deste PC usado para falar com `destino` (sem enviar nada)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((destino, 9))
        return s.getsockname()[0]
    finally:
        s.close()


def receptor(sistema, porta=PORTA_DADOS):
    avisar = sistema.monitor.evento_servidor
    sock = abrir_udp(porta, avisar)
    print(f'UDP escutando na porta {porta}', flush=True)
    erros = {}
    while True:
        try:
            data, (ip, _) = sock.recvfrom(2048)
        except (socket.timeout, ConnectionResetError):
            continue
        except OSError as exc:
            avisar('erro', f'falha no socket UDP ({exc}); reabrindo')
            sock.close()
            sock = abrir_udp(porta, avisar)
            continue
        try:
            sistema.receber(data, ip)
        except Exception as exc:  # noqa: BLE001 - um pacote ruim nunca derruba o receptor
            chave = (ip, type(exc).__name__)
            agora = time.monotonic()
            if agora - erros.get(chave, 0) > 30:
                erros[chave] = agora
                motivo = str(exc) if isinstance(exc, PacoteInvalido) else f'{type(exc).__name__}: {exc}'
                print(f'Pacote ignorado de {ip}: {motivo}', flush=True)


def descoberta(sistema, porta=PORTA_DESCOBERTA):
    """Anuncia o servidor por broadcast (1x/s, em cada interface) e responde às procuras."""
    avisar = sistema.monitor.evento_servidor
    ouvinte = abrir_udp(porta, avisar, timeout=0.5)
    print(f'Descoberta automática ativa (UDP {porta})', flush=True)
    emissores, proximo, ultimos_ips = {}, 0.0, None
    while True:
        agora = time.monotonic()
        if agora >= proximo:
            atuais = ips_locais()
            if atuais != ultimos_ips:
                sistema.monitor.definir_info_servidor(ips=atuais)
                if ultimos_ips is not None:
                    avisar('info', 'IPs do PC mudaram: ' + (', '.join(atuais) or 'nenhum') + ' (as placas acompanham sozinhas)')
                ultimos_ips = atuais
            for ip in list(emissores):
                if ip not in atuais:
                    emissores.pop(ip).close()
            # No Windows o broadcast 255.255.255.255 sai só pela interface do IP ao qual o socket está ligado.
            for ip in atuais:
                try:
                    if ip not in emissores:
                        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                        s.bind((ip, 0))
                        emissores[ip] = s
                    emissores[ip].sendto(ANUNCIO, ('255.255.255.255', porta))
                except OSError:
                    velho = emissores.pop(ip, None)
                    if velho:
                        velho.close()
            proximo = agora + 1.0
        try:
            data, endereco = ouvinte.recvfrom(512)
        except (socket.timeout, ConnectionResetError):
            continue
        except OSError as exc:
            avisar('erro', f'falha no socket de descoberta ({exc}); reabrindo')
            ouvinte.close()
            ouvinte = abrir_udp(porta, avisar, timeout=0.5)
            continue
        try:
            msg = json.loads(data.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            continue
        if not isinstance(msg, dict) or msg.get('smartroom') != 'procura':
            continue
        try:
            ouvinte.sendto(ANUNCIO, endereco)
        except OSError:
            continue
        sistema.registrar_procura(msg, endereco[0])


def enviar_comando(ip, mensagem: dict, porta=PORTA_DESCOBERTA):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.sendto(json.dumps(mensagem, separators=(',', ':')).encode(), (ip, porta))
    finally:
        s.close()

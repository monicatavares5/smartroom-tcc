"""SmartRoom - inicia o servidor, o painel e o monitor das placas.

O código fica no pacote `smartroom/` (ver smartroom/__main__.py). Este arquivo existe
para manter o comando de sempre:  python smartroom_servidor.py [opções]
Opções: --help
"""
from smartroom.__main__ import main

if __name__ == '__main__':
    raise SystemExit(main())

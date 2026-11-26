"""Gravação de CSV tolerante a falhas e com escrita em lote."""
import csv
import time


class CsvSeguro:
    """Grava linhas sem derrubar o servidor.

    - Escreve em memória e descarrega no disco no máximo 1x por segundo (descarregar()).
    - Se o arquivo ficar bloqueado (ex.: aberto no Excel), avisa uma vez, descarta as
      linhas e tenta reabrir a cada 10 s.
    """

    def __init__(self, caminho, colunas, nome, avisar, ativo=True):
        self.caminho, self.colunas, self.nome, self._avisar, self.ativo = caminho, colunas, nome, avisar, ativo
        self._arquivo = self._escritor = None
        self._proxima_tentativa = 0.0
        self._falhando = False
        self._pendente = False
        if ativo:
            self._abrir(primeira=True)

    def _abrir(self, primeira=False):
        try:
            self.caminho.parent.mkdir(parents=True, exist_ok=True)
            novo = primeira or not self.caminho.exists()
            self._arquivo = self.caminho.open('w' if primeira else 'a', newline='', encoding='utf-8', buffering=1 << 16)
            self._escritor = csv.DictWriter(self._arquivo, fieldnames=self.colunas, extrasaction='ignore')
            if novo:
                self._escritor.writeheader()
                self._arquivo.flush()
            if primeira:
                print(f'{self.nome}: {self.caminho}', flush=True)
            if self._falhando:
                self._avisar('ok', f'{self.nome}: gravação retomada')
            self._falhando = False
        except OSError as exc:
            self._falhou(exc)

    def _falhou(self, exc):
        try:
            if self._arquivo:
                self._arquivo.close()
        except OSError:
            pass
        self._arquivo = self._escritor = None
        self._proxima_tentativa = time.monotonic() + 10
        if not self._falhando:
            self._avisar('erro', f'{self.nome}: não foi possível gravar ({exc}); dados descartados até reabrir '
                                 '(feche o arquivo se estiver aberto em outro programa)')
        self._falhando = True

    def escrever(self, linha):
        if not self.ativo:
            return
        if self._escritor is None:
            if time.monotonic() < self._proxima_tentativa:
                return
            self._abrir()
            if self._escritor is None:
                return
        try:
            self._escritor.writerow(linha)
            self._pendente = True
        except OSError as exc:
            self._falhou(exc)

    def descarregar(self):
        if self._arquivo and self._pendente:
            try:
                self._arquivo.flush()
                self._pendente = False
            except OSError as exc:
                self._falhou(exc)

    def fechar(self):
        self.descarregar()
        try:
            if self._arquivo:
                self._arquivo.close()
        except OSError:
            pass

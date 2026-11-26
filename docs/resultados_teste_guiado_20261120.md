# Resultados — teste guiado de 20/11/2026

Gravação: `software/dados/smartroom_csi_20261120_232804.csv` (rótulos e calibração automáticos pelo teste guiado).
Avaliação: `python analise/avaliar_tempo_real.py dados/smartroom_csi_20261120_232804.csv --ate 623`.

## Montagem

- Sala de 2,5 × 3,5 m; 4 placas ESP32-S3 nos cantos sobre mesas de 1 m de altura, antena para cima.
- iPhone (hotspot, 2,4 GHz) numa mesa de 1 m no centro. Notebook com o painel fora das linhas iPhone → placas.
- ~12 pacotes CSI/s por placa (LLTF, 51 subportadoras úteis). Firmware 2.0.0.

## Protocolo

Sequência cronometrada com instruções por voz: sair do quarto (20 s) → calibração com o quarto vazio (70 s, primeiros 10 s descartados) → vazio (30 s) → andando (40 s) → parado perto de A, B, C e D (30 s cada, 8 s de deslocamento entre elas) → parado no centro (30 s) → vazio (40 s). Os 2 s seguintes a cada troca de etapa não entram nas métricas.

**Trecho válido: até 623 s.** Na etapa "parado no centro" o iPhone foi mexido: a partir dela a forma do espectro mudou ao mesmo tempo nos 4 enlaces e não voltou com o quarto vazio (sem movimento), e o RSSI da placa B deslocou ~9 desvios. Com o roteador em outra posição a calibração deixa de valer, então essa parte foi excluída das métricas e motivou o aviso automático de "ambiente alterado".

## Detecção de presença

| Métrica | Detector em tempo real (v3) | Detector clássico (V7.4) |
|---|---|---|
| **Acurácia balanceada** | **88,8%** | 76,2% |
| Pessoa detectada (sensibilidade) | **94,4%** | 52,3% |
| Quarto vazio reconhecido (especificidade) | 83,1% | 100% |
| Andando | 89,5% | 23,8% |
| Parado perto da A | 100% | 100% |
| Parado perto da B | 100% | 90,9% |
| Parado perto da C | 100% | 0% |
| Parado perto da D | 89,8% | 88,9% |
| Atraso mediano para detectar a entrada | 3,9 s | — |
| Atualização do painel | 10 Hz | 1 Hz |

O clássico acerta o quarto vazio porque quase nunca detecta movimento (andando 23,8%) e não vê a pessoa parada perto da C.

## Localização (detector em tempo real)

| Métrica | Valor |
|---|---|
| Região certa (média dos rótulos "parado perto de X") | 81,5% |
| Erro médio de posição | 0,50 m |
| A / C / D | 100% / 100% / 100% |
| B | 23% (classificada como centro) |

Na etapa "perto da B" o enlace da B foi pouco perturbado (o da D foi mais): limitação física da posição da pessoa em relação à linha de visada iPhone → B (ou obstrução da placa B), não do modelo. Nenhuma combinação de parâmetros testada resolveu B.

## Ajustes feitos a partir deste teste

| Parâmetro | Antes | Depois | Efeito |
|---|---|---|---|
| Limiar τ | quantil 99,5% + 0,5 | quantil 95% + 1, limitado a [2,5; 6] | robusto a transientes na calibração (antes chegou a τ = 23) |
| Referência do quarto vazio | média | mediana por subportadora | idem |
| KAPPA_DESCE | 0,6 | 1,0 | vazio 80% → 83% sem aumentar o atraso |
| Modelo RTI σ / κ | 0,45 m / 6 | 0,70 m / 12 | erro 0,65 → 0,50 m; região 50% → 81,5% |
| Regra de região | < 1 m de uma placa | < 0,5 m do iPhone = centro; senão placa mais próxima | idem |
| Ambiente alterado | — | forma > τ em ≥ 3 placas + movimento médio < 1 por 20 s → aviso | avisou aos 691 s (iPhone mexido), nenhum aviso falso durante o teste |

Busca em grade dos parâmetros do HMM: platô em ~87–90% de acurácia balanceada; o máximo (90,3%) dobrava o atraso (7 s), então foi mantido o equilíbrio acima.

## Limitações

- Um único teste, com 30 s de quarto vazio avaliável e uma pessoa: os números são indicativos. Repetir o teste guiado (de preferência em dias diferentes) dá intervalos de confiança.
- A calibração vale para a posição exata do iPhone e das mesas.

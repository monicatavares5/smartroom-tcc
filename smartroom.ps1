<#
.SYNOPSIS
    SmartRoom - roda o servidor + painel e grava/atualiza o firmware das placas ESP32-S3.

    Antes de tudo confere os requisitos (Python, venv, pacotes, firewall e, no modo
    firmware, o ESP-IDF); se faltar algo, chama o requisitos.ps1 para instalar.

.EXAMPLE
    .\smartroom.ps1
    Confere os requisitos, inicia o servidor e abre o painel.

.EXAMPLE
    .\smartroom.ps1 -Verificar
    Só diagnostica: requisitos, rede e 8 s escutando as placas. Não instala nada.

.EXAMPLE
    .\smartroom.ps1 -Firmware -Placa Todas
    Compila uma vez e grava as 4 placas por USB, uma de cada vez. O MAC de cada uma é
    registrado em software\placas.json (é assim que o servidor sabe qual é a A, B, C, D).

.EXAMPLE
    .\smartroom.ps1 -Firmware -Ota
    Compila e atualiza pelo Wi-Fi todas as placas online (o servidor precisa estar aberto).

.EXAMPLE
    .\smartroom.ps1 -Replay software\dados\smartroom_csi_20261102_174709.csv -Velocidade 2
    Abre o painel reproduzindo uma gravação, sem placas.
#>
[CmdletBinding()]
param(
    [switch]$Verificar,              # só diagnostica
    [switch]$SemNavegador,           # não abre o navegador
    [switch]$AtualizarDependencias,  # reinstala os pacotes Python
    [switch]$PularRequisitos,        # não confere os requisitos

    [switch]$Firmware,               # compila e grava as placas
    [ValidateSet('ESP_A', 'ESP_B', 'ESP_C', 'ESP_D', 'Todas')]
    [string]$Placa = 'Todas',        # qual placa gravar por USB
    [string]$Porta,                  # porta serial (ex.: COM5); detectada se omitida
    [switch]$ConfigurarWifi,         # pergunta de novo SSID/senha do hotspot
    [switch]$Ota,                    # com -Firmware: atualiza pelo Wi-Fi em vez de USB

    [string]$Replay,                 # CSV gravado para reproduzir no painel
    [double]$Velocidade = 1          # velocidade do replay
)

$ErrorActionPreference = 'Stop'
$Raiz             = $PSScriptRoot
$PastaSw          = Join-Path $Raiz 'software'
$PastaFw          = Join-Path $Raiz 'firmware\smartroom_node'
$Sdkconfig        = Join-Path $PastaFw 'sdkconfig'
$SdkDefaults      = Join-Path $PastaFw 'sdkconfig.defaults'
$VenvPython       = Join-Path $PastaSw 'venv\Scripts\python.exe'
$Servidor         = Join-Path $PastaSw 'smartroom_servidor.py'
$ArquivoPlacas    = Join-Path $PastaSw 'placas.json'
$ScriptRequisitos = Join-Path $Raiz 'requisitos.ps1'
$PortaDados       = 5005
$PortaHttp        = 8080
$Placas           = @('ESP_A', 'ESP_B', 'ESP_C', 'ESP_D')

function Escrever-Titulo([string]$t) { Write-Host ''; Write-Host "== $t ==" -ForegroundColor Cyan }
function Escrever-Ok([string]$t)     { Write-Host "  [ok] $t" -ForegroundColor Green }
function Escrever-Aviso([string]$t)  { Write-Host "  [!]  $t" -ForegroundColor Yellow }
function Escrever-Erro([string]$t)   { Write-Host "  [x]  $t" -ForegroundColor Red }
function Escrever-Info([string]$t)   { Write-Host "       $t" }

# ================================================================ requisitos
function Garantir-Requisitos([bool]$paraFirmware) {
    if ($PularRequisitos) { return }
    Escrever-Titulo 'Requisitos'
    $extra = @{}
    if ($paraFirmware) { $extra.Firmware = $true }
    if ($AtualizarDependencias) { $extra.AtualizarDependencias = $true }
    & $ScriptRequisitos -Verificar -Silencioso @extra
    if ($LASTEXITCODE -eq 0) { Escrever-Ok 'Requisitos ok'; return }
    Escrever-Info 'Faltam requisitos; rodando requisitos.ps1...'
    & $ScriptRequisitos @extra
    if ($LASTEXITCODE -ne 0) { Escrever-Erro 'Ambiente incompleto; corrija os itens acima e rode de novo.'; exit 1 }
}

# ================================================================ rede
function Obter-SsidAtual {
    try {
        $linha = netsh wlan show interfaces | Select-String '^\s*SSID\s*:' | Select-Object -First 1
        if ($linha) { return ($linha.Line -split ':', 2)[1].Trim() }
    } catch { }
    return $null
}

function Verificar-Rede {
    Escrever-Titulo 'Rede'
    $ssid = Obter-SsidAtual
    if ($ssid) { Escrever-Ok "Wi-Fi conectado em '$ssid'" } else { Escrever-Aviso 'Wi-Fi não conectado (conecte o PC ao hotspot das placas).' }
    $ips = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
             Where-Object { $_.InterfaceAlias -match 'Wi-?Fi|WLAN|Sem Fio|Wireless' -and $_.IPAddress -notlike '169.254.*' })
    if ($ips) { Escrever-Ok "IP do PC no Wi-Fi: $($ips[0].IPAddress) (as placas encontram o servidor sozinhas)" }
}

# ================================================================ servidor
function Rodar-Python([string[]]$argumentos) {
    Push-Location $PastaSw
    try { & $VenvPython -u $Servidor @argumentos } finally { Pop-Location }
}

function Iniciar-Servidor {
    Escrever-Titulo 'Servidor'
    Escrever-Info "Painel: http://localhost:$PortaHttp   (Ctrl+C para encerrar)"
    Escrever-Info 'Com as 4 placas OK, deixe o quarto vazio e clique em "Calibrar ambiente vazio".'
    Write-Host ''
    $argumentos = @()
    if (-not $SemNavegador) { $argumentos += '--abrir' }
    if ($Replay) {
        $csv = (Resolve-Path $Replay).Path
        $argumentos += @('--replay', $csv, '--velocidade', "$Velocidade", '--repetir')
    }
    Rodar-Python $argumentos
}

function Escutar-Placas([int]$segundos) {
    Escrever-Titulo "Placas (escutando por $segundos s, com descoberta automática)"
    if (Get-NetUDPEndpoint -LocalPort $PortaDados -ErrorAction SilentlyContinue) {
        Escrever-Aviso "A porta $PortaDados já está em uso: o servidor provavelmente já está aberto. Veja 'Saúde das placas' no painel."
        return
    }
    Rodar-Python @('--diagnostico', "$segundos")
}

# ================================================================ firmware
function Ativar-EspIdf {
    if (Get-Command idf.py -ErrorAction SilentlyContinue) { return }
    $ativacao = & $ScriptRequisitos -CaminhoEspIdf
    if (-not $ativacao) { Escrever-Erro 'ESP-IDF não encontrado. Rode .\requisitos.ps1 -Firmware'; exit 1 }
    Escrever-Info "Ativando ESP-IDF ($ativacao)..."
    . $ativacao *> $null
    if (-not (Get-Command idf.py -ErrorAction SilentlyContinue)) { Escrever-Erro 'Falha ao ativar o ESP-IDF.'; exit 1 }
}

function Executar-Idf([string[]]$argumentos, [switch]$Capturar) {
    $ErrorActionPreference = 'Continue'   # avisos do idf.py no stderr não são erro; vale o código de saída
    Push-Location $PastaFw
    try {
        if ($Capturar) {
            $saida = & idf.py @argumentos 2>&1 | ForEach-Object { $l = "$_"; Write-Host $l; $l }
        } else {
            & idf.py @argumentos
        }
        if ($LASTEXITCODE -ne 0) { throw "idf.py $($argumentos -join ' ') falhou (código $LASTEXITCODE)" }
        return $saida
    } finally { Pop-Location }
}

function Ler-Config([string]$chave) {
    if (-not (Test-Path $Sdkconfig)) { return $null }
    $m = Select-String -Path $Sdkconfig -Pattern "^$chave=`"(.*)`"" | Select-Object -First 1
    if ($m) { return $m.Matches[0].Groups[1].Value }
    return $null
}

function Definir-Config([string]$chave, [string]$valor) {
    $linhas = [System.Collections.Generic.List[string]](Get-Content $Sdkconfig)
    $i = $linhas.FindIndex({ param($l) $l -match "^(# )?$chave(=| is not set)" })
    if ($i -ge 0) { $linhas[$i] = "$chave=$valor" } else { $linhas.Add("$chave=$valor") }
    Set-Content -Path $Sdkconfig -Value $linhas -Encoding Ascii
}

function Preparar-Sdkconfig {
    # O sdkconfig (local, com a senha) tem prioridade sobre o sdkconfig.defaults. Se o defaults
    # for mais novo, o sdkconfig é recriado para as mudanças valerem; o Wi-Fi é preservado.
    $ssid = Ler-Config 'CONFIG_EXAMPLE_WIFI_SSID'
    $senha = Ler-Config 'CONFIG_EXAMPLE_WIFI_PASSWORD'
    $desatualizado = (Test-Path $Sdkconfig) -and ((Get-Item $SdkDefaults).LastWriteTime -gt (Get-Item $Sdkconfig).LastWriteTime)
    if (-not (Test-Path $Sdkconfig) -or $desatualizado) {
        if ($desatualizado) { Escrever-Info 'sdkconfig.defaults mudou: recriando o sdkconfig (mantendo o Wi-Fi)...'; Remove-Item $Sdkconfig }
        Executar-Idf @('set-target', 'esp32s3')
    }
    if ($ConfigurarWifi -or -not $ssid -or $ssid -eq 'myssid') {
        $sugestao = Obter-SsidAtual
        $novo = Read-Host "  Nome (SSID) do hotspot$(if ($sugestao) { " [$sugestao]" })"
        $ssid = if ([string]::IsNullOrWhiteSpace($novo)) { $sugestao } else { $novo }
        if ([string]::IsNullOrWhiteSpace($ssid)) { Escrever-Erro 'SSID obrigatório.'; exit 1 }
        $seguro = Read-Host '  Senha do hotspot' -AsSecureString
        $senha = [System.Net.NetworkCredential]::new('', $seguro).Password
    }
    Definir-Config 'CONFIG_EXAMPLE_CONNECT_WIFI' 'y'
    Definir-Config 'CONFIG_EXAMPLE_WIFI_SSID' ('"' + $ssid.Replace('"', '\"') + '"')
    Definir-Config 'CONFIG_EXAMPLE_WIFI_PASSWORD' ('"' + "$senha".Replace('"', '\"') + '"')
    Escrever-Ok "Wi-Fi do firmware: '$ssid' (fica só no sdkconfig local, fora do git)"
}

function Escolher-Porta {
    if ($Porta) { return $Porta }
    for ($t = 0; $t -lt 3; $t++) {
        $portas = @([System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object -Unique)
        if ($portas.Count -eq 1) { return $portas[0] }
        if ($portas.Count -gt 1) {
            Escrever-Info "Portas encontradas: $($portas -join ', ')"
            return (Read-Host '  Qual porta usar?').Trim().ToUpper()
        }
        Escrever-Aviso 'Nenhuma porta serial encontrada. Use um cabo USB de DADOS e a entrada "UART"/"COM" da placa.'
        Read-Host '  Reconecte a placa e pressione Enter para procurar de novo' | Out-Null
    }
    return $null
}

function Registrar-Mac([string]$placa, [string]$mac) {
    $mapa = [ordered]@{}
    if (Test-Path $ArquivoPlacas) {
        try { (Get-Content $ArquivoPlacas -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $mapa[$_.Name] = $_.Value } } catch { }
    }
    foreach ($k in @($mapa.Keys)) { if ($mapa[$k] -eq $placa -or $k -eq $mac) { $mapa.Remove($k) } }
    $mapa[$mac] = $placa
    ($mapa | ConvertTo-Json) | Set-Content -Path $ArquivoPlacas -Encoding UTF8
    Escrever-Ok "$placa = $mac (registrado em software\placas.json)"
}

function Atualizar-PorWifi {
    Escrever-Titulo 'Atualização pelo Wi-Fi (OTA)'
    try {
        $r = Invoke-RestMethod -Method Post -Uri "http://localhost:$PortaHttp/api/ota" -ContentType 'application/json' -Body '{"placas":"todas"}'
        Escrever-Ok $r.mensagem
        Escrever-Info 'Acompanhe em "Saúde das placas" no painel: cada placa baixa, reinicia e aparece com a versão nova.'
    } catch {
        $msg = $_.ErrorDetails.Message
        if ($msg) { try { $msg = ($msg | ConvertFrom-Json).erro } catch { } }
        if (-not $msg) { $msg = 'servidor não respondeu: abra o servidor (.\smartroom.ps1) em outro terminal e rode de novo' }
        Escrever-Erro "OTA não iniciado: $msg"
        exit 1
    }
}

function Gravar-Firmware {
    Escrever-Titulo 'Firmware'
    Ativar-EspIdf
    Preparar-Sdkconfig
    Escrever-Info 'Compilando (o mesmo firmware serve para as 4 placas)...'
    Executar-Idf @('build')
    Escrever-Ok 'Firmware compilado'
    if ($Ota) { Atualizar-PorWifi; return }

    $lista = if ($Placa -eq 'Todas') { $Placas } else { @($Placa) }
    $gravadas = @()
    foreach ($id in $lista) {
        Escrever-Titulo "Gravando $id"
        Read-Host "  Conecte SÓ a placa $id no USB e pressione Enter" | Out-Null
        $com = Escolher-Porta
        if (-not $com) { Escrever-Erro "$id pulada (sem porta serial)."; continue }
        try {
            # erase-flash: apaga tudo antes (a tabela de partições v2 é diferente da antiga)
            $saida = Executar-Idf @('-p', $com, 'erase-flash', 'flash') -Capturar
        } catch {
            Escrever-Erro "$id`: $($_.Exception.Message). Se parou em 'Connecting...', segure o botão BOOT e tente de novo."
            continue
        }
        $mac = ($saida | Select-String -Pattern 'MAC:\s*([0-9a-fA-F]{2}(:[0-9a-fA-F]{2}){5})' | Select-Object -First 1)
        if ($mac) { Registrar-Mac $id $mac.Matches[0].Groups[1].Value.ToLower() }
        else { Escrever-Aviso "Não consegui ler o MAC da $id; o servidor vai atribuir um ID livre quando ela conectar." }
        $gravadas += $id
    }
    Escrever-Titulo 'Resumo'
    if ($gravadas.Count) { Escrever-Ok "Gravadas: $($gravadas -join ', ')" }
    Escrever-Info 'Posicione: A = superior esquerdo, B = superior direito, C = inferior esquerdo, D = inferior direito.'
    Escrever-Info 'Próximas atualizações podem ser pelo Wi-Fi: .\smartroom.ps1 -Firmware -Ota'
}

# ================================================================ principal
if ($Verificar) {
    & $ScriptRequisitos -Verificar
    Verificar-Rede
    if (Test-Path $VenvPython) { Escutar-Placas 8 } else { Escrever-Aviso 'Sem venv: rode .\requisitos.ps1 primeiro.' }
    Write-Host ''
    exit 0
}

Garantir-Requisitos ([bool]$Firmware)
if ($Firmware) { Gravar-Firmware; exit 0 }
if (-not $Replay) { Verificar-Rede }
Iniciar-Servidor

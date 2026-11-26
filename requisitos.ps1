<#
.SYNOPSIS
    SmartRoom - instala e configura tudo o que o projeto precisa (o "requirements" do ambiente).

.DESCRIPTION
    Verifica cada requisito e instala/configura só o que estiver faltando:

      Para o servidor (sempre):
        1. Python 3.10+                      -> winget install Python.Python.3.13
        2. Ambiente virtual software\venv    -> python -m venv
        3. Pacotes Python                    -> pip install -r software\requirements.txt
        4. Firewall: UDP 5005 (dados), UDP 5006 (descoberta) e TCP 8081 (OTA)  -> New-NetFirewallRule (pede admin)

      Para gravar as placas (-Firmware):
        5. ESP-IDF v6.0.3 para ESP32-S3      -> winget install Espressif.EIM-CLI + eim install

    O smartroom.ps1 chama este script sozinho quando falta algo. Ele também pode ser
    rodado à mão a qualquer momento (é seguro rodar de novo: o que já está ok é pulado).

.EXAMPLE
    .\requisitos.ps1
    Instala/configura o que falta para rodar o servidor.

.EXAMPLE
    .\requisitos.ps1 -Firmware
    Idem, e também o ESP-IDF para gravar as placas (download grande, ~8 GB).

.EXAMPLE
    .\requisitos.ps1 -Verificar
    Só mostra o que está ok e o que falta. Código de saída 0 = tudo ok, 1 = falta algo.
#>
[CmdletBinding()]
param(
    # Inclui o ESP-IDF (necessário só para gravar o firmware).
    [switch]$Firmware,
    # Só verifica; não instala nada.
    [switch]$Verificar,
    # Reinstala os pacotes Python mesmo que o requirements.txt não tenha mudado.
    [switch]$AtualizarDependencias,
    # Não imprime a tabela (usado pelo smartroom.ps1 para checar rápido).
    [switch]$Silencioso,
    # Só imprime o caminho do export.ps1 do ESP-IDF (vazio se não houver) e sai.
    [switch]$CaminhoEspIdf
)

$ErrorActionPreference = 'Stop'
$Raiz           = $PSScriptRoot
$PastaSw        = Join-Path $Raiz 'software'
$Venv           = Join-Path $PastaSw 'venv'
$VenvPython     = Join-Path $Venv 'Scripts\python.exe'
$RequirementsPy = Join-Path $PastaSw 'requirements.txt'
$MarcaDeps      = Join-Path $Venv '.requirements.sha256'
$RegrasFirewall = @('UDP 5005', 'UDP 5006', 'TCP 8081')   # dados, descoberta, OTA
$PythonWinget   = 'Python.Python.3.13'
$EspIdfVersao   = 'v6.0.3'      # mesma do firmware/smartroom_node/dependencies.lock
$EspIdfAlvo     = 'esp32s3'
$EimJson        = 'C:\Espressif\tools\eim_idf.json'

function Escrever-Titulo([string]$t) { if (-not $Silencioso) { Write-Host ''; Write-Host "== $t ==" -ForegroundColor Cyan } }
function Escrever-Ok([string]$t)     { if (-not $Silencioso) { Write-Host "  [ok] $t" -ForegroundColor Green } }
function Escrever-Aviso([string]$t)  { if (-not $Silencioso) { Write-Host "  [!]  $t" -ForegroundColor Yellow } }
function Escrever-Erro([string]$t)   { if (-not $Silencioso) { Write-Host "  [x]  $t" -ForegroundColor Red } }
function Escrever-Info([string]$t)   { if (-not $Silencioso) { Write-Host "       $t" } }

function Atualizar-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Exigir-Winget {
    if (Get-Command winget -ErrorAction SilentlyContinue) { return $true }
    Escrever-Erro 'winget não encontrado. Instale o "Instalador de Aplicativo" pela Microsoft Store e rode de novo.'
    return $false
}

function Winget-Instalar([string]$id) {
    if (-not (Exigir-Winget)) { return $false }
    Write-Host "       winget install $id ..."
    winget install --id $id -e --accept-package-agreements --accept-source-agreements --disable-interactivity
    Atualizar-Path
    return ($LASTEXITCODE -eq 0 -or $LASTEXITCODE -eq -1978335189)  # -1978335189 = já instalado
}

# ================================================================ 1. Python
function Encontrar-Python {
    foreach ($cand in @(@('py', '-3'), @('python'))) {
        $exe = Get-Command $cand[0] -ErrorAction SilentlyContinue
        if (-not $exe) { continue }
        $extra = @($cand | Select-Object -Skip 1)
        try {
            $versao = & $exe.Source @extra -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>$null
            if ($LASTEXITCODE -eq 0 -and $versao) {
                $p = "$versao".Trim().Split('.')
                if ([int]$p[0] -eq 3 -and [int]$p[1] -ge 10) {
                    return @{ Exe = $exe.Source; Args = $extra; Versao = "$versao".Trim() }
                }
            }
        } catch { }
    }
    return $null
}

function Checar-Python {
    if (Test-Path $VenvPython) { return @{ Ok = $true; Detalhe = 'usando o Python do venv' } }
    $py = Encontrar-Python
    if ($py) { return @{ Ok = $true; Detalhe = "Python $($py.Versao)" } }
    return @{ Ok = $false; Detalhe = 'Python 3.10+ não encontrado' }
}
function Instalar-Python { [void](Winget-Instalar $PythonWinget) }

# ================================================================ 2. venv
function Checar-Venv {
    if (-not (Test-Path $VenvPython)) { return @{ Ok = $false; Detalhe = 'software\venv não existe' } }
    try {
        $v = (& $VenvPython -c 'import sys; print(sys.version.split()[0])' 2>$null)
        if ($LASTEXITCODE -eq 0) { return @{ Ok = $true; Detalhe = "software\venv (Python $("$v".Trim()))" } }
    } catch { }
    return @{ Ok = $false; Detalhe = 'software\venv está quebrado (Python foi removido/atualizado?)' }
}
function Instalar-Venv {
    $py = Encontrar-Python
    if (-not $py) { Escrever-Erro 'Sem Python para criar o venv.'; return }
    if (Test-Path $Venv) { Remove-Item $Venv -Recurse -Force }
    Write-Host "       python -m venv software\venv (Python $($py.Versao)) ..."
    & $py.Exe @($py.Args) -m venv $Venv
}

# ================================================================ 3. pacotes Python
function Hash-Requirements {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    [System.BitConverter]::ToString($sha.ComputeHash([System.IO.File]::ReadAllBytes($RequirementsPy))).Replace('-', '')
}
function Checar-Dependencias {
    if (-not (Test-Path $VenvPython)) { return @{ Ok = $false; Detalhe = 'aguardando o venv' } }
    if ($AtualizarDependencias) { return @{ Ok = $false; Detalhe = 'reinstalação pedida' } }
    $anterior = if (Test-Path $MarcaDeps) { (Get-Content $MarcaDeps -Raw).Trim() } else { '' }
    if ($anterior -ne (Hash-Requirements)) { return @{ Ok = $false; Detalhe = 'requirements.txt novo ou alterado' } }
    return @{ Ok = $true; Detalhe = 'requirements.txt instalado' }
}
function Instalar-Dependencias {
    Write-Host '       pip install -r software\requirements.txt ...'
    & $VenvPython -m pip install --disable-pip-version-check -q -r $RequirementsPy
    if ($LASTEXITCODE -eq 0) { Set-Content -Path $MarcaDeps -Value (Hash-Requirements) }
}

# ================================================================ 4. firewall
function Regras-Faltando {
    $RegrasFirewall | Where-Object {
        -not (Get-NetFirewallRule -DisplayName "SmartRoom $_" -ErrorAction SilentlyContinue |
              Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Action -eq 'Allow' })
    }
}
function Checar-Firewall {
    $faltando = @(Regras-Faltando)
    if ($faltando.Count -eq 0) { return @{ Ok = $true; Detalhe = "$($RegrasFirewall -join ', ') liberadas" } }
    return @{ Ok = $false; Detalhe = "falta liberar $($faltando -join ', ')" }
}
function Instalar-Firewall {
    $faltando = @(Regras-Faltando)
    $cmd = ($faltando | ForEach-Object {
        $proto, $porta = $_ -split ' '
        "New-NetFirewallRule -DisplayName 'SmartRoom $_' -Direction Inbound -Protocol $proto -LocalPort $porta -Action Allow -Profile Any | Out-Null"
    }) -join '; '
    $shell = if (Get-Command pwsh -ErrorAction SilentlyContinue) { 'pwsh' } else { 'powershell' }
    Write-Host '       Criando regras de firewall (o Windows vai pedir permissão de administrador)...'
    try {
        $p = Start-Process $shell -Verb RunAs -Wait -PassThru -WindowStyle Hidden -ArgumentList '-NoProfile', '-Command', $cmd
        if ($p.ExitCode -ne 0) { Escrever-Erro "New-NetFirewallRule falhou (código $($p.ExitCode))." }
    } catch {
        Escrever-Erro 'Permissão de administrador negada.'
    }
}

# ================================================================ 5. ESP-IDF
function Encontrar-Eim {
    $c = Get-Command eim -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $link = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links\eim.exe'
    if (Test-Path $link) { return $link }
    return $null
}

function Instalacoes-Eim {
    if (-not (Test-Path $EimJson)) { return @() }
    try {
        $j = Get-Content $EimJson -Raw | ConvertFrom-Json
        $sel = @($j.idfInstalled | Where-Object { $_.id -eq $j.idfSelectedId })
        return @($sel + @($j.idfInstalled | Where-Object { $_.id -ne $j.idfSelectedId }))
    } catch { return @() }
}

function Encontrar-EspIdf {
    # Devolve o script de ativação de uma instalação COMPLETA do ESP-IDF, ou $null.
    # Instalações do EIM usam o próprio "activationScript" (o export.ps1 delas não ativa o idf.py).
    $candidatos = @()
    foreach ($i in Instalacoes-Eim) {
        if ($i.status -ne 'in_progress' -and $i.activationScript) { $candidatos += $i.activationScript }
    }
    if ($env:IDF_PATH) { $candidatos += (Join-Path $env:IDF_PATH 'export.ps1') }
    foreach ($i in Instalacoes-Eim) {
        if ($i.status -ne 'in_progress') { $candidatos += (Join-Path $i.path 'export.ps1') }
    }
    $emAndamento = @(Instalacoes-Eim | Where-Object { $_.status -eq 'in_progress' } | ForEach-Object { $_.path })
    foreach ($base in @('C:\esp', 'C:\Espressif\frameworks', (Join-Path $HOME 'esp'))) {
        if (-not (Test-Path $base)) { continue }
        foreach ($d in Get-ChildItem $base -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending) {
            foreach ($idf in @((Join-Path $d.FullName 'esp-idf'), $d.FullName)) {
                if ($emAndamento -notcontains $idf) { $candidatos += (Join-Path $idf 'export.ps1') }
            }
        }
    }
    return ($candidatos | Where-Object { Test-Path $_ } | Select-Object -First 1)
}

function Checar-EspIdf {
    $export = Encontrar-EspIdf
    if ($export) {
        $inst = @(Instalacoes-Eim | Where-Object { $_.activationScript -eq $export }) | Select-Object -First 1
        return @{ Ok = $true; Detalhe = $(if ($inst) { "$($inst.path) (EIM)" } else { Split-Path $export -Parent }) }
    }
    $incompleta = @(Instalacoes-Eim | Where-Object { $_.status -eq 'in_progress' })
    if ($incompleta.Count) { return @{ Ok = $false; Detalhe = "instalação incompleta em $($incompleta[0].path)" } }
    return @{ Ok = $false; Detalhe = 'não instalado' }
}

function Instalar-EspIdf {
    $eim = Encontrar-Eim
    if (-not $eim) {
        if (-not (Winget-Instalar 'Espressif.EIM-CLI')) { Escrever-Erro 'Não foi possível instalar o EIM.'; return }
        $eim = Encontrar-Eim
        if (-not $eim) { Escrever-Erro 'EIM instalado, mas não encontrado. Abra um terminal novo e rode de novo.'; return }
    }
    $incompleta = @(Instalacoes-Eim | Where-Object { $_.status -eq 'in_progress' }) | Select-Object -First 1
    Write-Host '       Instalando ESP-IDF (download grande, pode levar de 20 min a 1 h)...'
    if ($incompleta) {
        Write-Host "       Retomando instalação incompleta em $($incompleta.path) ..."
        & $eim fix -p $incompleta.path -n true --do-not-track true
        if ($LASTEXITCODE -eq 0 -and (Encontrar-EspIdf)) { return }
        Write-Host '       Não deu para retomar; reinstalando do zero...'
        & $eim remove $incompleta.name 2>&1 | Out-Null
    }
    & $eim install -i $EspIdfVersao -t $EspIdfAlvo -n true -a true --do-not-track true --cleanup true
}

# ================================================================ principal
if ($CaminhoEspIdf) {
    $e = Encontrar-EspIdf
    if ($e) { Write-Output $e }
    exit 0
}

$itens = @(
    @{ Nome = 'Python 3.10+';         Checar = ${function:Checar-Python};       Instalar = ${function:Instalar-Python};       Obrigatorio = $true }
    @{ Nome = 'Ambiente virtual';     Checar = ${function:Checar-Venv};         Instalar = ${function:Instalar-Venv};         Obrigatorio = $true }
    @{ Nome = 'Pacotes Python';       Checar = ${function:Checar-Dependencias}; Instalar = ${function:Instalar-Dependencias}; Obrigatorio = $true }
    @{ Nome = 'Firewall do Windows';  Checar = ${function:Checar-Firewall};     Instalar = ${function:Instalar-Firewall};     Obrigatorio = $false }
)
if ($Firmware) {
    $itens += @{ Nome = "ESP-IDF $EspIdfVersao"; Checar = ${function:Checar-EspIdf}; Instalar = ${function:Instalar-EspIdf}; Obrigatorio = $true }
}

Escrever-Titulo $(if ($Verificar) { 'Requisitos (verificação)' } else { 'Requisitos' })
$faltandoObrigatorio = @()
$faltandoQualquer = @()
foreach ($item in $itens) {
    $r = & $item.Checar
    if (-not $r.Ok -and -not $Verificar) {
        Escrever-Aviso "$($item.Nome): $($r.Detalhe) -> instalando/configurando"
        try { & $item.Instalar } catch { Escrever-Erro "$($item.Nome): $($_.Exception.Message)" }
        $r = & $item.Checar
    }
    if ($r.Ok) {
        Escrever-Ok "$($item.Nome): $($r.Detalhe)"
    } else {
        $faltandoQualquer += $item.Nome
        if ($item.Obrigatorio) { $faltandoObrigatorio += $item.Nome; Escrever-Erro "$($item.Nome): $($r.Detalhe)" }
        else { Escrever-Aviso "$($item.Nome): $($r.Detalhe) (recomendado; sem isso as placas podem ser bloqueadas)" }
    }
}

if ($Verificar) {
    if ($faltandoQualquer.Count) {
        Escrever-Info "Falta: $($faltandoQualquer -join ', '). Rode .\requisitos.ps1$(if ($Firmware) { ' -Firmware' }) para instalar."
        exit 1
    }
    exit 0
}
if ($faltandoObrigatorio.Count) {
    Escrever-Erro "Não foi possível preparar: $($faltandoObrigatorio -join ', '). Veja as mensagens acima."
    exit 1
}
Escrever-Ok 'Ambiente pronto.'
exit 0

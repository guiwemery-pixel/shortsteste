# Baixa a versao mais recente do CorteCerto do GitHub e substitui os arquivos do site,
# sem mexer no ambiente (.venv), nos cortes e fundos enviados (data) e nas configuracoes (.env).
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$url = "https://github.com/guiwemery-pixel/shortsteste/archive/refs/heads/claude/vibrant-mccarthy-so66f2.zip"
$pasta = $PSScriptRoot
$zip = Join-Path $env:TEMP "cortecerto-atualizacao.zip"
$tmp = Join-Path $env:TEMP "cortecerto-atualizacao"

try {
    Write-Host "Baixando a versao mais recente do CorteCerto..."
    Invoke-WebRequest $url -OutFile $zip -UseBasicParsing
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    Expand-Archive $zip $tmp -Force
    $origem = (Get-ChildItem $tmp -Directory | Select-Object -First 1).FullName

    robocopy $origem $pasta /E /XD .venv data /XF .env /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "falha ao copiar os arquivos (codigo $LASTEXITCODE)" }

    Remove-Item $zip, $tmp -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Atualizado!"
    exit 0
}
catch {
    Write-Host ""
    Write-Host "Nao foi possivel atualizar: $($_.Exception.Message)"
    Write-Host "Verifique sua internet e tente de novo."
    exit 1
}

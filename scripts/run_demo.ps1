<#
.SYNOPSIS
    Демонстрационный запуск «с нуля»: окружение, миграции, данные, сервер.

.DESCRIPTION
    Скрипт — тонкая обёртка над tasks.ps1, чтобы демонстрация запускалась одной
    командой. Каждый шаг доступен и отдельно: .\tasks.ps1 install|migrate|seed|run
#>
[CmdletBinding()]
param(
    [string]$HostName = '127.0.0.1',
    [int]$Port = 8000,
    [switch]$Regenerate
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$tasks = Join-Path $root 'tasks.ps1'
$py = Join-Path $root '.venv\Scripts\python.exe'

& $tasks install
if ($LASTEXITCODE -ne 0) { throw 'Не удалось подготовить окружение' }

if ($Regenerate) {
    & $py -m sue.datagen --out data/fixtures
    if ($LASTEXITCODE -ne 0) { throw 'Генерация фикстур завершилась с ошибкой' }
}

& $tasks seed
if ($LASTEXITCODE -ne 0) { throw 'Загрузка демонстрационных данных не выполнена' }

& $tasks run -HostName $HostName -Port $Port

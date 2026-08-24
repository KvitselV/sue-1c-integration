<#
.SYNOPSIS
    Единая точка запуска рабочих команд проекта на Windows (аналог Makefile).

.EXAMPLE
    .\tasks.ps1 install
    .\tasks.ps1 lint
    .\tasks.ps1 test
    .\tasks.ps1 seed
    .\tasks.ps1 run -Port 8080
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('help', 'install', 'lint', 'format', 'typecheck', 'test', 'test-pg',
        'test-core', 'coverage', 'migrate', 'gen-data', 'seed', 'run',
        'docker-up', 'docker-down', 'check', 'clean')]
    [string]$Task = 'help',

    [string]$HostName = '127.0.0.1',
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$Venv = Join-Path $PSScriptRoot '.venv'
$Bin = Join-Path $Venv 'Scripts'
$Py = Join-Path $Bin 'python.exe'

function Invoke-Step {
    param([string]$Title, [scriptblock]$Body)

    Write-Host "==> $Title" -ForegroundColor Cyan
    & $Body
    if ($LASTEXITCODE -ne 0) {
        throw "Шаг «$Title» завершился с кодом $LASTEXITCODE"
    }
}

function Assert-Venv {
    if (-not (Test-Path $Py)) {
        throw "Окружение не найдено. Выполните: .\tasks.ps1 install"
    }
}

function Show-Help {
    Write-Host 'Доступные команды:' -ForegroundColor Cyan
    ([ordered]@{
        install       = 'Создать .venv и установить зависимости'
        lint          = 'Ruff: проверка правил и форматирования'
        format        = 'Ruff: автоисправление и форматирование'
        typecheck     = 'mypy'
        test          = 'Тесты на SQLite'
        'test-pg'     = 'Тесты на PostgreSQL (нужен SUE_TEST_PG_URL)'
        'test-core'   = 'Покрытие ядра, порог 85%'
        coverage      = 'Покрытие с HTML-отчётом'
        check         = 'lint + typecheck + test (как в CI)'
        migrate       = 'alembic upgrade head'
        'gen-data'    = 'Сгенерировать фикстуры'
        seed          = 'Миграции и загрузка демо-данных штатным ETL'
        run           = 'Запустить приложение (-HostName, -Port)'
        'docker-up'   = 'PostgreSQL + приложение в Docker'
        'docker-down' = 'Остановить контейнеры и удалить томы'
        clean         = 'Удалить кэши и артефакты'
    }).GetEnumerator() | ForEach-Object {
        Write-Host ('  {0,-13} {1}' -f $_.Key, $_.Value)
    }
}

switch ($Task) {
    'help' { Show-Help }

    'install' {
        if (-not (Test-Path $Py)) {
            Invoke-Step 'Создание виртуального окружения' { python -m venv $Venv }
        }
        Invoke-Step 'Обновление pip' { & $Py -m pip install --upgrade pip --quiet }
        Invoke-Step 'Зависимости (runtime + dev)' { & $Py -m pip install -r requirements-dev.txt }
        Invoke-Step 'Пакет sue в режиме разработки' { & $Py -m pip install -e . --no-deps }
        Invoke-Step 'Хуки pre-commit' { & $Py -m pre_commit install }
    }

    'lint' {
        Assert-Venv
        Invoke-Step 'ruff check' { & $Py -m ruff check . }
        Invoke-Step 'ruff format --check' { & $Py -m ruff format --check . }
    }

    'format' {
        Assert-Venv
        Invoke-Step 'ruff check --fix' { & $Py -m ruff check --fix . }
        Invoke-Step 'ruff format' { & $Py -m ruff format . }
    }

    'typecheck' {
        Assert-Venv
        Invoke-Step 'mypy' { & $Py -m mypy }
    }

    'test' {
        Assert-Venv
        Invoke-Step 'pytest' { & $Py -m pytest }
    }

    'test-pg' {
        Assert-Venv
        if (-not $env:SUE_TEST_PG_URL) {
            throw 'Задайте SUE_TEST_PG_URL, например postgresql+psycopg2://sue:sue@localhost:5432/sue'
        }
        Invoke-Step 'pytest -m pg' { & $Py -m pytest -m pg -p no:randomly }
    }

    'test-core' {
        Assert-Venv
        Invoke-Step 'Покрытие ядра' {
            & $Py -m pytest -q --cov=sue.domain --cov=sue.etl --cov=sue.adapter_1c `
                --cov=sue.money --cov-report=term-missing --cov-fail-under=85
        }
    }

    'coverage' {
        Assert-Venv
        Invoke-Step 'pytest --cov' {
            & $Py -m pytest --cov --cov-report=term-missing --cov-report=html
        }
        Write-Host 'Отчёт: htmlcov\index.html' -ForegroundColor Green
    }

    'check' {
        Assert-Venv
        Invoke-Step 'ruff check' { & $Py -m ruff check . }
        Invoke-Step 'ruff format --check' { & $Py -m ruff format --check . }
        Invoke-Step 'mypy' { & $Py -m mypy }
        Invoke-Step 'pytest' { & $Py -m pytest }
        Write-Host 'Все проверки пройдены' -ForegroundColor Green
    }

    'migrate' {
        Assert-Venv
        Invoke-Step 'alembic upgrade head' { & $Py -m alembic upgrade head }
    }

    'gen-data' {
        Assert-Venv
        Invoke-Step 'Генерация фикстур' { & $Py -m sue.datagen --out data/fixtures }
    }

    'seed' {
        Assert-Venv
        Invoke-Step 'alembic upgrade head' { & $Py -m alembic upgrade head }
        Invoke-Step 'Загрузка демо-данных' { & $Py scripts/seed.py }
    }

    'run' {
        Assert-Venv
        Write-Host "Интерфейс: http://${HostName}:$Port/ui" -ForegroundColor Green
        Write-Host "Документация API: http://${HostName}:$Port/docs" -ForegroundColor Green
        & $Py -m uvicorn sue.main:app --host $HostName --port $Port --reload
    }

    'docker-up' {
        Invoke-Step 'docker compose up' { docker compose up --build -d }
        Invoke-Step 'Миграции в контейнере' { docker compose exec -T api alembic upgrade head }
        Invoke-Step 'Демо-данные в контейнере' { docker compose exec -T api python scripts/seed.py }
        Write-Host 'Интерфейс: http://localhost:8000/ui' -ForegroundColor Green
    }

    'docker-down' {
        Invoke-Step 'docker compose down -v' { docker compose down -v }
    }

    'clean' {
        $targets = @('.pytest_cache', '.ruff_cache', '.mypy_cache', 'htmlcov',
            '.coverage', 'coverage.xml', 'report.xml')
        foreach ($item in $targets) {
            Remove-Item -Recurse -Force $item -ErrorAction SilentlyContinue
        }
        Get-ChildItem -Recurse -Directory -Filter __pycache__ -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notlike "*\.venv\*" } |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host 'Кэши удалены' -ForegroundColor Green
    }
}

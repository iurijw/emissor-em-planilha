@echo off
setlocal EnableExtensions
title Emissor em Planilha

rem Instala o que faltar e inicia o Emissor em Planilha.
rem Na primeira vez (e sempre que o codigo for atualizado) gera a pagina e instala as
rem dependencias; nas demais vezes, so inicia. Deixe esta janela aberta enquanto usar.
rem
rem Opcoes (descomente e ajuste):
rem   set EMISSOR_PORT=8000
rem   set EMISSOR_IPS_PERMITIDOS=192.168.0.0/24

if not defined EMISSOR_PORT set EMISSOR_PORT=8000

rem pushd funciona tambem em unidade de rede (caminho UNC)
pushd "%~dp0" || (
    echo ERRO: nao foi possivel acessar a pasta "%~dp0"
    pause
    exit /b 1
)

where uv >nul 2>nul || goto :sem_uv

rem A pagina precisa ser gerada se frontend\dist nao existe ou se algum arquivo do frontend
rem e mais novo que ela (codigo atualizado).
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$f='frontend'; $dist=Join-Path $f 'dist\index.html'; if (-not (Test-Path $dist)) { exit 1 };" ^
  "$t=(Get-Item $dist).LastWriteTimeUtc;" ^
  "$itens=@(Get-ChildItem -Recurse -File (Join-Path $f 'src'),(Join-Path $f 'public')) + @(Get-Item (Join-Path $f 'package-lock.json'),(Join-Path $f 'index.html'),(Join-Path $f 'vite.config.ts'));" ^
  "if ($itens | Where-Object { $_.LastWriteTimeUtc -gt $t }) { exit 1 }; exit 0"
if errorlevel 1 call :gerar_pagina || goto :falha

echo Conferindo as dependencias do servidor...
cd backend
uv sync --locked --no-dev || goto :falha

echo.
echo  Emissor em Planilha
echo  -------------------
echo  Neste computador:        http://localhost:%EMISSOR_PORT%
echo  Nos outros computadores: http://%COMPUTERNAME%:%EMISSOR_PORT%
echo  Para encerrar, feche esta janela ou aperte Ctrl+C.
echo.

uv run --locked --no-dev emissor

echo.
echo Emissor em Planilha foi encerrado.
popd
pause
exit /b 0

:gerar_pagina
where npm >nul 2>nul || (
    echo O Node.js nao esta instalado. Ele e necessario para gerar a pagina do sistema.
    echo Instale a versao LTS de https://nodejs.org/ ^(ou: winget install OpenJS.NodeJS.LTS^)
    echo e abra esta janela de novo.
    exit /b 1
)
echo Gerando a pagina (primeira execucao ou codigo atualizado; leva cerca de 1 minuto)...
pushd frontend
call npm ci --no-audit --no-fund || (popd & exit /b 1)
call npm run build || (popd & exit /b 1)
popd
exit /b 0

:sem_uv
echo O "uv" nao esta instalado. Ele instala o Python e as dependencias do servidor.
echo Instale com o comando abaixo (PowerShell) e abra esta janela de novo:
echo.
echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
echo.
echo Mais informacoes: https://docs.astral.sh/uv/getting-started/installation/
popd
pause
exit /b 1

:falha
echo.
echo FALHA ao preparar o Emissor em Planilha. Veja a mensagem acima.
popd
pause
exit /b 1

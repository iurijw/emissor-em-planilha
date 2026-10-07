@echo off
setlocal

rem Inicia o Emissor em Planilha.
rem Acesse pelo navegador: http://<nome-ou-IP-deste-computador>:8000
rem Para limitar o acesso a rede local, defina por exemplo:
rem   set EMISSOR_IPS_PERMITIDOS=192.168.0.0/24

rem Entra na pasta backend, funcionando tanto em unidade mapeada quanto em caminho UNC
pushd "%~dp0backend" || (
    echo ERRO: Nao foi possivel acessar a pasta backend:
    echo "%~dp0backend"
    pause
    exit /b 1
)

if not exist "..\frontend\dist\index.html" (
    echo O frontend ainda nao foi gerado. Rode atualizar.bat primeiro.
    popd
    pause
    exit /b 1
)

echo Iniciando Emissor em Planilha...
echo Diretorio: %CD%
echo.

uv run emissor

echo.
echo Emissor em Planilha foi encerrado.
popd
pause
endlocal
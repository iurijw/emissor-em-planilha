@echo off
rem Atualiza dependencias e gera o frontend. Rode depois de baixar uma nova versao do codigo.
cd /d "%~dp0frontend"
call npm install || goto :erro
call npm run build || goto :erro
cd /d "%~dp0backend"
uv sync || goto :erro
echo.
echo Pronto. Agora rode iniciar.bat
pause
exit /b 0
:erro
echo.
echo FALHA na atualizacao. Veja a mensagem acima.
pause
exit /b 1

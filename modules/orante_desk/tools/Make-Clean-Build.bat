@echo off
chcp 65001 >nul
echo.
echo 这个脚本会删掉你正在用的数据库和登录态，已停用。
echo 要出分发包，请用 tools\15-Make-Share-Package.bat（拷副本，不动本目录）。
echo.
exit /b 1

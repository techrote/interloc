@echo off
setlocal
where cl.exe >nul 2>nul
if errorlevel 1 (
  echo ERROR: cl.exe not found. Run from a Visual Studio Build Tools Developer Command Prompt with a current Windows SDK. 1>&2
  exit /b 2
)
cl.exe /nologo /std:c++20 /EHsc /W4 /O2 /DUNICODE /D_UNICODE wgc_probe.cpp /Fe:wgc_probe.exe /link d3d11.lib dxgi.lib windowsapp.lib user32.lib
if errorlevel 1 exit /b %errorlevel%
echo Built wgc_probe.exe

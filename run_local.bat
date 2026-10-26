@echo off
rem CampusTime local server launcher
rem Double-click this file, then open http://127.0.0.1:8000/student
rem
rem 端口必须是 8000 —— 这个脚本以前写的是 8080（注释里还有一句 :123），
rem 而应用实际一直在 8000 上跑。双击它的人会先看到 Edge 开着一个没人听的端口，
rem 于是一直转圈 —— "后台起不来"很多时候是这么来的，不是应用坏了。
rem
rem 如果这个窗口报 "address already in use"，说明 8000 上已经有一个在跑，
rem 直接开 http://127.0.0.1:8000/student 就行，不用再起第二个。
rem
rem Edge 的路径是**硬编码**的（下面那句 start 指向 Program Files (x86)）。
rem 没装 Edge 的机器上那一句会失败并弹一句"Windows 找不到..."，**但这不影响服务起来**：
rem 批处理是一行一行往下走的，上一句失败不会跳过后面那句 uvicorn —— 所以那台机器上
rem 看到的是"没有自动开浏览器"，不是"后台起不来"。窗口里出现 Uvicorn running 就是好了，
rem 自己开 http://127.0.0.1:8000/student 即可。（本机实测：那句 Edge 路径存在。）
cd /d "%~dp0"
start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --proxy-server="direct://" --proxy-bypass-list="*" http://127.0.0.1:8000/student
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause

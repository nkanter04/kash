' Starts the Kash server in the background with no window (used for auto-start at login).
' Output goes to .cache\server.log. Stop it with stop-kash.bat.
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
If Not fso.FolderExists(dir & "\.cache") Then fso.CreateFolder(dir & "\.cache")
' 0 = hidden window, False = don't wait
sh.Run "cmd /c "".venv\Scripts\python.exe -m uvicorn app:app --port 8050 --reload > .cache\server.log 2>&1""", 0, False

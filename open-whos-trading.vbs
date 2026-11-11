' Opens Who's Trading, starting the Kash server first if it isn't running.
Const URL = "http://localhost:8050/people"
Const HEALTH = "http://localhost:8050/healthz"

Function ServerUp()
    On Error Resume Next
    Dim http
    Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0")
    http.setTimeouts 1000, 1000, 2000, 2000
    http.open "GET", HEALTH, False
    http.send
    ServerUp = (Err.Number = 0 And http.status = 200)
    On Error GoTo 0
End Function

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)

If Not ServerUp() Then
    sh.Run "wscript.exe """ & dir & "\run-kash-hidden.vbs""", 0, False
    For i = 1 To 90
        WScript.Sleep 1000
        If ServerUp() Then Exit For
    Next
    If Not ServerUp() Then
        MsgBox "Kash didn't start within 90 seconds. Details are in .cache\server.log in the Kash folder.", vbExclamation, "Kash"
        WScript.Quit 1
    End If
End If

sh.Run URL

# Stops the Kash server, including one started hidden at login.
# With --reload, uvicorn runs a watcher process plus a worker child (a "spawn_main" python process whose command
# line doesn't mention uvicorn) that actually serves port 8050, so stop: the uvicorn processes, whatever is
# listening on 8050, and all their child processes.
$port = 8050
$all = Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='cmd.exe'"
$ids = @($all | Where-Object { $_.CommandLine -like "*uvicorn app:app --port $port*" } | ForEach-Object { [int]$_.ProcessId })

$listen = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object { [int]$_.OwningProcess })
if (-not $listen) {   # fallback when Get-NetTCPConnection isn't available
  $listen = @(netstat -ano | Select-String ":$port\s.*LISTENING" | ForEach-Object { [int](($_ -split '\s+')[-1]) })
}
$ids += $listen | Where-Object { $_ -gt 0 }

# the serving process records its PID at startup (app.py lifespan); only trust it if it's still a python process
$pidFile = Join-Path $PSScriptRoot ".cache\server.pid"
if (Test-Path $pidFile) {
  $served = [int](Get-Content $pidFile -Raw)
  if ($all | Where-Object { [int]$_.ProcessId -eq $served -and $_.Name -eq 'python.exe' }) { $ids += $served }
  Remove-Item $pidFile -ErrorAction SilentlyContinue
}

do {   # add children of anything already on the list
  $more = @($all | Where-Object { $ids -contains [int]$_.ParentProcessId -and $ids -notcontains [int]$_.ProcessId } | ForEach-Object { [int]$_.ProcessId })
  $ids += $more
} while ($more.Count)

$ids = $ids | Sort-Object -Unique
if ($ids) {
  $ids | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
  "Kash stopped."
} else {
  "Kash was not running."
}

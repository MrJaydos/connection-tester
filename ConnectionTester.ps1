<#
Polls for internet connectivity once per second.
Logs a single DISCONNECTED line when the connection drops, and a single
RECONNECTED line when it comes back, so the log doesn't fill with repeats.
#>

param(
    [string]$LogPath = (Join-Path $PSScriptRoot "connection-log.txt"),
    [int]$IntervalSeconds = 1,
    [string[]]$Targets = @("8.8.8.8:53", "1.1.1.1:53")
)

function Test-InternetConnection {
    param([string[]]$Targets)

    foreach ($target in $Targets) {
        $ip, $port = $target -split ':'
        $client = New-Object System.Net.Sockets.TcpClient
        try {
            $async = $client.BeginConnect($ip, [int]$port, $null, $null)
            if ($async.AsyncWaitHandle.WaitOne(1500) -and $client.Connected) {
                return $true
            }
        }
        catch {
            # try next target
        }
        finally {
            $client.Close()
        }
    }
    return $false
}

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $LogPath -Value $line
    Write-Host $line
}

Write-Host "Connection tester started. Logging to: $LogPath"
Write-Host "Checking every $IntervalSeconds second(s). Press Ctrl+C to stop."

$isConnected = Test-InternetConnection -Targets $Targets
Write-Log ("Monitoring started - internet is {0}." -f ($(if ($isConnected) { "UP" } else { "DOWN" })))

try {
    while ($true) {
        Start-Sleep -Seconds $IntervalSeconds
        $currentlyConnected = Test-InternetConnection -Targets $Targets

        if (-not $currentlyConnected -and $isConnected) {
            Write-Log "DISCONNECTED - internet connection lost."
            $isConnected = $false
        }
        elseif ($currentlyConnected -and -not $isConnected) {
            Write-Log "RECONNECTED - internet connection restored."
            $isConnected = $true
        }
    }
}
finally {
    Write-Log "Monitoring stopped."
}

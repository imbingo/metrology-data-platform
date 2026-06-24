param(
    [ValidateSet("Server", "Client")]
    [string]$Mode = "Server",
    [int]$Port = 8023,
    [string]$ServerIp = ""
)

$ErrorActionPreference = "Stop"

function Get-LocalIPv4Addresses {
    [System.Net.Dns]::GetHostAddresses($env:COMPUTERNAME) |
        Where-Object {
            $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and
            -not $_.IPAddressToString.StartsWith("127.") -and
            -not $_.IPAddressToString.StartsWith("169.254.")
        } |
        ForEach-Object { $_.IPAddressToString }
}

function Start-PortTestServer {
    param([int]$ListenPort)

    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any, $ListenPort)
    $listener.Start()
    $localIps = @(Get-LocalIPv4Addresses)

    Write-Host "============================================================"
    Write-Host "MDCP LAN port test server is listening on port $ListenPort"
    Write-Host "Press Ctrl+C to stop."
    Write-Host "============================================================"
    if ($localIps.Count -gt 0) {
        Write-Host "Try these URLs from another LAN PC:"
        foreach ($ip in $localIps) {
            Write-Host "  http://${ip}:${ListenPort}"
        }
    } else {
        Write-Host "No non-loopback IPv4 address was detected. Check the server network adapter."
    }

    try {
        while ($true) {
            $client = $listener.AcceptTcpClient()
            try {
                $remote = $client.Client.RemoteEndPoint.ToString()
                $stream = $client.GetStream()

                $buffer = New-Object byte[] 1024
                if ($stream.DataAvailable) {
                    [void]$stream.Read($buffer, 0, $buffer.Length)
                }

                $body = @"
MDCP 8023 LAN PORT TEST OK
Computer: $env:COMPUTERNAME
Port: $ListenPort
Remote: $remote
Time: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
"@
                $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)
                $header = "HTTP/1.1 200 OK`r`nContent-Type: text/plain; charset=utf-8`r`nConnection: close`r`nContent-Length: $($bodyBytes.Length)`r`n`r`n"
                $headerBytes = [System.Text.Encoding]::ASCII.GetBytes($header)
                $stream.Write($headerBytes, 0, $headerBytes.Length)
                $stream.Write($bodyBytes, 0, $bodyBytes.Length)
                $stream.Flush()
                Write-Host "Connection OK from $remote"
            } catch {
                Write-Warning "Connection closed before response completed: $($_.Exception.Message)"
            } finally {
                $client.Close()
            }
        }
    } finally {
        $listener.Stop()
    }
}

function Test-PortFromClient {
    param(
        [string]$TargetIp,
        [int]$TargetPort
    )

    if (-not $TargetIp) {
        throw "Client mode requires -ServerIp. Example: .\test_8023_lan_port.ps1 -Mode Client -ServerIp 192.168.1.20 -Port 8023"
    }

    Write-Host "Testing TCP connection to ${TargetIp}:${TargetPort} ..."
    $tcp = [System.Net.Sockets.TcpClient]::new()
    try {
        $async = $tcp.BeginConnect($TargetIp, $TargetPort, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(3000)) {
            throw "TCP connection timed out. Firewall, IP address, network segment, or server listener may be blocking the port."
        }
        $tcp.EndConnect($async)
        Write-Host "TCP OK: ${TargetIp}:${TargetPort} is reachable."
    } finally {
        $tcp.Close()
    }

    $url = "http://${TargetIp}:${TargetPort}"
    Write-Host "Testing HTTP GET $url ..."
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 5
        Write-Host "HTTP OK: status $($response.StatusCode)"
        $text = [string]$response.Content
        if ($text.Length -gt 0) {
            Write-Host "Response preview:"
            Write-Host ($text.Substring(0, [Math]::Min(300, $text.Length)))
        }
    } catch {
        throw "TCP connected, but HTTP request failed: $($_.Exception.Message)"
    }
}

if ($Mode -eq "Server") {
    Start-PortTestServer -ListenPort $Port
} else {
    Test-PortFromClient -TargetIp $ServerIp -TargetPort $Port
}

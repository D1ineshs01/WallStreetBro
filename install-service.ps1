# Run this script as Administrator
# Creates WallStreetBro as a Windows service using NSSM

$serviceName = "WallStreetBro"
$nssm = (Get-Command nssm).Source
$pythonPath = "C:\Windows\py.exe"
$appDir = "C:\Users\dines\OneDrive\Desktop\Projects\Wall Street Bro"
$logDir = "$appDir\logs"

# Create logs folder if it doesn't exist
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# Remove existing service if present
& $nssm stop $serviceName 2>$null
& $nssm remove $serviceName confirm 2>$null

# Install service
& $nssm install $serviceName $pythonPath "-3.11 main.py --mode all"
& $nssm set $serviceName AppDirectory $appDir
& $nssm set $serviceName AppStdout "$logDir\service.log"
& $nssm set $serviceName AppStderr "$logDir\service_error.log"
& $nssm set $serviceName AppRotateFiles 1
& $nssm set $serviceName AppRotateBytes 10485760
& $nssm set $serviceName Start SERVICE_AUTO_START
& $nssm set $serviceName DisplayName "Wall Street Bro Trading Agent"
& $nssm set $serviceName Description "Autonomous trading agent - Grok scanner + Claude executor"

# Start it
& $nssm start $serviceName

# Check status
Start-Sleep -Seconds 2
& $nssm status $serviceName

$ErrorActionPreference = 'Stop'
try {
    Add-Type -AssemblyName System.Drawing
    $config = Get-Content (Join-Path $PSScriptRoot 'config.json') -Raw | ConvertFrom-Json
    $printers = @([System.Drawing.Printing.PrinterSettings]::InstalledPrinters)
    if (-not ($printers -ccontains $config.printer)) {
        Write-Host 'De vereiste Windows-printer is niet gevonden:' $config.printer
        Write-Host 'Geinstalleerde printers:'
        $printers | ForEach-Object { Write-Host ('  ' + $_) }
        throw 'Controleer de printernaam in Windows. Er is niets geinstalleerd.'
    }
    $target = Join-Path $env:LOCALAPPDATA 'Weldingshop\PimPrint'
    New-Item -ItemType Directory -Path $target -Force | Out-Null
    # Keep the pairing credential accessible only to this Windows user and SYSTEM.
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @([Security.Principal.WindowsIdentity]::GetCurrent().User, (New-Object Security.Principal.SecurityIdentifier('S-1-5-18')))) {
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $target -AclObject $acl
    foreach ($name in @('agent.ps1','config.json')) {
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $target -Force
    }
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Startup')) 'Weldingshop PIM Print.lnk'))
    $shortcut.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + (Join-Path $target 'agent.ps1') + '"'
    $shortcut.WorkingDirectory = $target
    $shortcut.Save()
    Start-Process -FilePath $shortcut.TargetPath -ArgumentList $shortcut.Arguments -WindowStyle Hidden
    Write-Host 'Geinstalleerd. De printservice start voortaan bij aanmelden bij Windows.'
    Write-Host 'Open PIM > Labels > Mobiel en klik op Printerstatus vernieuwen.'
    Write-Host 'Bewaar dit installatiepakket prive: het bevat de koppeling met jouw PIM.'
} catch {
    Write-Host ('Installatie niet gelukt: ' + $_.Exception.Message) -ForegroundColor Red
}
Read-Host 'Druk op Enter om te sluiten'

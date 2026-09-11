$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$config = Get-Content (Join-Path $PSScriptRoot 'config.json') -Raw | ConvertFrom-Json
$mutex = New-Object Threading.Mutex($false, 'Local\WeldingshopPimPrint')
if (-not $mutex.WaitOne(0)) { exit }
$pendingPath = Join-Path $PSScriptRoot 'pending.json'
$logPath = Join-Path $PSScriptRoot 'print.log'
Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System;
using System.Drawing;
using System.Drawing.Printing;
using System.IO;
public static class PimLabelPrinter {
    public static bool Available(string name) {
        foreach (string installed in PrinterSettings.InstalledPrinters)
            if (String.Equals(installed, name, StringComparison.Ordinal)) return true;
        return false;
    }
    public static void Print(byte[] bytes, string printer, int copies, string id) {
        if (!Available(printer)) throw new Exception("Printer niet gevonden: " + printer);
        if (copies < 1 || copies > 500) throw new Exception("Ongeldig aantal labels");
        using (var stream = new MemoryStream(bytes))
        using (var image = Image.FromStream(stream))
        using (var document = new PrintDocument()) {
            document.PrinterSettings.PrinterName = printer;
            if (!document.PrinterSettings.IsValid) throw new Exception("Printer niet beschikbaar");
            document.PrintController = new StandardPrintController();
            document.DocumentName = "PIM label " + id;
            PaperSize paper = null;
            foreach (PaperSize size in document.PrinterSettings.PaperSizes) {
                if (Math.Abs(size.Width - 400) <= 3 && Math.Abs(size.Height - 600) <= 3) {
                    paper = size; break;
                }
            }
            if (paper == null) paper = new PaperSize("4x6 inch", 400, 600);
            document.DefaultPageSettings.PaperSize = paper;
            document.DefaultPageSettings.Landscape = true;
            document.DefaultPageSettings.Margins = new Margins(0, 0, 0, 0);
            document.OriginAtMargins = false;
            int page = 0;
            document.PrintPage += (sender, args) => {
                if (Math.Abs(args.PageBounds.Width - 600) > 5 || Math.Abs(args.PageBounds.Height - 400) > 5)
                    throw new Exception("Printerdriver gebruikt geen 4x6 inch liggend. Controleer de Windows-printerinstellingen.");
                args.Graphics.TranslateTransform(-args.PageSettings.HardMarginX, -args.PageSettings.HardMarginY);
                args.Graphics.DrawImage(image, new RectangleF(0, 0, 600, 400));
                page++;
                args.HasMorePages = page < copies;
            };
            document.Print();
        }
    }
}
'@
function Invoke-Pim($path, $body) {
    Invoke-RestMethod -Uri ($config.url + $path) -Method Post -Headers @{Authorization=('Bearer ' + $config.token)} -ContentType 'application/json' -Body ($body | ConvertTo-Json -Compress) -TimeoutSec 20
}
function Save-Pending($value) {
    $temporary = $pendingPath + '.tmp'
    $value | ConvertTo-Json -Compress | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $pendingPath -Force
}
try {
    while ($true) {
        try {
            # After a crash, report uncertainty instead of printing the same job again.
            if (Test-Path $pendingPath) {
                $pending = Get-Content $pendingPath -Raw | ConvertFrom-Json
                if ($pending.state -eq 'printing') {
                    $pending.state = 'uncertain'
                    $pending.detail = 'Printservice onderbroken. Controleer het label en de Windows-wachtrij voordat je opnieuw afdrukt.'
                    Save-Pending $pending
                }
                Invoke-Pim '/complete' $pending | Out-Null
                Remove-Item -LiteralPath $pendingPath
            }
            $printerError = ''
            if (-not [PimLabelPrinter]::Available($config.printer)) {
                $printerError = 'Windows-printer niet gevonden: ' + $config.printer
            }
            $response = Invoke-Pim '/claim' @{printer=$config.printer; error=$printerError}
            $job = $response.job
            if ($null -ne $job) {
                $pending = @{id=$job.id; claim=$job.claim; state='printing'; detail=''}
                Save-Pending $pending
                try {
                    if ($job.printer -ine $config.printer) { throw 'Onverwachte printer in opdracht' }
                    [PimLabelPrinter]::Print([Convert]::FromBase64String($job.image), $config.printer, [int]$job.copies, $job.id)
                    $pending.state = 'submitted'
                    $pending.detail = 'Aangeboden aan de Windows-afdrukwachtrij.'
                } catch {
                    # A driver error can happen after some labels have already printed.
                    $pending.state = 'uncertain'
                    $pending.detail = $_.Exception.Message
                }
                Save-Pending $pending
                Invoke-Pim '/complete' $pending | Out-Null
                Remove-Item -LiteralPath $pendingPath
            }
        } catch {
            if ((Test-Path $logPath) -and (Get-Item $logPath).Length -gt 1048576) { Clear-Content $logPath }
            Add-Content $logPath ((Get-Date -Format s) + ' Verbinding of verwerking mislukt: ' + $_.Exception.Message)
        }
        Start-Sleep -Seconds 5
    }
} finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}

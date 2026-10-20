param(
 [Parameter(Mandatory=$true)][string]$DocxPath,
 [Parameter(Mandatory=$true)][string]$PdfPath
)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$word=$null
$document=$null
$metadata=$null
$failure=$null
try {
 $source=(Resolve-Path -LiteralPath $DocxPath).Path
 $target=[IO.Path]::GetFullPath($PdfPath)
 if([IO.Path]::GetExtension($source) -ne '.docx' -or [IO.Path]::GetExtension($target) -ne '.pdf') {
  throw 'Word input must be DOCX and output must be PDF.'
 }
 $word=New-Object -ComObject Word.Application
 $word.Visible=$false
 $word.DisplayAlerts=0
 $word.AutomationSecurity=3
 $word.Options.SaveNormalPrompt=$false
 # Read-only opening and no-save closing preserve the source OOXML artwork.
 $document=$word.Documents.Open($source,$false,$true,$false)
 $document.Repaginate()
 [void]$document.Fields.Update()
 foreach($section in $document.Sections) {
  foreach($header in $section.Headers) { if($header.Exists){[void]$header.Range.Fields.Update()} }
  foreach($footer in $section.Footers) { if($footer.Exists){[void]$footer.Range.Fields.Update()} }
 }
 $document.Repaginate()
 $pages=[int]$document.ComputeStatistics(2)
 if($pages -lt 1) { throw 'The agreement has no printable pages.' }
 $document.ExportAsFixedFormat($target,17,$false,0,0,1,1,0,$true,$true,1,$true,$true,$false)
 if(-not (Test-Path -LiteralPath $target) -or (Get-Item -LiteralPath $target).Length -lt 1000) {
  throw 'Word did not write a complete PDF.'
 }
 $metadata=@{pages=$pages;engine='Microsoft Word';pdf=$target}
} catch {
 $failure=$_.Exception.Message
} finally {
 # Cleanup errors must not discard a completed export. Only this instance is closed.
 if($null -ne $document) {
  try{$document.Close(0)}catch{}
  try{[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($document)}catch{}
 }
 if($null -ne $word) {
  try{$word.Quit(0)}catch{}
  try{[void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($word)}catch{}
 }
 [GC]::Collect()
 [GC]::WaitForPendingFinalizers()
}
if($null -ne $failure){[Console]::Error.WriteLine($failure);exit 1}
$metadata | ConvertTo-Json -Compress

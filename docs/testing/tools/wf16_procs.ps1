# count test-install processes: the runtime exe and the Chromium it launched (Studio-deps chromium path)
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and ($_.CommandLine -match 'chromium_headless_shell|chromium-1217' -or $_.Name -eq 'conxa-runtime.exe') } | ForEach-Object { "$($_.ProcessId) $($_.Name)" }

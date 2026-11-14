#requires -Version 7.0
[CmdletBinding()]
param()
# No command selection and no execution path exists until the native trust,
# calibration, durable evidence and approval chain are independently verified.
Write-Error 'BLOCKED: native monitoring and independent approval are unavailable; network-sensitive tests cannot run through this entry point.'
exit 1

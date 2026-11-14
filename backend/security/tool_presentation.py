"""Conservative presentations for results without verified content provenance.

These projections are not authorization decisions and do not alter tool data.
"""
import math

RESTRICTED_TOOLS = frozenset({'hardware_status', 'model_scan', 'security_diagnostics', 'code_execution'})


def calculator_presentation(raw):
    value = raw.get('result') if isinstance(raw, dict) else raw
    if type(value) in (int, float) and (type(value) is int or math.isfinite(value)) and len(str(value)) <= 256:
        return f'Result: {value}'
    return 'Calculator result unavailable.'


def restricted_tool_presentation(tool, raw, success=True):
    if not success:
        return 'Tool execution failed.'
    if tool == 'code_execution':
        return 'Code execution output is unavailable: its provenance cannot be verified.'
    if tool == 'security_diagnostics':
        return 'Security diagnostics completed; detailed findings are restricted.'
    if not isinstance(raw, dict):
        return 'Tool completed; details are unavailable.'
    if tool == 'model_scan':
        available = raw.get('available_models')
        running = raw.get('running_models')
        if isinstance(available, list) and isinstance(running, list):
            return f'Model scan completed: {min(len(running), 10000)} active, {min(len(available), 10000)} available.'
        return 'Model scan completed; details are unavailable.'
    if tool == 'hardware_status':
        telemetry = raw.get('telemetry')
        if isinstance(telemetry, dict):
            values = []
            for key, label in [('cpu', 'CPU'), ('ram', 'RAM')]:
                data = telemetry.get(key)
                value = data.get('percent') if isinstance(data, dict) else None
                if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100:
                    values.append(f'{label} {value}%')
            if values:
                return 'Hardware status: ' + ', '.join(values) + '.'
        return 'Hardware status checked; details are unavailable.'
    return 'Tool result unavailable.'

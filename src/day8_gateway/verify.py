"""Bounded acceptance check through the selected proxy, including ChatGPT's trace."""
import subprocess


def check(proxy, expected, count=3):
    results = []
    for _ in range(count):
        process = subprocess.run(
            ['curl', '--silent', '--show-error', '--fail', '--noproxy', '',
             '--connect-timeout', '15', '--max-time', '45', '--proxy', proxy,
             'https://chatgpt.com/cdn-cgi/trace'], capture_output=True, text=True)
        trace = dict(line.split('=', 1) for line in process.stdout.splitlines() if '=' in line)
        ok = process.returncode == 0 and trace.get('ip') == expected and trace.get('h') == 'chatgpt.com'
        # Don't print potentially sensitive proxy URLs, endpoints, or curl errors.
        results.append({'ok': ok, 'curl_exit': process.returncode,
                        'trace_host_ok': trace.get('h') == 'chatgpt.com', 'exit_ip_matches': trace.get('ip') == expected})
    return {'ok': all(r['ok'] for r in results), 'checks': results}

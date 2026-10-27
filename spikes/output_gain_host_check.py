"""Verify gain API, persistence and SSE on a separate, finite Windows engine instance.

Run with Windows Python. Choose an idle capture endpoint and unused output; the harness
never changes routing. APPDATA is isolated so the owner's settings remain untouched.
No synthetic audio is played. This validates wiring, not perceived loudness or headroom.
"""
import argparse
import json
import os
import shutil
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', required=True)
    parser.add_argument('--install', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--port', type=int, default=8735)
    args = parser.parse_args()
    base = f'http://127.0.0.1:{args.port}'
    # Refuse an occupied port instead of accidentally changing another running instance.
    import socket
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', args.port))
    install = Path(args.install)
    with tempfile.TemporaryDirectory(prefix='wlbs-gain-check-') as temp:
        env = dict(os.environ, APPDATA=temp, PATH=str(install) + os.pathsep + os.environ.get('PATH', ''))
        # Run a local copy: Windows cannot load DLL symlinks in a WSL build directory.
        executable = Path(temp) / 'wlbs_engine.exe'
        shutil.copy2(args.engine, executable)
        command = [str(executable), '--secs', '10', '--port', str(args.port), '--no-camera',
                   '--source', args.source, '--output', args.output, '--block', '512',
                   '--latency-ms', '32', '--brir-dir', str(install / 'packs/default'),
                   '--hp-eq-dir', str(install / 'hp_eq'), '--web-dir', str(install / 'web_cockpit')]

        def config():
            with urllib.request.urlopen(base + '/api/get_config', timeout=2) as response:
                return json.load(response)

        def post(name, value=None):
            body = [] if value is None else [value]
            request = urllib.request.Request(base + '/api/' + name,
                                            data=json.dumps(body).encode(),
                                            headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=2) as response:
                response.read()

        def expect_gain(expected):
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if config()['output_gain_db'] == expected:
                    return
                time.sleep(.03)
            raise AssertionError(f'gain did not reach {expected}')

        for run in range(2):
            log = open(Path(temp) / f'engine-{run}.log', 'w')
            process = subprocess.Popen(command, env=env, cwd=install, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 6
                while True:
                    if process.poll() is not None:
                        raise RuntimeError(f'engine exited before startup: {process.returncode}')
                    try:
                        config()
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(.05)
                expect_gain(0 if run == 0 else -6)
                if run == 0:
                    post('set_output_gain', -6)
                    expect_gain(-6)
                    for invalid in [25, -25, 'loud']:
                        post('set_output_gain', invalid)
                        time.sleep(.1)
                        expect_gain(-6)
                    post('settings_begin')
                    post('set_output_gain', -12)
                    expect_gain(-12)
                    post('settings_cancel')
                    expect_gain(-6)
                    with urllib.request.urlopen(base + '/events', timeout=3) as response:
                        for raw in response:
                            if raw.startswith(b'data:'):
                                state = json.loads(raw[5:]).get('state')
                                if state:
                                    level = state['output_level']
                                    assert level['gain_db'] == -6, level
                                    assert all(key in level for key in ['peak_dbfs', 'rms_dbfs', 'clipped_total'])
                                    print(json.dumps({'sse_output_level': level}))
                                    break
                        else:
                            raise AssertionError('SSE ended without an engine state')
                # --secs releases audio devices normally on every success/failure path.
                result = process.wait(timeout=17)
                assert result == 0, result
            finally:
                if process.poll() is None:
                    # Never kill a device-owning process: allow its finite --secs shutdown.
                    process.wait(timeout=20)
                log.close()
                print(Path(temp, f'engine-{run}.log').read_text(errors='replace')[-1000:])
        print('PASS: config validation, settings cancel, SSE, persisted restart; isolated APPDATA')


if __name__ == '__main__':
    main()

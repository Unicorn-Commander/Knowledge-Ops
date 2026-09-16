"""Scanner transport must preserve findings and clean up across daemon boundaries."""
import os
from pathlib import Path
import subprocess

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/scan_image_archive.sh'


def test_scanner_preserves_exit_and_cleanup(tmp_path):
    docker = tmp_path / 'docker'
    docker.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$CALLS"
case "$1" in
 create) echo scanner-id;;
 cp) case "$2" in scanner-id:*) echo report > "$3";; esac;;
 start) exit "$FINDINGS";;
 inspect) case "$3" in *Status*) echo exited;; *) echo "$FINDINGS";; esac;;
 rm) :;;
esac
''')
    docker.chmod(0o755)
    archive = tmp_path / 'image.tar'
    archive.write_text('nonempty fixture')
    for code in (0, 1, 2):
        calls = tmp_path / f'calls-{code}'
        result = subprocess.run(
            ['sh', str(SCRIPT), str(archive), str(tmp_path / 'report'), '--exit-code', '1'],
            env={**os.environ, 'PATH': f'{tmp_path}:{os.environ["PATH"]}',
                 'CALLS': str(calls), 'FINDINGS': str(code)},
            capture_output=True,
        )
        assert result.returncode == code
        log = calls.read_text()
        assert '--volume' not in log
        assert f'cp {archive} scanner-id:/tmp/image.tar' in log
        assert 'cp scanner-id:/tmp/report' in log
        assert 'rm -f scanner-id' in log

"""Approved Skill scripts execute only inside an explicitly configured container."""
import base64
import os
import shutil
import subprocess
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path

from core.config import get_settings
from core.services.skills import safe_path


def run_script(files, path: str, args: list[str]) -> dict:
    settings = get_settings()
    image = settings.skill_sandbox_image
    docker = shutil.which('docker')
    if not image or not docker:
        raise ValueError('Skill 脚本沙箱未配置：需要 Docker 和 SKILL_SANDBOX_IMAGE；不会在后端进程中直接执行脚本。')
    path = safe_path(path)
    interpreter = {'.py': ['python', '-I'], '.js': ['node']}.get(Path(path).suffix)
    if not path.startswith('scripts/') or not interpreter or len(args) > 32 or any(not isinstance(arg, str) or len(arg) > 4096 or '\x00' in arg for arg in args):
        raise ValueError('只允许 scripts/ 内的 Python/JavaScript 脚本和有限的字符串参数。')
    env = {key: value for key, value in os.environ.items() if key in {'PATH', 'HOME', 'USERPROFILE', 'SYSTEMROOT', 'TEMP', 'TMP', 'DOCKER_HOST', 'DOCKER_CONTEXT'}}
    name = 'lingshu-skill-' + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix='lingshu-skill-') as directory:
        root = Path(directory).resolve()
        root.chmod(0o755)
        for file in files:
            target = root / safe_path(file.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(file.content_base64))
            target.chmod(0o444)
        command = [docker, 'run', '--rm', '--pull=never', '--name', name, '--network=none', '--read-only', '--cap-drop=ALL',
                   '--security-opt=no-new-privileges', '--pids-limit=64', '--memory=256m', '--cpus=1', '--user=65534:65534',
                   '--tmpfs', '/tmp:rw,nosuid,size=64m,mode=1777', '--tmpfs', '/work:rw,nosuid,size=64m,mode=1777',
                   '--mount', f'type=bind,src={root},dst=/skill,readonly', '--workdir=/work', image, *interpreter, '/skill/' + path, *args]
        process = None
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
            future = pool.submit(process.stdout.read, 65537)
            raw = future.result(timeout=60)
            truncated = len(raw) > 65536
            if truncated:
                process.kill()
            process.wait(timeout=5)
            return {'exit_code': process.returncode, 'output': raw[:65536].decode('utf-8', errors='replace'), 'truncated': truncated}
        except (subprocess.TimeoutExpired, FutureTimeout) as exc:
            raise ValueError('Skill 脚本执行超过 60 秒，已终止沙箱。') from exc
        finally:
            try:
                subprocess.run([docker, 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, timeout=10)
            finally:
                if process:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)
                    process.stdout.close()
                pool.shutdown(wait=False, cancel_futures=True)

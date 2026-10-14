import shutil
import subprocess
from pathlib import Path

import pytest

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg não instalado")


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> Path:
    """Vídeo horizontal de 2 minutos com áudio, gerado localmente."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg não instalado")
    path = tmp_path_factory.mktemp("media") / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-t", "120", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
    )
    return path

"""Start the identical product runtime with experiment adapters outside source."""
from pathlib import Path
import os
import sys
import threading
import signal

# Test-only profile relocation before any product imports; does not change
# HOME, the logged-in user's profile, tool grants or tool execution policy.
if os.environ.get('DICT_AB_PROFILE_ROOT'):
    profile_root = Path(os.environ['DICT_AB_PROFILE_ROOT']).resolve(strict=True)
    Path.home = classmethod(lambda cls: profile_root)

BASE = Path(__file__).resolve().parent
SOURCE = (BASE.parent / "source").resolve()
sys.path[:0] = [str(SOURCE / "src"), str(SOURCE / "app/backend/tiangong-backend"), str(BASE)]
from instrumentation import install
install(os.environ["DICT_AB_OUT"], os.environ["DICT_AB_ARM"])
from total_gateway.runtime import GatewayRuntime
from total_gateway.bootstrap import GatewayConfig
from total_gateway.server import GatewayHttpServer
runtime=GatewayRuntime.start(GatewayConfig.from_environment())
server=GatewayHttpServer(runtime)
done=threading.Event()
out=Path(os.environ['DICT_AB_OUT'])
def watcher():
    while not done.wait(0.2):
        if (out/'stop.requested').exists():
            server.shutdown();return
def stop(*_):
    threading.Thread(target=server.shutdown,daemon=True).start()
signal.signal(signal.SIGTERM,stop)
threading.Thread(target=watcher,daemon=True).start()
try:
    server.serve_forever(poll_interval=0.2)
finally:
    done.set();server.server_close();runtime.close()
    (out/'shutdown-complete.json').write_text('{"runtime_closed":true}')

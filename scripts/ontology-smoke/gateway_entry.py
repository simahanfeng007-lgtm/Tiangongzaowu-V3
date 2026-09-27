"""Isolated profile; ordinary product server and unmodified model workflow."""
from pathlib import Path
import os
import signal
import sys
import threading

out = Path(os.environ['ONTOLOGY_RUN'])
profile = out / 'profile'
Path.home = classmethod(lambda cls: profile)
source = Path(os.environ['ONTOLOGY_SOURCE'])
sys.path[:0] = [str(source / 'src'), str(source / 'app/backend/tiangong-backend')]
# Reuse the product's credential identity mapping inside the isolated profile.
# A vendor key must never be rebound to an arbitrary custom endpoint by this harness.
from v3.model_endpoint import duqu_model_endpoint_config
from v3.peizhi import provider_identity_env_names
from v3.endpoint_security import validate_model_endpoint
endpoint = duqu_model_endpoint_config()
binding = validate_model_endpoint(endpoint.provider_identity, endpoint.base_url, resolve_dns=False)
slots = provider_identity_env_names(endpoint.provider_identity)
if not binding.official or not slots:
    raise ValueError('This smoke harness requires an official provider profile; custom endpoint credentials need separate explicit binding.')
os.environ[slots[0]] = os.environ['ONTOLOGY_ACCEPTANCE_KEY']
from instrumentation import install
install(out)
from total_gateway.runtime import GatewayRuntime
from total_gateway.bootstrap import GatewayConfig
from total_gateway.server import GatewayHttpServer

runtime = GatewayRuntime.start(GatewayConfig.from_environment())
server = GatewayHttpServer(runtime)
done = threading.Event()
def watch():
    while not done.wait(.2):
        if (out / 'stop.requested').exists():
            server.shutdown()
            return
def stop(*_):
    threading.Thread(target=server.shutdown, daemon=True).start()
signal.signal(signal.SIGTERM, stop)
threading.Thread(target=watch, daemon=True).start()
try:
    server.serve_forever(poll_interval=.2)
finally:
    done.set()
    server.server_close()
    runtime.close()
    (out / 'shutdown-complete.json').write_text('{"runtime_closed":true}')

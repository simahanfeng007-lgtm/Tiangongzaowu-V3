"""Register an owner-supplied MCP environment in the existing configuration.

Run on the target host, not as a model-authored arbitrary command. This does
not install software, discover a secret endpoint, or claim successful login.
"""
import argparse
import json
import os
import re
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("server")
    p.add_argument("--config", type=Path, default=Path.home()/".tiangong/v3/mcp_servers.json")
    transport = p.add_mutually_exclusive_group(required=True)
    transport.add_argument("--url")
    transport.add_argument("--command")
    p.add_argument("--argument", action="append", default=[])
    p.add_argument("--application", action="append", required=True)
    p.add_argument("--location", required=True)
    p.add_argument("--workspace", required=True)
    p.add_argument("--account-label", default="")
    p.add_argument("--header-env", action="append", default=[], metavar="HEADER=ENV_NAME")
    p.add_argument("--issuer")
    p.add_argument("--client-id")
    p.add_argument("--scope", action="append", default=[])
    p.add_argument("--binding", action="append", default=[], metavar="ACTION=TOOL=SCHEMA_SHA256")
    p.add_argument("--replace", action="store_true")
    args=p.parse_args()
    from capability_dictionary import load_dictionary
    from omni_body_skill.tools.mcp_client import load_server_config
    release=load_dictionary()
    apps={a["app_id"] for a in release.applications["apps"]}
    if set(args.application)-apps: p.error("unknown application ID; use the current application dictionary")
    if bool(args.issuer)!=bool(args.client_id): p.error("issuer and registered client-id are required together")
    if args.command and (args.issuer or args.header_env): p.error("OAuth and header-env require HTTP transport")
    if args.url and args.argument: p.error("argument applies only to stdio commands")
    if args.scope and not args.issuer: p.error("scope requires OAuth configuration")
    if not args.location.strip() or not args.workspace.strip(): p.error("actual location and workspace are required")
    if not args.server.strip() or args.server!=args.server.strip(): p.error("server name is invalid")
    path=args.config.expanduser().absolute()
    if path.is_symlink(): p.error("configuration cannot be a symlink")
    before=path.read_bytes() if path.exists() else None
    data=json.loads(before) if before else {"servers":{}}
    if not isinstance(data,dict) or not isinstance(data.get("servers"),dict): p.error("existing servers configuration must be an object")
    if args.server in data["servers"] and not args.replace: p.error("server already exists; use --replace deliberately")
    headers={}
    for item in args.header_env:
        key,sep,value=item.partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z0-9-]+",key) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",value): p.error("header-env requires HEADER=ENV_NAME; pass variable names, not secret values")
        headers[key]=value
    row={"transport":"streamable_http" if args.url else "stdio", "enabled":True,
         "applications":args.application, "environment":{"location":args.location,"workspace":args.workspace,"account_label":args.account_label},
         "session_mode":"scoped"}
    if args.url: row.update(url=args.url,header_env=headers)
    else: row.update(command=args.command,args=args.argument,cwd=args.workspace)
    if args.issuer: row["oauth"]={"issuer":args.issuer,"client_id":args.client_id,"scopes":args.scope}
    bindings={}
    for value in args.binding:
        parts=value.split("=",2)
        if len(parts)!=3 or parts[0] not in release.tools or not parts[1] or len(parts[2])!=64 or any(c not in "0123456789abcdef" for c in parts[2]):
            p.error("binding requires an existing action, actual tool name and observed schema SHA256")
        associated={a["app_id"] for a in release.applications["apps"] if parts[0] in a["actions"]}
        if not associated.intersection(args.application):p.error("bound action is not associated with the selected applications")
        bindings[parts[0]]={"tool":parts[1],"input_schema_sha256":parts[2]}
    row["action_bindings"]=bindings
    data["servers"][args.server]=row
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix=".mcp-config-",dir=path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            f.write(json.dumps(data,ensure_ascii=False,indent=2)+"\n");f.flush();os.fsync(f.fileno())
        parsed=load_server_config(temporary).get(args.server)
        if not parsed or parsed.get("configuration_error"): p.error("invalid connection configuration")
        if (path.read_bytes() if path.exists() else None)!=before: p.error("configuration changed; reread before retrying")
        os.chmod(temporary,0o600)
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({"server":args.server,"config":str(path),"applications":args.application,
        "connection_state":"configured_not_verified","next_action":"mcp.auth.begin" if args.issuer else "mcp.tools.list"},ensure_ascii=False))


if __name__=="__main__":main()

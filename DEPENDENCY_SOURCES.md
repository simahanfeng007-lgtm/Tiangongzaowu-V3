# Dependency source fallback policy

Tiangong uses the user's normal or upstream dependency source first.  A
mainland-China mirror is attempted only after the primary download fails.  This
keeps the default path fast for international users and avoids mixing package
indexes within one resolver run.

| Dependency | Primary | Fallback |
| --- | --- | --- |
| PyPI packages | pip/user default (`pypi.org` normally) | Tsinghua TUNA PyPI |
| Embedded CPython | `python.org` | Tsinghua TUNA Python mirror |
| npm packages | npm/user default (`registry.npmjs.org` normally) | npmmirror npm registry |
| Electron binary | Electron upstream/GitHub | npmmirror Electron mirror |
| electron-builder tools | electron-builder upstream/GitHub | npmmirror builder mirror |

TUNA does not provide an npm package registry or Electron/electron-builder
binary mirror, so those dependencies use the compatible mainland mirror shown
above.  TUNA's Node.js release mirror can be used by `n`, `fnm`, `nvm`, `nvs`,
Volta, or other Node installers; it is not an npm registry.

Environment overrides:

- `TIANGONG_DISABLE_DEPENDENCY_FALLBACK=1`: fail after the primary source.
- `TIANGONG_PYPI_FALLBACK_INDEX`: replace the TUNA PyPI fallback.
- `TIANGONG_NPM_FALLBACK_REGISTRY`: replace the npm fallback.
- `TIANGONG_ELECTRON_FALLBACK_MIRROR`: replace the Electron fallback.
- `TIANGONG_ELECTRON_BUILDER_FALLBACK_MIRROR`: replace the builder fallback.

The fallback scripts do not modify global pip or npm configuration.

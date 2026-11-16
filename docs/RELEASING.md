# Releasing

How a version of `cage` is published. Two destinations, one version number
(`pyproject.toml` `version` and `server.json` `version` must match):

1. **PyPI** - the Python package `claude-agent-governance-evals`.
2. **The Official MCP Registry** - the server `io.github.ianchallis001/cage-governance-classifier`.

Everything up to the publish step is automated and reversible. The publish steps
themselves are deliberate, maintainer-only actions (milestone M7).

## One-time setup (done once, by the maintainer)

### PyPI Trusted Publishing (no API token)

On PyPI, open the project (or reserve the name with a first manual upload), go to
**Publishing -> Add a trusted publisher**, and enter:

| Field       | Value                                   |
| ----------- | --------------------------------------- |
| Owner       | `ianchallis001`                         |
| Repository  | `claude-agent-governance-evals`         |
| Workflow    | `release.yml`                           |
| Environment | `pypi`                                  |

Then, on this repository, create a GitHub **Environment** named `pypi`
(Settings -> Environments). Add a required reviewer if you want a manual gate
before each publish.

### MCP Registry ownership

The registry verifies ownership by finding the line

```
mcp-name: io.github.ianchallis001/cage-governance-classifier
```

in the PyPI long description. It is already in `README.md` (the PyPI
`long_description`), so a PyPI release carries it automatically - no extra step.

## Cutting a release

1. Bump `version` in `pyproject.toml`, `src/cage/__init__.py` (if pinned there),
   and `server.json` to the same value. Commit on a branch, open a PR, merge.
2. Tag and publish a GitHub **Release** (`vX.Y.Z`). This triggers
   `.github/workflows/release.yml`: it builds the sdist + wheel, runs
   `twine check`, and - on a Release - publishes to PyPI via Trusted Publishing.
   No token is stored anywhere; the publish uses a short-lived OIDC token.
3. Publish to the MCP Registry with the `mcp-publisher` CLI:

   ```bash
   # authenticate (GitHub OIDC), then publish the checked-in server.json
   mcp-publisher login github
   mcp-publisher publish
   ```

   `server.json` is validated against the registry schema
   (`2025-12-11`) before it is accepted.

## Dry run (no publish)

- `python -m build` then `python -m twine check dist/*` validates the package
  locally.
- Running the `Publish to PyPI` workflow by hand (`workflow_dispatch`) runs the
  build-and-check job **without** publishing - the publish job is gated on a
  real Release event.

# ICE Docker sandbox

Start Docker Desktop in Linux-container mode. Build from the ICE installation directory:

```powershell
python -m sandbox build
```

Launch ICE from the project directory you want it to work on (use `ice` if its launcher is on PATH). Startup requires the sandbox image and Docker Engine; there is no automatic host-execution fallback.

ICE's UI, API client and clipboard stay on Windows. All model-requested filesystem tools and shell commands run inside a non-root Linux container. Only planning remains in the host process. The container receives a filtered copy in a named Docker volume, no host bind mounts, no Docker socket, and no Gemini key. Normal execution is offline, root filesystem is read-only, and CPU, memory and process counts are limited. Python, Node, npm, Bash and Git are included. Dependencies and Git metadata are not copied.

Commands use Bash and `/workspace` paths, not PowerShell. Existing command approvals remain, with a separate conservative POSIX policy. Clipboard access is a trusted UI action on Windows, never exposed as an agent tool.

## Reviewing and applying

- `/sandbox`: isolation status and pending file count.
- `/changes`: session file history; `/changes N` shows a file's combined diff.
- `/apply`: apply validated staged files to the host project. It can create, replace or delete project files. The broker checks all files for host conflicts first; it refuses to overwrite edits made in your editor since the previous snapshot/apply. Later filesystem failures can still cause a partial apply, which is reported with remaining files kept pending.
- `/install [auto|npm|pip] [directory]`: install from `package.json` or `requirements.txt`, after a separate approval for temporary general internet access. Examples: `/install`, `/install npm web`, `/install pip api`. The agent can request the same operation through `install_dependencies`.

Applying is explicit. Ordinary edits and commands never modify original host files. Only successful, validated snapshots are eligible for apply. Binary bytes are preserved during staging/apply; the text diff viewer represents binary content imperfectly.

## Installing dependencies

Rebuild the image after upgrading ICE's sandbox code: `python -m sandbox build`.

Installation uses the same working volume. After approval, ICE stops the offline container, starts the session with bridge networking, installs, then stops that container and recreates it with network `none`. Old background processes never get the install network window, and install-time processes do not survive into normal execution. Other tool operations are serialized during this step. If offline restoration cannot be verified, ICE stops the session instead of continuing online. The online container also has a bounded lifetime as a fallback for unexpected broker shutdown.

This is general internet access during installation, not a package-registry allowlist. It can include access to local network services reachable from Docker. No host credentials are supplied. Declining approval leaves the original offline container untouched. Cancellation stops execution and prevents automatic restart; a normal installation timeout/failure restores an offline container so you can keep working.

Node uses `npm ci` when a lockfile exists, otherwise `npm install`. Install scripts are skipped by default. Python recreates `/workspace/.venv` from `requirements.txt` and installs wheels only by default; later commands automatically use that environment. Requirements must be declared in the manifest, rather than installed ad hoc. If a package genuinely needs scripts or source builds, ask ICE to request `allow_scripts=True`; that is disclosed in the new approval prompt. Source builds may still need toolchains absent from the image.

Dependencies remain in Docker. `/apply` copies source and manifests/lockfiles, not `node_modules` or `.venv`. After applying, ICE prints Windows install/start instructions where it can infer them from manifests. Install dependencies on Windows once, and again when requirements change. Container dependencies are session-local and are not included in saved source recovery manifests.

## Exclusions and limits

The copy skips symlinks/junctions, `.git`, `.env` variants, common key/credential filenames, virtual environments, dependencies and caches. This is a filename filter, not a guarantee that arbitrary source files contain no secrets. Imports and exported snapshots are limited to 32 MiB / 5,000 files. Ordinary shell commands have a 1 MiB per-file write limit and captured output limit, and a maximum timeout of 300 seconds. Installation allows 128 MiB per file and up to 900 seconds, while captured output remains capped at 1 MiB per stream. These limits do not provide a total disk quota for the Docker volume. Secrets required by project tests are intentionally unavailable.

Snapshots are retained under the host project's `.ice-sandbox/sessions/` directory on exit if there is unapplied work. Recover a trusted manifest with:

```powershell
python -m sandbox recover "C:\path\project\.ice-sandbox\sessions\SESSION.json"
```

Recovery applies saved changes with the same path/conflict checks. If snapshot export failed, the manifest also references a retained Docker volume and recovery requires Docker. Review the manifest/project before applying. Snapshots contain project source; treat them like source backups.

Esc/Ctrl+C kills the session container when execution is active, including detached children. A command timeout also stops the sandbox. Valid remaining files are exported through a fresh read-only container. Restart ICE to execute more tools after stopping; `/changes` and `/apply` remain available. Closing normally removes session containers and volumes after saving unapplied work. If export fails, the volume is retained.

## Verification

```powershell
python -m unittest sandbox.test_sandbox -v
$env:ICE_RUN_DOCKER_TESTS = '1'
python -m unittest sandbox.test_sandbox.DockerIntegrationTests -v
```

Docker is an isolation layer, not a claim of a perfectly secure boundary against all kernel/runtime vulnerabilities. Keep Docker Desktop updated. The embedded Python tools remain callable directly by developers outside the agent; the CLI agent's tool router is what enforces sandbox execution.
